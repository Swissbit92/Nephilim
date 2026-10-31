#!/usr/bin/env python3
"""Telegram alert when SearXNG stops answering. Run by com.nephilim.searxng-watch.

"Up" is not enough: engines block this IP with CAPTCHAs while /healthz stays 200,
so the probe runs a real image search, the category nsfw personas and media
searches have no fallback for. Nothing else notices, because a failed search just
looks like a companion with no results.

Alerts once after ALERT_AFTER failed runs in a row, and once on recovery. A send
that fails leaves the state unchanged, so the next run tries again.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE_URL = "http://127.0.0.1:8888"
GATEWAY_ENV = REPO / "services" / "telegram-gateway" / ".env"
STATE_FILE = REPO / "logs" / "searxng_watch.state.json"
ALERT_AFTER = 2
SEND_ATTEMPTS = 3
SEND_BACKOFF_SECONDS = 5
TIMEOUT_SECONDS = 25


def probe(base_url: str = BASE_URL) -> str | None:
    """Return None when healthy, else what is wrong."""
    query = urllib.parse.urlencode(
        {"q": "swiss alps", "format": "json", "categories": "images", "safesearch": 0}
    )
    try:
        with urllib.request.urlopen(f"{base_url}/search?{query}", timeout=TIMEOUT_SECONDS) as r:
            data = json.load(r)
    except (OSError, ValueError) as e:
        return f"not answering ({e})"
    if not data.get("results"):
        blocked = sorted({u[0] for u in data.get("unresponsive_engines", [])})
        return f"up but returning nothing (blocked: {', '.join(blocked) or 'none reported'})"
    return None


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def send_telegram(text: str, env_path: Path = GATEWAY_ENV) -> bool:
    env = read_env(env_path) if env_path.exists() else {}
    token = env.get("TG_BOT_TOKEN", "")
    # The first allowlisted id is the operator's; others are private chat partners.
    chat_id = env.get("TG_ALLOWED_CHAT_IDS", "").split(",")[0].strip()
    if not token or not chat_id:
        print("no Telegram credentials", file=sys.stderr)
        return False
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for attempt in range(1, SEND_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, data=body, timeout=TIMEOUT_SECONDS):
                return True
        except OSError as e:
            # The type only: the request URL carries the token.
            print(f"send attempt {attempt} failed: {type(e).__name__}", file=sys.stderr)
            if attempt < SEND_ATTEMPTS:
                time.sleep(SEND_BACKOFF_SECONDS)
    return False


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"failures": 0, "alerted": False}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def step(state: dict, problem: str | None, send: Callable[[str], bool]) -> dict:
    """Advance the failure counter and send whatever this run owes."""
    if problem is None:
        if state["alerted"] and not send(
            "✅ SearXNG recovered. Image and video search work again."
        ):
            return state
        return {"failures": 0, "alerted": False}

    failures = state["failures"] + 1
    if state["alerted"] or failures < ALERT_AFTER:
        return {**state, "failures": failures}
    sent = send(
        f"⚠️ SearXNG is {problem}.\n"
        "Gwen's searches and all image and video search return nothing until it is back."
    )
    return {"failures": failures, "alerted": sent}


def main() -> int:
    state = load_state(STATE_FILE)
    problem = probe()
    print(f"{time.strftime('%F %T')} {problem or 'ok'}")
    save_state(STATE_FILE, step(state, problem, send_telegram))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
