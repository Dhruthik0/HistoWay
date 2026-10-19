"""Picked places -> stories: batched LLM prompt, JSON parse, rule checks, cache. Then the journey pieces (opening,
closing, callbacks between stories), an optional fact check on a second provider, and MP3s in the chosen voice."""
import asyncio
import hashlib
import json
import os
import re
import time
import urllib.request
from string import Template

from .common import CONFIG, ROOT, cache_get, cache_put
from .llm import LLMError, ask
from .places import source


def _prompt(name: str) -> tuple[str, str]:
    text = (ROOT / "prompts" / name).read_text()
    return text, hashlib.sha1(text.encode()).hexdigest()[:8]


PROMPT, PROMPT_VERSION = _prompt("story.txt")
JOURNEY, JOURNEY_VERSION = _prompt("journey.txt")
LINK, LINK_VERSION = _prompt("link.txt")
FACTCHECK, FACTCHECK_VERSION = _prompt("factcheck.txt")
ANSWERS, ANSWERS_VERSION = _prompt("answers.txt")
VERSIONS = {PROMPT_VERSION, JOURNEY_VERSION, LINK_VERSION, ANSWERS_VERSION}  # cache keys end in one of these
SEL, REV = CONFIG["select"], CONFIG["review"]
SOURCE_CHARS = 7000  # per place: 3 places + the prompt stay under the Bridge's 32 KB request limit
ALSO_CHARS = 2000    # per merged neighbour, taken out of the place's own share
REQUEST_CHARS = 26000  # fact-check and callback batches are packed up to this much text
QUESTIONS = {"dark": "Tell me the dark history of this place", "why": "Why is this place here?"}  # the Ask button's fixed ones
ANSWER_WORDS = 75


def story_key(p: dict) -> str:
    return p.get("key") or f"{p['qid']}:{p['tier']}:{PROMPT_VERSION}"


def where(p: dict, origin: str, total_km: float) -> str:
    if p.get("region"):
        return f"crossing into {p['name']} (a {p['region']}) on the road, {p['km']:.0f} km from {origin}"
    if p.get("crossing"):
        return f"crossing the {p['name']} on the road, {p['km']:.0f} km from {origin}"
    side = "right beside the road" if p["off_km"] < 0.3 else f"about {p['off_km']:.1f} km off the road to the {p['side']}"
    return f"{p['km']:.0f} of {total_km:.0f} km from {origin}, {side}"


def again_line(p: dict) -> str:
    """One line for a place already told on an earlier trip."""
    if p.get("region"):
        return f"Back in {p['name']}."
    if p.get("crossing"):
        return f"Crossing the {p['name']} again."
    return f"Right beside you again: {p['name']}." if p["off_km"] < 0.3 else f"On your {p['side']} again: {p['name']}."


def gather(p: dict) -> list[dict]:
    also = p.get("also", [])
    return [source(p["name"], SOURCE_CHARS - ALSO_CHARS * len(also))] + [source(a["name"], ALSO_CHARS) for a in also]


def build_prompt(batch: list[tuple[dict, list[dict]]], trip: str, previous: list[str]) -> tuple[str, str]:
    lines = []
    for n, (p, _) in enumerate(batch, 1):
        name = p["name"] + (f" (one story that also covers: {', '.join(a['name'] for a in p['also'])})" if p.get("also") else "")
        more = (f'write "more_script" of about {SEL["more_words"]} words' if p["tier"] == "L" else 'set "more_script" to null')
        line = f"{n}. {name}. Where the listener is: {p['where']}. Length: about {SEL['words'][p['tier']]} words for \"script\" ({p['tier']} tier); {more}."
        if note := cache_get("note", p["qid"]):
            line += f" REVIEWER NOTE: {note}"
        lines.append(line)
    instruction = Template(PROMPT).substitute(trip=trip, previous=", ".join(previous[-2:]) or "nothing yet",
                                              places="\n".join(lines))
    src = "\n\n".join(f"=== PLACE {n}: {p['name']} ===\n" + "\n\n".join(f"[{d['title']}]\n{d['text']}" for d in docs)
                      for n, (p, docs) in enumerate(batch, 1))
    return instruction, src


def extract_json(reply: str):
    m = re.search(r"```(?:json)?\s*([\[{].*[\]}])\s*```", reply, re.S) or re.search(r"[\[{].*[\]}]", reply, re.S)
    if not m:
        raise ValueError("no JSON in reply")
    return json.loads(m.group(1) if m.re.groups else m.group(0))


def parse_reply(reply: str) -> list[dict]:
    items = extract_json(reply)
    items = [items] if isinstance(items, dict) else items
    for it in items:
        if not isinstance(it.get("script"), str) or not isinstance(it.get("claims"), list):
            raise ValueError("missing script/claims")
    return items


_STOP = {"I", "I'm", "I'd", "I'll", "I've", "OK", "Okay", "Oh", "Mr", "Mrs", "Sir",
         "First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Tenth"}  # regnal ordinals


def names(text: str) -> set[str]:
    """Capitalised words that are not at the start of a sentence: the people and places a script names."""
    found = re.findall(r"(?<![.!?\"'\n]\s)(?<!^)\b([A-Z][a-z]+(?:['’-][A-Za-z]+)?)", text)
    return {re.sub(r"['’]s$", "", w) for w in found} - _STOP


def check(story: dict, source_text: str, words: int, known: str = "", dark: bool = True) -> tuple[list[str], list[str]]:
    """Rule checks. flags -> needs_review (not played until approved). notes are for information only.
    known: names of the trip's other places, which a story may mention. Not proof of correctness.
    Mirrored in app/live.js for live stories: change both together."""
    text = story["script"] + " " + (story.get("more_script") or "")
    src, low = source_text.replace(",", ""), text.lower()
    flags = [f"year {y} not in source" for y in sorted(set(re.findall(r"\b(1[0-9]{3}|20[0-9]{2})\b", text)))
             if y not in src]
    flags += [f"number {n} not in source" for n in sorted(set(re.findall(r"\b\d[\d,.]*\d\b|\b\d\b", text)))
              if not re.fullmatch(r"1[0-9]{3}|20[0-9]{2}", n) and n.replace(",", "") not in src]
    flags += [f"name '{w}' not in source" for w in sorted(names(text)) if w.lower() not in (src + " " + known).lower()]
    if re.search(r"wikipedia|\bsources\b", text, re.I):
        flags.append("mentions sources/Wikipedia")
    if not story["claims"]:
        flags.append("no claims listed")
    src_low = src.lower()
    flags += [f"source mentions '{m.group(0)}', story does not" for w in REV["dark_themes"]
              if dark and (m := re.search(w, src_low)) and not re.search(w, low)]
    if hit := [w for w in REV["sensitive_words"] if re.search(w, low)]:
        flags.append(f"sensitive topic ({', '.join(hit)}): review by hand")
    n = len(story["script"].split())
    notes = [] if 0.6 * words <= n <= 1.4 * words else [f"length {n} words, target {words}"]
    return flags, notes


def settle(s: dict) -> dict:
    """Status from the rule flags and the fact check. Approved stays approved; "none" is a callback the LLM declined."""
    if s["status"] not in ("approved", "none"):
        s["status"] = "needs_review" if s["flags"] or (s.get("factcheck") or {}).get("problems") else "ok"
    return s


def playable(s: dict | None) -> bool:
    """Goes into the pack: passed (or approved), and fact-checked when a fact checker is configured."""
    return bool(s) and (s["status"] == "approved" or s["status"] == "ok" and (not CONFIG["factcheck"] or "factcheck" in s))


def record(name: str, story: dict, flags: list, notes: list, provider: str, version: str, docs: list[dict],
           source_text: str) -> dict:
    return settle({k: story.get(k) for k in ("script", "more_script", "claims", "has_dark_history")} | {
        "name": name, "flags": flags, "notes": notes, "status": "new",
        "provider": provider, "model": CONFIG["providers"].get(provider, {}).get("model", "chatgpt-web"),
        "prompt_version": version, "created_at": time.time(), "source_text": source_text,
        "sources": [{"title": d["title"], "url": d["url"], "revid": d["revid"]} for d in docs]})


def _ask(provider: str, instruction: str, src: str) -> str:
    for attempt in range(4):
        try:
            return ask(provider, instruction, src)
        except LLMError as e:
            if "RATE_LIMITED" not in e.code or attempt == 3:
                raise SystemExit(f"LLM stopped the build: {e}\nFinished work is cached; rerun the same command to resume.")
            print(f"   rate limited; waiting 5 minutes ({e.code})", flush=True)
            time.sleep(300)


_last_request = 0.0


def _paced(provider: str, instruction: str, src: str) -> str:
    """_ask, keeping pace_s between requests (fast bursts trip ChatGPT's throttle)."""
    global _last_request
    time.sleep(max(0.0, _last_request + CONFIG["pace_s"] - time.time()))
    try:
        return _ask(provider, instruction, src)
    finally:
        _last_request = time.time()


def _known(picks: list[dict]) -> str:
    return " ".join([p["name"] for p in picks] + [a["name"] for p in picks for a in p.get("also", [])])


def write_stories(picks: list[dict], trip: str, origin: str, total_km: float, provider: str) -> list[str]:
    """Re-check cached stories (so changed rules in config.toml apply without regenerating), then generate every
    pick without one, CONFIG["batch"] places per request. Returns the story keys."""
    for p in picks:
        p["where"] = where(p, origin, total_km)
    order = {p["qid"]: i for i, p in enumerate(picks)}
    known = _known(picks)
    for p in picks:
        if (s := cache_get("story", story_key(p))) and s["status"] != "approved":
            text = "\n".join(d["text"] for d in gather(p))
            flags, notes = check(s, text, SEL["words"][p["tier"]], known)
            cache_put("story", story_key(p), settle(s | {"flags": flags, "notes": notes, "source_text": text}))
    todo = [p for p in picks if cache_get("story", story_key(p)) is None]
    tries: dict[str, int] = {}
    size = CONFIG["batch"] if provider == "bridge" else 1
    while todo:
        batch, todo = todo[:size], todo[size:]
        docs = [(p, gather(p)) for p in batch]
        previous = [q["name"] for q in picks[:order[batch[0]["qid"]]]]
        instruction, src = build_prompt(docs, trip, previous)
        print(f"-> {', '.join(p['name'] for p in batch)} ({len((instruction + src).encode()) // 1024} KB)", flush=True)
        try:
            items = {int(it.get("n", i + 1)): it for i, it in enumerate(parse_reply(_paced(provider, instruction, src)))}
        except (ValueError, json.JSONDecodeError) as e:
            print(f"   bad reply ({e})", flush=True)
            items = {}
        for n, (p, ds) in enumerate(docs, 1):
            story = items.get(n)
            if story is None:
                tries[p["qid"]] = tries.get(p["qid"], 0) + 1
                if tries[p["qid"]] < 3:
                    todo.append(p)  # retried later, on its own if batches keep failing
                    size = 1 if tries[p["qid"]] > 1 else size
                else:
                    print(f"   giving up on {p['name']} for now; rerun to retry", flush=True)
                continue
            text = "\n".join(d["text"] for d in ds)
            flags, notes = check(story, text, SEL["words"][p["tier"]], known)
            s = record(p["name"], story, flags, notes, provider, PROMPT_VERSION, ds, text)
            cache_put("story", story_key(p), s)
            print(f"   {p['name']}: {len(s['script'].split())} words, {s['status']}"
                  + (f" ({'; '.join(flags)})" if flags else ""), flush=True)
    return [story_key(p) for p in picks]


def journey(picks: list[dict], origin: str, dest: str, slug: str, total_km: float, provider: str) -> list[dict]:
    """The opening (played as you set off) and closing (as you arrive) pieces, as pseudo-places at km 0 and near the
    end. One request per trip; cached. Returns the ones that exist, with their story key in "key"."""
    out = [{"qid": f"intro-{slug}", "name": f"Setting off from {origin}", "km": 0.0, "late_km": 3.0, "gap_s": 10, "words": 90},
           {"qid": f"outro-{slug}", "name": f"Arriving in {dest}", "km": round(max(0.0, total_km - 2), 2), "late_km": 2.5,
            "words": 120}]
    for p in out:
        p |= {"key": f"{p['qid']}:{JOURNEY_VERSION}", "tier": "M", "side": "crossing", "off_km": 0.0, "types": []}
    start, end = source(origin, 2500), source(dest, 5000)
    ahead = sorted((p for p in picks if not p.get("region") and playable(cache_get("story", story_key(p)))),
                   key=lambda p: -p.get("score", 0))[:8]
    along = [source(p["name"], 500) for p in ahead]
    texts = {"intro": "\n".join(d["text"] for d in [start, *along, end]), "outro": end["text"]}
    docs = {"intro": [start, *along, end], "outro": [end]}
    known = _known(picks) + f" {origin} {dest}"
    for p, part in zip(out, ("intro", "outro")):
        if (s := cache_get("story", p["key"])) and s["status"] != "approved":
            flags, notes = check(s, texts[part], p["words"], known)
            cache_put("story", p["key"], settle(s | {"flags": flags, "notes": notes}))
    if any(cache_get("story", p["key"]) is None for p in out):
        instruction = Template(JOURNEY).substitute(trip=f"{origin} to {dest} by road", origin=origin, dest=dest,
                                                   places=", ".join(p["name"] for p in sorted(ahead, key=lambda p: p["km"])) or "none")
        src = (f"=== WHERE THE TRIP STARTS ===\n[{start['title']}]\n{start['text']}\n\n=== PLACES ALONG THE WAY ===\n"
               + "\n\n".join(f"[{d['title']}]\n{d['text']}" for d in along)
               + f"\n\n=== WHERE THE TRIP ENDS ===\n[{end['title']}]\n{end['text']}")
        print(f"-> opening and closing ({len((instruction + src).encode()) // 1024} KB)", flush=True)
        try:
            reply = extract_json(_paced(provider, instruction, src))
            if not isinstance(reply, dict):
                raise ValueError("expected one JSON object")
        except (ValueError, json.JSONDecodeError) as e:
            print(f"   bad reply ({e}); no opening/closing this time, rerun to retry", flush=True)
            reply = {}
        for p, part in zip(out, ("intro", "outro")):
            if cache_get("story", p["key"]) is None and isinstance(reply.get(part), str):
                story = {"script": reply[part], "more_script": None, "claims": [{}], "has_dark_history": False}
                flags, notes = check(story, texts[part], p["words"], known)
                s = record(p["name"], story, flags, notes, provider, JOURNEY_VERSION, docs[part], texts[part])
                cache_put("story", p["key"], s)
                print(f"   {p['name']}: {len(s['script'].split())} words, {s['status']}"
                      + (f" ({'; '.join(flags)})" if flags else ""), flush=True)
    return [p for p in out if cache_get("story", p["key"])]


def _batches(items: list, size, most: int = 3) -> list[list]:
    """Pack items into requests of at most REQUEST_CHARS (by size(item)) and `most` items."""
    out = [[]]
    for it in items:
        if out[-1] and (len(out[-1]) == most or sum(map(size, out[-1])) + size(it) > REQUEST_CHARS):
            out.append([])
        out[-1].append(it)
    return [b for b in out if b]


# Shared by stories without linking them: peoples, languages, faiths and titles. Names, dynasties and events link.
_GENERIC = {w.lower() for w in """British English Indian India Kannada Hindi Tamil Telugu Hindu Hindus Muslim Muslims Christian
    Jain Buddhist Islam Portuguese French Dutch European Europeans Mughal Mughals Lord King Queen Prince Princess Emperor Maharaja
    Maharani Sultan Raja Nawab Governor General Colonel Captain Saint Temple Church Mosque River Hill Hills Lake Palace Fort
    Empire Kingdom Company East Government State City Town North South West Today Road Street Highway Bridge Railway
    Station Park Garden Gardens Museum Market Island Valley Dam""".split()}


def pair_up(told: list[tuple[dict, dict]], trip: str) -> list[tuple]:
    """For each story (in route order), the most recent earlier one naming the same person or event, in both
    scripts and both sources: [(a, story_a, b, story_b, shared names)]. Names in many stories (Karnataka,
    British...) link nothing; nor do the trip's own place names, or a pair whose later story already names the earlier."""
    trip_words = {w.lower() for w in re.findall(r"[A-Za-z]+", trip)} | _GENERIC
    found = [{w for w in names(s["script"]) if w.lower() not in trip_words and len(w) > 3
              and w.lower() in s["source_text"].lower()} for _, s in told]
    common = {w for ws in found for w in ws if sum(w in x for x in found) > max(3, len(told) // 4)}
    pairs = []
    for j, (b, sb) in enumerate(told):
        for i in range(j - 1, -1, -1):
            a, sa = told[i]
            if shared := (found[i] & found[j]) - common:
                if a["name"].lower() not in sb["script"].lower():
                    pairs.append((a, sa, b, sb, sorted(shared)))
                break
    return pairs


def threads(picks: list[dict], origin: str, dest: str, provider: str) -> list[str]:
    """Callbacks between stories: when a later story names the same person or event as an earlier one on this trip
    ("Remember Tipu, from Lalbagh?"), one spoken line that links them. The phone plays it before the later story,
    only if the earlier one was heard. Sets p["link_key"] on the later place; returns the keys."""
    told = [(p, s) for p in picks if not p.get("region") and playable(s := cache_get("story", story_key(p)))]
    pairs = pair_up(told, _known(picks) + f" {origin} {dest}")
    for a, _, b, _, _ in pairs:
        b["link_key"] = f"link-{a['qid']}-{b['qid']}:{LINK_VERSION}"
    known = _known(picks)
    todo = [x for x in pairs if cache_get("story", x[2]["link_key"]) is None]
    for batch in _batches(todo, lambda x: len(x[1]["script"]) + len(x[3]["script"]) + 200, most=8):
        instruction = Template(LINK).substitute(count=len(batch))
        src = "\n\n".join(f"=== PAIR {n}: shared names: {', '.join(shared)} ===\nEARLIER STORY ({a['name']}):\n{sa['script']}"
                          f"\n\nLATER STORY ({b['name']}):\n{sb['script']}"
                          for n, (a, sa, b, sb, shared) in enumerate(batch, 1))
        print(f"-> callbacks for {', '.join(x[2]['name'] for x in batch)}", flush=True)
        try:
            items = {int(it.get("n", i + 1)): it for i, it in enumerate(extract_json(_paced(provider, instruction, src)))}
        except (ValueError, json.JSONDecodeError, AttributeError) as e:
            print(f"   bad reply ({e}); rerun to retry", flush=True)
            continue
        for n, (a, sa, b, sb, _) in enumerate(batch, 1):
            if n not in items:
                continue
            line, text = items[n].get("line"), sa["script"] + "\n" + sb["script"]
            story = {"script": line or "", "more_script": None, "claims": [{}], "has_dark_history": False}
            flags, notes = check(story, text, 25, known, dark=False) if line else ([], [])
            s = record(f"{a['name']} -> {b['name']}", story, flags, notes, provider, LINK_VERSION, [], text)
            s |= {"after": a["qid"], "status": s["status"] if line else "none"}
            cache_put("story", b["link_key"], s)
            print(f"   {s['name']}: {s['status']}" + (f" ({'; '.join(flags)})" if flags else ""), flush=True)
    return [x[2]["link_key"] for x in pairs]


def answer_key(p: dict, q: str) -> str:
    return f"{q}-{p['qid']}:{ANSWERS_VERSION}"


def answers(picks: list[dict], provider: str) -> list[str]:
    """Spoken answers to the Ask button's fixed QUESTIONS, from each place's own source text, CONFIG["batch"] places
    per request. Cached, checked and reviewed like stories (IDs dark-<QID>, why-<QID>); a question the source cannot
    answer is stored with status "none", so it is not asked again. Returns the keys."""
    known, todo = _known(picks), []
    for p in picks:
        ds = gather(p)
        text = "\n".join(d["text"] for d in ds)
        for q in QUESTIONS:
            if (s := cache_get("story", answer_key(p, q))) and s["status"] not in ("approved", "none"):
                flags, notes = check(s, text, ANSWER_WORDS, known, dark=q == "dark")
                cache_put("story", answer_key(p, q), settle(s | {"flags": flags, "notes": notes, "source_text": text}))
        if any(cache_get("story", answer_key(p, q)) is None for q in QUESTIONS):
            todo.append((p, ds, text))
    for batch in _batches(todo, lambda x: len(x[2]), most=CONFIG["batch"] if provider == "bridge" else 1):
        lines = []
        for n, (p, _, _) in enumerate(batch, 1):
            lines.append(f"{n}. {p['name']}" + "".join(f' REVIEWER NOTE on "{q}": {note}' for q in QUESTIONS
                                                       if (note := cache_get("note", f"{q}-{p['qid']}"))))
        instruction = Template(ANSWERS).substitute(places="\n".join(lines))
        src = "\n\n".join(f"=== PLACE {n}: {p['name']} ===\n" + "\n\n".join(f"[{d['title']}]\n{d['text']}" for d in ds)
                          for n, (p, ds, _) in enumerate(batch, 1))
        print(f"-> answers for {', '.join(p['name'] for p, _, _ in batch)}", flush=True)
        try:
            items = {int(it.get("n", i + 1)): it for i, it in enumerate(extract_json(_paced(provider, instruction, src)))}
        except (ValueError, json.JSONDecodeError, AttributeError) as e:
            print(f"   bad reply ({e}); rerun to retry", flush=True)
            continue
        for n, (p, ds, text) in enumerate(batch, 1):
            it = items.get(n) if isinstance(items.get(n), dict) else {}
            for q in QUESTIONS:
                if q not in it or cache_get("story", answer_key(p, q)):
                    continue  # not answered this time (asked again next build), or already cached
                line = it[q] if isinstance(it[q], str) and it[q].strip() else None
                story = {"script": line or "", "more_script": None, "claims": [{}], "has_dark_history": q == "dark" and bool(line)}
                flags, notes = check(story, text, ANSWER_WORDS, known, dark=q == "dark") if line else ([], [])
                s = record(f"{p['name']}: {QUESTIONS[q]}", story, flags, notes, provider, ANSWERS_VERSION, ds, text)
                s["status"] = s["status"] if line else "none"
                cache_put("story", answer_key(p, q), s)
                print(f"   {s['name']}: {s['status']}" + (f" ({'; '.join(flags)})" if flags else ""), flush=True)
    return [answer_key(p, q) for p in picks for q in QUESTIONS]


def factcheck(keys: list[str], provider: str) -> None:
    """Second opinion: an LLM compares each story with its source text. Any problem it finds holds the story for
    review. Approved stories are left alone; a changed fact-check prompt or provider checks again."""
    version = f"{provider}:{FACTCHECK_VERSION}"
    todo = [(k, s) for k in keys if (s := cache_get("story", k)) and s["status"] not in ("approved", "none")
            and (s.get("factcheck") or {}).get("version") != version and s.get("source_text") is not None]
    for batch in _batches(todo, lambda x: len(x[1]["source_text"]) + len(x[1]["script"]) + len(x[1]["more_script"] or "")):
        instruction = Template(FACTCHECK).substitute(count=len(batch))
        src = "\n\n".join(f"=== STORY {n}: {s['name']} ===\nSOURCE TEXT:\n{s['source_text']}\n\nSTORY:\n{s['script']}"
                          + (f"\n\nSECOND STORY:\n{s['more_script']}" if s["more_script"] else "")
                          for n, (_, s) in enumerate(batch, 1))
        print(f"-> fact check: {', '.join(s['name'] for _, s in batch)}", flush=True)
        try:
            items = {int(it.get("n", i + 1)): it for i, it in enumerate(extract_json(_paced(provider, instruction, src)))}
        except (ValueError, json.JSONDecodeError, AttributeError) as e:
            print(f"   bad reply ({e}); rerun to retry", flush=True)
            continue
        for n, (k, s) in enumerate(batch, 1):
            if not isinstance(items.get(n, {}).get("problems"), list):
                continue
            problems = [f"fact check: {p.get('issue', p)}" + (f' ("{p["quote"]}")' if p.get("quote") else "")
                        if isinstance(p, dict) else f"fact check: {p}" for p in items[n]["problems"]]
            cache_put("story", k, settle(s | {"factcheck": {"version": version, "problems": problems}}))
            print(f"   {s['name']}: " + ("; ".join(problems) or "ok"), flush=True)


def render(text: str | None, voice: str) -> "Path | None":
    """MP3 for text in the configured voice, cached by content. None = the phone speaks it with its own TTS."""
    if not text or voice == "device":
        return None
    engine, name = voice.split(":", 1)
    if engine not in ("edge", "deepgram"):
        raise SystemExit(f"voice {voice!r}: use edge:<voice>, deepgram:<model> or device")
    path = ROOT / "cache" / "audio" / (hashlib.sha1(f"{voice}\n{text}".encode()).hexdigest()[:16] + ".mp3")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        try:
            if engine == "edge":
                import edge_tts  # Microsoft Edge's read-aloud voices: free, no key, unofficial use (personal prototype only)
                asyncio.run(edge_tts.Communicate(text, name, rate="+5%").save(str(tmp)))
            else:
                _deepgram(text, name, tmp)
        except Exception as e:  # network or service change: fall back to phone TTS for this story
            print(f"   warning: voice rendering failed ({e}); the phone will use its own TTS", flush=True)
            return None
        tmp.rename(path)
    return path


def _deepgram(text: str, model: str, out) -> None:
    """Deepgram Aura (paid after the signup credit). At most 2,000 characters per request, so the text goes in
    sentence chunks and the MP3s are joined (MP3 frames concatenate cleanly)."""
    if not (key := os.environ.get("DEEPGRAM_API_KEY")):
        raise SystemExit("voice deepgram:...: set the DEEPGRAM_API_KEY environment variable")
    chunks = [""]
    for sentence in re.findall(r"[^.!?]+[.!?]*[\"')\]]*\s*", text):
        if chunks[-1] and len(chunks[-1]) + len(sentence) > 2000:
            chunks.append("")
        chunks[-1] += sentence
    with open(out, "wb") as f:
        for c in chunks:
            req = urllib.request.Request(f"https://api.deepgram.com/v1/speak?model={model}",
                                         data=json.dumps({"text": c.strip()}).encode(),
                                         headers={"Authorization": f"Token {key}", "Content-Type": "application/json"})
            f.write(urllib.request.urlopen(req, timeout=120).read())
