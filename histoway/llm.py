"""Swappable LLM providers: the local AI Bridge, or any OpenAI-compatible chat endpoint (Gemini, Grok, Ollama)."""
import json
import os
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from .common import CONFIG


class LLMError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def bridge(instruction: str, source: str) -> str:
    """The AI Bridge on 127.0.0.1 (laptop only). Token from its own config, never printed."""
    cfg = tomllib.loads((Path.home() / ".config/ai-bridge/config.toml").read_text())["server"]
    body = json.dumps({"instruction": instruction, "input": source, "timeout": 300}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{cfg['port']}/ask", data=body, headers={
        "Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=330))["response"]
    except urllib.error.HTTPError as e:
        err = json.load(e).get("error", {})
        raise LLMError(err.get("code", f"HTTP_{e.code}"), f"{err.get('message')} {err.get('hint') or ''}")
    except urllib.error.URLError:
        raise LLMError("BRIDGE_DOWN", "AI Bridge is not running. Start it with: ai-up (then ai-check)")


def openai_compat(instruction: str, source: str, *, base_url: str, model: str, key_env: str) -> str:
    key = os.environ.get(key_env, "") if key_env else "none"
    if not key:
        raise LLMError("NO_KEY", f"set the {key_env} environment variable")
    body = json.dumps({"model": model, "messages": [
        {"role": "system", "content": instruction},
        {"role": "user", "content": "INPUT START\n" + source + "\nINPUT END"}]}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=300))["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        raise LLMError("RATE_LIMITED" if e.code == 429 else f"HTTP_{e.code}", e.read()[:300].decode(errors="replace"))
    except urllib.error.URLError as e:
        raise LLMError("UNREACHABLE", str(e.reason))


def ask(provider: str, instruction: str, source: str) -> str:
    if provider == "bridge":
        return bridge(instruction, source)
    if provider not in CONFIG["providers"]:
        raise LLMError("UNKNOWN_PROVIDER", f"{provider!r}; use bridge or one of {list(CONFIG['providers'])}")
    return openai_compat(instruction, source, **CONFIG["providers"][provider])
