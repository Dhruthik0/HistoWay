"""Histoway trip-pack builder.

  python -m histoway build "Bangalore" "Mysore" [--llm gemini] [--dry]   route -> places -> stories -> pack
  python -m histoway review                    flagged stories, waiting for you
  python -m histoway approve Q123 [Q456 ...]   play these flagged stories after all
  python -m histoway redo Q123 [--note "..."]  regenerate one place's story, optionally telling the LLM what to fix

Packs land in app/packs/<slug>/ (pack.json + audio/). Serve app/ and open it on the phone (see implementation.md).
"""
import argparse
import json
import re
import shutil

from . import narrate, places, route
from .common import CONFIG, ROOT, cache_delete, cache_get, cache_keys, cache_put

PACKS = ROOT / "app" / "packs"


def slug(a: str, b: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{a} {b}".lower()).strip("-")


def build(origin: str, dest: str, provider: str, dry: bool) -> None:
    a, b = route.geocode(origin), route.geocode(dest)
    print(f"Route: {a[2]}  ->  {b[2]}", flush=True)
    rt = route.route(a[:2], b[:2])
    print(f"{rt['total_km']} km; finding places...", flush=True)
    picks = places.find(rt)
    print(f"{len(picks)} places picked:")
    for p in picks:
        also = f" (+ {', '.join(x['name'] for x in p['also'])})" if p.get("also") else ""
        print(f"  km {p['km']:6.1f}  {p['tier']}  {p['name']}{also}  [{p['qid']}]")
    if dry:
        return
    trip = f"{origin} to {dest} by road"
    narrate.write_stories(picks, trip, origin, rt["total_km"], provider)
    narrate.recheck(picks)

    out = PACKS / slug(origin, dest)
    shutil.rmtree(out, ignore_errors=True)
    (out / "audio").mkdir(parents=True)
    pack, held = [], []
    for p in picks:
        s = cache_get("story", narrate.story_key(p))
        if s is None or s["status"] == "needs_review":
            held += [p["name"]] if s else []
            continue
        entry = {"qid": p["qid"], "name": p["name"], "also": [x["name"] for x in p.get("also", [])], "km": p["km"],
                 "side": p["side"], "off_km": p["off_km"], "tier": p["tier"], "script": s["script"],
                 "more_script": s["more_script"], "has_dark_history": s["has_dark_history"], "sources": s["sources"]}
        for key, text in (("audio", s["script"]), ("more_audio", s["more_script"])):
            mp3 = narrate.render(text, CONFIG["voice"])
            entry[key] = f"audio/{mp3.name}" if mp3 else None
            if mp3:
                shutil.copy(mp3, out / "audio" / mp3.name)
        pack.append(entry)
    (out / "pack.json").write_text(json.dumps({
        "pack_version": 1, "title": f"{origin} to {dest}", "origin": origin, "dest": dest,
        "total_km": rt["total_km"], "voice": CONFIG["voice"], "route": {"points": rt["points"], "km": rt["km"]},
        "places": pack}, ensure_ascii=False))
    index = sorted({d.name for d in PACKS.iterdir() if (d / "pack.json").exists()})
    (PACKS / "index.json").write_text(json.dumps([{"id": d, "title": json.loads((PACKS / d / "pack.json").read_text())["title"]}
                                                  for d in index]))
    print(f"\nPack: {out / 'pack.json'}  ({len(pack)} stories ready)")
    if held:
        print(f"Held for review ({len(held)}): {' | '.join(held)}\n  -> python -m histoway review")


def review() -> None:
    for key in cache_keys("story"):
        s = cache_get("story", key)
        if s["status"] == "needs_review" and key.endswith(narrate.PROMPT_VERSION):
            qid = key.split(":")[0]
            print(f"\n=== {s['name']} [{qid}]  {s['sources'][0]['url']}\nFLAGS: " + "; ".join(s["flags"]))
            print(f"\n{s['script']}\n" + (f"\nMORE: {s['more_script']}\n" if s["more_script"] else ""))
    print("\napprove: python -m histoway approve QID   |   regenerate: python -m histoway redo QID --note \"...\"")


def main() -> None:
    ap = argparse.ArgumentParser(prog="histoway", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("origin")
    b.add_argument("dest")
    b.add_argument("--llm", default=CONFIG["llm"])
    b.add_argument("--dry", action="store_true", help="route and places only, no LLM")
    sub.add_parser("review")
    a = sub.add_parser("approve")
    a.add_argument("qids", nargs="+")
    r = sub.add_parser("redo")
    r.add_argument("qid")
    r.add_argument("--note")
    args = ap.parse_args()
    if args.cmd == "build":
        build(args.origin, args.dest, args.llm, args.dry)
    elif args.cmd == "review":
        review()
    elif args.cmd == "approve":
        for q in args.qids:
            keys = cache_keys("story", q + ":")
            for k in keys:
                cache_put("story", k, cache_get("story", k) | {"status": "approved"})
            print(f"{q}: {'approved' if keys else 'no story found'}")
        print("Rebuild the pack to include them (cached, so it is quick).")
    elif args.cmd == "redo":
        for k in cache_keys("story", args.qid + ":"):
            cache_delete("story", k)
        if args.note:
            cache_put("note", args.qid, args.note)
        print("Story dropped; the next build regenerates it.")


if __name__ == "__main__":
    main()
