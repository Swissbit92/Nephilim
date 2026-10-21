"""Facts about the OPERATOR, in the graph. ADR-021's write path.

🔴 WHY THE GRAPH AND NOT `profile_data["facts"]`. That store is a JSON blob of
BARE STRINGS, so there is nowhere to hang a subject or a speaker. Measured
2026-10-21: after the self-facts section shipped, she recited her own appearance
and the extractor filed it under HIM — `Assistant's eyes are a deep ocean blue`
sat in the operator's fact list, and would have been read back as "things you
know about him". With no subject field that contamination is unrepresentable,
let alone preventable. 38 such rows appeared in ~20 minutes of probing.

**The schema is dictated by the competency questions, not chosen here.**
`cmp.cq01` ("What do you know about me?") demands labels `[Operator, Fact]`,
rel `[ABOUT]`, props `[valid_from, valid_to]`. `cmp.cq02` ("Which of those did
you learn from me, and which did you always know?") demands `[origin,
source_session_id]`. C17 runs backwards — nothing is built that no question
demands — and both labels were already demanded and unbuilt.

⚠️ `:Fact` IS DELIBERATELY NOT A `Shape`. Shapes are card-derived identity, and
`identity_repository.apply_card` EXPIRES every node absent from the card on each
rebuild. A learned operator fact is absent from the card by definition, so
modelling it as a Shape would delete the entire learned store on the next
re-seed — exactly the hazard `cmp.cq02`'s negative fixture calls load-bearing:
"re-seeding the card silently overwrites months of learned facts with shipped
defaults". It is therefore hand-declared in `graph_surface`, and `_BASE_CONTENT`
does not admit `asserted_by` anyway.

⚠️ **`asserted_by` IS SET FROM THE SQLITE `role` COLUMN AND NEVER BY AN LLM.**
That is ADR-021 Phase 0's requirement and the provenance research confirms it is
stronger than production practice: Mem0 is the only system of six with a
speaker field (`attributed_to`) and its LLM-judged version failed in exactly our
shape — roles stripped upstream of extraction so everything defaulted to the
user (mem0ai/mem0#5642). Graphiti, Zep, Letta, Cognee and LangGraph carry no
fact-level speaker attribution at all.

⚠️ **EVERY POINT-IN-TIME READ GUARDS BOTH BOUNDS WITH `IS NULL OR`.** A
still-valid fact has `valid_to = NULL`, and `f.valid_to > $t` evaluates to NULL
rather than false, so the row is silently skipped. This repo has already shipped
that bug once — `NOT [field, null] IN $live` left 128 stale nodes live and
reported `expired: 0` with no error. Graphiti's tracker carries the same class
twice (#769, #2012). The tests exercise it against a NEVER-superseded fact,
which is the case a green suite cannot see.

Invalidate, never delete.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..graph_driver import read, write
from ..graph_ids import new_id, now_iso

logger = logging.getLogger(__name__)

#: Who asserted the fact. Set from the SQLite `role` column, never by a model.
ASSERTED_BY = frozenset({"user", "assistant", "operator_curated", "card"})

#: How the fact arrived. Reuses the identity vocabulary so one word means one
#: thing across the graph — `cmp.cq02` partitions on exactly this.
FACT_ORIGINS = frozenset({"card", "learned", "conversation", "inferred"})


class OperatorFactRepository:
    """Read and write `(:Fact)-[:ABOUT]->(:Operator)`."""

    def __init__(self, driver: Any, database: str = "neo4j") -> None:
        self._driver = driver
        self._database = database

    # ---------- schema ----------------------------------------------------

    def ensure_schema(self) -> None:
        """Constraints and indexes. One statement per call — Neo4j refuses to
        mix schema and data changes in a single transaction."""
        for stmt in (
            "CREATE CONSTRAINT operator_id_unique IF NOT EXISTS "
            "FOR (o:Operator) REQUIRE o.operator_id IS UNIQUE",
            "CREATE CONSTRAINT fact_id_unique IF NOT EXISTS "
            "FOR (f:Fact) REQUIRE f.fact_id IS UNIQUE",
            # The read in `current_facts` filters on operator + validity.
            "CREATE INDEX fact_operator_read IF NOT EXISTS "
            "FOR (f:Fact) ON (f.operator_id, f.valid_to)",
            # `cmp.cq02` partitions by origin.
            "CREATE INDEX fact_origin_read IF NOT EXISTS "
            "FOR (f:Fact) ON (f.operator_id, f.origin)",
        ):
            try:
                write(self._driver, stmt, self._database)
            except Exception as e:  # noqa: BLE001
                # Equivalent-constraint races are benign; anything else is not.
                if "EquivalentSchemaRuleAlreadyExists" not in str(e):
                    raise

    # ---------- write -----------------------------------------------------

    def add_fact(self, operator_id: str, text: str, *, asserted_by: str,
                 origin: str = "learned",
                 source_session_id: Optional[str] = None) -> Optional[str]:
        """Record one fact about the operator. Returns its `fact_id`.

        Refuses rather than coerces on an unknown vocabulary value: a silently
        accepted `asserted_by` is indistinguishable from a correct one later, and
        this field exists precisely so that distinction survives.
        """
        if asserted_by not in ASSERTED_BY:
            raise ValueError(
                f"asserted_by={asserted_by!r} is not in {sorted(ASSERTED_BY)}. "
                "It must come from the SQLite role column, never from a model."
            )
        if origin not in FACT_ORIGINS:
            raise ValueError(f"origin={origin!r} is not in {sorted(FACT_ORIGINS)}")
        text = (text or "").strip()
        if not text:
            return None

        fact_id = new_id()
        stamp = now_iso()
        write(
            self._driver,
            """
            MERGE (o:Operator {operator_id: $operator_id})
              ON CREATE SET o.created_at = $stamp
            CREATE (f:Fact {
                fact_id:           $fact_id,
                operator_id:       $operator_id,
                text:              $text,
                origin:            $origin,
                asserted_by:       $asserted_by,
                source_session_id: $source_session_id,
                valid_from:        $stamp,
                created_at:        $stamp
            })
            CREATE (f)-[:ABOUT]->(o)
            """,
            self._database,
            operator_id=operator_id, fact_id=fact_id, text=text,
            origin=origin, asserted_by=asserted_by,
            source_session_id=source_session_id, stamp=stamp,
        )
        return fact_id

    def invalidate_fact(self, fact_id: str) -> bool:
        """Mark a fact no longer true. NEVER deletes — ADR-021.

        `valid_to` is world time (it stopped being true) and `expired_at` is
        transaction time (we stopped believing it). Both are set, because a
        transaction time cannot be reconstructed after the fact.
        """
        # ⚠️ `write`, not `read` — `read` routes with RoutingControl.READ and
        # Neo4j refuses "Writing in read access mode not allowed" even though
        # the statement RETURNs. The first version used `read` to get the return
        # value and failed on all three invalidation tests; `write` returns rows
        # too, so there was never a reason to reach for `read` here.
        stamp = now_iso()
        rows = write(
            self._driver,
            """
            MATCH (f:Fact {fact_id: $fact_id})
            WHERE f.valid_to IS NULL
            SET f.valid_to = $stamp, f.expired_at = $stamp
            RETURN f.fact_id AS fact_id
            LIMIT 1
            """,
            self._database, fact_id=fact_id, stamp=stamp,
        )
        return bool(rows)

    # ---------- read ------------------------------------------------------

    def current_facts(self, operator_id: str, *,
                      limit: int = 256) -> List[Dict[str, Any]]:
        """Facts believed true NOW. Answers `cmp.cq01`.

        ⚠️ `f.valid_to IS NULL OR` is load-bearing and its absence is SILENT —
        every still-valid fact has a null `valid_to`, and a bare comparison
        against null yields null, which WHERE treats as false. `cmp.cq01`'s
        negative fixture is the other half: an expired fact must not appear.
        """
        return read(
            self._driver,
            """
            MATCH (f:Fact)-[:ABOUT]->(o:Operator {operator_id: $operator_id})
            WHERE f.valid_to IS NULL
            RETURN f.fact_id           AS fact_id,
                   f.text              AS text,
                   f.origin            AS origin,
                   f.asserted_by       AS asserted_by,
                   f.source_session_id AS source_session_id,
                   f.valid_from        AS valid_from
            ORDER BY f.valid_from ASC, f.fact_id ASC
            LIMIT $limit
            """,
            self._database, operator_id=operator_id, limit=int(limit),
        )

    def facts_at(self, operator_id: str, when: str, *,
                 limit: int = 256) -> List[Dict[str, Any]]:
        """Facts believed true AT `when`. Point-in-time, both bounds guarded."""
        return read(
            self._driver,
            """
            MATCH (f:Fact)-[:ABOUT]->(o:Operator {operator_id: $operator_id})
            WHERE f.valid_from <= $when
              AND (f.valid_to IS NULL OR f.valid_to > $when)
            RETURN f.fact_id     AS fact_id,
                   f.text        AS text,
                   f.origin      AS origin,
                   f.asserted_by AS asserted_by,
                   f.valid_from  AS valid_from,
                   f.valid_to    AS valid_to
            ORDER BY f.valid_from ASC, f.fact_id ASC
            LIMIT $limit
            """,
            self._database, operator_id=operator_id, when=when, limit=int(limit),
        )

    def facts_by_origin(self, operator_id: str, *,
                        limit: int = 256) -> List[Dict[str, Any]]:
        """Answers `cmp.cq02` — learned from him vs always known.

        ⚠️ The negative fixture is the load-bearing part: a card-declared fact
        must NOT come back as learned. `origin` is recorded at write time and
        never inferred from recency.
        """
        return read(
            self._driver,
            """
            MATCH (f:Fact)-[:ABOUT]->(o:Operator {operator_id: $operator_id})
            WHERE f.valid_to IS NULL
            RETURN f.origin            AS origin,
                   f.asserted_by       AS asserted_by,
                   f.text              AS text,
                   f.source_session_id AS source_session_id
            ORDER BY f.origin ASC, f.valid_from ASC
            LIMIT $limit
            """,
            self._database, operator_id=operator_id, limit=int(limit),
        )
