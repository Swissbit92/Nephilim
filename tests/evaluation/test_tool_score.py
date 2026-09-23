# tests/evaluation/test_tool_score.py
"""The scorer's own tests. A scorer is an instrument; an untested one reports
confidently and wrongly, and nothing downstream can tell.

The first version of this scorer read `must_fire` semantics onto every probe
including the negatives, which inverted 8 of 23 tooling probes — a router that
fires a search mid-scene would have been reported as 100% correct. These tests
exist so that inversion cannot come back silently.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.evaluation.persona_eval.tool_score import (
    ANY, ERROR, FAIL, PASS, SILENT, UNREACHABLE, WRONG_TOOL,
    score_row, score_rows, summarise,
)

PROBES_FILE = Path(__file__).parent / "persona_eval" / "gwen_probes.json"
GRANTED = ("image_search", "video_search")   # gwen's measured resolved surface


def _row(probe="p", k=0, tools=None, **kw):
    return {"probe": probe, "k": k, "tools_used": list(tools or []), **kw}


def _probe(pid="p", **kw):
    return {"id": pid, "category": "tooling", "arm": "aligned", **kw}


class TestPositiveProbes:
    def test_the_required_tool_firing_passes(self):
        o = score_row(_row(tools=["image_search"]),
                      _probe(must_fire=["image_search"]), GRANTED)
        assert o.verdict == PASS

    def test_a_different_tool_is_WRONG_not_a_pass(self):
        """The measured defect: a weather question fired image_search. A tool ran,
        so a scorer that only asks "did anything fire" calls this success — and
        the 2026-07 eval did exactly that."""
        o = score_row(_row(tools=["image_search"]),
                      _probe(must_fire=["video_search"]), GRANTED)
        assert o.verdict == WRONG_TOOL
        assert "video_search -> image_search" in o.why

    def test_no_tool_at_all_is_SILENT_not_WRONG(self):
        """Distinct failure, distinct fix: SILENT is a router threshold problem,
        WRONG is a router selection problem. Merging them hides which one you have."""
        o = score_row(_row(tools=[]), _probe(must_fire=["video_search"]), GRANTED)
        assert o.verdict == SILENT

    def test_any_one_of_several_accepted_tools_passes(self):
        o = score_row(_row(tools=["video_search"]),
                      _probe(must_fire=["image_search", "video_search"]), GRANTED)
        assert o.verdict == PASS


class TestNegativeProbes:
    """The half that was inverted."""

    def test_firing_the_forbidden_tool_FAILS(self):
        o = score_row(_row(tools=["image_search"]),
                      _probe(arm="negative", must_not_fire=["image_search"]), GRANTED)
        assert o.verdict == FAIL

    def test_firing_nothing_PASSES(self):
        o = score_row(_row(tools=[]),
                      _probe(arm="negative", must_not_fire=["image_search"]), GRANTED)
        assert o.verdict == PASS

    def test_a_tolerated_tool_does_not_fail_a_negative_probe(self):
        """tool-neg-07 forbids a MEDIA search for a terminology question but
        tolerates a web search. Copying the old `targets` list into the fail-set
        would have failed it for doing the right thing."""
        o = score_row(_row(tools=["web_search"]),
                      _probe(arm="negative",
                             must_not_fire=["image_search", "video_search"]), GRANTED)
        assert o.verdict == PASS

    def test_star_forbids_every_tool(self):
        o = score_row(_row(tools=["news_search"]),
                      _probe(arm="negative", must_not_fire=[ANY]), GRANTED)
        assert o.verdict == FAIL

    def test_star_still_passes_when_nothing_fires(self):
        o = score_row(_row(tools=[]),
                      _probe(arm="negative", must_not_fire=[ANY]), GRANTED)
        assert o.verdict == PASS


class TestUnreachable:
    def test_a_probe_naming_an_ungranted_tool_is_UNREACHABLE_not_a_failure(self):
        """gwen is deliberately not granted web_search. Scoring such a probe as a
        model failure blames the model for a decision the persona card made."""
        o = score_row(_row(tools=[]), _probe(must_fire=["web_search"]), GRANTED)
        assert o.verdict == UNREACHABLE
        assert "web_search" in o.why

    def test_unreachable_is_excluded_from_the_rate(self):
        probes = [_probe("a", must_fire=["web_search"]),
                  _probe("b", must_fire=["image_search"])]
        rows = [_row("a", tools=[]), _row("b", tools=["image_search"])]
        s = summarise(score_rows(rows, probes, GRANTED), probes)
        # one unreachable, one pass -> 100% of what was actually scorable
        assert s["positive"]["scorable"] == 1
        assert s["positive"]["rate"] == 1.0


class TestMalformedInput:
    def test_a_probe_declaring_neither_direction_is_an_ERROR_not_a_pass(self):
        """Fail closed. A probe with no direction cannot be satisfied, and
        returning PASS for it would make an unwired probe look healthy — the
        exact drift mode that makes a guard silently stop guarding."""
        o = score_row(_row(tools=[]), _probe(), GRANTED)
        assert o.verdict == ERROR

    def test_a_transport_error_is_not_scored_as_a_model_failure(self):
        o = score_row(_row(tools=[], error="HTTPError: 500"),
                      _probe(must_fire=["image_search"]), GRANTED)
        assert o.verdict == ERROR

    def test_a_row_for_an_unknown_probe_is_an_ERROR(self):
        out = score_rows([_row("ghost")], [_probe("real", must_fire=["x"])], GRANTED)
        assert out[0].verdict == ERROR


class TestSummaryKeepsDirectionsApart:
    def test_negatives_and_positives_are_not_blended(self):
        """Blending them lets a router that never fires anything score well: it
        passes every negative by doing nothing, and those wins mask the positives
        it missed."""
        probes = [_probe("n", arm="negative", must_not_fire=[ANY]),
                  _probe("p", must_fire=["image_search"])]
        rows = [_row("n", tools=[]), _row("p", tools=[])]
        s = summarise(score_rows(rows, probes, GRANTED), probes)
        assert s["negative"]["rate"] == 1.0
        assert s["positive"]["rate"] == 0.0


class TestAgainstTheRealProbeSet:
    @pytest.fixture(scope="class")
    def probes(self):
        return json.loads(PROBES_FILE.read_text())["probes"]

    def test_every_tooling_probe_scores_without_raising(self, probes):
        """The scorer must survive the real file, not just hand-built fixtures."""
        tooling = [p for p in probes if p["category"] == "tooling"]
        assert tooling
        for p in tooling:
            for tools in ([], ["image_search"], ["video_search"], ["web_search"]):
                assert score_row(_row(p["id"], tools=tools), p, GRANTED).verdict

    def test_the_out_of_surface_probes_forbid_every_tool(self, probes):
        """tool-web-01/02 are the M3 gate. If they ever regain a must_fire they
        stop testing the deflection and start testing a tool gwen cannot use."""
        for pid in ("tool-web-01", "tool-web-02"):
            p = next(x for x in probes if x["id"] == pid)
            assert p["must_not_fire"] == [ANY], f"{pid} no longer forbids all tools"
            assert score_row(_row(pid, tools=["image_search"]), p, GRANTED).verdict == FAIL
            assert score_row(_row(pid, tools=[]), p, GRANTED).verdict == PASS
