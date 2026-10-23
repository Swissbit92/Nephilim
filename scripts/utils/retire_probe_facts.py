#!/usr/bin/env python3
"""Invalidate operator facts that a TEST HARNESS taught her, not the operator.

🔴 WHY THIS IS NEEDED, AND WHY IT IS MY FAULT.

Every learning-verification run teaches her a deliberately unusual fact so a
correct answer cannot come from the base model's priors ("my favourite band is
Marrowfield Drift"). That part is sound. What no run ever did was RETIRE the
previous set — so each verification left a contradiction behind, and they
accumulated.

Measured 2026-10-23 on the live store: THREE coexisting "favourite band" facts
(The Rusted Compass, Marrowfield Drift, Halloway Verge) and TWO dog names
(Tulliver, Wickham), all `valid_to IS NULL`, all injected every turn as
numbered facts. Asked "what's my favorite band?" she answered **"Marrowfield
Drift"** — confidently, from a superseded probe. Asked with the British
spelling she refused. One question, three stored answers, and the reply depends
on phrasing.

So this is not housekeeping. Until it is run, no recall measurement on this
store means anything, because a miss cannot be distinguished from a fact that
lost a coin toss against its own predecessors.

=== HOW A PROBE FACT IS IDENTIFIED ===

By the TITLE OF THE SESSION IT CAME FROM, via `source_session_id`. That is a
property of how the fact was created, not a guess about its content.

⚠️ NOT by `asserted_by`. The writer stamps `"user"` on everything it extracts,
because the claim is window-level and non-user turns are physically redacted
from its input. A probe fact and a real one are indistinguishable there.

⚠️ NOT by content, and this is the important one. Three facts in the same
`asserted_by="user"` group came from a session titled "New Chat" and are REAL:
"Money runway of 8 months", "Considering quitting job to start own venture",
"Has a family that thinks the decision is reckless". A content heuristic — or
a sweep of everything the harness could have written — would have destroyed
them. The 32 curated facts carry no `source_session_id` at all and are never
candidates.

=== WHAT IT DOES ===

`invalidate_fact`, never delete — ADR-021. `valid_to` (it stopped being true)
and `expired_at` (we stopped believing it) are both set, so the history remains
queryable with `facts_at` and the retirement is itself a recorded event.

Dry run by default.

    python scripts/utils/retire_probe_facts.py            # inspect
    python scripts/utils/retire_probe_facts.py --apply    # retire
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

#: Session titles created by a test harness. Anchored and explicit rather than a
#: loose substring: "New Chat" must never match, and a real session the operator
#: happened to title "testing a thing" is not this script's business.
PROBE_TITLE = re.compile(
    r"^(learning probe|flush baseline|flush live verify|flush recall verify"
    r"|band discriminator|probe |eval |harness |arm )", re.I)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--operator", default="")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    from src.coordinator.config import get_settings
    from src.coordinator.graph_driver import build_driver, read
    from src.coordinator.repositories.operator_fact_repository import (
        OperatorFactRepository,
    )

    operator = args.operator or get_settings().memory.single_operator_user_id
    if not operator:
        print("REFUSING: no operator id.", file=sys.stderr)
        return 2
    cfg = get_settings().graph
    if not cfg.enabled:
        print("REFUSING: GRAPH_ENABLED is false.", file=sys.stderr)
        return 2

    con = sqlite3.connect(f"file:{ROOT / 'data' / 'chats.db'}?mode=ro", uri=True)
    titles = {r[0]: (r[1] or "") for r in
              con.execute("SELECT id, title FROM chat_sessions")}
    con.close()

    drv = build_driver(cfg.base_url, cfg.username, cfg.password)
    repo = OperatorFactRepository(drv, cfg.database)
    try:
        rows = read(drv, """
            MATCH (f:Fact)-[:ABOUT]->(:Operator {operator_id: $op})
            WHERE f.valid_to IS NULL
            RETURN f.fact_id AS id, f.text AS text,
                   f.source_session_id AS sid, f.asserted_by AS by
            ORDER BY f.created_at
        """, cfg.database, op=operator)

        probe, real, curated = [], [], []
        for r in rows:
            if not r["sid"]:
                curated.append(r)
            elif PROBE_TITLE.search(titles.get(r["sid"], "")):
                probe.append(r)
            else:
                real.append(r)

        print(f"{len(rows)} current facts for {operator}\n")
        print(f"  curated (no source session, never a candidate) : {len(curated)}")
        print(f"  from a real session, KEPT                      : {len(real)}")
        for r in real:
            print(f"      {r['text'][:58]:<60} <- {titles.get(r['sid'],'?')[:20]!r}")
        print(f"  from a PROBE session, to retire                : {len(probe)}")
        for r in probe:
            print(f"      {r['text'][:58]:<60} <- {titles.get(r['sid'],'?')[:24]!r}")

        if not probe:
            print("\nnothing to retire.")
            return 0
        if not args.apply:
            print("\nDRY RUN. Nothing written. Re-run with --apply.")
            return 0

        done = 0
        for r in probe:
            if repo.invalidate_fact(r["id"]):
                done += 1
            else:
                print(f"  ⚠️ refused: {r['text'][:50]}", file=sys.stderr)

        after = read(drv, """
            MATCH (f:Fact)-[:ABOUT]->(:Operator {operator_id: $op})
            WHERE f.valid_to IS NULL RETURN count(f) AS n
        """, cfg.database, op=operator)[0]["n"]
        print(f"\nretired {done} of {len(probe)}; current facts now {after} "
              f"(was {len(rows)})")
        if after != len(rows) - done:
            print("⚠️ count does not reconcile.", file=sys.stderr)
            return 1
    finally:
        drv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
