"""One function per competency question. The missing `query:` field, in Python.

The ecosystem defines 31 competency questions and **every one of them has
`query: null`** — so the set states what the graph must answer and nothing
anywhere answers it. `check.py` has been emitting `verified but query is null`
for all 28 verified questions since the set was written. This module is that
field.

**It is deliberately NOT 31 functions.** Measured against what the live graph
actually holds, the set splits four ways and only the first two are buildable:

    answerable today ......  7   idn.cq01-04, idn.cq06, rul.cq03-04
    degraded / retargeted .  3   tmp.cq01-03, which target `:Fact` and are
                                 implemented here for `:Rule`, the only label
                                 that carries the two temporal axes today
    needs absent schema ... 16   the whole Fact / Episode / Preference / scoping
                                 half. ADR-021 is the accepted design and its
                                 read path is explicitly CLOSED pending one
                                 experiment. Writing stubs for these would
                                 manufacture the appearance of a query layer
                                 over a schema that does not exist.
    must NOT be written ...  4   wal.cq01, hab.cq01, wel.cq01 are `class:
                                 scoping` and `check.py` makes a query on one a
                                 HARD ERROR. idn.cq05 is deliberately
                                 uninstrumented — its own negative fixture says
                                 re-adding a counter to satisfy it is itself a
                                 failure.

**Contract with `graph_driver`, which this module must not break.** That file's
docstring is the architecture: nothing outside it imports `neo4j`, every read
carries a `LIMIT` because `records` is materialised, and a query must never
`RETURN n` — `_plain()` raises `TypeError` when a `Node` reaches the seam,
because `Record.data()` flattens it lossily. So every query here projects named
scalars.

**The `IS NULL` branch in a temporal read is load-bearing and its absence is
SILENT.** `e.valid_to > $t` alone drops every row that was never superseded.
This repo has shipped that exact bug once — 128 stale nodes, `expired: 0`, no
error, no failing test — so every temporal query below guards both bounds with
`IS NULL OR`, and the test suite checks each one against a NEVER-superseded row
rather than only against a superseded one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .graph_driver import read
from .graph_ids import now_iso

#: Every query, keyed by competency-question id. Populated by `@answers`. The MCP
#: server builds its tool list from this, so a question answered here is exposed
#: there without a second registration to keep in sync.
CATALOG: dict[str, Query] = {}


@dataclass(frozen=True)
class Query:
    """A competency question and the function that answers it, kept together.

    Ontology engineering's own practice is to pair each CQ with an executable
    artifact and run them as tests rather than keeping the question in a document
    and the query somewhere else — CLaRO, the SULO test harness and CQChecker all
    take that shape. The pairing is here rather than in the YAML because a query
    that lives in a data file cannot be type-checked, imported, or unit-tested.
    """

    cq: str
    question: str
    fn: Callable[..., Any]
    #: True when this answers a question the set asks about a label that does not
    #: exist, by retargeting it at one that does. Never silently: the caller and
    #: the MCP tool description both say so.
    degraded: bool = False
    degraded_because: str = ""


def answers(cq: str, question: str, *, degraded: bool = False,
            because: str = "") -> Callable:
    """Register a function as the answer to one competency question."""

    def deco(fn: Callable) -> Callable:
        if cq in CATALOG:
            raise ValueError(f"{cq} already answered by {CATALOG[cq].fn.__name__}")
        CATALOG[cq] = Query(cq=cq, question=question, fn=fn,
                            degraded=degraded, degraded_because=because)
        fn.cq = cq  # type: ignore[attr-defined]
        return fn

    return deco


# Every read is bounded. 256 is the identity read's own default and comfortably
# exceeds gwen's 128 live nodes; a persona that outgrows it should raise the
# constant deliberately rather than silently return a truncated answer.
_DEFAULT_LIMIT = 256


# ---------- identity ----------------------------------------------------------


@answers("idn.cq01", "What do you look like?")
def what_do_you_look_like(driver, database: str, persona_id: str,
                          limit: int = _DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Her lore entries, in card order.

    ⚠️ This answers the question only as far as the DATA allows, and the gap is
    the data's not the query's. `LORE_KINDS` is `{lore, signature_move}` — there
    is no `appearance` kind, because her appearance is embedded in 21 free-text
    lore strings. So this returns everything that could describe how she looks
    and leaves the extraction to the reader. `identity.yaml` records the same
    limitation at the question itself.
    """
    return read(
        driver,
        """
        MATCH (p:Persona {persona_id: $persona_id})-[:HAS_LORE]->(n:LoreEntry)
        WHERE n.expired_at IS NULL
        RETURN n.node_id      AS node_id,
               n.text         AS text,
               n.kind         AS kind,
               n.origin       AS origin,
               n.valid_from   AS valid_from,
               n.source_field AS source_field,
               n.source_index AS source_index
        ORDER BY n.source_field ASC, n.source_index ASC
        LIMIT $limit
        """,
        database, persona_id=persona_id, limit=limit,
    )


@answers("idn.cq02", "What are you actually good at?")
def what_are_you_good_at(driver, database: str, persona_id: str,
                         limit: int = _DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Her expertise, strongest first.

    The one question the existing `identity()` read already fully satisfies.
    `level` is the controlled vocabulary `{strong, familiar, avoid}` — note that
    `avoid` is an expertise entry meaning "do not go here", so a caller rendering
    this as a list of strengths must filter it rather than assume.
    """
    return read(
        driver,
        """
        MATCH (p:Persona {persona_id: $persona_id})-[:HAS_EXPERTISE]->(n:Expertise)
        WHERE n.expired_at IS NULL
        RETURN n.node_id AS node_id,
               n.text    AS text,
               n.level   AS level,
               n.origin  AS origin
        ORDER BY CASE n.level WHEN 'strong' THEN 0 WHEN 'familiar' THEN 1 ELSE 2 END,
                 n.source_index ASC
        LIMIT $limit
        """,
        database, persona_id=persona_id, limit=limit,
    )


@answers("idn.cq03", "What won't you do?")
def what_wont_you_do(driver, database: str, persona_id: str,
                     limit: int = _DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Her boundaries, highest priority first.

    ⚠️ `priority` is why this is a NEW read rather than a filter over
    `IdentityRepository.identity()`. That projection deliberately omits
    `priority`, `created_at` and the temporal columns, and widening it is
    forbidden — it feeds the prompt overlay, and ADR-019's byte-identity boundary
    is not worth risking to serve a query layer. `identity_ab.py` records the
    same refusal for `created_at`.
    """
    return read(
        driver,
        """
        MATCH (p:Persona {persona_id: $persona_id})-[:HAS_BOUNDARY]->(n:Boundary)
        WHERE n.expired_at IS NULL
        RETURN n.node_id  AS node_id,
               n.text     AS text,
               n.kind     AS kind,
               n.origin   AS origin,
               n.priority AS priority
        ORDER BY coalesce(n.priority, 0) DESC, n.source_index ASC
        LIMIT $limit
        """,
        database, persona_id=persona_id, limit=limit,
    )


@answers("idn.cq04", "Is everything on her card actually in the graph?")
def is_the_card_fully_in_the_graph(driver, database: str, persona_id: str,
                                   card: dict,
                                   limit: int = _DEFAULT_LIMIT) -> dict[str, Any]:
    """A changeset: what the card implies, what the graph holds, and the difference.

    Compared on `(source_field, source_key)` — the natural key the rebuild MERGEs
    on — and NOT on text, because a reworded entry is the same entry and a
    text-keyed diff would report drift on every copy-edit.

    The answer_type is `changeset`, so returning a bare count would not answer it:
    `missing` and `extra` are the useful halves and an empty pair is the pass.
    """
    from .identity_from_card import identity_nodes

    want = {(n["source_field"], n["source_key"]) for n in identity_nodes(card)}
    rows = read(
        driver,
        """
        MATCH (p:Persona {persona_id: $persona_id})-[]->(n:CurrentIdentity)
        RETURN n.source_field AS source_field,
               n.source_key   AS source_key,
               n.created_at   AS created_at,
               n.origin       AS origin,
               labels(n)[0]   AS label
        ORDER BY n.source_field ASC, n.source_index ASC
        LIMIT $limit
        """,
        database, persona_id=persona_id, limit=limit,
    )
    have = {(r["source_field"], r["source_key"]) for r in rows}
    return {
        "card_implies": len(want),
        "graph_holds": len(have),
        "missing": sorted(want - have),
        "extra": sorted(have - want),
        "complete": not (want - have),
        "truncated": len(rows) >= limit,
    }


@answers("idn.cq06", "What did she say to me in that conversation three weeks ago?")
def can_she_quote_a_past_message(driver, database: str, persona_id: str) -> dict[str, Any]:
    """Answered in the NEGATIVE, and that is the correct answer, not a gap.

    No node in the graph carries `source_session_id` or `source_message_id`, and
    that is a decision rather than an omission: ADR-021 pins message text to
    SQLite and keeps the graph for claims about the world. So this returns a
    structural refusal with the reason, which is what the question's own
    `answer_type: binary` asks for.

    It is written as a query rather than a constant so that the day the schema
    changes, this starts returning `True` on its own instead of lying.
    """
    rows = read(
        driver,
        """
        MATCH (p:Persona {persona_id: $persona_id})-[]->(n:CurrentIdentity)
        WHERE n.source_message_id IS NOT NULL OR n.source_session_id IS NOT NULL
        RETURN count(n) AS n
        LIMIT 1
        """,
        database, persona_id=persona_id,
    )
    anchored = bool(rows and rows[0]["n"])
    return {
        "can_quote": anchored,
        "reason": (
            "Message text lives in SQLite, not the graph (ADR-021). No identity "
            "node carries source_message_id or source_session_id, so the graph "
            "cannot anchor a quote to a conversation. This is the designed answer."
        ) if not anchored else "Some nodes now carry message provenance.",
    }


# ---------- rules -------------------------------------------------------------


@answers("rul.cq03", "Which parts of her can I change by talking to her, and which can't I?")
def what_can_i_change_by_talking(driver, database: str, persona_id: str,
                                 limit: int = _DEFAULT_LIMIT) -> dict[str, list]:
    """Her live rules sorted into the three buckets the operator asked for.

    ⚠️ **Unclassified FAILS CLOSED into hard_wall**, and the asymmetry is the
    point: a soft wall wrongly treated as hard is an annoyance the operator
    notices and fixes; a hard wall wrongly treated as soft is an identity
    boundary that became negotiable through an omission, and nothing announces
    it. `rule_type_source` is returned alongside so "hard because someone decided"
    stays distinguishable from "hard because nobody classified it".

    ⚠️ Half-green against the question as written. It covers `:Rule` fully and
    `:Trait` not at all — `allowed_min`/`allowed_max` exist nowhere in the graph,
    and Trait nodes carry no `rule_type`. The half that works is the half the
    question was written for.
    """
    rows = read(
        driver,
        """
        MATCH (p:Persona {persona_id: $persona_id})-[:HAS_RULE]->(r:CurrentRule)
        WHERE r.expired_at IS NULL
        RETURN r.rule_id          AS rule_id,
               r.text             AS text,
               r.rule_type        AS rule_type,
               r.rule_type_source AS rule_type_source,
               r.origin           AS origin,
               r.priority         AS priority
        ORDER BY r.priority DESC, r.rule_id DESC
        LIMIT $limit
        """,
        database, persona_id=persona_id, limit=limit,
    )
    buckets: dict[str, list] = {"dial": [], "soft_wall": [], "hard_wall": []}
    for r in rows:
        buckets.get(r["rule_type"] or "hard_wall", buckets["hard_wall"]).append(r)
    return buckets


@answers("rul.cq04", "What changes to how you behave are waiting on me to say yes?")
def what_is_waiting_on_me(driver, database: str, persona_id: str,
                          limit: int = 50) -> list[dict[str, Any]]:
    """Pending rule proposals, newest first.

    ⚠️ Returns empty on the live graph, and that is a CORRECT empty rather than a
    failure: `rule_proposals.propose()` has no non-test caller, so nothing has
    ever been proposed. `graph_surface.py` records the same thing as "built is
    not reached". An empty list here means the queue is empty, not that the query
    is broken — a distinction the caller must not collapse.

    TTL expiry is applied by `RuleProposalStore.pending()` in Python rather than
    in Cypher; this is the raw read, so a caller wanting the operator-facing view
    should use that instead.
    """
    return read(
        driver,
        """
        MATCH (p:Persona {persona_id: $persona_id})-[:HAS_PROPOSAL]->(x:ProposedRule)
        WHERE x.status = 'pending'
        RETURN x.proposal_id    AS proposal_id,
               x.target_rule_id AS target_rule_id,
               x.new_text       AS new_text,
               x.quote          AS quote,
               x.durability     AS durability,
               x.created_at     AS created_at
        ORDER BY x.created_at DESC
        LIMIT $limit
        """,
        database, persona_id=persona_id, limit=limit,
    )


# ---------- temporal: retargeted, and saying so -------------------------------

_RETARGET = (
    "The question targets `:Fact`, which does not exist. Answered here for "
    "`:Rule`, the only label in the live graph carrying both temporal axes. The "
    "shape of the answer is the one the question asks for; the subject is not."
)


@answers("tmp.cq01", "When did that stop being true?", degraded=True, because=_RETARGET)
def when_did_it_stop_being_true(driver, database: str, rule_id: str,
                                max_depth: int = 10) -> list[dict[str, Any]]:
    """Walk the supersession chain newest-first and report each generation's end.

    `SUPERSEDES` points new->old (PROV-O's `wasRevisionOf` direction), so the walk
    starts at the current node and goes backwards. Verified live against a
    three-generation chain by `scripts/research/exercise_supersedes.py`.
    """
    return read(
        driver,
        f"""
        MATCH path = (r:Rule {{rule_id: $rule_id}})-[:SUPERSEDES*0..{int(max_depth)}]->(a:Rule)
        RETURN length(path)  AS generations_back,
               a.rule_id     AS rule_id,
               a.text        AS text,
               a.valid_from  AS valid_from,
               a.valid_to    AS valid_to,
               a.created_at  AS created_at,
               a.expired_at  AS expired_at
        ORDER BY generations_back
        LIMIT 64
        """,
        database, rule_id=rule_id,
    )


@answers("tmp.cq02", "What was true about me last March?", degraded=True, because=_RETARGET)
def what_was_true_at(driver, database: str, persona_id: str,
                     valid_time: str | None = None,
                     limit: int = 64) -> list[dict[str, Any]]:
    """WORLD time: what held in the world at `valid_time`, as believed now.

    ⚠️ Both bounds are guarded with `IS NULL OR`. Written as `r.valid_to > $vt`
    alone this silently drops every rule that was never superseded — which is
    most of them — and returns a confidently short list with no error. That exact
    bug has shipped in this repo once.
    """
    vt = valid_time or now_iso()
    return read(
        driver,
        """
        MATCH (p:Persona {persona_id: $persona_id})-[:HAS_RULE]->(r:Rule)
        WHERE r.valid_from <= $vt
          AND (r.valid_to IS NULL OR r.valid_to > $vt)
        RETURN r.rule_id   AS rule_id,
               r.text      AS text,
               r.rule_type AS rule_type,
               r.priority  AS priority,
               r.origin    AS origin,
               r.valid_from AS valid_from,
               r.valid_to   AS valid_to
        ORDER BY r.priority DESC, r.rule_id ASC
        LIMIT $limit
        """,
        database, persona_id=persona_id, vt=vt, limit=limit,
    )


@answers("tmp.cq03", "What did you think was true about me last March, back then?",
         degraded=True, because=_RETARGET)
def what_did_you_believe_at(driver, database: str, persona_id: str,
                            system_time: str | None = None,
                            limit: int = 64) -> list[dict[str, Any]]:
    """BELIEF time: what the system held as true at `system_time`, right or wrong.

    The axis that makes this bi-temporal rather than merely historical. `tmp.cq02`
    asks what WAS true; this asks what was BELIEVED, and the two differ exactly
    where the system was wrong — which is the interesting case and the only one
    that can show a correction.

    ⚠️ `IS NULL OR` on `expired_at` for the same reason as above.
    """
    st = system_time or now_iso()
    return read(
        driver,
        """
        MATCH (p:Persona {persona_id: $persona_id})-[:HAS_RULE]->(r:Rule)
        WHERE r.created_at <= $st
          AND (r.expired_at IS NULL OR r.expired_at > $st)
        RETURN r.rule_id    AS rule_id,
               r.text       AS text,
               r.rule_type  AS rule_type,
               r.priority   AS priority,
               r.origin     AS origin,
               r.created_at AS created_at,
               r.expired_at AS expired_at
        ORDER BY r.priority DESC, r.rule_id ASC
        LIMIT $limit
        """,
        database, persona_id=persona_id, st=st, limit=limit,
    )
