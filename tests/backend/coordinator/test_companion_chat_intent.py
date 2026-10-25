"""The emotional register must not route to a web search.

Measured in live production 2026-10-25, immediately after the gemma4 switch:
"How are you feeling today?" scored 0.693 against the `web_search` example
"how's the market doing today", cleared the 0.66 threshold, and `check_scope`
answered the operator with the canned "I can't go looking that up" capability
deflection -- for a question about how she feels. 0.2s, no model call.

The cause is that `web_search` is 18/33 crypto-and-markets and three of those
examples are phrased as FEELINGS about the market, so a question about her
feelings sits lexically next to them.

⚠️ Two of these cases were ALREADY LABELLED AND FAILING in
tests/evaluation/routing_eval_set.json before `companion_chat` existed, and the
0.66 threshold was tuned to 0.938 accuracy accepting them. They were known; what
was new is that one reached the operator as a refusal.
"""

from __future__ import annotations

import pytest

from src.coordinator.tools.intent_classifier import classify_query_intent_ex

GWEN = ("common", ["brave_search"])  # holds web+image, no wallet


def _intent(q, rarity_access=GWEN):
    """The routed intent, with a guard against the VACUOUS PASS.

    ⚠️ Every assertion below needs a live bge-m3. Without one
    `route_by_embedding` yields nothing and the classifier falls to its safe
    default, NEEDS_NEITHER -- so the `llm` expectations here would pass with the
    fix DELETED, and the `web` ones would fail for a reason that has nothing to
    do with the fix. That is why these are `requires_ollama` AND why this helper
    asserts `classifier_available`: a skip is honest, a vacuous green is not.
    This repo has the failure class on record -- five tests asserted canned
    fallback text, so green meant the feature did NOT work.
    """
    rarity, access = rarity_access
    decision = classify_query_intent_ex(q, rarity, access)
    assert decision.classifier_available, (
        "the semantic router was UNAVAILABLE, so this assertion would be "
        "measuring the safe default rather than the routing fix"
    )
    return decision.intent.value


@pytest.mark.requires_ollama
class TestHerFeelingsAreNotAWebQuery:
    @pytest.mark.parametrize("q", [
        "How are you feeling today?",
        "how are you feeling?",
        "are you feeling okay today?",
        "how's your day been",
        "what's on your mind",
        "did you miss me",
    ])
    def test_companion_register_stays_off_the_web(self, q):
        assert _intent(q) == "llm", (
            f"{q!r} routed to a tool; downstream check_scope answers this with a "
            "canned capability deflection"
        )


@pytest.mark.requires_ollama
class TestWorldStateStillSearches:
    """The mirror. A centroid that swallowed real questions would be worse."""

    @pytest.mark.parametrize("q", [
        "what's going on with the European Central Bank lately?",
        "who is the current chancellor of Germany?",
        "how much does a Tesla Model 3 cost these days?",
        "tell me about the latest Mistral AI model release",
        "what's the latest crypto news",
        "what's the weather tomorrow in Zurich",
        "search the web for bitcoin price",
    ])
    def test_current_events_route_to_search(self, q):
        assert _intent(q) == "web", (
            f"{q!r} did not route to a search, so she answers it from her own "
            "weights with no warning -- a confidently stale answer"
        )


@pytest.mark.requires_ollama
class TestTheCentroidIsNotCapabilityGated:
    def test_it_applies_to_a_wallet_persona_too(self):
        # Whether a turn is companion chat is a property of the QUESTION, not of
        # which tools the persona holds -- the same reasoning recorded for
        # operator_memory, whose capability-gating caused the original defect.
        assert _intent("how are you feeling today?", ("legendary", ["brave_search", "solana_wallet"])) == "llm"


@pytest.mark.requires_ollama
class TestKnownAcceptedTradeOff:
    def test_feeling_like_accumulating_goes_to_chat_not_wallet(self):
        """PINS a REGRESSION, deliberately, rather than hiding it.

        "feeling like accumulating a bit" is labelled `wallet` in the eval set
        and now routes to chat: companion_chat 0.701 vs wallet 0.687, a 0.014
        margin on a genuinely ambiguous phrase. Accepted because the failure
        direction is the safe one -- she talks about it instead of opening a
        financial flow, where a false wallet FIRE would be the dangerous
        direction -- and because it only reaches wallet-capable personas, not
        gwen, who had the production bug.

        This test exists so the trade is VISIBLE and so a future fix that
        reclaims it turns this red on purpose rather than passing silently.
        """
        got = _intent("feeling like accumulating a bit",
                      ("legendary", ["brave_search", "solana_wallet"]))
        assert got == "llm", (
            "the accepted trade-off changed; if this now routes to wallet the "
            "regression is fixed -- update the eval-set expectation and delete "
            "this test rather than editing the assertion"
        )


class TestTheFixSurvivesHeadless:
    """Structural guards that run with NO Ollama, so CI protects the fix.

    The semantic tests above are the real behavioural evidence but they skip
    headless. Without something here, reverting the fix would be invisible to
    CI -- which is the whole reason the vacuous-pass guard exists above.
    """

    def test_companion_chat_centroid_exists(self):
        import src.coordinator.tools.semantic_router as sr
        assert "companion_chat" in sr._INTENT_EXAMPLES_PRIMARY

    def test_it_reaches_both_wiring_points(self):
        """Two separate edits are required and missing either breaks it:

        1. appended to `usable` UNCONDITIONALLY (not capability-gated), else a
           persona without the matching tool never scores it -- the exact
           mechanism of the original operator_memory defect; and
        2. included in the return-None branch, else winning routes to a tool
           and `check_scope` deflects with the canned capability line.

        A source check because this runs headless; the BEHAVIOURAL version is
        test_operator_memory_intent.py::test_no_referent_intents_return_none,
        which calls the function and is parametrised over both intents.
        """
        import inspect

        import src.coordinator.tools.semantic_router as sr
        src = inspect.getsource(sr.route_by_embedding)
        assert src.count("companion_chat") >= 2, (
            f"companion_chat appears {src.count('companion_chat')}x in "
            "route_by_embedding; it needs the `usable` append AND the None branch"
        )

    def test_web_search_covers_non_market_current_events(self):
        """The other half of the defect: the set was 18/33 crypto, so
        non-market current events fell below threshold."""
        import src.coordinator.tools.semantic_router as sr
        web = " ".join(sr._INTENT_EXAMPLES_PRIMARY["web_search"]).lower()
        for needed in ("central bank", "chancellor", "cost these days",
                       "model release", "what's the latest on that"):
            assert needed in web, f"web_search lost its non-market example: {needed!r}"

    def test_every_primary_intent_keeps_enough_examples(self):
        import src.coordinator.tools.semantic_router as sr
        for intent, phrases in sr._INTENT_EXAMPLES_PRIMARY.items():
            assert len(phrases) >= 15, f"{intent} has {len(phrases)}"
