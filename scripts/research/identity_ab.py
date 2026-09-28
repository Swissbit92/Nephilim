#!/usr/bin/env python3
"""A/B: does the content/annotation split actually save her learning? (ADR-018)

WHY THIS IS NOT A BEHAVIOURAL A/B, stated plainly rather than dressed up. Nothing in the
identity graph reaches her prompt — ADR-018 scopes that out deliberately, and injection
has already been measured to flatten her voice. So there is no reply to score. The
testable claim is a BUILD-CORRECTNESS one, and it is the claim that matters: under
ecosystem ADR-012 the graph is the system of record, so a rebuild that wipes annotations
destroys data with no second copy.

ARM A  the pre-existing semantics — one unconditional SET covering every property, which
       is exactly what `seed_rules` does today and is pinned by
       `test_an_edited_rule_updates_in_place`.
ARM B  the content/annotation split.

Both arms: build, annotate N nodes, edit the card, rebuild, count surviving annotations.

THE MEASURE IS A PRE-REBUILD SNAPSHOT DIFF, not "annotations at default". Those are
different questions and only the first one is the right one: "at default" cannot
distinguish a node that legitimately still has defaults from one whose non-default value
was reset back to default by the rebuild — which is the precise failure being tested.
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.coordinator.config import get_settings  # noqa: E402
from src.coordinator.graph_driver import build_driver, read, write  # noqa: E402
from src.coordinator.identity_from_card import identity_nodes  # noqa: E402
from src.coordinator.repositories.identity_repository import IdentityRepository  # noqa: E402
from src.coordinator.identity_shapes import SHAPES  # noqa: E402

PERSONA = "_ab_identity"


def _overwrite_rebuild(drv, db, card) -> None:
    """ARM A. The pre-ADR-018 semantics, reproduced faithfully rather than described.

    One unconditional SET over every property the mapper produces, plus the annotation
    defaults — which is what a naive rebuild does when it has no declared split and
    therefore no way to know which properties are hers.
    """
    for spec in identity_nodes(card):
        shape = SHAPES[spec["_shape"]]
        props = {k: v for k, v in spec.items() if k != "_shape"}
        write(
            drv,
            f"""
            MATCH (p:Persona {{persona_id: $pid}})
            MERGE (n:{shape.label}:CurrentIdentity {{persona_id: $pid,
                                                     source_field: $sf,
                                                     source_index: $si}})
            SET n.text = $text, n.origin = $origin, n.source_hash = $hash,
                n.salience = 0.0, n.reinforced_count = 0
            MERGE (p)-[:HAS_LORE]->(n)
            """,
            db, pid=PERSONA, sf=props["source_field"], si=props["source_index"],
            text=props["text"], origin=props["origin"], hash=props["source_hash"],
        )


def run_arm(drv, db, card, arm: str) -> dict:
    repo = IdentityRepository(drv, db)
    write(drv, "MATCH (p:Persona {persona_id:$p}) OPTIONAL MATCH (p)-[]->(n) "
               "DETACH DELETE p,n", db, p=PERSONA)
    repo.ensure_schema()
    repo.apply_card(card)

    live = repo.identity(PERSONA, limit=400)
    targets = [live[i] for i in (0, len(live) // 3, 2 * len(live) // 3)]
    for t in targets:
        repo.reinforce(t["node_id"], salience_delta=0.3)
        repo.reinforce(t["node_id"], salience_delta=0.3)

    # THE SNAPSHOT. Taken before the rebuild, because that is the only thing that can
    # prove a value was preserved rather than merely still plausible.
    before = {x["node_id"]: (x["salience"], x["reinforced_count"])
              for x in repo.identity(PERSONA, limit=400)}
    annotated = {k: v for k, v in before.items() if (v[1] or 0) > 0}

    edited = copy.deepcopy(card)
    edited["lore"][0] = "REWRITTEN BY THE OPERATOR"
    if arm == "A":
        _overwrite_rebuild(drv, db, edited)
    else:
        repo.apply_card(edited)

    after = {x["node_id"]: (x["salience"], x["reinforced_count"])
             for x in repo.identity(PERSONA, limit=400)}
    survived = sum(1 for k, v in annotated.items() if after.get(k) == v)
    fresh = any(x["text"] == "REWRITTEN BY THE OPERATOR"
                for x in repo.identity(PERSONA, limit=400))
    return {"arm": arm, "annotated": len(annotated), "survived": survived,
            "content_refreshed": fresh, "live_after": len(after)}


def main() -> int:
    g = get_settings().graph
    drv = build_driver(g.base_url, g.username, g.password)
    db = g.database
    card = copy.deepcopy(json.load(open(ROOT / "personas" / "gwen.json")))
    card["key"] = PERSONA
    try:
        results = [run_arm(drv, db, card, a) for a in ("A", "B")]
        print(f"{'arm':<4} {'annotated':>10} {'survived':>9} {'content fresh':>14} {'live':>6}")
        for r in results:
            print(f"{r['arm']:<4} {r['annotated']:>10} {r['survived']:>9} "
                  f"{str(r['content_refreshed']):>14} {r['live_after']:>6}")
        a, b = results
        print()
        print(f"ARM A (overwrite): {a['survived']}/{a['annotated']} annotations survived")
        print(f"ARM B (split)    : {b['survived']}/{b['annotated']} annotations survived")
        ok = b["survived"] == b["annotated"] and a["survived"] < a["annotated"]
        print()
        if ok:
            print("RESULT: the split is load-bearing. Arm A destroys her learning on a "
                  "card edit; arm B preserves all of it, and both refresh content.")
        elif b["survived"] == b["annotated"]:
            print("RESULT: INCONCLUSIVE — arm B preserves, but arm A did not lose "
                  "anything either, so this run does not show the fix was necessary.")
        else:
            print("RESULT: FAIL — arm B lost annotations. The split is not working.")
        return 0 if ok else 1
    finally:
        write(drv, "MATCH (p:Persona {persona_id:$p}) OPTIONAL MATCH (p)-[]->(n) "
                   "DETACH DELETE p,n", db, p=PERSONA)
        drv.close()


if __name__ == "__main__":
    raise SystemExit(main())
