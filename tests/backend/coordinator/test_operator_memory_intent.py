"""A question about the OPERATOR'S own past must never route to a web search.

🔴 THE DEFECT. Every "about the user" phrasing in this repo lived in the
capability-gated `wallet` intent and `WALLET_FASTPATH`. gwen holds no wallet, so
they were filtered out BY CAPABILITY rather than by meaning, leaving `web_search`
as the only usable intent — and with one candidate the margin gate cannot fire,
because it requires >= 2 scores. So "how much bitcoin did i buy and when?"
scored against "what's the current price of bitcoin" and routed to the web;
`check_scope` then deflected it with a canned "I can't go looking that up"
BEFORE she reached her memory.

Measured 2026-10-18, and this contrast is what identifies the guard rather than
the store: that question deflected while "whats my money situation?" answered
"you bought 2.5 BTC back in 2021" from the SAME facts in the SAME session.

Baseline 2026-10-21, 2 reps through the live route: ANSWERABLE wrongly deflected
4/12; WORLD correctly deflected 4/6 with 2/6 already leaking.
"""
from __future__ import annotations

import pytest

from coordinator.tools.capability_scope import INTENT_REQUIREMENTS, check_scope
from coordinator.tools.intent_classifier import QueryIntent
from coordinator.tools.semantic_router import _INTENT_EXAMPLES_PRIMARY


class TestTheIntentExists:
    def test_operator_memory_is_a_primary_intent(self):
        assert "operator_memory" in _INTENT_EXAMPLES_PRIMARY

    def test_it_has_examples_across_all_the_fact_classes(self):
        ex = _INTENT_EXAMPLES_PRIMARY["operator_memory"]
        assert len(ex) >= 15, "one phrasing per class is not enough for max-over-examples"

    def test_no_example_asks_about_current_world_state(self):
        """⚠️ The failure this guards. "what's my local weather" is about him AND
        about the world — it must keep routing to web_search. An example here
        containing a now-word would drag that class onto this intent."""
        now_words = ("right now", "today", "latest", "current", "price",
                     "weather", "news", "tomorrow", "closed at")
        bad = [e for e in _INTENT_EXAMPLES_PRIMARY["operator_memory"]
               if any(w in e.lower() for w in now_words)]
        assert not bad, f"these examples reach for current world state: {bad}"

    def test_examples_cover_her_own_self_knowledge_too(self):
        """The self-facts section answers these, so they must not deflect either.
        One of 5 measured self-recall refusals was the groundedness gate
        abstaining on a question about her own HEIGHT."""
        ex = " ".join(_INTENT_EXAMPLES_PRIMARY["operator_memory"]).lower()
        assert "how tall are you" in ex
        assert "what colour are your eyes" in ex


class TestItIsNotCapabilityGated:
    """The root cause was capability gating. Re-gating it rebuilds the defect."""

    def test_the_usable_filter_admits_it_without_any_tool_grant(self):
        import inspect

        from coordinator.tools import semantic_router

        src = inspect.getsource(semantic_router.route_by_embedding)
        i = src.index('usable: List[str] = []')
        block = src[i:i + 500]
        assert '"operator_memory" in source' in block
        assert "can_use_wallet and \"operator_memory\"" not in block
        assert "can_use_brave and \"operator_memory\"" not in block

    @pytest.mark.requires_ollama
    def test_a_personas_tools_do_not_decide_whether_a_question_is_about_him(self):
        """⚠️ Hits the real bge-m3 embedder, so it carries the marker. Without it
        the test fails headless, and this repo's conftest exists to make that
        impossible to miss."""
        from coordinator.tools.semantic_router import route_by_embedding

        q = "how much bitcoin did i buy and when?"
        no_tools = route_by_embedding(q, can_use_wallet=False, can_use_brave=False,
                                      drop_llm_only_centroid=True)
        with_web = route_by_embedding(q, can_use_wallet=False, can_use_brave=True,
                                      drop_llm_only_centroid=True)
        assert no_tools != "web_search" and with_web != "web_search"


class TestWinningMeansNoDeflection:
    def test_needs_neither_is_the_one_intent_that_never_deflects(self):
        """Why returning None is the fix: NEEDS_NEITHER's requirement set is
        empty, and check_scope returns IN_SURFACE on an empty set."""
        assert INTENT_REQUIREMENTS[QueryIntent.NEEDS_NEITHER] == frozenset()
        verdict = check_scope(QueryIntent.NEEDS_NEITHER, set(), media_type=None)
        assert not verdict, "NEEDS_NEITHER must never be out-of-surface"

    def test_operator_memory_returns_none_so_it_lands_on_needs_neither(self):
        import inspect

        from coordinator.tools import semantic_router

        src = inspect.getsource(semantic_router.route_by_embedding)
        assert 'if best_intent in ("llm_only", "operator_memory"):' in src, (
            "operator_memory must share llm_only's return-None path"
        )

    def test_a_web_intent_without_web_tools_still_deflects(self):
        """The guard must keep working for the class it was built for."""
        verdict = check_scope(QueryIntent.NEEDS_WEB_SEARCH, set(), media_type=None)
        assert verdict, "a genuine web question with no web tools must deflect"


class TestFabricationIsStillHandled:
    def test_the_recall_instruction_is_the_control_not_the_deflection(self):
        """Removing the deflection for this class is only safe because the frame
        already instructs her to decline when a fact is absent — measured at
        fabrication 0.875 -> 0.000 over 160 generations."""
        from coordinator.context_framing import RECALL_INSTRUCTION

        low = RECALL_INSTRUCTION.lower()
        assert "not in the list" in low
        assert "do not guess" in low
