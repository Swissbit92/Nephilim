#!/usr/bin/env python3
"""Build `:Episode` nodes from the existing message log. Dry-run default.

Pointers only — `session_id`, when it ran, how many messages. NO message text:
ADR-021 keeps text in SQLite and `idn.cq06` requires the graph to REFUSE "what
did she say three weeks ago".

⚠️ The boundary is `session_id`, not a time gap. Confirmed by reading source
across Mem0, Letta, Graphiti/Zep and LangGraph: not one computes a session
boundary from elapsed time, and the only published alternative is LLM-judged
topic shift (SeCom, arXiv 2502.05589). Cognee is the single near-miss and
deliberately declines to PERSIST its one gap-derived state.

Exit: 0 ok · 1 refused · 2 could not determine.
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DB = ROOT / "data" / "chats.db"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--operator", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--limit", type=int, default=2000)
    a = ap.parse_args()

    from src.coordinator.config import get_settings
    from src.coordinator.graph_driver import build_driver
    from src.coordinator.repositories.episode_repository import EpisodeRepository

    cfg = get_settings().graph
    if not cfg.enabled:
        print("could not determine: GRAPH_ENABLED is false", file=sys.stderr)
        return 2
    db = pathlib.Path(a.db)
    if not db.exists():
        print(f"could not determine: {db} does not exist", file=sys.stderr)
        return 2

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        -- chat_sessions keys on `id`, NOT `session_id`; only user_sessions and
        -- messages use the `session_id` spelling. Guessing cost one failed run.
        SELECT s.id                    AS session_id,
               s.persona_key           AS persona_key,
               COUNT(m.id)             AS message_count,
               MIN(m.timestamp)        AS started_at,
               MAX(m.timestamp)        AS ended_at
        FROM user_sessions us
        JOIN chat_sessions s ON s.id = us.session_id
        LEFT JOIN messages m ON m.session_id = s.id
        WHERE us.user_id = ?
        GROUP BY s.id
        HAVING COUNT(m.id) > 0
        ORDER BY MIN(m.timestamp) ASC
        LIMIT ?
        """,
        (a.operator, a.limit),
    ).fetchall()
    con.close()

    if not rows:
        print(f"REFUSING: no sessions with messages found for {a.operator}",
              file=sys.stderr)
        return 1

    drv = build_driver(cfg.base_url, cfg.username, cfg.password)
    repo = EpisodeRepository(drv, cfg.database)
    try:
        repo.ensure_schema()
        before = len(repo.recent_episodes(a.operator, limit=4096))
        print(f"=== episodes -> graph for {a.operator} ===")
        print(f"  sessions with messages : {len(rows)}")
        print(f"  episodes already built : {before}")
        print(f"  message span           : {rows[0]['started_at']} .. "
              f"{rows[-1]['ended_at']}")
        print(f"  messages covered       : "
              f"{sum(r['message_count'] for r in rows)}")
        for r in rows[:3]:
            print(f"    + {r['session_id'][:8]}  {r['persona_key']:8} "
                  f"{r['message_count']:4} msgs  {r['started_at']}")
        if len(rows) > 3:
            print(f"    … and {len(rows) - 3} more")

        if not a.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply.")
            return 0

        for r in rows:
            repo.record_episode(
                a.operator, r["session_id"],
                started_at=str(r["started_at"]),
                ended_at=str(r["ended_at"]) if r["ended_at"] else None,
                message_count=int(r["message_count"]),
                persona_key=r["persona_key"] or "",
            )
        edges = repo.link_chronology(a.operator)
        after = repo.recent_episodes(a.operator, limit=4096)
        print(f"\nAPPLIED. {len(after)} episodes, {edges} CONTINUES edges")
        if len(after) != len(rows):
            print(f"VERIFY FAILED: expected {len(rows)} episodes, graph has "
                  f"{len(after)}", file=sys.stderr)
            return 1
        return 0
    finally:
        drv.close()


if __name__ == "__main__":
    raise SystemExit(main())
