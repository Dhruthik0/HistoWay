"""Phase 0: generate stories for 10 hand-picked places and build a listening page.

Run:   python3 phase0/run.py            (needs the AI Bridge up: ai-up)
Then:  open phase0/out/listen.html      (or copy it to the phone)

Results are cached in out/stories.json; a rerun only generates what is missing.
Delete an entry (or the file) to regenerate it.
"""
import json
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
PROMPT = (ROOT / "story.txt").read_text()  # frozen Phase 0 prompt; prompts/story.txt is the batched one
UA = "Histoway/0.1 (personal prototype; phase0)"
TRIP = "Bengaluru to Mysuru by road"
WORDS = {"S": 60, "M": 180, "L": 400}
SOURCE_CHARS = 8000
PACE_S = 20  # gap between Bridge requests; fast bursts trip ChatGPT's throttle

# (Wikipedia title, tier, where the listener is, sensitive?) in driving order.
PLACES = [
    ("Vidhana Soudha", "M", "In central Bengaluru, at the start of the drive", False),
    ("Attara Kacheri", "M", "In central Bengaluru, at the start of the drive", False),
    ("Cubbon Park", "M", "Driving through central Bengaluru", False),
    ("M G Road, Bengaluru", "S", "Driving through central Bengaluru", False),
    ("Lal Bagh", "L", "Driving through south Bengaluru", True),
    ("Ramanagara", "M", "On the Bengaluru-Mysuru road, outside Bengaluru", False),
    ("Kaveri", "M", "Crossing the Kaveri river on the Bengaluru-Mysuru road, near Srirangapatna", False),
    ("Srirangapatna", "L", "Approaching Srirangapatna on the Bengaluru-Mysuru road", True),
    ("Mysore Palace", "L", "Arriving in Mysuru", False),
    ("Chamundi Hills", "M", "In Mysuru, with the hill visible on the skyline", False),
]

_JUNK = re.compile(r"see also|references|external links|further reading|gallery|notes|bibliography|citations|sources", re.I)
_HOOK = re.compile(r"histor|legend|myth|controvers|disput|conflict|war|battle|siege|demoli|disaster|fire|etymolog|origin", re.I)


def wiki_extract(title: str) -> tuple[str, str]:
    """Plain-text article -> (excerpt, url). Intro first, then history/conflict sections, then the rest."""
    q = urllib.parse.urlencode({"action": "query", "format": "json", "redirects": 1, "prop": "extracts|info",
                                "explaintext": 1, "inprop": "url", "titles": title})
    req = urllib.request.Request(f"https://en.wikipedia.org/w/api.php?{q}", headers={"User-Agent": UA})
    page = next(iter(json.load(urllib.request.urlopen(req, timeout=30))["query"]["pages"].values()))
    return pick_sections(page["extract"]), page["fullurl"]


def pick_sections(text: str, limit: int = SOURCE_CHARS) -> str:
    parts = re.split(r"\n(?===[^=])", text)  # split on level-2 "== Heading ==" only
    intro, sections = parts[0], [p for p in parts[1:] if not _JUNK.search(p.split("\n", 1)[0])]
    sections.sort(key=lambda p: not _HOOK.search(p.split("\n", 1)[0]))  # stable: hooks first, else article order
    return "\n".join([intro, *sections])[:limit]


def bridge(instruction: str, input_text: str) -> str:
    cfg = tomllib.loads((Path.home() / ".config/ai-bridge/config.toml").read_text())["server"]
    body = json.dumps({"instruction": instruction, "input": input_text, "timeout": 300}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{cfg['port']}/ask", data=body, headers={
        "Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=330))["response"]
    except urllib.error.HTTPError as e:
        err = json.load(e).get("error", {})
        raise SystemExit(f"Bridge error {err.get('code')}: {err.get('message')} {err.get('hint') or ''}")
    except urllib.error.URLError:
        raise SystemExit("AI Bridge is not running. Start it with: ai-up  (then ai-check)")


def parse_reply(reply: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", reply, re.S) or re.search(r"\{.*\}", reply, re.S)
    story = json.loads(m.group(1) if m.re.groups else m.group(0))
    if not isinstance(story.get("script"), str) or not isinstance(story.get("claims"), list):
        raise ValueError("missing script/claims")
    return story


def check(story: dict, source: str, target_words: int) -> list[str]:
    """Cheap grounding checks. Flags are for the human reviewer, not proof of correctness."""
    text = story["script"] + " " + (story.get("more_script") or "")
    flags = [f"year {y} not in source" for y in sorted(set(re.findall(r"\b(1[0-9]{3}|20[0-9]{2})\b", text)))
             if y not in source]
    words = len(story["script"].split())
    if not 0.6 * target_words <= words <= 1.4 * target_words:
        flags.append(f"length {words} words, target {target_words}")
    if re.search(r"wikipedia|\bsources\b", text, re.I):  # singular "source" is legit (a river's source)
        flags.append("mentions sources/Wikipedia")
    if not story["claims"]:
        flags.append("no claims listed")
    return flags


def main() -> None:
    OUT.mkdir(exist_ok=True)
    cache_file = OUT / "stories.json"
    stories = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    told: list[str] = []
    first = True
    for title, tier, context, sensitive in PLACES:
        if title not in stories:
            if not first:
                time.sleep(PACE_S)
            first = False
            source, url = wiki_extract(title)
            instruction = Template(PROMPT).substitute(
                trip=TRIP, context=context, previous=", ".join(told[-2:]) or "nothing yet",
                name=title, tier=tier, words=WORDS[tier],
                more_rule=' Also write "more_script" of about 250 words.' if tier == "L" else ' Set "more_script" to null.')
            print(f"-> {title} ({tier}, {len(source)} source chars)", flush=True)
            reply = bridge(instruction, source)
            try:
                story = parse_reply(reply)
            except (ValueError, AttributeError, json.JSONDecodeError):
                print("   reply was not valid JSON, retrying once", flush=True)
                time.sleep(PACE_S)
                story = parse_reply(bridge(instruction, source))
            story |= {"name": title, "tier": tier, "sensitive": sensitive, "url": url,
                      "flags": check(story, source, WORDS[tier])}
            stories[title] = story
            cache_file.write_text(json.dumps(stories, indent=2, ensure_ascii=False))
            print(f"   {len(story['script'].split())} words, flags: {story['flags'] or 'none'}", flush=True)
        told.append(title)

    ordered = [stories[t] for t, *_ in PLACES]
    html = (ROOT / "listen.template.html").read_text().replace("/*STORIES*/[]", json.dumps(ordered, ensure_ascii=False).replace("</", "<\\/"))
    (OUT / "listen.html").write_text(html)
    print(f"\nDone. Open {OUT / 'listen.html'}")


if __name__ == "__main__":
    sys.exit(main())
