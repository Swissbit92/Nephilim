# src/coordinator/research_trace.py
"""One JSONL line per research turn: which backend answered, what ran, how long.

Before this, none of it could be answered afterwards. `messages` stores no tool
calls and `latency_ms` is NULL on every assistant row, so "how often does the
model read a page" was a guess.

No query or answer text is written, only counts and names. Off unless
RESEARCH_TRACE_PATH is set, so a test run cannot write to a live file.

The record travels in a ContextVar for the same reason current_session_id does:
the tool brain runs synchronously in the request's own thread, and the executors
take (arguments, persona_card) with no room for it.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)

_current: ContextVar[Optional[Dict[str, Any]]] = ContextVar("research_turn", default=None)


def _write(record: Dict[str, Any]) -> None:
    from .config import get_settings

    path = get_settings().web_search.research_trace_path
    if not path or not isinstance(path, str):  # a mocked Settings answers truthy
        return
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:  # noqa: BLE001 - telemetry must never break a turn
        logger.warning(f"[ResearchTrace] write failed ({e})")


def _new(persona: str, path: str) -> Dict[str, Any]:
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "persona": persona,
        "path": path,
        "status": "",
        "tools": [],
        "searches": [],
        "pages_fetched": 0,
        "pages_failed": 0,
    }


@contextmanager
def turn(persona: str, path: str) -> Iterator[Dict[str, Any]]:
    """Trace one turn. Written on exit even when the body raises."""
    record = _new(persona, path)
    token = _current.set(record)
    started = time.monotonic()
    try:
        yield record
    finally:
        _current.reset(token)
        record["latency_ms"] = int((time.monotonic() - started) * 1000)
        _write(record)


def note_search(backend: str, category: str, results: Optional[int]) -> None:
    """`results` is None when the backend failed, as opposed to finding nothing."""
    record = _current.get()
    if record is not None:
        record["searches"].append({"backend": backend, "category": category, "results": results})


def note_fetch(ok: bool) -> None:
    record = _current.get()
    if record is not None:
        record["pages_fetched" if ok else "pages_failed"] += 1


def note_deflection(persona: str) -> None:
    """A turn refused before the tool loop ever started."""
    record = _new(persona, "tool_brain")
    record["status"] = "deflected"
    record["latency_ms"] = 0
    _write(record)
