"""Build and rebuild the persona identity graph (ADR-018, ecosystem ADR-012).

TWO MODES, AND THE SECOND ONE IS THE WHOLE POINT.

    build    an empty graph -> every identity node the card implies
    rebuild  an existing graph -> regenerate CONTENT, PRESERVE her annotations

`seed_rules` does the first but not the second: its `SET` block is unconditional, so every
property is regenerated. That was harmless while the graph was a droppable projection. It
is not harmless now — ecosystem ADR-012 makes the graph the system of record, so an
overwriting rebuild after a card edit destroys learned state permanently, with no error
and no second copy.

The mechanism is Cypher's own, not something invented here:

    MERGE (n:Label:CurrentIdentity {natural key})
      ON CREATE SET n.node_id = ..., n.salience = 0.0, n.reinforced_count = 0
      SET n.text = ..., n.origin = ..., n.source_hash = ...   <-- content only

The annotation properties are named ONLY in the `ON CREATE` branch, so a rebuild that
takes the match path cannot touch them. Preservation is therefore structural: a property
cannot be wiped by a statement that never mentions it.

PROPERTIES ARE ASSIGNED INDIVIDUALLY, NEVER VIA A MAP, and that is deliberate. Neo4j's
SET documentation is explicit on two map behaviours that are both landmines here:
`SET n = $map` REPLACES the entire property set (one character from `+=`, and every
annotation is gone), and `SET n += $map` DELETES any key whose map value is null. An
optional content field arriving as null would therefore silently remove a property rather
than leave it unset. Explicit per-property assignment is immune to both, and the verbose
form is the one Neo4j's own worked examples use.

TWO TRAPS, BOTH GUARDED RATHER THAN NOTED.
  * A MERGE pattern matching MULTIPLE nodes behaves as MATCH and binds ALL of them, and a
    following SET then applies to every match — Neo4j's own MERGE docs demonstrate this
    with a pattern that binds 6 existing nodes and updates all 6. That is a live hazard
    here: an EXPIRED node and a live node share the same positional key, so a rebuild
    would bind the expired one and resurrect it.

    The guard is a secondary label, `:CurrentIdentity`, and MERGE matches on BOTH labels.
    An expired node keeps its primary label (so history stays traversable and its edge
    stays intact) and loses `:CurrentIdentity`, so it becomes invisible to the MERGE
    pattern by construction rather than by a filter MERGE cannot express. This mirrors
    `:CurrentRule` exactly — the same trick, for the same reason: Neo4j has no partial
    index and no WHERE inside a MERGE pattern.

    A composite uniqueness constraint on the positional triple is available on Community
    (node KEY constraints are Enterprise-only, plain composite PROPERTY UNIQUENESS is
    not) — but it is deliberately NOT created, because it would reject the
    expired-plus-live pair this design requires. `check_identity_integrity` looks for
    duplicate live keys after the fact instead.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from ..graph_driver import read, write
from ..graph_ids import new_id, now_iso
from ..identity_from_card import identity_nodes
from ..identity_shapes import (BASELINE_EDGE, BASELINE_LABEL, EDGES, SHAPES,
                               SLIDER_NAMES, Shape)

logger = logging.getLogger(__name__)

#: Defaults written ONCE, on create. Never on match — that is the preservation.
#: `last_referenced` is DELIBERATELY ABSENT. Neo4j's SET docs are explicit that with
#: `n += $map`, "if any property in the map is null, it will be removed" — so a default of
#: None would delete the key rather than initialise it, and the key would then never enter
#: the property-key registry either. It is set on first reinforcement and not before.
ANNOTATION_DEFAULTS: Dict[str, Any] = {
    "salience": 0.0,
    "reinforced_count": 0,
}

_LABEL_TO_EDGE = {tgt: rel for rel, (_src, tgt) in EDGES.items()}


def _card_fingerprint(card: Dict[str, Any]) -> str:
    """Stable digest of the card as shipped, stored with the baseline.

    Deliberately NOT cv_summarizer._fingerprint: that one EXCLUDES five presentation
    keys so a theme change does not force an LLM to rewrite <identity>. A baseline wants
    the opposite -- to record exactly which card version was live when the snapshot was
    taken -- so it hashes everything.
    """
    return hashlib.sha256(
        json.dumps(card, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


class IdentityRepository:
    """Card -> identity graph. Never writes the card."""

    def __init__(self, driver: Any = None, database: str = "neo4j") -> None:
        self._driver = driver
        self._database = database

    # ---- schema ---------------------------------------------------------
    def ensure_schema(self) -> bool:
        """Uniqueness on node_id per label, plus the read index. Nothing else is
        available: Community has no property-existence or property-type constraints."""
        if self._driver is None:
            return False
        for label in SHAPES:
            write(self._driver,
                  f"CREATE CONSTRAINT {label.lower()}_node_id_unique IF NOT EXISTS "
                  f"FOR (n:{label}) REQUIRE n.node_id IS UNIQUE",
                  self._database)
            write(self._driver,
                  f"CREATE INDEX {label.lower()}_persona_read IF NOT EXISTS "
                  f"FOR (n:{label}) ON (n.persona_id, n.source_field)",
                  self._database)
            # The MERGE key, ENFORCED rather than trusted. Composite property-uniqueness
            # IS available in Community -- only property-EXISTENCE, property-TYPE and KEY
            # constraints are Enterprise-only, which is a distinction worth stating
            # because the repo previously recorded uniqueness as "all Community gives us".
            # Without this, a source_key collision silently folds two card entries into
            # one node and takes the annotations of whichever it matched.
            write(self._driver,
                  f"CREATE CONSTRAINT {label.lower()}_source_key_unique IF NOT EXISTS "
                  f"FOR (n:{label}) REQUIRE (n.persona_id, n.source_field, n.source_key) "
                  f"IS UNIQUE",
                  self._database)
        # Register the nullable annotation key so a read does not emit UNRECOGNIZED
        # notifications. Same reason as the rule store's _REGISTER_KEYS: setting a
        # property to null DELETES it, so a key only ever null never enters the registry.
        write(self._driver,
              "CREATE (x:_PropertyKeyRegistry {last_referenced: ''}) DELETE x",
              self._database)
        return True

    # ---- build / rebuild ------------------------------------------------
    def apply_card(self, card: Dict[str, Any], *, dry_run: bool = False) -> Dict[str, Any]:
        """Build or rebuild from a card. Idempotent. Preserves annotations on rebuild.

        There is deliberately ONE method rather than `build` and `rebuild`: the difference
        between them is whether a node already exists, which Cypher's MERGE already knows.
        Two methods would be two code paths that must agree, and the first time they
        disagreed the rebuild would either wipe annotations or ignore a card edit.
        """
        nodes = identity_nodes(card)
        if dry_run:
            return {"applied": False, "dry_run": True, "would_write": len(nodes)}
        if self._driver is None:
            return {"applied": False, "reason": "graph unavailable"}

        persona_id = card.get("key") or "unknown"
        now = now_iso()
        # `display_name` is a scalar label rather than a fact, so it is a PROPERTY here
        # rather than its own node -- but it must be stored, because prompt_builder
        # renders it as the name in "You are {who}, ..." and cv_summarizer seeds the
        # generated <identity> paragraph with its first token. Under ADR-012 the graph is
        # the system of record; a rendered field held only in the card is a field that
        # dropping the card would lose. It is SET unconditionally (not ON CREATE) so a
        # renamed persona propagates -- it is card-owned content, never her annotation.
        write(self._driver,
              "MERGE (p:Persona {persona_id: $pid}) "
              "ON CREATE SET p.created_at = $now "
              "SET p.display_name = $display_name",
              self._database, pid=persona_id, now=now,
              display_name=(card.get("display_name") or persona_id))

        self._capture_baseline(persona_id, card, now)
        self._set_current_dials(persona_id, card)

        # ONE transaction for the whole rebuild. At ~128 nodes there is no size
        # argument for batching, and batching would trade away the property that
        # matters most for a system of record: Neo4j's docs confirm that with
        # `CALL { } IN TRANSACTIONS`, inner transactions that already committed are NOT
        # rolled back on a later failure — leaving some nodes regenerated and some stale
        # with no automatic repair.
        # The generic form above needs a per-label CREATE, which Cypher cannot
        # parameterise — a label is not a parameter, and interpolating one from
        # anything but a closed code-side enum is an injection surface. So the write is
        # one statement PER LABEL, each with its label interpolated from SHAPES, and all
        # of them inside one driver transaction.
        created = updated = 0
        by_label: Dict[str, List[Dict[str, Any]]] = {}
        for spec in nodes:
            shape: Shape = SHAPES[spec["_shape"]]
            props = {k: v for k, v in spec.items() if k != "_shape"}
            content, annotation = shape.split(props)
            if annotation:
                raise ValueError(
                    f"the card produced an ANNOTATION property {sorted(annotation)} for "
                    f"{shape.label}. A card can only ever produce content — an annotation "
                    f"arriving from the card would be her learning overwritten by her "
                    f"factory settings on every rebuild."
                )
            content["node_id"] = new_id()
            content["created_at"] = now
            content.setdefault("valid_from", now)
            # Absent optional properties are passed as null EXPLICITLY so every row of a
            # single UNWIND has the same shape; the SET below assigns them individually,
            # never via `+=`, so a null here cannot delete a property it did not set.
            for optional in ("kind", "level", "priority"):
                content.setdefault(optional, None)
            by_label.setdefault(shape.label, []).append(content)

        for label, specs in by_label.items():
            rows = write(
                self._driver,
                f"""
                MATCH (p:Persona {{persona_id: $pid}})
                UNWIND $specs AS s
                MERGE (n:{label}:CurrentIdentity {{persona_id: s.persona_id,
                                                   source_field: s.source_field,
                                                   source_key: s.source_key}})
                  ON CREATE SET n.node_id = s.node_id,
                                n.created_at = s.created_at,
                                n.salience = 0.0,
                                n.reinforced_count = 0
                SET n.text        = s.text,
                    n.origin      = s.origin,
                    n.source_hash = s.source_hash,
                    n.source_index = s.source_index,
                    n.valid_from  = coalesce(n.valid_from, s.valid_from),
                    n.kind        = s.kind,
                    n.level       = s.level,
                    n.priority    = s.priority
                MERGE (p)-[:{_LABEL_TO_EDGE[label]}]->(n)
                RETURN n.node_id AS node_id, n.created_at AS created_at
                """,
                self._database, pid=persona_id, specs=specs,
            )
            for r in rows or []:
                if r.get("created_at") == now:
                    created += 1
                else:
                    updated += 1

        # A card entry that was REMOVED should stop being current. Soft-expire rather
        # than delete: the graph is the system of record, so a hard delete is the one
        # operation with no recovery, and an annotation pointing at a deleted node is
        # worse than one pointing at an expired node.
        # Keyed the SAME WAY as the MERGE. Keyed on source_index this compared a live
        # node against a REORDERED position, so a pure reorder marked surviving nodes
        # as absent from the card and expired them.
        live_keys = [[n["source_field"], n["source_key"]] for n in nodes]
        expired = write(
            self._driver,
            """
            MATCH (p:Persona {persona_id: $pid})-[]->(n:CurrentIdentity)
            WHERE NOT [n.source_field, n.source_key] IN $live
            SET n.expired_at = $now, n.valid_to = coalesce(n.valid_to, $now)
            REMOVE n:CurrentIdentity
            RETURN count(n) AS n
            """,
            self._database, pid=persona_id, live=live_keys, now=now,
        )
        return {
            "applied": True, "persona_id": persona_id,
            "created": created, "updated": updated,
            "expired": (expired[0]["n"] if expired else 0),
            "total": len(nodes),
        }

    # ---- read -----------------------------------------------------------
    def identity(self, persona_id: str, *, kind: Optional[str] = None,
                 limit: int = 256) -> List[Dict[str, Any]]:
        """Her live identity, newest-anchored ordering, content and annotations together."""
        if self._driver is None:
            return []
        return read(
            self._driver,
            """
            MATCH (p:Persona {persona_id: $pid})-[]->(n:CurrentIdentity)
            WHERE ($kind IS NULL OR n.kind = $kind)
            RETURN labels(n)[0] AS label, n.node_id AS node_id, n.text AS text,
                   n.kind AS kind, n.level AS level, n.origin AS origin,
                   n.source_field AS source_field, n.source_index AS source_index,
                   n.source_hash AS source_hash,
                   n.salience AS salience, n.reinforced_count AS reinforced_count,
                   n.last_referenced AS last_referenced
            ORDER BY n.source_field ASC, n.source_index ASC
            LIMIT $limit
            """,
            self._database, pid=persona_id, kind=kind, limit=int(limit),
        )

    def _capture_baseline(self, persona_id: str, card: Dict[str, Any],
                          now: str) -> None:
        """Snapshot the shipped dials ONCE, the first time this persona is applied.

        evolution.yaml calls this its day-1 irreversible item, and the reason is not
        ceremony: "Have you changed since we met?" (evo.cq01) is a diff against a
        starting point, so a starting point that was never recorded makes every
        evolution question permanently unanswerable -- and no later work recovers it,
        because by then the starting point has already moved.

        EVERY property is set under ON CREATE. That is the entire mechanism: a rebuild
        after a card edit binds the existing :Baseline and writes nothing, so the
        snapshot cannot be refreshed into meaninglessness by the routine path. A
        baseline that a rebuild updates is not a baseline.
        """
        if self._driver is None:
            return
        sliders = ((card.get("emotional_profile") or {}).get("sliders") or {})
        dials = {n: sliders.get(n) for n in SLIDER_NAMES if sliders.get(n) is not None}
        sets = " ".join(f"b.dial_{n} = ${n}," for n in dials)
        write(
            self._driver,
            f"""
            MATCH (p:Persona {{persona_id: $pid}})
            MERGE (p)-[:{BASELINE_EDGE}]->(b:{BASELINE_LABEL} {{persona_id: $pid}})
            ON CREATE SET {sets}
                          b.captured_at = $now,
                          b.card_fingerprint = $fingerprint,
                          b.dial_names = $dial_names
            """,
            self._database, pid=persona_id, now=now,
            fingerprint=_card_fingerprint(card),
            dial_names=list(dials), **dials,
        )

    def _set_current_dials(self, persona_id: str, card: Dict[str, Any]) -> None:
        """Her CURRENT dial values, on the Persona node, refreshed every apply.

        The baseline alone cannot answer evo.cq01 -- a diff needs both ends. Holding
        current values here rather than reading them from the card keeps the comparison
        answerable from the graph alone, which is what ADR-012 asks for. These are
        card-owned content today; when she can move a dial, this is the property that
        moves, and the :Baseline stays where it is.
        """
        if self._driver is None:
            return
        sliders = ((card.get("emotional_profile") or {}).get("sliders") or {})
        dials = {n: sliders.get(n) for n in SLIDER_NAMES if sliders.get(n) is not None}
        if not dials:
            return
        sets = ", ".join(f"p.dial_{n} = ${n}" for n in dials)
        write(self._driver,
              f"MATCH (p:Persona {{persona_id: $pid}}) SET {sets}",
              self._database, pid=persona_id, **dials)

    def baseline(self, persona_id: str) -> Optional[Dict[str, Any]]:
        """The recorded starting point, or None if this persona was never applied."""
        if self._driver is None:
            return None
        # Named projections, never `RETURN b`: graph_driver refuses a whole Node at the
        # serialisation seam because Record.data() flattens it and drops labels silently.
        dial_proj = ", ".join(f"b.dial_{n} AS {n}" for n in SLIDER_NAMES)
        rows = read(
            self._driver,
            f"""
            MATCH (p:Persona {{persona_id: $pid}})-[:{BASELINE_EDGE}]->(b:{BASELINE_LABEL})
            RETURN b.captured_at AS captured_at,
                   b.card_fingerprint AS card_fingerprint,
                   {dial_proj}
            """,
            self._database, pid=persona_id)
        if not rows:
            return None
        row = rows[0]
        return {
            "captured_at": row["captured_at"],
            "card_fingerprint": row["card_fingerprint"],
            "dials": {n: row[n] for n in SLIDER_NAMES if row.get(n) is not None},
        }

    def dial_drift(self, persona_id: str) -> Dict[str, Any]:
        """Current dials minus baseline dials -- the raw material for evo.cq01.

        Returns the per-dial delta rather than a verdict. "Has she changed?" answered as
        a bare boolean is what lets a persona at temperature 0.9 confabulate growth;
        evolution.yaml's negative fixture is explicit that an affirmative answer which
        resolves to nothing is a FAILURE, so this returns the resolvable numbers.
        """
        base = self.baseline(persona_id)
        if base is None or self._driver is None:
            return {"available": False, "reason": "no baseline recorded"}
        dial_proj = ", ".join(f"p.dial_{n} AS {n}" for n in SLIDER_NAMES)
        rows = read(self._driver,
                    f"MATCH (p:Persona {{persona_id: $pid}}) RETURN {dial_proj}",
                    self._database, pid=persona_id)
        if not rows:
            return {"available": False, "reason": "persona not found"}
        current = rows[0]
        drift = {}
        for name, was in base["dials"].items():
            now_v = current.get(name)
            if was is not None and now_v is not None and now_v != was:
                drift[name] = {"baseline": was, "current": now_v,
                               "delta": round(now_v - was, 6)}
        return {"available": True, "captured_at": base["captured_at"],
                "changed": drift, "unchanged": len(base["dials"]) - len(drift)}

    def reinforce(self, node_id: str, *, salience_delta: float = 0.1) -> Dict[str, Any]:
        """Her one write. Touches ANNOTATION properties only, by construction.

        The Cypher names three properties and no others, so this cannot reach content
        even if called with something odd — the same discipline as the rule store's
        hard-wall guard, applied by omission rather than by validation.
        """
        if self._driver is None:
            return {"reinforced": False, "reason": "graph unavailable"}
        rows = write(
            self._driver,
            """
            MATCH (n:CurrentIdentity {node_id: $nid})
            SET n.reinforced_count = coalesce(n.reinforced_count, 0) + 1,
                n.salience = CASE WHEN coalesce(n.salience, 0.0) + $d > 1.0 THEN 1.0
                                  ELSE coalesce(n.salience, 0.0) + $d END,
                n.last_referenced = $now
            RETURN n.node_id AS node_id, n.salience AS salience,
                   n.reinforced_count AS reinforced_count
            """,
            self._database, nid=node_id, d=float(salience_delta), now=now_iso(),
        )
        return {"reinforced": bool(rows), **(rows[0] if rows else {})}
