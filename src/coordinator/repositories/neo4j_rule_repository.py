"""Standing behavioural rules in Neo4j — ADR-014.

DELIBERATELY NOT A BaseRepository SUBCLASS, and CLAUDE.md's "all repositories
extend BaseRepository" is knowingly departed from here. That base class is not a
datastore abstraction, it is a SQLite-with-pooling abstraction: `_execute(query,
params: tuple)` is positional-SQL shaped and the adapter contract underneath is
cursor/row-dict shaped. Cypher takes named parameters and has no cursor. Forcing
the fit would mean a `Neo4jAdapter` that satisfies `fetchone`/`fetchall`/`execute`
awkwardly and fights the driver at every call. This mirrors the base's SHAPE
instead — injected connection with a settings fallback, `_ensure_*` schema
bootstrap from __init__, verb methods, module logger — which is what the callers
actually depend on, since every repository is typed `Any` at the ChatDeps seam.

THE MODEL. Rules are NODES, not relationship properties, and that is not a
stylistic choice: a Neo4j relationship cannot be the endpoint of another
relationship, and a rule needs two things pointed AT it — a supersession chain and
a provenance edge. Graphiti, the reference production graph memory for agents,
puts facts on edges and pays exactly that price: its provenance is an array of
opaque UUID strings inside a property, and it has NO supersession edge at all, so
"what did I believe before X" is a scan-and-reconstruct rather than a traversal.
Facts may keep the edge shape; rules cannot.

  (:Persona {persona_id})-[:HAS_RULE]->(:Rule:CurrentRule {...})
  (:Rule)-[:SUPERSEDES]->(:Rule)        new -> old, chain-walkable
  (:Rule)-[:LEARNED_FROM]->(:Message)   a real edge, not a uuid array

`:CurrentRule` IS A LABEL BECAUSE NEO4J INDEXES DO NOT STORE NULLS. The hot
predicate is `expired_at IS NULL`, and a range index on `expired_at` can never
serve it while still costing on every write. A label-scoped index is Neo4j's only
partial index, so the index contains exactly the rows the read touches. Null stays
the semantic truth; the label is derived state, reconciled by
`check_integrity()`. Graphiti ships the useless `expired_at` index — do not copy
it. The same mistake already exists locally in
`idx_memory_facts_valid ON memory_facts(valid_to)`.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..graph_driver import read, write
from ..graph_ids import new_id, now_iso

logger = logging.getLogger(__name__)

# Controlled vocabularies, module-level and fail-loud, matching
# memory_fact_repository.PREDICATE_VOCABULARY. Community enforces uniqueness and
# NOTHING else — no property existence, no node keys, no type constraints, and no
# edition of Neo4j has a value-domain constraint. So these are the only thing
# standing between the graph and a rule with rule_type="hardwall" (sic) that
# silently reads as unclassified forever.
RULE_TYPES: frozenset[str] = frozenset({"dial", "soft_wall", "hard_wall"})
RULE_TYPE_SOURCES: frozenset[str] = frozenset({"declared", "default"})
ORIGINS: frozenset[str] = frozenset({"card", "conversation", "inferred"})

#: The fail-closed default. An unclassified rule is a HARD WALL, never a movable
#: one, because the costs are asymmetric: a soft wall wrongly held as hard is an
#: annoyance the operator notices and corrects, while a hard wall wrongly treated
#: as soft is an identity or consent boundary that became negotiable through an
#: OMISSION, and nothing announces it. See docs/INVARIANTS.md.
DEFAULT_RULE_TYPE = "hard_wall"

# Named explicitly. An unnamed constraint gets a server-generated name, which makes
# SHOW CONSTRAINTS output and any future DROP unpredictable.
_SCHEMA: tuple[str, ...] = (
    # Uniqueness is the ONLY constraint Community enforces — and it is load-bearing
    # rather than hygiene: `ORDER BY priority DESC, rule_id DESC` is a TOTAL order
    # only if rule_id is unique, so this constraint is half the determinism proof.
    "CREATE CONSTRAINT rule_id_unique IF NOT EXISTS "
    "FOR (r:Rule) REQUIRE r.rule_id IS UNIQUE",
    "CREATE CONSTRAINT persona_id_unique IF NOT EXISTS "
    "FOR (p:Persona) REQUIRE p.persona_id IS UNIQUE",
    # THE read index. TWO columns, not three, and that is MEASURED rather than
    # chosen — see the note below. Scoped to :CurrentRule so it behaves as a
    # partial index over exactly the live rows.
    "CREATE INDEX current_rule_read IF NOT EXISTS "
    "FOR (r:CurrentRule) ON (r.persona_id, r.priority)",
    # Time travel over the full history, which :CurrentRule deliberately excludes.
    "CREATE INDEX rule_history_read IF NOT EXISTS "
    "FOR (r:Rule) ON (r.persona_id, r.valid_from)",
    # DELIBERATELY ABSENT: an index on expired_at or valid_to. See the module
    # docstring — it cannot serve IS NULL and costs on every write.
)

# MEASURED 2026-09-26 on Neo4j 5.26.31, and it corrects an earlier claim in this
# file and in ADR-014 that the read is "index-backed with no Sort operator".
#
# It is half true. EXPLAIN on the real read, with the real index ONLINE:
#
#   3-column composite (persona_id, priority, rule_id) -> NodeByLabelScan
#   single-property    (persona_id)                    -> NodeIndexSeek
#   2-column           (persona_id, priority)          -> NodeIndexSeek
#
# So a THREE-column composite is not usable for a predicate that constrains only
# the leading property — it was dead weight, paying write cost on every insert and
# never once serving a query. Two columns seek. That is the fix.
#
# AND `Top` IS PRESENT IN ALL THREE PLANS. The ordering is NEVER supplied by the
# index on this version: `ORDER BY priority DESC, rule_id DESC` always becomes a
# bounded sort. Neo4j's own documentation covers only single-property ASCENDING
# index-backed ordering and says nothing about composite or descending, and the
# existence of PartialSort/PartialTop for prefix ordering implies the general case
# is partial at best.
#
# AND THE HONEST CONCLUSION, after chasing it further than it deserved: at this
# data size the planner is RIGHT to scan. There are 9 :CurrentRule nodes. A label
# scan over 9 rows beats an index seek, and it will keep beating it into the
# hundreds. `USING INDEX` is refused outright for this query shape, because
# `expired_at IS NULL` cannot be index-served at all — indexes do not store nulls,
# which is the same fact that made :CurrentRule a label in the first place.
#
# THE REAL ERROR WAS CONFLATING TWO INDEPENDENT CLAIMS, and it is worth naming
# because it survived several passes of review:
#
#   DETERMINISM comes from the ORDER BY being a TOTAL order — a priority plus a
#   UNIQUE, fixed-width, lexicographically-sortable rule_id. It holds under a label
#   scan, under a seek, under `Sort`, under `Top`, and after a restore that changes
#   the planner's mind. It is a property of the query's semantics.
#
#   INDEX-BACKING is a performance property. It is planner-dependent, it changes
#   with the statistics, and at 9 rows it is correctly absent.
#
# ADR-014 claimed "deterministic AND index-backed with no Sort operator" as though
# they were one claim. They are not, and only the first is load-bearing. The tests
# therefore assert DETERMINISM — the same answer across repeated calls, and a
# stable order under tied priorities — and deliberately do NOT assert plan shape,
# which would pin planner behaviour at a scale that does not represent production
# and would fail for a reason unrelated to correctness.
#
# The 2-column index stays: it is measured to be seekable for this predicate shape
# (unlike the 3-column form) and costs almost nothing to maintain at this size. It
# is insurance for scale, not a load-bearing part of today's read.

# REGISTERS THE BI-TEMPORAL PROPERTY KEYS. This looks like a hack and is the
# opposite of one; the alternative is strictly worse.
#
# IN NEO4J, SETTING A PROPERTY TO NULL DELETES IT — there is no stored null. So
# `SET r.valid_to = null` never creates the key, and the key never enters the
# database's property-key registry. Every read then emits
#   "warn: property key does not exist. The property `expired_at` does not exist.
#    Verify that the spelling is correct."
# ...which is CORRECT (IS NULL is true for an absent property, so the semantics
# are right) and fires on every single read until the first supersession happens
# to create the keys.
#
# The obvious fix — disabling the UNRECOGNIZED notification classification on the
# driver — would also silence the case that notification exists for: a genuinely
# misspelled property name, which in a store with no type constraints is a silent
# always-null filter. MEASURED 2026-09-26: with the keys registered, the real read
# emits 0 notifications while `WHERE r.expried_at IS NULL` still emits 1. So
# registering the keys keeps the detection and loses only the noise.
#
# The registry is append-only, so this runs once and the node never survives the
# transaction.
_REGISTER_KEYS = (
    "CREATE (x:_PropertyKeyRegistry {expired_at: '', valid_to: '', "
    "updated_at: ''}) DELETE x"
)

# Emitted when two boots race an identical CREATE ... IF NOT EXISTS: both pass the
# existence check, then one loses at commit. The post-state is correct either way.
_BENIGN_SCHEMA_RACE = frozenset({
    "Neo.ClientError.Schema.EquivalentSchemaRuleAlreadyExists",
    "Neo.ClientError.Schema.ConstraintAlreadyExists",
    "Neo.ClientError.Schema.IndexAlreadyExists",
})


class Neo4jRuleRepository:
    """Read and write a persona's standing rules. Never raises on a graph outage."""

    def __init__(self, driver: Any = None, database: str = "neo4j",
                 ensure_schema: bool = True) -> None:
        self._driver = driver
        self._database = database
        if driver is not None and ensure_schema:
            self.ensure_schema()

    # ---- schema ---------------------------------------------------------

    def ensure_schema(self) -> bool:
        """Create constraints and indexes if absent. Idempotent. Never raises.

        Safe on every boot: with IF NOT EXISTS, no error is thrown and nothing
        happens when a constraint with the same name OR the same schema and type
        already exists.

        ONE STATEMENT PER CALL, deliberately — Neo4j forbids mixing schema and data
        changes in one transaction, and execute_query wraps each call in its own,
        so one-per-call satisfies that for free.

        `db.awaitIndexes()` IS NOT CALLED HERE. It blocks until EVERY index in the
        database is ONLINE — not only ours — with a default timeout of 300 seconds,
        and it throws if any index is FAILED. At startup that is up to five minutes
        of blocked boot, and one poisoned index anywhere would turn every
        subsequent boot into a hard failure. It belongs in a bulk-load path with an
        explicit small timeout, where the question is "is this index usable for the
        query I am about to run".
        """
        if self._driver is None:
            return False
        ok = True
        try:
            write(self._driver, _REGISTER_KEYS, self._database)
        except Exception as e:  # noqa: BLE001
            # Cosmetic only — failure means noisier logs, never wrong answers.
            logger.debug("[Neo4jRules] property-key registration skipped: %s", e)
        for stmt in _SCHEMA:
            try:
                write(self._driver, stmt, self._database)
            except Exception as e:  # noqa: BLE001
                code = getattr(e, "code", "")
                if code in _BENIGN_SCHEMA_RACE:
                    logger.debug("[Neo4jRules] schema already present (concurrent boot): %s", code)
                    continue
                # By CODE, not by exception class: driver 6.0 reshuffled which
                # errors are DriverError versus Neo4jError, so class-based matching
                # written against 5.x is no longer reliable.
                logger.warning("[Neo4jRules] schema bootstrap failed (degraded): %s", e)
                ok = False
        logger.debug("[Neo4jRules] schema ensured (ok=%s)", ok)
        return ok

    # ---- the read -------------------------------------------------------

    def standing_rules(self, persona_id: str, limit: int = 8,
                       now: Optional[str] = None) -> list[dict[str, Any]]:
        """The rules in force, highest priority first. THE deterministic read.

        Same answer every turn for the same graph state. Never a similarity search
        — a rule fetched because it looked relevant to the current message is a
        rule that is SOMETIMES not fetched, and an instruction obeyed
        intermittently is worse than one never given, because the operator can no
        longer tell whether he was heard.

        `now` is a parameter rather than `datetime()` inside the query on purpose:
        computing it server-side lets two calls within one logical turn straddle a
        boundary, and the read stops being reproducible for reasons nothing logs.

        Returns [] on a graph outage rather than raising. The caller renders a
        prompt either way; a graph that is down must cost rules, not the turn.
        """
        if self._driver is None:
            return []
        t = now or now_iso()
        try:
            return read(
                self._driver,
                """
                MATCH (r:CurrentRule {persona_id: $persona_id})
                WHERE r.expired_at IS NULL
                  AND r.valid_from <= $now
                  AND (r.valid_to IS NULL OR r.valid_to > $now)
                RETURN r.rule_id                            AS rule_id,
                       r.text                               AS text,
                       r.source_field                       AS source_field,
                       r.source_index                       AS source_index,
                       coalesce(r.rule_type, $default_type) AS rule_type,
                       coalesce(r.rule_type_source, 'default') AS rule_type_source,
                       r.origin                             AS origin,
                       r.priority                           AS priority
                ORDER BY r.priority DESC, r.rule_id DESC
                LIMIT $limit
                """,
                self._database,
                persona_id=persona_id, now=t, limit=limit,
                default_type=DEFAULT_RULE_TYPE,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[Neo4jRules] standing_rules failed for %s (returning none): %s",
                           persona_id, e)
            return []

    # ---- writes ---------------------------------------------------------

    def seed_rules(self, persona_id: str, rules: list[dict[str, Any]],
                   dry_run: bool = False) -> dict[str, Any]:
        """Import a persona's rules from her card. Idempotent on (field, index).

        THE MERGE KEY IS (persona_id, source_field, source_index), NOT the text.
        Keying on text would make an edit to `dont[2]` create a second rule and
        orphan the first, so the graph would hold both and the read would return
        whichever won on priority. Keying on position means a re-seed UPDATES the
        row that position refers to.

        Mutable properties are in ON CREATE SET / ON MATCH SET and NEVER in the
        MERGE pattern. A property inside the pattern is part of the match
        criteria, so putting `text` there would silently create a duplicate on
        every edit instead of updating — the single most common MERGE defect.

        WHY A POSITIONAL KEY IS SAFE HERE, WHICH IS NOT OBVIOUS AND WAS CHALLENGED.
        A positional key into a hand-edited list is normally unsound, and the worst
        case is not an edit but a DELETION: remove `dont[1]` and every later index
        shifts down, so slot 2 now holds what slot 3 held. A naive positional MERGE
        would then record an EDIT where a rule was in fact retired and a different
        one renumbered — and the genuinely deleted rule would survive as an orphaned
        :CurrentRule, still enforced, with no card backing and no supersessor.

        That cannot happen here, because the TIER OVERLAY PINS THE TEXT AT EACH
        POSITION and the seed script compares them before writing anything. Any
        shift, reorder or in-place edit desynchronises text from position and the
        seed REFUSES with a diff. Verified 2026-09-26 by deleting dont[1] from the
        card and running the seed: exit 1, "the card and the overlay disagree about
        the rule text." So the safety property is not the key — it is the key plus
        the pin, and neither is sufficient alone.

        It fails LOUD rather than fails correct: the operator must re-run the
        classification. That is the right trade for a hand-edited card, because a
        changed rule needs a human tier decision anyway — a content-addressed key
        would silently create a new node and supersede, which for a CARD edit is
        wrong. The card is the origin; git holds its history, not the graph.
        """
        if self._driver is None:
            return {"seeded": 0, "skipped": len(rules), "reason": "graph unavailable"}

        rows = []
        for r in rules:
            rule_type = r.get("rule_type") or DEFAULT_RULE_TYPE
            if rule_type not in RULE_TYPES:
                raise ValueError(
                    f"rule_type {rule_type!r} not in the controlled vocabulary "
                    f"{sorted(RULE_TYPES)} — refusing to write a value the read "
                    f"would coalesce into silence"
                )
            origin = r.get("origin", "card")
            if origin not in ORIGINS:
                raise ValueError(f"origin {origin!r} not in {sorted(ORIGINS)}")
            rows.append({
                "rule_id": r.get("rule_id") or new_id(),
                "text": r["text"],
                "source_field": r["source_field"],
                "source_index": int(r["source_index"]),
                "rule_type": rule_type,
                # Derived HERE, in one place, so "hard because someone decided" stays
                # distinguishable from "hard because nobody classified it". Both
                # enforce identically; only one needs a decision, and collapsing them
                # would mean never being able to list what is still unclassified.
                "rule_type_source": "declared" if r.get("rule_type") else "default",
                "origin": origin,
                "priority": int(r["priority"]),
                "valid_from": r.get("valid_from") or now_iso(),
            })

        if dry_run:
            return {"seeded": 0, "would_seed": len(rows), "dry_run": True}

        now = now_iso()
        written = write(
            self._driver,
            """
            MERGE (p:Persona {persona_id: $persona_id})
              ON CREATE SET p.created_at = $now
            WITH p
            UNWIND $rows AS row
            MERGE (r:Rule {persona_id: $persona_id,
                           source_field: row.source_field,
                           source_index: row.source_index})
              ON CREATE SET r.rule_id    = row.rule_id,
                            r.created_at = $now,
                            r.valid_from = row.valid_from,
                            r.valid_to   = null,
                            r.expired_at = null
              ON MATCH  SET r.updated_at = $now
            SET r:CurrentRule,
                r.text             = row.text,
                r.rule_type        = row.rule_type,
                r.rule_type_source = row.rule_type_source,
                r.origin           = row.origin,
                r.priority         = row.priority
            MERGE (p)-[:HAS_RULE]->(r)
            RETURN count(r) AS seeded
            """,
            self._database,
            persona_id=persona_id, rows=rows, now=now,
        )
        n = written[0]["seeded"] if written else 0
        logger.info("[Neo4jRules] seeded %s rule(s) for %s", n, persona_id)
        return {"seeded": n}

    def supersede_rule(self, old_rule_id: str, text: str, *,
                       rule_type: Optional[str] = None,
                       priority: Optional[int] = None,
                       origin: str = "conversation",
                       valid_from: Optional[str] = None) -> dict[str, Any]:
        """Replace a rule without destroying it. Sets BOTH clocks.

        A supersession asserts two different things and they are easy to conflate:
        `expired_at` says the system stopped BELIEVING this row, and `valid_to`
        says the rule stopped APPLYING in the world. A pure correction — "I
        recorded that wrong, it was never true" — sets only `expired_at` and must
        leave `valid_to` alone, which is why that is a separate method and not a
        flag on this one.

        The old row keeps every property and loses only the :CurrentRule label,
        which drops it out of the read index while leaving it fully traversable.
        """
        if self._driver is None:
            return {"superseded": False, "reason": "graph unavailable"}
        if rule_type is not None and rule_type not in RULE_TYPES:
            raise ValueError(f"rule_type {rule_type!r} not in {sorted(RULE_TYPES)}")
        now = now_iso()
        vf = valid_from or now
        rows = write(
            self._driver,
            """
            MATCH (old:Rule {rule_id: $old_rule_id})
            WHERE old.expired_at IS NULL
            MATCH (p:Persona {persona_id: old.persona_id})
            CREATE (new:Rule:CurrentRule {
                rule_id:          $new_rule_id,
                persona_id:       old.persona_id,
                text:             $text,
                source_field:     old.source_field,
                source_index:     old.source_index,
                rule_type:        coalesce($rule_type, old.rule_type, $default_type),
                rule_type_source: CASE WHEN $rule_type IS NULL
                                       THEN coalesce(old.rule_type_source, 'default')
                                       ELSE 'declared' END,
                origin:           $origin,
                priority:         coalesce($priority, old.priority),
                valid_from:       $valid_from,
                valid_to:         null,
                created_at:       $now,
                expired_at:       null
            })
            SET old.expired_at = $now,
                old.valid_to   = coalesce(old.valid_to, $valid_from)
            REMOVE old:CurrentRule
            CREATE (p)-[:HAS_RULE]->(new)
            CREATE (new)-[:SUPERSEDES]->(old)
            RETURN new.rule_id AS new_rule_id, old.rule_id AS old_rule_id
            """,
            self._database,
            old_rule_id=old_rule_id, new_rule_id=new_id(), text=text,
            rule_type=rule_type, priority=priority, origin=origin,
            valid_from=vf, now=now, default_type=DEFAULT_RULE_TYPE,
        )
        if not rows:
            return {"superseded": False, "reason": "no live rule with that id"}
        return {"superseded": True, **rows[0]}

    def history(self, rule_id: str, max_depth: int = 10) -> list[dict[str, Any]]:
        """Walk the supersession chain, newest first. One query, bounded depth.

        This is the capability the node shape was chosen for. On the relationship
        shape there is no edge to walk and this becomes a scan-and-reconstruct.
        """
        if self._driver is None:
            return []
        return read(
            self._driver,
            f"""
            MATCH path = (r:Rule {{rule_id: $rule_id}})-[:SUPERSEDES*0..{int(max_depth)}]->(a:Rule)
            RETURN length(path) AS generations_back,
                   a.rule_id    AS rule_id,
                   a.text       AS text,
                   a.valid_from AS valid_from,
                   a.valid_to   AS valid_to,
                   a.created_at AS created_at,
                   a.expired_at AS expired_at
            ORDER BY generations_back
            """,
            self._database, rule_id=rule_id,
        )

    # ---- integrity ------------------------------------------------------

    def check_integrity(self) -> dict[str, Any]:
        """The graph equivalent of the backup restore-check. Must return zeros.

        Community enforces uniqueness and nothing else, so everything below would
        otherwise be enforced only by hope. Asserted in the test suite.
        """
        if self._driver is None:
            return {"checked": False, "reason": "graph unavailable"}
        bad_vocab = read(
            self._driver,
            """
            MATCH (r:Rule)
            WHERE r.rule_type IS NULL OR NOT r.rule_type IN $rule_types
               OR r.rule_type_source IS NULL OR NOT r.rule_type_source IN $sources
               OR r.origin IS NULL OR NOT r.origin IN $origins
               OR r.priority IS NULL OR r.valid_from IS NULL
               OR r.created_at IS NULL OR r.rule_id IS NULL
            RETURN r.rule_id AS rule_id, r.source_field AS source_field,
                   r.source_index AS source_index
            LIMIT 50
            """,
            self._database,
            rule_types=sorted(RULE_TYPES), sources=sorted(RULE_TYPE_SOURCES),
            origins=sorted(ORIGINS),
        )
        # The label/timestamp reconciliation — the one that will actually fire,
        # because :CurrentRule is derived state and nothing in the database keeps
        # it honest.
        label_drift = read(
            self._driver,
            """
            MATCH (r:Rule)
            WITH r, (r.expired_at IS NULL) AS believed, (r:CurrentRule) AS labelled
            WHERE believed <> labelled
            RETURN r.rule_id AS rule_id, believed, labelled LIMIT 50
            """,
            self._database,
        )
        orphans = read(
            self._driver,
            """
            MATCH (r:Rule) WHERE NOT (:Persona)-[:HAS_RULE]->(r)
            RETURN r.rule_id AS rule_id LIMIT 50
            """,
            self._database,
        )
        return {
            "checked": True,
            "bad_vocabulary": bad_vocab,
            "label_drift": label_drift,
            "orphans": orphans,
            "clean": not (bad_vocab or label_drift or orphans),
        }
