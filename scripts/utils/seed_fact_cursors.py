#!/usr/bin/env python3
"""Set every existing session's fact cursor to its LAST message. One-shot.

🔴 RUN THIS BEFORE ENABLING THE SWEEPER (`MEMORY_FLUSH_SWEEP_SECONDS`).

Without it, the first sweeps would treat 323 idle historical sessions as
never-extracted and re-extract months of transcript at a measured median of
5.54 s each -- contending with her replies the whole way, because
`OLLAMA_NUM_PARALLEL=1` serialises extraction against generation.

With it, the flush only ever covers messages written AFTER deployment. That is
also the honest position: the historical facts are already in the store (42
recalled + 8 self as of 2026-10-23), so re-mining the archive would mostly
rediscover what is held and re-pay for it.

⚠️ `started_at` is `ON CREATE` only in `advance_fact_cursor`, so seeding does
NOT disturb the 229 episodes the backfill already wrote.

Dry run by default. Nothing is written without `--apply`.

    python scripts/utils/seed_fact_cursors.py            # inspect
    python scripts/utils/seed_fact_cursors.py --apply    # write
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=str(ROOT / "data" / "chats.db"))
    ap.add_argument("--operator", default="")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    from src.coordinator.config import get_settings
    from src.coordinator.graph_driver import build_driver
    from src.coordinator.repositories.episode_repository import EpisodeRepository

    operator = args.operator or get_settings().memory.single_operator_user_id
    if not operator:
        print("REFUSING: no operator id. Set SINGLE_OPERATOR_USER_ID or pass "
              "--operator; a cursor with no owner is unreadable.", file=sys.stderr)
        return 2

    db = pathlib.Path(args.db)
    if not db.exists():
        print(f"REFUSING: no database at {db}", file=sys.stderr)
        return 2

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        """
        SELECT s.id, s.persona_key,
               MIN(m.timestamp) AS first_ts,
               MAX(m.timestamp) AS last_ts,
               COUNT(m.id)      AS n,
               SUM(CASE WHEN m.role='user' THEN 1 ELSE 0 END) AS u
        FROM chat_sessions s JOIN messages m ON m.session_id = s.id
        GROUP BY s.id
        """
    ).fetchall()
    con.close()

    short = [r for r in rows if (r[5] or 0) < 10]
    print(f"sessions with messages : {len(rows)}")
    print(f"  of those, under 10 user turns (the gate could never fire): "
          f"{len(short)} ({100.0 * len(short) / max(len(rows), 1):.1f}%)")
    print(f"  span                 : {min(r[2] for r in rows)} .. "
          f"{max(r[3] for r in rows)}")
    print(f"  operator             : {operator}")

    if not args.apply:
        print("\nDRY RUN. Nothing written. Re-run with --apply.")
        for r in rows[:5]:
            print(f"  would set {r[0][:8]} -> {r[3]}  ({r[5]} user turns)")
        return 0

    cfg = get_settings().graph
    if not cfg.enabled:
        print("REFUSING: GRAPH_ENABLED is false; the cursor lives in the graph.",
              file=sys.stderr)
        return 2

    drv = build_driver(cfg.base_url, cfg.username, cfg.password)
    repo = EpisodeRepository(drv, cfg.database)
    repo.ensure_schema()
    written = 0
    try:
        for sid, persona, first_ts, last_ts, n, _u in rows:
            repo.advance_fact_cursor(
                operator, sid, cursor_at=str(last_ts),
                cursor_message_id="", message_count=int(n or 0),
                persona_key=str(persona or ""), started_at=str(first_ts),
            )
            written += 1

        # Verify rather than assume: read back a sample.
        checked = ok = 0
        for sid, _p, _f, last_ts, _n, _u in rows[:25]:
            got = repo.fact_cursor(operator, sid)
            checked += 1
            ok += 1 if got and got.get("at") == str(last_ts) else 0
        print(f"\nwrote {written} cursor(s); verified {ok}/{checked} sampled")
        if ok != checked:
            print("⚠️ VERIFICATION FAILED on some rows.", file=sys.stderr)
            return 1
    finally:
        drv.close()

    print("\nNow set MEMORY_FLUSH_SWEEP_SECONDS=900 in .env and restart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
