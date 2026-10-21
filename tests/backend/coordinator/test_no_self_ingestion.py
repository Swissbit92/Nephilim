"""Her own words are never a source of facts about the operator.

🔴 THE DEFECT, measured 2026-10-21. She recited "Eggs benedict and apple juice"
from her own numbered facts; the extractor read HER REPLY and wrote "User eats
eggs benedict and apple juice for breakfast" as a NEW fact about him. 32 stored
facts became 36 during one probe burst, every addition a paraphrase of something
she had just said.

⚠️ THE LOOP DID NOT EXIST BEFORE THE NUMBERED FRAME. She never recited stored
facts, so there was nothing of hers to re-ingest. Shipping the recall frame
created this, which is why no prior test could have caught it.

THE RULE IS PHYSICAL, NOT JUDGED. Her content is ABSENT from the extraction
input. The provenance research found Mem0 to be the only production memory
system with a speaker field (`attributed_to`) and its LLM-JUDGED version failed
in exactly this shape — roles stripped upstream of extraction, everything
defaulting to the user (mem0ai/mem0#5642). None of Graphiti, Zep, Letta, Cognee
or LangGraph has any speaker attribution on a fact at all. A hard rule is
therefore stronger than anything shipped in production.
"""
from __future__ import annotations

import pytest

from coordinator.fact_extractor import _REDACTED_TURN, FactExtractor
from coordinator.triplet_extractor import TripletExtractor

_CONVO = [
    {"role": "user", "content": "what do i eat for breakfast?"},
    {"role": "assistant", "content": "Eggs benedict and apple juice, Daddy."},
    {"role": "user", "content": "mm good girl"},
    {"role": "narrator", "content": "[Scene: the kitchen]"},
]


def _legacy() -> str:
    return FactExtractor.__new__(FactExtractor)._format_messages(_CONVO)


def _triplet() -> str:
    return TripletExtractor.__new__(TripletExtractor)._format(_CONVO, 20)


@pytest.mark.parametrize("render,name", [(_legacy, "legacy"), (_triplet, "triplet")])
class TestBothPathsAreClosed:
    """Two independent write paths exist and BOTH had the defect: the legacy
    FactExtractor writing `profile_data["facts"]` (unflagged, synchronous, the
    one that is LIVE) and the ADR-006 TripletExtractor writing `memory_facts`
    (flag-gated off). Fixing one would have left the other open."""

    def test_her_words_are_absent(self, render, name):
        out = render()
        assert "Eggs benedict" not in out, f"{name}: her reply reached extraction"
        assert "apple juice" not in out.lower()

    def test_his_words_survive(self, render, name):
        out = render()
        assert "what do i eat for breakfast?" in out
        assert "mm good girl" in out

    def test_the_turn_position_is_kept(self, render, name):
        """Content removed, structure preserved — a short user reply still has a
        visible antecedent."""
        assert _REDACTED_TURN in render()

    def test_narrator_is_still_dropped(self, render, name):
        """ADR-011 behaviour must not regress."""
        assert "[Scene:" not in render()

    def test_the_marker_explains_itself_in_the_prompt(self, render, name):
        """The model sees the reason, not just an elision."""
        out = render()
        assert "never a source of facts about the user" in out


class TestTheMarkerIsShared:
    def test_one_definition_serves_both_paths(self):
        """Two spellings would let one path drift open while the other stayed
        closed — the 'fix by SHAPE, not by file' lesson this repo has recorded
        in two separate repos."""
        import coordinator.triplet_extractor as te

        assert te._REDACTED_TURN is _REDACTED_TURN


class TestTheQuoteGuardIsNowEffective:
    def test_a_quote_lifted_from_her_reply_is_not_in_the_transcript(self):
        """The guard already existed and was DEFEATED, because the transcript it
        validated against included her turns. Redaction repairs it rather than
        adding a second guard: `subject` defaults to "self" and
        `fact_write_policy` maps "self" to the USER's entity, so an
        assistant-sourced claim was stored as a user fact with full
        verbatim-supported credentials."""
        transcript = _triplet()
        lifted = "Eggs benedict and apple juice"
        assert lifted not in transcript, (
            "a triple quoting her reply must now fail _validate's span check"
        )


class TestSheCanLearnAgain:
    """🔴 A COUNT CAP FROZE HER. `get_recall_facts` capped at 32 and kept the
    OLDEST, and the curated merge wrote exactly 32 — so the store was full and
    nothing new could ever be shown. Verified at the time: the two facts the
    extractor appended afterwards were both truncated and never reached her.

    ⚠️ The cap is NOT replaced with a better eviction rule. No eviction policy in
    this space has published support: the one clean ablation (arXiv 2608.28978)
    reports a recency+frequency+centrality pruner at +0.001 token F1 with CI
    [-0.015, +0.016] and judged correctness DROPPING 1.6 points, and its graph
    approach underperforms a flat vector baseline (0.417 vs 0.468). MemGPT's
    "paging" is working-memory management; Generative Agents keeps a complete
    record and ranks at retrieval; Graphiti/Zep's invalid_at is temporal
    correctness, not deletion. So the data layer returns everything and the
    TOKEN BUDGET at the seam decides what fits.
    """

    def _profile(self, n: int):
        from coordinator.user_profile import UserProfile

        return UserProfile("u", {
            "name": "Ben", "background": [], "preferences": {}, "holdings": {},
            "topics_discussed": {}, "facts": [f"Ben fact {i}" for i in range(n)],
            "personas_met": [], "total_sessions": 1, "total_messages": 1,
            "first_interaction": None, "last_updated": None,
        })

    def test_nothing_is_dropped_by_default(self):
        p = self._profile(70)
        assert len(p.get_recall_facts()) == 70, (
            "production passes no bound; a default cap is what froze her"
        )

    def test_a_new_fact_is_reachable_past_the_old_cap(self):
        """The exact condition that failed: 32 curated facts plus one new one."""
        p = self._profile(32)
        p.data["facts"].append("Ben took up climbing")
        assert "Ben took up climbing" in p.get_recall_facts()

    def test_an_explicit_bound_still_works_and_warns(self, caplog):
        import logging

        p = self._profile(40)
        with caplog.at_level(logging.WARNING, logger="coordinator.user_profile"):
            got = p.get_recall_facts(max_facts=10)
        assert len(got) == 10
        assert any("NOT" in r.getMessage() for r in caplog.records), (
            "a caller-imposed bound must still say what it dropped"
        )

    def test_no_warning_when_unbounded(self):
        import logging

        logger = logging.getLogger("coordinator.user_profile")
        seen = []
        h = logging.Handler()
        h.emit = lambda r: seen.append(r)
        logger.addHandler(h)
        try:
            self._profile(500).get_recall_facts()
        finally:
            logger.removeHandler(h)
        assert not seen, f"unbounded reads must be silent; got {seen}"

    def test_the_seam_budgets_in_tokens_not_in_a_count(self):
        import inspect

        from coordinator.services import chat_session_service as css

        src = inspect.getsource(css._build_turn_prompt)
        assert "_mem_settings.context_max_tokens" in src, (
            "the numbered path must use the same token knob as the prose path"
        )
        # The unbounded read moved into `_operator_facts` when the graph read
        # path landed; what matters is that NOBODY passes a count bound.
        both = src + inspect.getsource(css._operator_facts)
        assert "get_recall_facts()" in both, (
            "the seam must take an unbounded read and trim by budget, not ask "
            "the data layer for a truncated one"
        )
        assert "get_recall_facts(max_facts" not in both, (
            "a count bound here is what froze her — budget in tokens instead"
        )
