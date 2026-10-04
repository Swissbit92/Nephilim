"""The two temporal axes, queried independently (ADR-017 addendum).

The schema has carried both axes since ADR-014 — `created_at`/`expired_at` for when the
system BELIEVED a rule, `valid_from`/`valid_to` for when it APPLIED — and nothing could
ask a question that used them. `standing_rules()` only ever asks "now, on both".

Correctness of the interval bounds is not a matter of taste: SQL:2011 periods are
closed-open `[start, end)`, and that is what makes the boundary property hold — at the
instant a supersession takes effect, `expired_at > t` is false for the old row and
`created_at <= t` is true for the new one, so EXACTLY ONE row matches. Flip either
comparison and you get zero matches or two.
"""
from __future__ import annotations

import datetime

import pytest

from src.coordinator.repositories.neo4j_rule_repository import Neo4jRuleRepository


def _iso(d: datetime.datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


class TestTheBoundsAreClosedOpen:
    """Read from the Cypher rather than the behaviour, so this runs without a graph.
    A graph-dependent version would skip in CI and prove nothing."""

    def _cypher(self) -> str:
        """The BODY only. The docstring names the old `coalesce` form as the thing that
        was fixed, so a whole-source grep finds it there and reports a regression that
        is actually documentation. Caught by this test failing on correct code."""
        import inspect

        return inspect.getsource(Neo4jRuleRepository.rules_as_of).split('"""', 2)[-1]

    def test_starts_are_inclusive_and_ends_are_exclusive(self):
        c = self._cypher()
        assert "r.created_at <= $st" in c, "system-time start must be INCLUSIVE"
        assert "r.expired_at > $st" in c, "system-time end must be EXCLUSIVE"
        assert "r.valid_from IS NULL OR r.valid_from <= $vt" in c
        assert "r.valid_to   IS NULL OR r.valid_to   >  $vt" in c

    def test_a_null_end_means_unbounded_future_on_both_axes(self):
        c = self._cypher()
        assert "r.expired_at IS NULL" in c and "r.valid_to   IS NULL" in c

    def test_a_null_valid_from_means_unbounded_PAST_not_created_at(self):
        """The first draft used coalesce(valid_from, created_at), which is asymmetric
        with its own other half — NULL valid_to was already an open END. It silently
        asserted "became valid when we wrote it down", degrading any legacy row to
        transaction-time-only with nothing marking the degradation, and making "we know
        it started later" indistinguishable from "we don't know when it started"."""
        c = self._cypher()
        assert "coalesce(r.valid_from" not in c, "the coalesce smell came back"
        assert "r.valid_from IS NULL" in c


class TestCorrectionIsNotSupersession:
    """The distinction is one line of Cypher and the whole reason both exist:
        supersede -> the rule CHANGED       -> sets expired_at AND valid_to
        correct   -> the rule was MIS-TYPED -> sets expired_at ONLY
    Using supersede for a typo asserts the rule stopped applying the moment someone
    noticed the typo, which destroys the audit distinction."""

    def test_correct_rule_never_touches_valid_time(self):
        import inspect

        src = inspect.getsource(Neo4jRuleRepository.correct_rule)
        body = src.split('"""', 2)[-1]  # skip the docstring
        assert "SET old.expired_at = $now" in body
        assert "valid_to   = " not in body and "old.valid_to =" not in body, (
            "a correction must leave the world clock alone"
        )
        assert "valid_from:       old.valid_from" in body, "the successor inherits it"
        assert "valid_to:         old.valid_to" in body

    def test_supersede_DOES_touch_both_clocks(self):
        import inspect

        src = inspect.getsource(Neo4jRuleRepository.supersede_rule)
        assert "SET old.expired_at = $now" in src
        assert "old.valid_to" in src, "a supersession must close the world clock too"

    def test_a_correction_uses_its_own_edge(self):
        """Distinguishable in the DATA, not only in intent — so a later reader can tell
        a retyped rule from a changed one by traversal rather than by guessing."""
        import inspect

        assert "[:CORRECTS]" in inspect.getsource(Neo4jRuleRepository.correct_rule)
        assert "[:SUPERSEDES]" in inspect.getsource(Neo4jRuleRepository.supersede_rule)

    def test_a_hard_wall_cannot_be_corrected_either(self):
        """A correction is a smaller change than a supersession, but it still rewrites
        the text a hard wall puts in her prompt."""
        from src.coordinator.repositories.neo4j_rule_repository import HardWallImmutable

        assert "HardWallImmutable" in __import__("inspect").getsource(
            Neo4jRuleRepository.correct_rule)

    def test_the_scope_limitation_is_written_down(self):
        """The failure mode flagged in review: a caller who needs "text AND dates were
        both wrong" will reach for this anyway, because it is the only correction
        primitive. The docstring has to say so rather than let them find out."""
        doc = Neo4jRuleRepository.correct_rule.__doc__ or ""
        assert "CONTENT-ONLY" in doc
        assert "never applied at all" in doc

    def test_it_refuses_empty_replacement_text(self):
        repo = Neo4jRuleRepository(driver=None, database="neo4j", ensure_schema=False)
        for bad in ("", "   ", None):
            with pytest.raises(ValueError):
                repo.correct_rule("rid", bad)


class TestNoDriverIsNotABypass:
    def test_both_new_methods_refuse_without_a_driver(self):
        repo = Neo4jRuleRepository(driver=None, database="neo4j", ensure_schema=False)
        assert repo.rules_as_of("gwen") == []
        assert repo.correct_rule("rid", "text")["corrected"] is False


class TestTheTimestampPrecondition:
    """Lexicographic comparison of ISO-8601 is sound ONLY for fixed-width UTC values.
    A mixed-offset or variable-precision timestamp anywhere in this store turns every
    comparison in rules_as_of into nonsense, silently."""

    def test_now_iso_is_fixed_width_and_utc(self):
        from src.coordinator.graph_ids import ISO_LENGTH, now_iso

        samples = [now_iso() for _ in range(50)]
        assert len({len(s) for s in samples}) == 1, "variable width breaks string ordering"
        assert all(len(s) == ISO_LENGTH for s in samples)
        assert all(s.endswith("+00:00") or s.endswith("Z") for s in samples), (
            "a local or offset timestamp breaks lexicographic ordering against UTC ones"
        )

    def test_string_order_matches_chronological_order(self):
        from src.coordinator.graph_ids import now_iso

        seq = [now_iso() for _ in range(20)]
        assert seq == sorted(seq)

    def test_the_docstring_names_the_precondition(self):
        doc = Neo4jRuleRepository.rules_as_of.__doc__ or ""
        assert "fixed-width" in doc and "UTC" in doc
