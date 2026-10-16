#!/usr/bin/env python3
"""Remove the deleted counter properties from live identity nodes. DRY-RUN by default.

The code that read and wrote `reinforced_count` / `salience` / `last_referenced` is gone,
so these are inert: nothing projects them, nothing updates them, and `Shape.validated`
now refuses the names outright. They are stale rows on what ecosystem ADR-012 made the
SYSTEM OF RECORD, which is the one place stale data is least acceptable.

WHY THIS IS A SEPARATE, OPT-IN SCRIPT rather than part of the deletion. Removing a
property from 256 live nodes is a write to the system of record, and it buys tidiness
rather than correctness — leaving them costs nothing functional. So the decision belongs
to an operator, not to a merge. `--apply` is required; without it this reports and exits.

SAFE BY CONSTRUCTION, three ways:
  * It only ever REMOVEs the three named properties. No MERGE, no SET, no DELETE of any
    node, so it cannot create, resurrect or orphan anything.
  * It refuses to run if any node carries a NON-DEFAULT value, because that would mean
    the counter had actually been used and this is no longer a no-op cleanup — it would
    be destroying the only copy of something. Measured before writing this: 256 nodes,
    exactly one distinct row, `(0, 0.0)`. If that ever stops being true, stop and think.
  * Nightly `com.eeva.neo4j-backup` dumps the database and the restore is verified by
    restoring, not by an exit code. Today's dump was confirmed present and 4.3 MB before
    this script was written.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.coordinator.config import get_settings  # noqa: E402
from src.coordinator.graph_driver import build_driver, read, write  # noqa: E402

GONE = ("reinforced_count", "salience", "last_referenced")
#: The values the deleted `ON CREATE` branch wrote. Anything else means it RAN.
DEFAULTS = {"reinforced_count": 0, "salience": 0.0}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually remove them (default: report only)")
    args = ap.parse_args()

    g = get_settings().graph
    drv = build_driver(g.base_url, g.username, g.password)
    if drv is None:
        print("FAIL: no graph driver — cannot tell what is there, which is not a pass.")
        return 2
    db = g.database
    try:
        rows = read(drv, """
            MATCH (n)
            WHERE n.reinforced_count IS NOT NULL OR n.salience IS NOT NULL
               OR n.last_referenced IS NOT NULL
            RETURN DISTINCT n.reinforced_count AS rc, n.salience AS sal,
                   n.last_referenced AS lr, count(*) AS nodes
            """, db)
        if not rows:
            print("OK: no live node carries any of the three. Nothing to do.")
            return 0

        total = sum(r["nodes"] for r in rows)
        print(f"{total} node(s) carry at least one, in {len(rows)} distinct combination(s):")
        for r in rows:
            print(f"  reinforced_count={r['rc']!r} salience={r['sal']!r} "
                  f"last_referenced={r['lr']!r}  ->  {r['nodes']} node(s)")

        used = [r for r in rows
                if r["rc"] not in (None, DEFAULTS["reinforced_count"])
                or r["sal"] not in (None, DEFAULTS["salience"])
                or r["lr"] is not None]
        if used:
            print("\nREFUSING: at least one node holds a NON-DEFAULT value, so the "
                  "counter was actually used at some point. This script assumes it "
                  "never was. Removing these would destroy the only copy — decide "
                  "deliberately what that data is worth before proceeding.")
            return 1

        if not args.apply:
            print("\nDRY RUN. Every value is at its create-time default, so removal is "
                  "a no-op in behaviour. Re-run with --apply to remove them.")
            return 0

        res = write(drv, f"""
            MATCH (n)
            WHERE {' OR '.join(f'n.{p} IS NOT NULL' for p in GONE)}
            REMOVE {', '.join(f'n.{p}' for p in GONE)}
            RETURN count(n) AS touched
            """, db)
        touched = res[0]["touched"] if res else 0
        print(f"\nremoved the three properties from {touched} node(s).")

        left = read(drv, """
            MATCH (n) WHERE n.reinforced_count IS NOT NULL OR n.salience IS NOT NULL
               OR n.last_referenced IS NOT NULL RETURN count(n) AS n
            """, db)
        remaining = left[0]["n"] if left else 0
        # Verify by re-reading, not by trusting the write's own report — the repo has a
        # standing lesson that a 200 is not evidence the thing happened.
        print(f"verified by re-read: {remaining} node(s) still carry one "
              f"({'OK' if remaining == 0 else 'UNEXPECTED — investigate'})")
        return 0 if remaining == 0 else 1
    finally:
        drv.close()


if __name__ == "__main__":
    sys.exit(main())
