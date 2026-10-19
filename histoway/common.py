"""Config, the SQLite cache, and polite HTTP for the free public APIs."""
import json
import sqlite3
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text())
UA = "Histoway/0.1 (personal prototype; road-trip storyteller)"
_db = None


def db() -> sqlite3.Connection:
    global _db
    if _db is None:
        (ROOT / "cache").mkdir(exist_ok=True)
        _db = sqlite3.connect(ROOT / "cache" / "histoway.sqlite")
        _db.execute("CREATE TABLE IF NOT EXISTS cache (kind TEXT, key TEXT, value TEXT, at REAL, PRIMARY KEY (kind, key))")
    return _db


def cache_get(kind: str, key: str):
    row = db().execute("SELECT value FROM cache WHERE kind = ? AND key = ?", (kind, key)).fetchone()
    return json.loads(row[0]) if row else None


def cache_put(kind: str, key: str, value) -> None:
    with db():
        db().execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?)",
                     (kind, key, json.dumps(value, ensure_ascii=False), time.time()))


def cache_keys(kind: str, prefix: str = "") -> list[str]:
    return [r[0] for r in db().execute("SELECT key FROM cache WHERE kind = ? AND key LIKE ?", (kind, prefix + "%"))]


def cache_delete(kind: str, key: str) -> None:
    with db():
        db().execute("DELETE FROM cache WHERE kind = ? AND key = ?", (kind, key))


_last_call: dict[str, float] = {}


def get_json(url: str, params: dict | None = None, data: str | None = None, min_gap: float = 1.0, tries: int = 5):
    """GET (or form POST) returning JSON, cached forever by URL+body. One request per host per min_gap seconds
    (Nominatim's policy is 1/s; the others ask for similar politeness). Retries 429/5xx with backoff."""
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    key = full + ("\n" + data if data else "")
    if (hit := cache_get("http", key)) is not None:
        return hit
    host = urllib.parse.urlsplit(url).netloc
    for attempt in range(tries):
        time.sleep(max(0.0, _last_call.get(host, 0) + min_gap - time.time()))
        _last_call[host] = time.time()
        req = urllib.request.Request(full, data=data.encode() if data else None, headers={"User-Agent": UA})
        try:
            value = json.load(urllib.request.urlopen(req, timeout=120))
            cache_put("http", key, value)
            return value
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
        time.sleep(10 * 2 ** attempt)
