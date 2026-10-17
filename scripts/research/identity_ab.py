#!/usr/bin/env python3
"""A/B: does the create-once / refresh-every-build split actually preserve what it must?

RE-POINTED 2026-10-16. This measured whether the content/ANNOTATION split saved her
learning — `salience` and `reinforced_count` survival across a card edit, 0 of 3 for one
unconditional SET versus 3 of 3 for the split. Those properties have been deleted: on the
live graph 256 nodes carried them and returned exactly one distinct row, `(0, 0.0)`, so
the counter never incremented once in the whole life of the graph.

The MECHANISM the A/B tested is untouched, and it still guards two properties that must
survive a rebuild, so the harness keeps its two arms and changes only its subject:

  node_id     a surrogate id. If a rebuild minted a new one, every edge pointing at the
              node would be orphaned — the node would still exist and still look right.
  created_at  transaction time, and the one genuinely irreversible property here. It
              cannot be reconstructed after the fact, which is why the ecosystem
              migration doc ranks transaction time the most irreversible day-1 decision.

This is a stronger subject than the counter was, not a weaker one: the counter was never
written, whereas both of these are written on every node that exists.

WHY THIS IS NOT A BEHAVIOURAL A/B, stated plainly rather than dressed up. Nothing in the
identity graph reaches her prompt — ADR-018 scopes that out deliberately, and injection
has already been measured to flatten her voice. So there is no reply to score. The
testable claim is a BUILD-CORRECTNESS one, and it is the claim that matters: under
ecosystem ADR-012 the graph is the system of record, so a rebuild that wipes a
create-once property destroys data with no second copy.

ARM A  the pre-existing semantics — one unconditional SET covering every property, which
       is exactly what `seed_rules` does today and is pinned by
       `test_an_edited_rule_updates_in_place`.
ARM B  the create-once / refresh split: `node_id` and `created_at` named only in
       `ON CREATE`, so the rebuild path cannot reach them.

Both arms: build, snapshot, edit the card, rebuild, count how many survived.

THE MEASURE IS A PRE-REBUILD SNAPSHOT DIFF, and that is the methodologically load-bearing
part — it is why this harness is worth keeping rather than replacing with a grep. A check
of the form "is the value still plausible?" cannot distinguish a property that was
preserved from one that was overwritten with something equally plausible. Only comparing
against the exact pre-rebuild value can, and for `created_at` that distinction is the
whole question: a rebuild that reset it would write a perfectly valid timestamp.
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
from src.coordinator.graph_ids import new_id, now_iso  # noqa: E402
from src.coordinator.identity_from_card import identity_nodes  # noqa: E402
from src.coordinator.identity_shapes import SHAPES  # noqa: E402
from src.coordinator.repositories.identity_repository import IdentityRepository  # noqa: E402

PERSONA = "_ab_identity"
#: How many card entries `run_arm` rewrites. The edited entry gets a new `source_key`, so
#: it is a new node and must not preserve the old one's create-once properties.
EDITED_ENTRIES = 1


def _overwrite_rebuild(drv, db, card) -> None:
    """ARM A. The pre-ADR-018 semantics, reproduced faithfully rather than described.

    One unconditional SET over every property the mapper produces, including the
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
                n.node_id = $nid, n.created_at = $created
            MERGE (p)-[:HAS_LORE]->(n)
            """,
            db, pid=PERSONA, sf=props["source_field"], si=props["source_index"],
            text=props["text"], origin=props["origin"], hash=props["source_hash"],
            # A fresh id and timestamp per build — which is exactly what an
            # unconditional SET of every property produces, and the damage under test.
            nid=new_id(), created=now_iso(),
        )


def run_arm(drv, db, card, arm: str) -> dict:
    repo = IdentityRepository(drv, db)
    write(drv, "MATCH (p:Persona {persona_id:$p}) OPTIONAL MATCH (p)-[]->(n) "
               "DETACH DELETE p,n", db, p=PERSONA)
    repo.ensure_schema()
    repo.apply_card(card)

    # Read the two create-once properties DIRECTLY rather than through
    # `repo.identity()`, which deliberately does not project `created_at`: widening a
    # projection that feeds the prompt overlay to serve a research script would be the
    # tail wagging the dog, and ADR-019's byte-identity boundary is not worth risking
    # for a measurement.
    # `label_filter` was a parameter here and neither caller ever passed it, so the
    # flexibility was unreached. It also made the label `CurrentIdentity{filter}` — a
    # literal fragment glued to an interpolation, which no static scan can resolve, and
    # `scripts/checks/the_built_graph_surface_is_declared.py` now refuses that shape
    # rather than guessing a truncated name. Deleted rather than reworked: the honest
    # resolution for unreached flexibility is to remove it.
    def _create_once() -> dict:
        rows = read(drv, """
            MATCH (p:Persona {persona_id: $pid})-[]->(n:CurrentIdentity)
            RETURN n.source_field AS sf, n.source_index AS si,
                   n.node_id AS node_id, n.created_at AS created_at
            """, db, pid=PERSONA)
        # Keyed by the NATURAL key, not node_id — node_id is one of the values under
        # test, so keying on it would make the comparison trivially agree with itself.
        return {(r["sf"], r["si"]): (r["node_id"], r["created_at"]) for r in rows}

    # THE SNAPSHOT. Taken before the rebuild, because that is the only thing that can
    # prove a value was preserved rather than merely still plausible.
    create_once_nodes = _create_once()

    edited = copy.deepcopy(card)
    edited["lore"][0] = "REWRITTEN BY THE OPERATOR"
    if arm == "A":
        _overwrite_rebuild(drv, db, edited)
    else:
        repo.apply_card(edited)

    after = _create_once()
    survived = sum(1 for k, v in create_once_nodes.items() if after.get(k) == v)
    # The other half of the claim: a genuinely edited entry must appear as a NEW node,
    # not as the old node with its id preserved and its text swapped.
    new_nodes = sum(1 for k, v in after.items() if create_once_nodes.get(k) != v)
    fresh = any(x["text"] == "REWRITTEN BY THE OPERATOR"
                for x in repo.identity(PERSONA, limit=400))
    return {"arm": arm, "create_once_nodes": len(create_once_nodes),
            "survived": survived, "new_nodes": new_nodes,
            "content_refreshed": fresh, "live_after": len(after)}


def main() -> int:
    g = get_settings().graph
    drv = build_driver(g.base_url, g.username, g.password)
    db = g.database
    card = copy.deepcopy(json.load(open(ROOT / "personas" / "gwen.json")))
    card["key"] = PERSONA
    try:
        results = [run_arm(drv, db, card, a) for a in ("A", "B")]
        print(f"{'arm':<4} {'create_once_nodes':>10} {'survived':>9} {'content fresh':>14} {'live':>6}")
        for r in results:
            print(f"{r['arm']:<4} {r['create_once_nodes']:>10} {r['survived']:>9} "
                  f"{str(r['content_refreshed']):>14} {r['live_after']:>6}")
        a, b = results
        print()
        exp = b["create_once_nodes"] - EDITED_ENTRIES
        print(f"ARM A (overwrite): {a['survived']}/{a['create_once_nodes']} survived "
              f"(expected {exp})")
        print(f"ARM B (split)    : {b['survived']}/{b['create_once_nodes']} survived "
              f"(expected {exp}; the 1 rewritten entry is a NEW node by design)")
        # EXPECTED is N-1, not N, and getting this wrong cost a false FAIL on the first
        # run. The harness rewrites exactly one lore entry, and the MERGE key is
        # content-derived (`source_key`), so the edited entry is a DIFFERENT node by
        # construction — it must NOT carry the old node's id or created_at. Demanding
        # that all N survive would demand that a content edit mutate a node in place,
        # which is the behaviour the content-keyed MERGE was built to avoid.
        expected = b["create_once_nodes"] - EDITED_ENTRIES
        ok = (b["survived"] == expected
              and b["new_nodes"] == EDITED_ENTRIES
              and a["survived"] < a["create_once_nodes"])
        print()
        if ok:
            print("RESULT: the split is load-bearing. Arm A resets node_id and "
                  "created_at on EVERY node when one entry is edited; arm B preserves "
                  "every untouched node and creates exactly one new node for the entry "
                  "that genuinely changed. Both refresh content.")
        elif b["survived"] == b["create_once_nodes"]:
            print("RESULT: INCONCLUSIVE — arm B preserves, but arm A did not lose "
                  "anything either, so this run does not show the fix was necessary.")
        else:
            print("RESULT: FAIL — arm B lost a create-once property. The split is "
                  "not working, and created_at cannot be reconstructed.")
        return 0 if ok else 1
    finally:
        write(drv, "MATCH (p:Persona {persona_id:$p}) OPTIONAL MATCH (p)-[]->(n) "
                   "DETACH DELETE p,n", db, p=PERSONA)
        drv.close()


if __name__ == "__main__":
    raise SystemExit(main())
