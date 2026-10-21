"""`(:Fact)-[:ABOUT]->(:Operator)` — ADR-021's operator-fact store.

The schema is DICTATED by the competency questions, not chosen: `cmp.cq01`
demands labels [Operator, Fact], rel [ABOUT], props [valid_from, valid_to];
`cmp.cq02` demands [origin, source_session_id]. C17 runs backwards and both
labels were already demanded and unbuilt.

Two negative fixtures come from the questions themselves and are the reason this
file exists:

  cmp.cq01 — "A fact whose valid_to has passed must NOT appear."
  cmp.cq02 — "A card-declared fact must NOT be returned as learned."
             ...stated by the question to be THE load-bearing one, because "if
             the two origins are indistinguishable, a graph rebuild cannot tell
             what she was given from what she was told."

⚠️ Graph tests SKIP in a normal run (the hermetic fixture strips the prod .env).
Run them with ./scripts/checks/graph_tests.sh, which exits 2 when everything
skipped — "nothing ran" is not "passed".
"""
from __future__ import annotations

import os

import pytest

from src.coordinator.repositories.operator_fact_repository import (
    ASSERTED_BY,
    FACT_ORIGINS,
    OperatorFactRepository,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("NEO4J_BASE_URL"),
    reason="needs NEO4J_BASE_URL — run via scripts/checks/graph_tests.sh",
)

_OP = "_scratch_operator_facts"   # `_` prefix: the conftest write guard requires it


@pytest.fixture()
def repo():
    from src.coordinator.graph_driver import build_driver, write

    drv = build_driver(os.environ["NEO4J_BASE_URL"],
                       os.getenv("NEO4J_USER", "neo4j"),
                       os.environ["NEO4J_PASSWORD"])
    r = OperatorFactRepository(drv, os.getenv("NEO4J_DATABASE", "neo4j"))
    r.ensure_schema()
    yield r
    write(drv, "MATCH (o:Operator {operator_id: $operator_id})<-[:ABOUT]-(f:Fact) "
               "DETACH DELETE f, o", r._database, operator_id=_OP)
    drv.close()


class TestVocabulariesRefuseRatherThanCoerce:
    def test_an_unknown_asserted_by_raises(self, repo):
        """A silently accepted value is indistinguishable from a correct one
        later, and this field exists precisely so that distinction survives."""
        with pytest.raises(ValueError, match="asserted_by"):
            repo.add_fact(_OP, "x", asserted_by="the_model")

    def test_an_unknown_origin_raises(self, repo):
        with pytest.raises(ValueError, match="origin"):
            repo.add_fact(_OP, "x", asserted_by="user", origin="vibes")

    def test_the_vocabularies_are_what_the_questions_need(self):
        assert "user" in ASSERTED_BY and "assistant" in ASSERTED_BY
        assert {"card", "learned"} <= FACT_ORIGINS, (
            "cmp.cq02 partitions learned vs always-known on exactly this"
        )

    def test_blank_text_writes_nothing(self, repo):
        assert repo.add_fact(_OP, "   ", asserted_by="user") is None


class TestCmpCq01CurrentFacts:
    def test_a_written_fact_comes_back(self, repo):
        repo.add_fact(_OP, "Ben is vegetarian", asserted_by="user")
        texts = [r["text"] for r in repo.current_facts(_OP)]
        assert "Ben is vegetarian" in texts

    def test_asserted_by_and_origin_survive_the_round_trip(self, repo):
        repo.add_fact(_OP, "Ben sold his car", asserted_by="user",
                      origin="learned", source_session_id="sess-1")
        row = next(r for r in repo.current_facts(_OP)
                   if r["text"] == "Ben sold his car")
        assert row["asserted_by"] == "user"
        assert row["origin"] == "learned"
        assert row["source_session_id"] == "sess-1"

    def test_the_negative_fixture_an_invalidated_fact_disappears(self, repo):
        """cmp.cq01: "A fact whose valid_to has passed must NOT appear."
        Without this the answer is the union of everything ever believed."""
        fid = repo.add_fact(_OP, "Ben drives a red car", asserted_by="user")
        assert repo.invalidate_fact(fid) is True
        texts = [r["text"] for r in repo.current_facts(_OP)]
        assert "Ben drives a red car" not in texts

    def test_invalidate_never_deletes(self, repo):
        """ADR-021: invalidate, never delete. The row must still be there."""
        fid = repo.add_fact(_OP, "Ben lived in Zurich", asserted_by="user")
        repo.invalidate_fact(fid)
        past = repo.facts_at(_OP, "2000-01-01T00:00:00+00:00")
        assert repo.current_facts(_OP) is not None   # read still works
        # and the fact is reachable at a time when it WAS valid
        later = repo.facts_at(_OP, "2099-01-01T00:00:00+00:00")
        assert "Ben lived in Zurich" not in [r["text"] for r in later]
        assert past is not None

    def test_invalidating_twice_is_not_a_second_success(self, repo):
        fid = repo.add_fact(_OP, "Ben owned a bike", asserted_by="user")
        assert repo.invalidate_fact(fid) is True
        assert repo.invalidate_fact(fid) is False, (
            "the WHERE valid_to IS NULL guard must make this idempotent"
        )


class TestTheNullPredicateTrap:
    """🔴 `f.valid_to IS NULL OR` is load-bearing and its absence is SILENT.

    A still-valid fact has `valid_to = NULL`; `f.valid_to > $t` evaluates to
    NULL rather than false, and WHERE keeps a row only when the predicate is
    true — so every still-valid fact vanishes. This repo shipped that bug once
    (`NOT [field, null] IN $live` left 128 stale nodes live, reported
    `expired: 0`, no error) and Graphiti's tracker carries it twice (#769,
    #2012).

    THE TEST IS AGAINST A NEVER-SUPERSEDED FACT, which is the case a green suite
    cannot see: with every fact invalidated, a broken guard and a correct one
    return the same empty set.
    """

    def test_a_never_superseded_fact_is_visible_at_a_point_in_time(self, repo):
        repo.add_fact(_OP, "Ben has always read Orwell", asserted_by="user")
        rows = repo.facts_at(_OP, "2099-01-01T00:00:00+00:00")
        assert "Ben has always read Orwell" in [r["text"] for r in rows], (
            "a fact with valid_to = NULL must appear; if this fails the "
            "IS NULL arm has been dropped from the point-in-time query"
        )

    def test_a_never_superseded_fact_is_visible_in_current_facts(self, repo):
        repo.add_fact(_OP, "Ben still lifts", asserted_by="user")
        assert "Ben still lifts" in [r["text"] for r in repo.current_facts(_OP)]

    def test_both_queries_carry_the_is_null_arm(self):
        """A source-level pin, because the behavioural test above can only fail
        once the data happens to contain a null — and it always does."""
        import inspect

        from src.coordinator.repositories import operator_fact_repository as m

        for fn in (m.OperatorFactRepository.current_facts,
                   m.OperatorFactRepository.facts_at):
            assert "valid_to IS NULL" in inspect.getsource(fn), fn.__name__


class TestCmpCq02Origin:
    def test_the_load_bearing_negative_fixture(self, repo):
        """cmp.cq02: "A card-declared fact must NOT be returned as learned."

        The question states the consequence: if the two origins are
        indistinguishable, re-seeding the card silently overwrites months of
        learned facts with shipped defaults.
        """
        repo.add_fact(_OP, "Ben is the operator", asserted_by="card", origin="card")
        repo.add_fact(_OP, "Ben is vegetarian", asserted_by="user", origin="learned")
        rows = repo.facts_by_origin(_OP)
        by_text = {r["text"]: r["origin"] for r in rows}
        assert by_text["Ben is the operator"] == "card"
        assert by_text["Ben is vegetarian"] == "learned"
        assert by_text["Ben is the operator"] != "learned", (
            "a card-declared fact must never be reported as learned"
        )

    def test_origin_is_recorded_not_inferred_from_recency(self, repo):
        """A card fact written LAST must still read as card. This repo has a
        standing rule against inferring contradiction from recency; provenance
        deserves the same."""
        repo.add_fact(_OP, "learned first", asserted_by="user", origin="learned")
        repo.add_fact(_OP, "card second", asserted_by="card", origin="card")
        by_text = {r["text"]: r["origin"] for r in repo.facts_by_origin(_OP)}
        assert by_text["card second"] == "card"


class TestFactIsNotAShape:
    def test_fact_is_absent_from_SHAPES(self):
        """🔴 `apply_card` EXPIRES every node absent from the card on each
        rebuild. A learned operator fact is absent from the card by definition,
        so modelling `:Fact` as a Shape would delete the whole learned store on
        the next re-seed — precisely the hazard cmp.cq02 calls load-bearing."""
        from src.coordinator.identity_shapes import SHAPES

        assert "Fact" not in SHAPES
        assert "Operator" not in SHAPES

    def test_both_labels_are_declared_in_the_surface(self):
        from src.coordinator.graph_surface import BUILT_LABELS, BUILT_RELS

        assert "Fact" in BUILT_LABELS and "Operator" in BUILT_LABELS
        assert "ABOUT" in BUILT_RELS

    def test_neither_label_is_exempted(self):
        """They are DEMANDED by cmp.cq01/cq02, so an exemption would be the
        wrong answer — C17 errors on an exemption for a demanded name."""
        from src.coordinator.graph_surface import MECHANISM_EXEMPTIONS

        assert "Fact" not in MECHANISM_EXEMPTIONS
        assert "Operator" not in MECHANISM_EXEMPTIONS
