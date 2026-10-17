#!/usr/bin/env python3
"""Does the SUPERSEDES chain actually traverse? Three generations, on a scratch persona.

Decision 2 (2026-10-17) kept `(new)-[:SUPERSEDES]->(old)` and renamed the competency
set's `SUPERSEDED_BY` to match, rather than reversing the code. The grounds were partly
that `history()` walks this direction — so this exists to watch it walk, because the
direction of an edge is exactly the kind of thing that reads as obviously-correct in
Cypher and turns out to be reversed in the one query anybody runs.

It also re-checks the NULL trap on the live read, against a rule that was NEVER
superseded. `e.expired_at IS NULL` is the guard; written as `e.expired_at > $T` it drops
every still-valid row silently, and this repo has shipped that once already — 128 stale
nodes, `expired: 0`, no error, no failing test.

Writes to a scratch persona id and deletes it on the way out. Exit 0 = the chain
traverses, 1 = it does not.
"""
from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from neo4j import GraphDatabase  # noqa: E402

from src.coordinator.repositories.neo4j_rule_repository import (  # noqa: E402
    Neo4jRuleRepository,
)

PERSONA = "_scratch_supersedes_exercise"

#: Everything this script wrote, by persona_id. Run on the way in as well as out, so a
#: previous crashed run cannot make this one look like it seeded the wrong count.
_PURGE = """
MATCH (n {persona_id: $pid}) DETACH DELETE n
"""


def main() -> int:
    uri = os.environ.get("NEO4J_URI") or os.environ.get("NEO4J_BASE_URL")
    if not uri or not os.environ.get("NEO4J_PASSWORD"):
        print("could not determine: NEO4J_URI/NEO4J_PASSWORD not in the environment")
        return 2
    driver = GraphDatabase.driver(
        uri, auth=(os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]))
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    repo = Neo4jRuleRepository(driver=driver, database=database)

    def purge() -> None:
        with driver.session(database=database) as s:
            s.run(_PURGE, pid=PERSONA).consume()

    failures: list[str] = []
    try:
        purge()

        # `source_field` and `source_index` are REQUIRED keys, not optional ones —
        # seed_rules reads them with `r["source_field"]` so a seed dict without them
        # raises KeyError rather than defaulting. They are the natural key the rebuild
        # path MERGEs on, which is why there is no default to fall back to.
        seeded = repo.seed_rules(PERSONA, [
            {"text": "generation 0 — never superseded",
             "source_field": "dont", "source_index": 0, "priority": 100,
             "rule_type": "hard_wall"},
            {"text": "generation 0 of the chain",
             "source_field": "dont", "source_index": 1, "priority": 90,
             "rule_type": "soft_wall"},
        ])
        print(f"seeded: {seeded}")

        standing = repo.standing_rules(PERSONA, limit=50)
        chain_head = next(r for r in standing if r["source_index"] == 1)
        never = next(r for r in standing if r["source_index"] == 0)

        # Three supersessions on one rule.
        current = chain_head["rule_id"]
        for gen in (1, 2, 3):
            out = repo.supersede_rule(current, f"generation {gen} of the chain",
                                      origin="conversation")
            if not out.get("superseded"):
                failures.append(f"generation {gen} did not supersede: {out}")
                break
            current = out["new_rule_id"]
            print(f"  gen {gen}: ...{out['old_rule_id'][-8:]} <- ...{current[-8:]}")

        hist = repo.history(current)
        print(f"\nhistory() from the newest node returned {len(hist)} rows:")
        for row in hist:
            print(f"  {row['generations_back']} back: {row['text']!r} "
                  f"expired={row['expired_at'] is not None}")

        if len(hist) != 4:
            failures.append(
                f"history() walked {len(hist)} generations, expected 4 (the newest plus "
                f"three ancestors). If it returned 1, the edge points old->new and the "
                f"walk starts at a leaf — which is Decision 2 being wrong."
            )
        elif [r["generations_back"] for r in hist] != [0, 1, 2, 3]:
            failures.append(f"generations_back was {[r['generations_back'] for r in hist]}")
        elif hist[0]["expired_at"] is not None:
            failures.append("the newest node in the chain is expired")
        elif any(r["expired_at"] is None for r in hist[1:]):
            failures.append("an ancestor in the chain is not expired")

        # The NULL trap, on the rule that was never superseded.
        still = {r["rule_id"] for r in repo.standing_rules(PERSONA, limit=50)}
        if never["rule_id"] not in still:
            failures.append(
                "the NEVER-superseded rule is missing from standing_rules — this is the "
                "NULL trap: a predicate on expired_at that is not written as "
                "`IS NULL OR ...` drops every row that was never superseded, and it "
                "drops them silently"
            )
        if chain_head["rule_id"] in still:
            failures.append("a superseded rule is still returned as standing")
        if current not in still:
            failures.append("the newest node in the chain is not returned as standing")
        print(f"\nstanding_rules: {len(still)} rules "
              f"(never-superseded present: {never['rule_id'] in still}, "
              f"newest present: {current in still})")
    finally:
        purge()
        driver.close()

    if failures:
        for f in failures:
            print(f"\nFAIL {f}")
        return 1
    print("\nOK: SUPERSEDES points new->old, history() walks 3 generations back, and the "
          "never-superseded rule survives the standing read.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
