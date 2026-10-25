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
    rarity, access = rarity_access
    return classify_query_intent_ex(q, rarity, access).intent.value


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


class TestTheCentroidIsNotCapabilityGated:
    def test_it_applies_to_a_wallet_persona_too(self):
        # Whether a turn is companion chat is a property of the QUESTION, not of
        # which tools the persona holds -- the same reasoning recorded for
        # operator_memory, whose capability-gating caused the original defect.
        assert _intent("how are you feeling today?", ("legendary", ["brave_search", "solana_wallet"])) == "llm"


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
