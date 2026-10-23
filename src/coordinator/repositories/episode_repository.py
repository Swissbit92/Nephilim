"""Episodes in the graph: POINTERS ONLY. ADR-021 Phase 1.

An `:Episode` is one conversation session, recorded as a pointer — `session_id`,
when it ran, how many messages. **No message text.** ADR-021 is explicit that
message text stays in SQLite, and `idn.cq06` requires the graph to REFUSE "what
did she say to me three weeks ago". Copying text here would make that question
answerable and the refusal is the design.

`CONTINUES` orders episodes chronologically, which is the part `prf.cq03` calls
"genuinely GRAPH-shaped rather than table-shaped": two scenes are two ordered
sequences, not two rows with timestamps.

🔴 **`:Thread` AND `PART_OF` ARE DELIBERATELY NOT BUILT HERE, and that is a
refusal rather than an omission.** `prf.cq03` demands them, but its negative
fixture is "She must NOT merge two separate threads into one" — and grouping
sessions into topical threads requires scene-boundary detection, which this
phase explicitly excludes (no LLM). The only groupings available without it are
both wrong:

  * one thread per operator — merges every scene, which IS the forbidden error
  * one thread per session — makes Thread a synonym for Episode, carrying no
    information and answering nothing

So the layer that can be built correctly is built, and the layer that cannot is
left for the phase that has the instrument. C17 errors on a label that is BUILT
and undemanded, never on one demanded and unbuilt, so declining costs nothing
and inventing would cost correctness.

⚠️ **NO TIME-GAP BOUNDARY DETECTION.** Confirmed by reading source across Mem0,
Letta, Graphiti/Zep and LangGraph: not one computes a session boundary from
elapsed time — a session is whatever opaque id the caller supplies, and the only
published alternative is LLM-judged topic shift (SeCom, arXiv 2502.05589).
Cognee is the single near-miss and it is instructive: it has a SessionRecord with
a status, and its one gap-derived state (`abandoned`) is deliberately NOT
persisted — inferred at read time instead. So `session_id` is the boundary here,
which is the pattern that actually ships.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..graph_driver import read, write
from ..graph_ids import new_id, now_iso

logger = logging.getLogger(__name__)


class EpisodeRepository:
    """Read and write `(:Episode)` and the `CONTINUES` chain."""

    def __init__(self, driver: Any, database: str = "neo4j") -> None:
        self._driver = driver
        self._database = database

    def ensure_schema(self) -> None:
        """One statement per call — Neo4j refuses to mix schema and data
        changes in a single transaction."""
        for stmt in (
            "CREATE CONSTRAINT episode_id_unique IF NOT EXISTS "
            "FOR (e:Episode) REQUIRE e.episode_id IS UNIQUE",
            # The session is the boundary, so it is also the natural key.
            "CREATE CONSTRAINT episode_session_unique IF NOT EXISTS "
            "FOR (e:Episode) REQUIRE (e.operator_id, e.session_id) IS UNIQUE",
            "CREATE INDEX episode_operator_read IF NOT EXISTS "
            "FOR (e:Episode) ON (e.operator_id, e.started_at)",
        ):
            try:
                write(self._driver, stmt, self._database)
            except Exception as e:  # noqa: BLE001
                if "EquivalentSchemaRuleAlreadyExists" not in str(e):
                    raise

    def record_episode(self, operator_id: str, session_id: str, *,
                       started_at: str, ended_at: Optional[str] = None,
                       message_count: int = 0,
                       persona_key: str = "") -> str:
        """Upsert one episode. Idempotent on `(operator_id, session_id)`.

        ⚠️ MERGE on the natural key, then SET the mutable fields individually.
        `SET e += $map` DELETES any key whose value is null — a recorded trap in
        this repo, which is why `ended_at` is assigned on its own line and a
        null simply writes nothing rather than removing a previously-set value.
        """
        stamp = now_iso()
        rows = write(
            self._driver,
            """
            MERGE (e:Episode {operator_id: $operator_id, session_id: $session_id})
              ON CREATE SET e.episode_id = $episode_id,
                            e.created_at = $stamp
            SET e.started_at    = $started_at,
                e.message_count = $message_count,
                e.persona_key   = $persona_key,
                e.last_touched  = $stamp
            RETURN e.episode_id AS episode_id
            """,
            self._database,
            operator_id=operator_id, session_id=session_id,
            episode_id=new_id(), started_at=started_at,
            message_count=int(message_count), persona_key=persona_key,
            stamp=stamp,
        )
        episode_id = rows[0]["episode_id"] if rows else ""
        if ended_at:
            # Separate statement precisely so a null never reaches a SET.
            write(
                self._driver,
                "MATCH (e:Episode {episode_id: $episode_id, "
                "operator_id: $operator_id}) SET e.ended_at = $ended_at",
                self._database, episode_id=episode_id, operator_id=operator_id,
                ended_at=ended_at,
            )
        return episode_id

    def fact_cursor(self, operator_id: str, session_id: str) -> dict | None:
        """The high-water mark for fact extraction on this session, or None.

        Returns `{"at": <timestamp>, "message_id": <id>}`. `None` means this
        session has never had a successful extraction, and the caller should
        treat the whole transcript as uncovered.

        A `read()`, projecting named scalars rather than the node, per the
        `graph_driver` contract.
        """
        rows = read(
            self._driver,
            """
            MATCH (e:Episode {operator_id: $operator_id, session_id: $session_id})
            WHERE e.fact_cursor_at IS NOT NULL
            RETURN e.fact_cursor_at         AS at,
                   e.fact_cursor_message_id AS message_id
            LIMIT 1
            """,
            self._database,
            operator_id=operator_id, session_id=session_id,
        )
        if not rows:
            return None
        return {"at": rows[0]["at"], "message_id": rows[0].get("message_id") or ""}

    def advance_fact_cursor(self, operator_id: str, session_id: str, *,
                            cursor_at: str, cursor_message_id: str = "",
                            message_count: int = 0, persona_key: str = "",
                            started_at: str = "") -> str:
        """Move this session's fact-extraction high-water mark.

        ⚠️ `write()`, NOT `read()`. A Cypher `SET` under a read transaction
        fails with "Writing in read access mode not allowed" — already hit once
        in this repo on `invalidate_fact`. `write()` returns rows too, so there
        is no reason to reach for `read()` here.

        ⚠️ `started_at` IS `ON CREATE` ONLY, UNLIKE `record_episode`, WHICH SETS
        IT UNCONDITIONALLY. 229 `:Episode` nodes already carry a correct
        `started_at` from `scripts/utils/backfill_episodes.py`. A flush knows
        only its own window and would overwrite that with the window's first
        message — corrupting the history of every session it touches. The
        asymmetry is deliberate and is pinned by a test.

        ⚠️ THIS REPOSITORY IS DUMB ABOUT MONOTONICITY, ON PURPOSE. It writes
        whatever `cursor_at` it is given, including one that moves backwards.
        Refusing a rewind here would put the compare-and-advance in the wrong
        place: it has to happen at the single consumer (the worker thread),
        which re-reads the stored cursor at execution time. Putting it at the
        enqueue site, or splitting it across both, is exactly the shape of
        Mem0 #6515's TOCTOU race. The responsibility boundary is pinned by a
        test asserting the repo accepts a rewind.

        `$operator_id` appears in the MERGE pattern AND in the parameters,
        for the reason `link_chronology` already documents: `guarded_write` can
        only refuse a write whose owner it can read off the parameters.
        """
        stamp = now_iso()
        rows = write(
            self._driver,
            """
            MERGE (e:Episode {operator_id: $operator_id, session_id: $session_id})
              ON CREATE SET e.episode_id = $episode_id,
                            e.created_at = $stamp,
                            e.started_at = $started_at
            SET e.fact_cursor_at         = $cursor_at,
                e.fact_cursor_message_id = $cursor_message_id,
                e.message_count          = $message_count,
                e.persona_key            = $persona_key,
                e.last_touched           = $stamp
            RETURN e.episode_id AS episode_id
            """,
            self._database,
            operator_id=operator_id, session_id=session_id,
            episode_id=new_id(), cursor_at=cursor_at,
            cursor_message_id=cursor_message_id or "",
            message_count=int(message_count), persona_key=persona_key or "",
            started_at=started_at or cursor_at, stamp=stamp,
        )
        return rows[0]["episode_id"] if rows else ""

    def link_chronology(self, operator_id: str) -> int:
        """Build the `CONTINUES` chain in `started_at` order. Returns edges made.

        Ordered EDGES, not rows with timestamps — `prf.cq03` is explicit that
        this is the graph-shaped part. Idempotent: MERGE on the pair, and the
        `WHERE` restricts each episode to its immediate predecessor so a
        re-run cannot fan out.
        """
        rows = read(
            self._driver,
            """
            MATCH (e:Episode {operator_id: $operator_id})
            RETURN e.episode_id AS episode_id
            ORDER BY e.started_at ASC, e.episode_id ASC
            LIMIT 4096
            """,
            self._database, operator_id=operator_id,
        )
        made = 0
        for prev, nxt in zip(rows, rows[1:]):
            # ⚠️ `operator_id` is in the MATCH and in the params ON PURPOSE,
            # even though episode_id is already unique. The test write-guard in
            # tests/conftest.py can only refuse writes whose OWNER it can read
            # off the parameters — it checks persona_id / p / pid / operator_id.
            # A write keyed only on $prev/$next is invisible to it, so a test
            # could link CONTINUES edges between LIVE episodes. Naming the owner
            # makes the write both narrower and guardable; widening the guard
            # again would have left the next writer to rediscover this.
            write(
                self._driver,
                """
                MATCH (a:Episode {episode_id: $prev, operator_id: $operator_id})
                MATCH (b:Episode {episode_id: $next, operator_id: $operator_id})
                MERGE (b)-[:CONTINUES]->(a)
                """,
                self._database, operator_id=operator_id,
                prev=prev["episode_id"], next=nxt["episode_id"],
            )
            made += 1
        return made

    def recent_episodes(self, operator_id: str, *,
                        limit: int = 10) -> List[Dict[str, Any]]:
        """Most recent episodes first. Pointers only — no message text.

        `status` is DERIVED here, not stored. Cognee is the only production
        system with anything like it and its one gap-derived state is
        deliberately not persisted; a stored status computed from a clock goes
        stale the moment the clock moves on. No published system ships
        open/paused/resumed, so this is ours to design and the cheap honest
        version is to compute it on read.
        """
        return read(
            self._driver,
            """
            MATCH (e:Episode {operator_id: $operator_id})
            OPTIONAL MATCH (e)-[:CONTINUES]->(prev:Episode)
            RETURN e.episode_id    AS episode_id,
                   e.session_id    AS session_id,
                   e.started_at    AS started_at,
                   e.ended_at      AS ended_at,
                   e.message_count AS message_count,
                   e.persona_key   AS persona_key,
                   e.last_touched  AS last_touched,
                   e.fact_cursor_at AS fact_cursor_at,
                   prev.session_id AS continues_session_id
            ORDER BY e.started_at DESC
            LIMIT $limit
            """,
            self._database, operator_id=operator_id, limit=int(limit),
        )
