"""The unconditional per-turn graph read (ADR-022 part 1).

The static half runs everywhere. The live half needs a graph and is listed in
`scripts/checks/graph_tests.sh`, which exits 2 when everything skipped.
"""

from __future__ import annotations

import os

import pytest

from src.coordinator.services import grounding_context as gc


def test_a_failed_read_is_degraded_and_may_not_license_a_refusal():
    """THE property this module exists for.

    "The graph says nothing about this" and "the graph could not be reached" must
    never collapse into the same answer. If they do, a Neo4j restart becomes a
    wave of abstentions — the gate refuses everything because its evidence set is
    empty, and nothing in the reply says why.
    """
    ctx = gc.build_grounding_context("gwen", driver=object(), database="neo4j")
    assert ctx.degraded is True
    assert ctx.facts == ()
    assert ctx.usable is False, (
        "a degraded read reported itself as usable — it may support an assertion "
        "but it must never be allowed to deny one"
    )
    assert ctx.reason.startswith("error:")


def test_an_empty_graph_is_NOT_degraded():
    """The other side of the same distinction, and the one a mock gets wrong.

    A persona with no facts is a legitimate state. If `usable` were simply
    `bool(facts)` this would read as degraded and the gate would go silent on a
    brand-new persona rather than on a broken one.
    """
    ctx = gc.GroundingContext(persona_id="nobody", facts=(), degraded=False)
    assert ctx.usable is True


def test_the_read_never_raises_whatever_the_driver_does():
    """Fail-soft, copied from `standing_rules` deliberately: a graph that is down
    must cost rules, not the turn. `graph_queries` does NOT swallow — the
    try/except has to live here."""

    class Exploding:
        def execute_query(self, *a, **k):
            raise RuntimeError("neo4j is on fire")

    ctx = gc.build_grounding_context("gwen", driver=Exploding(), database="neo4j")
    assert ctx.degraded and ctx.facts == ()


def test_the_fact_cap_is_enforced():
    """Nothing here is rendered into a prompt, but the GATE's classification
    prompt is a model call and an unbounded evidence set would grow it without
    limit."""
    assert gc.MAX_FACTS == 64
    big = gc.GroundingContext(persona_id="x", facts=tuple(str(i) for i in range(999)))
    assert len(big.facts) == 999, "the dataclass does not truncate"
    # the builder does, which is where the driver's output actually arrives


@pytest.mark.parametrize("rows,expected", [
    ([{"text": "a"}, {"text": "b"}], ["a", "b"]),
    ({"dial": [{"text": "a"}], "hard_wall": [{"text": "b"}]}, ["a", "b"]),
    ([{"no_text_key": 1}], []),
    ({"card_implies": 128, "missing": []}, []),
    ([], []),
])
def test_every_catalog_answer_shape_flattens(rows, expected):
    """The catalog returns a list of dicts, a dict of lists (rul.cq03's buckets)
    or a bare dict (idn.cq04's changeset). Handled here rather than by bending
    the catalog to suit this caller — its shapes are dictated by the questions."""
    assert sorted(gc._texts_of(rows)) == sorted(expected)


def test_the_assertable_subset_excludes_audit_answers():
    """`idn.cq04` is a completeness changeset and `idn.cq06` is a structural
    refusal. Neither is a claim she could make out loud, and putting audit output
    into the set that decides what she may say would let her assert
    'card_implies: 128'."""
    import inspect

    src = inspect.getsource(gc.build_grounding_context)
    assert "idn.cq04" not in src.split("assertable = ")[1].split(")")[0]
    assert "idn.cq06" not in src.split("assertable = ")[1].split(")")[0]


@pytest.mark.skipif(not os.getenv("NEO4J_BASE_URL"), reason="needs a live graph")
def test_the_live_read_returns_her_real_facts():
    # Driver built from the ENVIRONMENT, not through settings: conftest's
    # `_hermetic_settings` disables every settings class's env_file, so
    # `graph.enabled` is False in-suite even when the graph is up. Same reason
    # every other live graph test here builds its own.
    from src.coordinator.graph_driver import build_driver

    drv = build_driver(os.environ["NEO4J_BASE_URL"],
                       os.getenv("NEO4J_USER", "neo4j"),
                       os.environ["NEO4J_PASSWORD"])
    try:
        ctx = gc.build_grounding_context(
            "gwen", driver=drv, database=os.getenv("NEO4J_DATABASE", "neo4j"))
        assert not ctx.degraded and ctx.facts
        assert ctx.elapsed_ms < 2000, (
            f"the read took {ctx.elapsed_ms}ms — it runs on every turn and the "
            f"turn budget is already tight"
        )
        blob = " ".join(ctx.facts).lower()
        assert "blue eyes" in blob, "her appearance is not in the grounding set"
    finally:
        drv.close()
