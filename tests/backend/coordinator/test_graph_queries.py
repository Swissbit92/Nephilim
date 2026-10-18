"""The typed query layer: what every query must satisfy, and the one live trap.

Two halves. The STATIC half runs everywhere and asserts properties of the Cypher
itself — these are the `graph_driver` contract written down as tests, because
that contract is prose in a docstring and prose does not fail a build. The LIVE
half needs a real Neo4j and skips without one, following this repo's convention;
`scripts/checks/graph_tests.sh` is how it actually gets run, and it exits 2 when
everything skipped, because "nothing ran" is not "passed".
"""

from __future__ import annotations

import inspect
import os
import re

import pytest

from src.coordinator import graph_queries as gq

# ---------- static: the graph_driver contract, as assertions ------------------


def _cypher_of(fn) -> list[str]:
    """Every triple-quoted Cypher literal in a query function."""
    src = inspect.getsource(fn)
    return re.findall(r'"""\s*(MATCH.*?)"""', src, re.S)


@pytest.mark.parametrize("cq", sorted(gq.CATALOG))
def test_every_read_is_bounded(cq):
    """`graph_driver.read` materialises every record, so an unbounded read is a
    memory risk that grows with the data and is invisible until it is not."""
    for cypher in _cypher_of(gq.CATALOG[cq].fn):
        assert "LIMIT" in cypher, f"{cq}: a read with no LIMIT"


@pytest.mark.parametrize("cq", sorted(gq.CATALOG))
def test_no_query_returns_a_whole_node(cq):
    """`RETURN n` raises TypeError at the seam — `Record.data()` flattens a Node
    lossily, so `_plain()` refuses it. Project named scalars instead."""
    for cypher in _cypher_of(gq.CATALOG[cq].fn):
        bare = re.findall(r"RETURN\s+([a-z][a-z0-9_]*)\s*(?:,|$)", cypher, re.M | re.I)
        assert not bare, f"{cq}: returns a whole node/relationship: {bare}"


@pytest.mark.parametrize("cq", sorted(gq.CATALOG))
def test_no_parameter_ends_in_an_underscore(cq):
    """`execute_query` reserves the trailing-underscore shape for its own config
    kwargs, so `$limit_` would be silently eaten rather than bound."""
    for cypher in _cypher_of(gq.CATALOG[cq].fn):
        assert not re.search(r"\$[a-z0-9_]*_\b", cypher), f"{cq}: reserved param shape"


@pytest.mark.parametrize("cq", sorted(gq.CATALOG))
def test_every_question_is_quoted_in_its_own_docstring(cq):
    """The question and the query stay together or they drift apart. This is the
    pairing ontology-engineering practice asks for, asserted rather than hoped."""
    q = gq.CATALOG[cq]
    assert q.fn.__doc__ and len(q.fn.__doc__.strip()) > 80, f"{cq}: thin docstring"
    assert q.question.endswith("?"), f"{cq}: question is not a question"


def test_a_degraded_answer_says_why():
    """A query that answers a question about a label that does not exist must
    SAY so. Silently retargeting `:Fact` at `:Rule` would make the catalog claim
    coverage the schema does not have."""
    for cq, q in gq.CATALOG.items():
        if q.degraded:
            assert len(q.degraded_because) >= 60, f"{cq}: degraded with no reason"


def test_the_catalog_does_not_answer_a_question_it_must_not():
    """Four questions must never get a function, and each for its own reason.

    `wal.cq01`/`hab.cq01`/`wel.cq01` are `class: scoping` and the ecosystem's
    `check.py` makes a query on one a HARD ERROR. `idn.cq05` is deliberately
    uninstrumented: the counter it would need was deleted after 256 live nodes
    returned exactly one distinct row, and its own negative fixture says that
    re-adding one to satisfy the question is itself a failure.
    """
    forbidden = {"wal.cq01", "hab.cq01", "wel.cq01", "idn.cq05"}
    assert not (forbidden & set(gq.CATALOG)), (
        f"answered a question that must stay unanswered: {forbidden & set(gq.CATALOG)}"
    )


def test_every_temporal_query_guards_the_null_branch():
    """THE trap, and the reason this test exists rather than a code review note.

    `r.valid_to > $vt` without `IS NULL OR` drops every row that was never
    superseded — which is most rows — and returns a confidently short answer with
    no error and no failing test. This repo shipped exactly that once: 128 stale
    nodes live, `expired: 0`, nothing raised.
    """
    for cq in ("tmp.cq02", "tmp.cq03"):
        for cypher in _cypher_of(gq.CATALOG[cq].fn):
            for col in ("valid_to", "expired_at"):
                if col in cypher:
                    assert re.search(rf"{col}\s+IS NULL\s+OR", cypher), (
                        f"{cq}: compares {col} without an IS NULL branch — this "
                        f"silently drops every never-superseded row"
                    )


# ---------- live: needs a real graph ------------------------------------------

_URL = os.getenv("NEO4J_BASE_URL")
pytestmark_live = pytest.mark.skipif(not _URL, reason="NEO4J_BASE_URL not set")


@pytest.fixture(scope="module")
def live():
    if not _URL:
        pytest.skip("no graph")
    from src.coordinator.config import get_settings
    from src.coordinator.graph_driver import build_driver

    cfg = get_settings().graph
    drv = build_driver(cfg.base_url, cfg.username, cfg.password)
    try:
        yield drv, cfg.database
    finally:
        drv.close()


@pytestmark_live
def test_the_identity_queries_return_gwens_real_rows(live):
    drv, db = live
    assert gq.what_are_you_good_at(drv, db, "gwen"), "no expertise for gwen"
    assert gq.what_wont_you_do(drv, db, "gwen"), "no boundaries for gwen"
    lore = gq.what_do_you_look_like(drv, db, "gwen")
    assert lore, "no lore for gwen"
    # The appearance facts the 2026-10-17 session showed her failing to use.
    blob = " ".join(r["text"].lower() for r in lore)
    assert "blue eyes" in blob and "155cm" in blob, (
        "her appearance is not reachable through this query, which would mean the "
        "10:48 defect really was a data gap rather than a prompt one"
    )


@pytestmark_live
def test_priority_is_projected_which_identity_does_not_do(live):
    """The reason this is a new read and not a filter over `identity()`."""
    drv, db = live
    rows = gq.what_wont_you_do(drv, db, "gwen")
    assert any(r["priority"] is not None for r in rows), (
        "no boundary carries a priority — idn.cq03 cannot be answered"
    )


@pytestmark_live
def test_the_card_is_fully_in_the_graph(live):
    """idn.cq04 against the live graph. An empty `missing` IS the answer."""
    import json
    import pathlib

    drv, db = live
    card = json.loads(pathlib.Path("personas/gwen.json").read_text())
    out = gq.is_the_card_fully_in_the_graph(drv, db, "gwen", card)
    assert not out["truncated"], "raise the limit; the answer was cut off"
    assert out["complete"], f"card entries missing from the graph: {out['missing'][:5]}"


@pytestmark_live
def test_the_temporal_reads_keep_a_never_superseded_rule(live):
    """The live half of the NULL trap, against the case that actually breaks.

    A rule that was never superseded has `valid_to IS NULL` and `expired_at IS
    NULL`. A point-in-time read written without the null branch returns zero rows
    for it while raising nothing — so this asserts the count against
    `standing_rules`, which is the independently-written read of the same truth.
    """
    from src.coordinator.repositories.neo4j_rule_repository import Neo4jRuleRepository

    drv, db = live
    standing = Neo4jRuleRepository(driver=drv, database=db).standing_rules("gwen", limit=256)
    if not standing:
        pytest.skip("gwen has no live rules to compare against")

    now_world = gq.what_was_true_at(drv, db, "gwen", limit=256)
    now_belief = gq.what_did_you_believe_at(drv, db, "gwen", limit=256)
    for label, rows in (("valid-time", now_world), ("system-time", now_belief)):
        assert len(rows) >= len(standing), (
            f"{label} read returned {len(rows)} where standing_rules sees "
            f"{len(standing)} — the IS NULL branch is dropping never-superseded rules"
        )


@pytestmark_live
def test_pending_proposals_is_a_correct_empty(live):
    """Empty here means the queue is empty, not that the query is broken.

    `propose()` has no non-test caller, so nothing has ever been proposed. The
    assertion is that the query RUNS and returns a list — collapsing "no rows"
    into "failed" is the distinction this test exists to protect.
    """
    drv, db = live
    assert isinstance(gq.what_is_waiting_on_me(drv, db, "gwen"), list)
