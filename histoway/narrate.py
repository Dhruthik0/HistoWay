"""Picked places -> stories: batched LLM prompt, JSON parse, rule checks, cache. Then MP3s in the chosen voice."""
import asyncio
import hashlib
import json
import re
import time
from string import Template

from .common import CONFIG, ROOT, cache_get, cache_put
from .llm import LLMError, ask
from .places import source

PROMPT = (ROOT / "prompts" / "story.txt").read_text()
PROMPT_VERSION = hashlib.sha1(PROMPT.encode()).hexdigest()[:8]
SEL, REV = CONFIG["select"], CONFIG["review"]
SOURCE_CHARS = 7000  # per place: 3 places + the prompt stay under the Bridge's 32 KB request limit
ALSO_CHARS = 2000    # per merged neighbour, taken out of the place's own share


def story_key(p: dict) -> str:
    return f"{p['qid']}:{p['tier']}:{PROMPT_VERSION}"


def where(p: dict, origin: str, total_km: float) -> str:
    if p.get("crossing"):
        return f"crossing the {p['name']} on the road, {p['km']:.0f} km from {origin}"
    side = "right beside the road" if p["off_km"] < 0.3 else f"about {p['off_km']:.1f} km off the road to the {p['side']}"
    return f"{p['km']:.0f} of {total_km:.0f} km from {origin}, {side}"


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


def parse_reply(reply: str) -> list[dict]:
    m = re.search(r"```(?:json)?\s*([\[{].*[\]}])\s*```", reply, re.S) or re.search(r"\[.*\]", reply, re.S)
    if not m:
        raise ValueError("no JSON in reply")
    items = json.loads(m.group(1) if m.re.groups else m.group(0))
    items = [items] if isinstance(items, dict) else items
    for it in items:
        if not isinstance(it.get("script"), str) or not isinstance(it.get("claims"), list):
            raise ValueError("missing script/claims")
    return items


_STOP = {"I", "I'm", "I'd", "I'll", "I've", "OK", "Okay", "Oh", "Mr", "Mrs", "Sir",
         "First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Tenth"}  # regnal ordinals


def check(story: dict, source_text: str, words: int, known: str = "") -> tuple[list[str], list[str]]:
    """Rule checks. flags -> needs_review (not played until approved). notes are for information only.
    known: names of the trip's other places, which a story may mention. Not proof of correctness."""
    text = story["script"] + " " + (story.get("more_script") or "")
    src, low = source_text.replace(",", ""), text.lower()
    flags = [f"year {y} not in source" for y in sorted(set(re.findall(r"\b(1[0-9]{3}|20[0-9]{2})\b", text)))
             if y not in src]
    flags += [f"number {n} not in source" for n in sorted(set(re.findall(r"\b\d[\d,.]*\d\b|\b\d\b", text)))
              if not re.fullmatch(r"1[0-9]{3}|20[0-9]{2}", n) and n.replace(",", "") not in src]
    names = {re.sub(r"['’]s$", "", w) for w in re.findall(r"(?<![.!?\"'\n]\s)(?<!^)\b([A-Z][a-z]+(?:['’-][A-Za-z]+)?)", text)}
    flags += [f"name '{w}' not in source" for w in sorted(names - _STOP) if w.lower() not in (src + " " + known).lower()]
    if re.search(r"wikipedia|\bsources\b", text, re.I):
        flags.append("mentions sources/Wikipedia")
    if not story["claims"]:
        flags.append("no claims listed")
    src_low = src.lower()
    flags += [f"source mentions '{m.group(0)}', story does not" for w in REV["dark_themes"]
              if (m := re.search(w, src_low)) and not re.search(w, low)]
    if hit := [w for w in REV["sensitive_words"] if re.search(w, low)]:
        flags.append(f"sensitive topic ({', '.join(hit)}): review by hand")
    n = len(story["script"].split())
    notes = [] if 0.6 * words <= n <= 1.4 * words else [f"length {n} words, target {words}"]
    return flags, notes


def _ask(provider: str, instruction: str, src: str) -> str:
    for attempt in range(4):
        try:
            return ask(provider, instruction, src)
        except LLMError as e:
            if "RATE_LIMITED" not in e.code or attempt == 3:
                raise SystemExit(f"LLM stopped the build: {e}\nFinished stories are cached; rerun the same command to resume.")
            print(f"   rate limited; waiting 5 minutes ({e.code})", flush=True)
            time.sleep(300)


def write_stories(picks: list[dict], trip: str, origin: str, total_km: float, provider: str) -> None:
    """Generate every pick without a cached story, CONFIG["batch"] places per request."""
    for p in picks:
        p["where"] = where(p, origin, total_km)
    order = {p["qid"]: i for i, p in enumerate(picks)}
    known = " ".join([p["name"] for p in picks] + [a["name"] for p in picks for a in p.get("also", [])])
    todo = [p for p in picks if cache_get("story", story_key(p)) is None]
    tries: dict[str, int] = {}
    size = CONFIG["batch"] if provider == "bridge" else 1
    first = True
    while todo:
        batch, todo = todo[:size], todo[size:]
        if not first:
            time.sleep(CONFIG["pace_s"])
        first = False
        docs = [(p, gather(p)) for p in batch]
        previous = [q["name"] for q in picks[:order[batch[0]["qid"]]]]
        instruction, src = build_prompt(docs, trip, previous)
        print(f"-> {', '.join(p['name'] for p in batch)} ({len((instruction + src).encode()) // 1024} KB)", flush=True)
        try:
            items = {int(it.get("n", i + 1)): it for i, it in enumerate(parse_reply(_ask(provider, instruction, src)))}
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
            flags, notes = check(story, "\n".join(d["text"] for d in ds), SEL["words"][p["tier"]], known)
            story = {k: story.get(k) for k in ("script", "more_script", "claims", "has_dark_history")} | {
                "name": p["name"], "flags": flags, "notes": notes, "status": "needs_review" if flags else "ok",
                "provider": provider, "model": CONFIG["providers"].get(provider, {}).get("model", "chatgpt-web"),
                "prompt_version": PROMPT_VERSION, "created_at": time.time(),
                "sources": [{"title": d["title"], "url": d["url"], "revid": d["revid"]} for d in ds]}
            cache_put("story", story_key(p), story)
            print(f"   {p['name']}: {len(story['script'].split())} words, {story['status']}"
                  + (f" ({'; '.join(flags)})" if flags else ""), flush=True)


def recheck(picks: list[dict]) -> None:
    """Re-run the rule checks on cached stories, so changed rules in config.toml apply without regenerating.
    Stories you approved stay approved."""
    known = " ".join([p["name"] for p in picks] + [a["name"] for p in picks for a in p.get("also", [])])
    for p in picks:
        s = cache_get("story", story_key(p))
        if s and s["status"] != "approved":
            flags, notes = check(s, "\n".join(d["text"] for d in gather(p)), SEL["words"][p["tier"]], known)
            cache_put("story", story_key(p), s | {"flags": flags, "notes": notes,
                                                   "status": "needs_review" if flags else "ok"})


def render(text: str | None, voice: str) -> "Path | None":
    """MP3 for text in the configured voice, cached by content. None = the phone speaks it with its own TTS."""
    if not text or voice == "device":
        return None
    engine, name = voice.split(":", 1)
    if engine != "edge":
        raise SystemExit(f"voice {voice!r}: only edge:<voice> or device are supported")
    path = ROOT / "cache" / "audio" / (hashlib.sha1(f"{voice}\n{text}".encode()).hexdigest()[:16] + ".mp3")
    if not path.exists():
        import edge_tts  # Microsoft Edge's read-aloud voices: free, no key, unofficial use (personal prototype only)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        try:
            asyncio.run(edge_tts.Communicate(text, name, rate="+5%").save(str(tmp)))
        except Exception as e:  # network or service change: fall back to phone TTS for this story
            print(f"   warning: voice rendering failed ({e}); the phone will use its own TTS", flush=True)
            return None
        tmp.rename(path)
    return path
