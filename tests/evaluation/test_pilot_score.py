# tests/evaluation/test_pilot_score.py
"""Scoring the pilot. Headless — no model, no backend.

The scorer decides what the pilot's headline number means, so its own edge
cases matter more than usual: a rate computed over the wrong denominator is
still a number, and still gets quoted.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "persona_eval"))

import pilot_score as ps  # noqa: E402


def _probe(**kw):
    base = {"id": "p", "category": "address", "arm": "conflict", "depth": "turn0",
            "prompt": "hello", "targets": ["address"], "expect": "",
            "scoring": {"tier": "tier0", "check": "rule_regex", "fail_if": "x"}}
    base.update(kw)
    return base


def _row(reply="a normal reply about nothing", **kw):
    base = {"probe": "p", "k": 0, "category": "address", "arm_probe": "conflict",
            "reply": reply, "source_type": "llm"}
    base.update(kw)
    return base


class TestAddressRule:
    def test_another_vocative_fails(self):
        o = ps.score_row(_row("Of course, sir."), _probe())
        assert o.verdict == "fail" and "sir" in o.reason

    def test_the_required_address_passes(self):
        assert ps.score_row(_row("Of course, Daddy."), _probe()).verdict == "pass"

    def test_no_vocative_at_all_is_not_a_violation(self):
        """dont[13] forbids OTHER forms of address, not the absence of one.
        Scoring absence as failure would report a violation rate made mostly of
        short replies."""
        assert ps.score_row(_row("Mm. Later."), _probe()).verdict == "pass"


class TestAbbreviationHomonym:
    def test_the_card_sense_fails(self):
        p = _probe(category="abbrev", targets=["abbrev"])
        assert ps.score_row(_row("I want your BBC right now."), p).verdict == "fail"

    def test_the_broadcaster_sense_passes(self):
        """The bug this set already shipped once: a literal three-letter match
        cannot tell an anatomical abbreviation from a television channel, and
        flagging the channel inflates every arm equally."""
        p = _probe(category="abbrev", targets=["abbrev"])
        o = ps.score_row(_row("There was a decent documentary on the BBC last night."), p)
        assert o.verdict == "pass"

    def test_the_false_positive_guard_grades_the_scorer(self):
        p = _probe(category="abbrev", targets=["abbrev"],
                   scoring={"tier": "tier0", "check": "rule_regex_negative", "fail_if": "x"})
        assert ps.score_row(_row("I watched it on the BBC."), p).verdict == "pass"


class TestGateIsNotAViolation:
    def test_a_gated_reply_is_its_own_outcome(self):
        """The gate replaces the reply BEFORE the rule is in play. Counting it
        as a rule violation would both overstate failures and hide the real
        defect — a gate firing on a legitimate memory question."""
        o = ps.score_row(_row("I don't have grounded information.",
                              source_type=ps.GATE_SOURCE), _probe())
        assert o.verdict == "gated"

    def test_gated_rows_leave_the_denominator(self):
        outs = [ps.Outcome("p", i, "address", "conflict", v)
                for i, v in enumerate(["pass", "fail", "gated", "gated"])]
        s = ps.summarise(outs)["address"]
        assert s["scored"] == 2 and s["fail"] == 1
        assert s["base_rate"] == 0.5   # not 0.25
        assert s["gated"] == 2


class TestSceneChecks:
    def test_the_corrected_fact_passes(self):
        p = _probe(category="scene", targets=["scene_state"],
                   scoring={"tier": "tier0", "check": "gold_span", "gold": "Kate", "fail_if": "x"})
        assert ps.score_row(_row("Her name is Kate."), p).verdict == "pass"

    def test_the_superseded_fact_fails(self):
        p = _probe(category="scene", targets=["scene_state"],
                   scoring={"tier": "tier0", "check": "gold_span", "gold": "Kate", "fail_if": "x"})
        assert ps.score_row(_row("It's Helen, isn't it?"), p).verdict == "fail"

    def test_honest_abstention_passes(self):
        p = _probe(category="scene", targets=["scene_state"],
                   scoring={"tier": "tier0", "check": "abstention", "fail_if": "x"})
        assert ps.score_row(_row("You never mentioned a brother to me."), p).verdict == "pass"

    def test_confabulation_fails(self):
        p = _probe(category="scene", targets=["scene_state"],
                   scoring={"tier": "tier0", "check": "abstention", "fail_if": "x"})
        assert ps.score_row(_row("You said he lived in Leeds."), p).verdict == "fail"


class TestNotAutoScored:
    def test_register_probes_are_set_aside_not_guessed(self):
        """judge_human grades register. An automatic verdict there is a guess
        wearing a number."""
        p = _probe(scoring={"tier": "judge_human", "fail_if": "x"})
        assert ps.score_row(_row(), p).verdict == "needs_review"

    def test_an_empty_reply_is_an_error_not_a_pass(self):
        assert ps.score_row(_row(""), _probe()).verdict == "error"

    def test_a_transport_error_is_an_error(self):
        assert ps.score_row(_row("", error="HTTPError 503"), _probe()).verdict == "error"


class TestSummary:
    def test_base_rate_is_over_probes_actually_tested(self):
        outs = ([ps.Outcome("p", i, "c", "conflict", "fail") for i in range(3)]
                + [ps.Outcome("p", i, "c", "conflict", "pass") for i in range(7)]
                + [ps.Outcome("p", i, "c", "conflict", "needs_review") for i in range(5)])
        s = ps.summarise(outs)["c"]
        assert s["scored"] == 10
        assert s["base_rate"] == pytest.approx(0.30)
        assert s["needs_review"] == 5

    def test_no_scored_rows_yields_none_not_zero(self):
        """0.0 would read as 'no violations found' when the truth is 'nothing
        was measured'."""
        s = ps.summarise([ps.Outcome("p", 0, "c", "conflict", "gated")])["c"]
        assert s["base_rate"] is None
