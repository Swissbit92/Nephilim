#!/usr/bin/env python3
"""Does the throttle block a REAL refinement sequence?

Replays the user's actual image prompts out of chats.db — not pairs anyone
invented — through the live throttle, in order, as one session.

Exit 1 if any request after the first is refused for being a duplicate. A
user iterating on a picture keeps the scaffolding and changes one or two
attributes; that is refinement, and refusing it is the defect this guards.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "chats.db"


def main() -> int:
    if not DB.exists():
        print("UNDETERMINED — no chats.db to replay against")
        return 2

    from src.coordinator.services.image_gen.prompt import GenerationIntent, compose
    from src.coordinator.services.image_gen.throttle import GenerationThrottle

    c = sqlite3.connect(DB)
    prompts = [r[0] for r in c.execute(
        "SELECT prompt FROM image_jobs ORDER BY created_at")]
    # Plus the user turns that ASKED for an image, which is where the
    # refinements live — most never became jobs, because they were refused.
    asked = [r[0] for r in c.execute(
        "SELECT content FROM messages WHERE role='user' "
        "AND lower(content) LIKE '%image generator%' ORDER BY timestamp")]
    for a in asked:
        body = a.split(":", 1)[1].strip() if ":" in a else a
        try:
            prompts.append(compose(GenerationIntent(subject=body)))
        except Exception:
            pass

    if len(prompts) < 2:
        print(f"UNDETERMINED — only {len(prompts)} real prompt(s) to replay")
        return 2

    # Cooldown off: this check is about the DUPLICATE rule, not about pacing.
    t = GenerationThrottle(cooldown_seconds=0, per_session_limit=10_000)
    blocked = []
    for i, p in enumerate(prompts):
        d = t.check("replay", p, now=float(i))
        if not d and "same picture" in d.reason:
            blocked.append((i, p[:70]))
        t.record("replay", p, now=float(i))

    if blocked:
        print(f"BLOCKED {len(blocked)} of {len(prompts)} REAL requests as duplicates:")
        for i, p in blocked:
            print(f"  #{i}: {p}")
        print("\nThese are refinements, not a loop. A user changes one or two")
        print("attributes and keeps the rest of the prompt.")
        return 1

    print(f"ok — {len(prompts)} real prompts replayed, none refused as duplicates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
