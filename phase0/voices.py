"""Phase 0 voice test: render stories with free neural TTS for listening on the phone.

  .venv/bin/python phase0/voices.py compare            one story, every candidate voice
  .venv/bin/python phase0/voices.py all kokoro:af_heart all 10 stories in the chosen voice

Engines:
  kokoro  local, offline, Apache-2.0. Model files in models/ (from huggingface.co/fastrtc/kokoro-onnx)
  edge    Microsoft Edge's online read-aloud voices via edge-tts. Free, no key, but unofficial use
          of that service (same risk class as the AI Bridge) -- fine for testing, not for a product.
"""
import asyncio
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out" / "voices"
MODELS = ROOT.parent / "models"
CANDIDATES = ["kokoro:af_heart", "kokoro:bm_george", "kokoro:bf_emma",
              "edge:en-IN-NeerjaNeural", "edge:en-IN-PrabhatNeural", "edge:en-US-AvaMultilingualNeural"]
COMPARE_STORY = "Attara Kacheri"

_kokoro = None


def render(spec: str, text: str, stem: Path) -> Path:
    engine, voice = spec.split(":", 1)
    if engine == "kokoro":
        global _kokoro
        if _kokoro is None:
            import soundfile  # noqa: F401  (fail early if missing)
            from kokoro_onnx import Kokoro
            _kokoro = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
        import soundfile
        samples, rate = _kokoro.create(text, voice=voice, speed=1.05, lang="en-gb" if voice[:1] == "b" else "en-us")
        path = stem.with_suffix(".wav")
        soundfile.write(path, samples, rate)
        return path
    if engine == "edge":
        import edge_tts
        path = stem.with_suffix(".mp3")
        asyncio.run(edge_tts.Communicate(text, voice, rate="+5%").save(str(path)))
        return path
    raise SystemExit(f"unknown engine {engine!r}; use kokoro:<voice> or edge:<voice>")


def page(title: str, items: list[tuple[str, str, Path]]) -> None:
    rows = "\n".join(f'<div class="card"><strong>{html.escape(h)}</strong><div class="meta">{html.escape(sub)}</div>'
                     f'<audio controls preload="none" src="{p.name}"></audio></div>' for h, sub, p in items)
    (OUT / "index.html").write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Histoway voices</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:760px;margin:0 auto;padding:16px;background:#fff;color:#222}}
@media(prefers-color-scheme:dark){{body{{background:#121212;color:#ddd}}.card{{border-color:#444}}}}
.card{{border:1px solid #ccc;border-radius:8px;padding:12px;margin:12px 0}}.meta{{font-size:13px;opacity:.75}}audio{{width:100%;margin-top:8px}}</style>
</head><body><h1>{html.escape(title)}</h1>{rows}
<script>const a=[...document.querySelectorAll("audio")];a.forEach((x,i)=>x.onended=()=>a[i+1]?.play());</script></body></html>""")


def main() -> None:
    stories = json.loads((ROOT / "out" / "stories.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    mode = sys.argv[1] if len(sys.argv) > 1 else "compare"
    items = []
    if mode == "compare":
        text = stories[COMPARE_STORY]["script"]
        for spec in CANDIDATES:
            print(f"-> {spec}", flush=True)
            path = render(spec, text, OUT / f"compare_{spec.replace(':', '_')}")
            items.append((spec.split(":")[1], "offline, local" if spec.startswith("kokoro") else "online, unofficial", path))
        page(f"Voice comparison: {COMPARE_STORY}", items)
    elif mode == "all" and len(sys.argv) == 3:
        spec = sys.argv[2]
        for i, (name, s) in enumerate(stories.items(), 1):
            print(f"-> {name}", flush=True)
            items.append((f"{i}. {name}", spec, render(spec, s["script"], OUT / f"{i:02d}_{spec.replace(':', '_')}")))
        page(f"Histoway stories: {spec}", items)
    else:
        raise SystemExit(__doc__)
    print(f"Done: {OUT / 'index.html'}")


if __name__ == "__main__":
    main()
