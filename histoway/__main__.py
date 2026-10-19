"""Histoway trip-pack builder.

  python -m histoway build "Bangalore" "Mysore" [--llm gemini] [--dry]   route -> places -> stories -> pack
  python -m histoway review [--web]            stories held for review (--web: approve/regenerate in the browser)
  python -m histoway approve ID [ID ...]       play these held stories after all (ID: a QID, intro-..., link-...)
  python -m histoway redo ID [--note "..."]    regenerate one story, optionally telling the LLM what to fix

Packs land in app/packs/<slug>/ (pack.json + audio/). Serve app/ and open it on the phone (see implementation.md).
"""
import argparse
import json
import re
import shutil

from . import narrate, places, review, route
from .common import CONFIG, ROOT, cache_get

PACKS = ROOT / "app" / "packs"


def slug(a: str, b: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{a} {b}".lower()).strip("-")


def entry(p: dict, s: dict, out) -> dict:
    """One place of the pack, with its MP3s copied into out/audio."""
    def audio(text):
        mp3 = narrate.render(text, CONFIG["voice"])
        if mp3:
            shutil.copy(mp3, out / "audio" / mp3.name)
        return f"audio/{mp3.name}" if mp3 else None

    e = {"qid": p["qid"], "name": p["name"], "also": [x["name"] for x in p.get("also", [])], "km": p["km"],
         "side": p["side"], "off_km": p["off_km"], "tier": p["tier"], "types": p.get("types", []),
         "score": p.get("score"), "script": s["script"], "audio": audio(s["script"]), "more_script": s["more_script"],
         "more_audio": audio(s["more_script"]), "has_dark_history": s["has_dark_history"], "sources": s["sources"]}
    e |= {k: p[k] for k in ("late_km", "gap_s") if k in p}
    if p.get("region"):
        e["late_km"] = 5.0  # a story about a district works anywhere inside it
    if not p["qid"].startswith(("intro-", "outro-")):
        line = narrate.again_line(p)
        e["again"] = {"script": line, "audio": audio(line)}
    if (link := cache_get("story", p.get("link_key", ""))) and narrate.playable(link):
        e["link"] = {"after": link["after"], "script": link["script"], "audio": audio(link["script"])}
    return e


def build(origin: str, dest: str, provider: str, dry: bool) -> None:
    a, b = route.geocode(origin), route.geocode(dest)
    print(f"Route: {a[2]}  ->  {b[2]}", flush=True)
    rt = route.route(a[:2], b[:2])
    print(f"{rt['total_km']} km; finding places...", flush=True)
    picks = places.find(rt)
    print("finding district and state lines...", flush=True)
    picks = sorted(picks + places.regions(rt, {p["qid"] for p in picks}), key=lambda p: p["km"])
    print(f"{len(picks)} places picked:")
    for p in picks:
        also = f" (+ {', '.join(x['name'] for x in p['also'])})" if p.get("also") else ""
        print(f"  km {p['km']:6.1f}  {p['tier']}  {p['name']}{also}  [{p['qid']}]" + (f"  {p['region']} line" if p.get("region") else ""))
    if dry:
        return
    sl, checker = slug(origin, dest), CONFIG["factcheck"]
    keys = narrate.write_stories(picks, f"{origin} to {dest} by road", origin, rt["total_km"], provider)
    if checker:  # before the journey pieces, which only tease places whose stories will play
        narrate.factcheck(keys, checker)
    ends = narrate.journey(picks, origin, dest, sl, rt["total_km"], provider)
    keys = [p["key"] for p in ends] + narrate.threads(picks, origin, dest, provider)
    if checker:
        narrate.factcheck(keys, checker)

    out = PACKS / sl
    shutil.rmtree(out, ignore_errors=True)
    (out / "audio").mkdir(parents=True)
    pack, held, unchecked = [], [], []
    for p in sorted(picks + ends, key=lambda p: p["km"]):
        s = cache_get("story", narrate.story_key(p))
        if narrate.playable(s):
            pack.append(entry(p, s, out))
        elif s and s["status"] == "needs_review":
            held.append(p["name"])
        elif s:
            unchecked.append(p["name"])
    (out / "pack.json").write_text(json.dumps({
        "pack_version": 2, "title": f"{origin} to {dest}", "origin": origin, "dest": dest,
        "total_km": rt["total_km"], "voice": CONFIG["voice"], "route": {"points": rt["points"], "km": rt["km"]},
        "engine": {"minScore": CONFIG["select"]["min_score"], "skipPenalty": CONFIG["select"]["skip_penalty"]},
        "live": {"prompt": narrate.PROMPT, "words": CONFIG["select"]["words"], "review": CONFIG["review"]},
        "places": pack}, ensure_ascii=False))
    index = sorted({d.name for d in PACKS.iterdir() if (d / "pack.json").exists()})
    (PACKS / "index.json").write_text(json.dumps([{"id": d, "title": json.loads((PACKS / d / "pack.json").read_text())["title"]}
                                                  for d in index]))
    print(f"\nPack: {out / 'pack.json'}  ({len(pack)} stories ready)")
    if held:
        print(f"Held for review ({len(held)}): {' | '.join(held)}\n  -> python -m histoway review --web")
    if unchecked:
        print(f"Not fact-checked yet ({len(unchecked)}): {' | '.join(unchecked)}\n  -> rerun the same build to finish")


def main() -> None:
    ap = argparse.ArgumentParser(prog="histoway", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("origin")
    b.add_argument("dest")
    b.add_argument("--llm", default=CONFIG["llm"])
    b.add_argument("--dry", action="store_true", help="route and places only, no LLM")
    sub.add_parser("review").add_argument("--web", action="store_true", help="review page on http://127.0.0.1:8766/")
    a = sub.add_parser("approve")
    a.add_argument("ids", nargs="+")
    r = sub.add_parser("redo")
    r.add_argument("id")
    r.add_argument("--note")
    args = ap.parse_args()
    if args.cmd == "build":
        build(args.origin, args.dest, args.llm, args.dry)
    elif args.cmd == "review":
        review.serve() if args.web else review.show()
    elif args.cmd == "approve":
        for sid in args.ids:
            print(f"{sid}: {'approved' if review.approve(sid) else 'no story found'}")
        print("Rebuild the pack to include them (cached, so it is quick).")
    elif args.cmd == "redo":
        review.redo(args.id, args.note)
        print("Story dropped; the next build regenerates it.")


if __name__ == "__main__":
    main()
