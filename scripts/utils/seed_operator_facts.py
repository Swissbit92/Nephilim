#!/usr/bin/env python3
"""Migrate the curated operator facts from SQLite into the graph. Dry-run default.

🔴 WHY. `user_profiles.profile_data["facts"]` is a JSON blob of BARE STRINGS, so
there is nowhere to record who a fact is ABOUT or who ASSERTED it. Measured
2026-10-21: after the self-facts section shipped she recited her own appearance
and the extractor filed it under HIM — `Assistant's eyes are a deep ocean blue`
sat in the operator's fact list and would have been read back as "things you know
about him". 38 such rows appeared in ~20 minutes. With no subject field that
contamination is unrepresentable, let alone preventable.

In the graph each fact is a node with `origin` and `asserted_by`, pointing at an
`:Operator` through `ABOUT` — the schema `cmp.cq01`/`cmp.cq02` already demanded
and nothing built.

**Provenance for THIS migration is `operator_curated` / `origin=learned`**, and
that is a deliberate claim rather than a default: these 32 lines were hand-
labelled by the operator against a frozen rubric out of 292 extracted candidates
(`scripts/research/facts/CURATION_RUBRIC.md`). They were LEARNED from him in
conversation, and the curation was a human filter over that, so `learned` is the
honest origin and `operator_curated` the honest asserter. Marking them `card`
would be false — the card declares nothing about his breakfast.

Dry-run is the default, matching `seed_identity.py`. Exit: 0 ok · 1 refused · 2
could not determine.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "chats.db"
CURATED = ROOT / "scripts" / "research" / "facts" / "curated.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--operator", required=True,
                    help="the operator id, e.g. user_be5d9dbe")
    ap.add_argument("--apply", action="store_true",
                    help="write. Omit for a dry run, which is the default")
    ap.add_argument("--curated", default=str(CURATED))
    ap.add_argument("--db", default=str(DB))
    a = ap.parse_args()

    from src.coordinator.config import get_settings
    from src.coordinator.graph_driver import build_driver
    from src.coordinator.repositories.operator_fact_repository import (
        OperatorFactRepository,
    )

    cfg = get_settings().graph
    if not cfg.enabled:
        print("could not determine: GRAPH_ENABLED is false", file=sys.stderr)
        return 2

    cur_path = pathlib.Path(a.curated)
    if not cur_path.exists():
        print(f"could not determine: {cur_path} does not exist", file=sys.stderr)
        return 2
    facts = list(json.loads(cur_path.read_text()).get("facts") or [])
    if not facts:
        print(f"REFUSING: {cur_path} admits zero facts", file=sys.stderr)
        return 1

    # Cross-check against SQLite so a drifted curated file is visible BEFORE the
    # write, not after. The graph is becoming the system of record; a silent
    # divergence at the moment of migration is the worst time for one.
    db = pathlib.Path(a.db)
    in_sqlite: list[str] = []
    if db.exists():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = con.execute(
            "SELECT profile_data FROM user_profiles WHERE user_id = ?",
            (a.operator,),
        ).fetchone()
        con.close()
        if row:
            in_sqlite = list(json.loads(row[0] or "{}").get("facts") or [])

    drift = sorted(set(facts) ^ set(in_sqlite))

    drv = build_driver(cfg.base_url, cfg.username, cfg.password)
    repo = OperatorFactRepository(drv, cfg.database)
    try:
        repo.ensure_schema()
        existing = repo.current_facts(a.operator, limit=1024)
        have = {r["text"] for r in existing}
        todo = [f for f in facts if f.strip() and f.strip() not in have]

        print(f"=== operator facts -> graph for {a.operator} ===")
        print(f"  curated file           : {len(facts)}")
        print(f"  live SQLite profile    : {len(in_sqlite)}")
        print(f"  already in the graph   : {len(existing)}")
        print(f"  would write            : {len(todo)}")
        print(f"  origin / asserted_by   : learned / operator_curated")
        if drift:
            print(f"  ⚠️  {len(drift)} line(s) differ between the curated file and "
                  f"SQLite — the graph will follow the CURATED file:")
            for d in drift[:5]:
                print(f"       {d[:84]}")
        for f in todo[:5]:
            print(f"    + {f[:84]}")
        if len(todo) > 5:
            print(f"    … and {len(todo) - 5} more")

        if not a.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply.")
            return 0

        written = 0
        for f in todo:
            if repo.add_fact(a.operator, f, asserted_by="operator_curated",
                             origin="learned"):
                written += 1
        after = repo.current_facts(a.operator, limit=1024)
        print(f"\nAPPLIED. wrote {written}, graph now holds {len(after)} current facts")
        if len(after) != len(have) + written:
            print("VERIFY FAILED: the post-write read does not match", file=sys.stderr)
            return 1
        return 0
    finally:
        drv.close()


if __name__ == "__main__":
    raise SystemExit(main())
