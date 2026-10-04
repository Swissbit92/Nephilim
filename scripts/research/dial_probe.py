#!/usr/bin/env python3
"""Does the assertiveness dial change anything? Prompt level first, then behaviour.

Run with no args for the PROMPT check (free, deterministic, no model). This is the
baseline instrument: on the unchanged tree it reports IDENTICAL at every dial value,
which is the state the wiring is supposed to break.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.coordinator import prompt_builder as pb  # noqa: E402

CARD = Path(__file__).resolve().parents[2] / "personas" / "gwen.json"
DIAL = "assertiveness"
VALUES = [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0]


def prompt_for(value: float) -> str:
    """Build the prompt the way PRODUCTION does: from the card ON DISK, by key.

    An earlier version of this script passed the card DICT to
    ``build_system_prompt``, which takes a persona KEY. Every call fell through to
    the same fallback persona and reported IDENTICAL at every dial value — a
    broken instrument that looked like a finding. The card has to be written to
    disk and the lru_cache cleared, or nothing is being measured.
    """
    original = CARD.read_text()
    try:
        card = json.loads(original)
        card["emotional_profile"]["sliders"][DIAL] = value
        CARD.write_text(json.dumps(card, indent=2))
        pb.build_system_prompt.cache_clear()
        return pb.build_system_prompt("gwen")
    finally:
        CARD.write_text(original)
        pb.build_system_prompt.cache_clear()


def main() -> int:
    base = None
    seen: dict[str, list[float]] = {}
    for v in VALUES:
        p = prompt_for(v)
        h = hashlib.sha256(p.encode()).hexdigest()[:12]
        seen.setdefault(h, []).append(v)
        if base is None:
            base = h
        print(f"  {DIAL}={v:<4} sha={h} len={len(p)}")
    print()
    if len(seen) == 1:
        print(f"RESULT: IDENTICAL at all {len(VALUES)} dial values — the dial is INERT.")
        return 1
    print(f"RESULT: {len(seen)} distinct prompts across {len(VALUES)} values — the dial reaches the prompt.")
    for h, vs in seen.items():
        print(f"  bucket {h}: {vs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
