"""Stories held for review: list them in the terminal or on a local web page, approve them, or send them back."""
import html
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import narrate
from .common import cache_delete, cache_get, cache_keys, cache_put


def held(everything: bool = False) -> list[tuple[str, dict]]:
    """(id, story) for current stories needing review; with everything=True, all current stories."""
    out = []
    for key in cache_keys("story"):
        sid, version = key.split(":")[0], key.rsplit(":", 1)[1]
        s = cache_get("story", key)
        if version in narrate.VERSIONS and (everything or s["status"] == "needs_review"):
            out.append((sid, s))
    return sorted(out, key=lambda x: x[1]["name"])


def problems(s: dict) -> list[str]:
    fc = s.get("factcheck") or {}
    return s["flags"] + fc.get("problems", []) + [f"(minor) {m}" for m in fc.get("minor", [])]


def approve(sid: str) -> bool:
    keys = cache_keys("story", sid + ":")
    for k in keys:
        cache_put("story", k, cache_get("story", k) | {"status": "approved"})
    return bool(keys)


def redo(sid: str, note: str | None) -> None:
    for k in cache_keys("story", sid + ":"):
        cache_delete("story", k)
    if note:
        cache_put("note", sid, note)


def show() -> None:
    for sid, s in held():
        url = s["sources"][0]["url"] if s["sources"] else ""
        print(f"\n=== {s['name']} [{sid}]  {url}\nFLAGS: " + "; ".join(problems(s)))
        print(f"\n{s['script']}\n" + (f"\nMORE: {s['more_script']}\n" if s["more_script"] else ""))
    print("\napprove: python -m histoway approve ID   |   regenerate: python -m histoway redo ID --note \"...\"")


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Histoway review</title><style>
body {{ font: 16px/1.5 system-ui, sans-serif; max-width: 860px; margin: 0 auto; padding: 16px; background: #f6f4ee; color: #1d1d1b; }}
@media (prefers-color-scheme: dark) {{ body {{ background: #121411; color: #e9e7df; }} .card {{ background: #1c1f1b !important; border-color: #33372f !important; }} }}
.card {{ background: #fff; border: 1px solid #d9d5c9; border-radius: 12px; padding: 14px; margin: 14px 0; }}
.flag {{ color: #b4521b; }} .muted {{ opacity: .7; font-size: 14px; }} pre {{ white-space: pre-wrap; font-size: 13px; }}
form {{ display: inline-block; margin: 6px 8px 0 0; }} textarea {{ width: 100%; min-height: 3em; font: inherit; }}
button {{ font: inherit; padding: 6px 14px; border-radius: 8px; }}
</style></head><body><h1>Histoway review</h1>
<p class="muted">{count} stories {what}. <a href="/{other}">{other_label}</a>. Changes apply at the next <code>python -m histoway build</code>.</p>
{cards}</body></html>"""

CARD = """<div class="card"><h2>{name} <span class="muted">[{sid}] · {status}</span></h2>
<ul>{flags}</ul><p>{script}</p>{more}<p class="muted">{sources}</p>
<details><summary class="muted">Source text</summary><pre>{source}</pre></details>
<form method="post" action="/approve"><input type="hidden" name="sid" value="{sid}"><input type="hidden" name="token" value="{token}">
<button>Approve</button></form>
<form method="post" action="/redo" style="display:block"><input type="hidden" name="sid" value="{sid}"><input type="hidden" name="token" value="{token}">
<textarea name="note" placeholder="Optional note for the LLM, e.g. include the deportations"></textarea><button>Regenerate</button></form></div>"""


def page(everything: bool, token: str) -> str:
    e = html.escape
    cards = "".join(CARD.format(
        sid=e(sid), name=e(s["name"]), status=e(s["status"]), token=token,
        flags="".join(f'<li class="flag">{e(f)}</li>' for f in problems(s)),
        script=e(s["script"]), more=f"<p><b>More:</b> {e(s['more_script'])}</p>" if s["more_script"] else "",
        sources=" · ".join(f'<a href="{e(d["url"] or "")}">{e(d["title"])}</a>' for d in s["sources"]),
        source=e(s.get("source_text") or "(not stored; rebuild to add it)")) for sid, s in held(everything))
    return PAGE.format(count=len(held(everything)), what="in total" if everything else "held for review",
                       other="" if everything else "?all=1",
                       other_label="Only held stories" if everything else "Show all stories", cards=cards)


def serve(port: int = 8766) -> None:
    """Review page on 127.0.0.1 only. Every form carries a random token, so other web pages cannot post to it."""
    token = secrets.token_urlsafe(16)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: str = "", location: str | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            if self.headers.get("Host") not in hosts:
                return self._send(403, "wrong host")
            if urllib.parse.urlsplit(self.path).path != "/":
                return self._send(404, "not found")
            self._send(200, page("all=1" in self.path, token))

        def do_POST(self):
            form = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
            get = lambda k: form.get(k, [""])[0]  # noqa: E731
            if self.headers.get("Host") not in hosts or not secrets.compare_digest(get("token"), token):
                return self._send(403, "bad token: reload the review page")
            if self.path == "/approve":
                approve(get("sid"))
            elif self.path == "/redo":
                redo(get("sid"), get("note").strip() or None)
            self._send(303, location="/")

        def log_message(self, *args):
            pass

    print(f"Review page: http://127.0.0.1:{port}/   (Ctrl+C to stop)")
    try:
        HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        pass
