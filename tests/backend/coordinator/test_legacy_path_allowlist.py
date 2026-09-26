"""The LEGACY tool offer must respect what a persona is actually granted.

This file exists because the suite was green while the hole was open. Nothing
covered `get_tools_for_query` against a persona whose card RESTRICTS its tools,
so a function that performed no authorization at all passed 2468 tests.

Two separate defects, measured 2026-09-24:

1. A persona-card `tools` allowlist was invisible here. gwen is scoped to
   image/video search (ADR-008, deliberate) and the legacy path handed her
   `brave_web_search` anyway — the exact tool the registry withholds.

2. The wallet branch checked nothing. Forced to NEEDS_WALLET, a persona whose
   `mcp_access` is only `["brave_search"]` received all seven wallet tools. Not
   reachable in production, because `routes/chat.py` classifies intent with the
   persona's own `mcp_access` and that intent therefore cannot arise for her —
   but the function had no defence of its own. "One caller happens to check
   first" is a single point of failure, not an authorization model, and the
   caller-side check is skipped entirely whenever `precomputed_intent` is passed
   (which `chat.py` always does).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.coordinator.tools import registrations  # noqa: F401 - registers specs
from src.coordinator.tools.capability_scope import GENERAL_LOOKUP_TOOLS
from src.coordinator.tools.intent_classifier import QueryIntent
from src.coordinator.tools.registry import registry
from src.coordinator.tools.tool_utils import get_tools_for_query

PERSONAS = Path(__file__).parents[3] / "personas"
GWEN = json.loads((PERSONAS / "gwen.json").read_text())          # has a `tools` allowlist
EEVA = json.loads((PERSONAS / "nephilim_eeva.json").read_text())  # unrestricted + wallet


def _names(tools):
    return sorted(t["function"]["name"] for t in tools)


def _offer(card, intent):
    return get_tools_for_query(
        "anything", card.get("key", "x"), card.get("rarity", "common"),
        mcp_access=card.get("mcp_access"), precomputed_intent=intent,
        persona_card=card,
    )


class TestRestrictedPersona:
    def test_a_withheld_general_search_is_not_offered(self):
        """The reported defect. gwen's card grants the `web` toolset but its
        `tools` allowlist narrows her to image/video — so no general lookup."""
        assert _names(_offer(GWEN, QueryIntent.NEEDS_WEB_SEARCH)) == []

    def test_the_registry_agrees_she_was_never_granted_it(self):
        """Guards against 'fixing' this by changing what she is granted rather
        than by honouring the grant."""
        granted = {s.name for s in registry.specs_for_persona(GWEN)}
        assert granted == {"image_search", "video_search"}
        assert not (granted & GENERAL_LOOKUP_TOOLS)

    def test_her_media_tools_are_NOT_offered_instead(self):
        """Deliberate: the legacy force-search path can only execute
        `brave_web_search` (tool_calling_service keys on that literal name), so
        offering her media tools would route the turn into chat.py's `else`
        branch, which cannot run them. Withholding is correct; substituting is
        a silent dead end."""
        offered = _names(_offer(GWEN, QueryIntent.NEEDS_WEB_SEARCH))
        assert "image_search" not in offered and "video_search" not in offered


class TestWalletIsNotHandedOutOnIntentAlone:
    def test_a_persona_without_the_wallet_grant_gets_no_wallet_tools(self):
        """Defence in depth. The intent is forced here precisely because that is
        what the production caller does — it passes `precomputed_intent`, which
        skips the classifier's own `can_use_wallet` gate."""
        assert _offer(GWEN, QueryIntent.NEEDS_WALLET) == []

    def test_a_persona_WITH_the_grant_still_gets_them(self):
        """The fix must not cost the persona the capability actually exists for."""
        offered = _names(_offer(EEVA, QueryIntent.NEEDS_WALLET))
        assert len(offered) == 7
        assert "wallet_get_balances" in offered


class TestUnrestrictedPersonasAreUnaffected:
    """7 of 8 shipped personas have no `tools` allowlist. The fix must be
    byte-identical for them, or it buys one persona's correctness with everyone
    else's behaviour."""

    @pytest.mark.parametrize("name", [
        "nephilim_eeva", "nephilim_aegis", "nephilim_aurora",
        "nephilim_cipher", "nephilim_solace",
    ])
    def test_web_intent_still_offers_brave_web_search(self, name):
        card = json.loads((PERSONAS / f"{name}.json").read_text())
        assert _names(_offer(card, QueryIntent.NEEDS_WEB_SEARCH)) == ["brave_web_search"]

    @pytest.mark.parametrize("name", ["gojo", "nephilim_nyx"])
    def test_personas_with_no_grants_still_get_nothing(self, name):
        card = json.loads((PERSONAS / f"{name}.json").read_text())
        assert _offer(card, QueryIntent.NEEDS_WEB_SEARCH) == []

    def test_only_two_shipped_personas_have_an_allowlist(self):
        """Pins the blast radius. If a further persona gains a `tools` allowlist,
        this fails and whoever added it has to confirm the legacy consequences
        rather than discover them in production.

        gwen_dev added 2026-09-26 (ADR-014), and the consequence WAS confirmed
        rather than assumed: its offer surface was compared against gwen's across
        every QueryIntent and is byte-identical, because it is a copy of her card
        with four fields changed (key, display_name, coordinator_label, active).
        So this adds no new tool exposure — it inherits gwen's existing surface,
        including the still-open finding that the legacy `get_tools_for_persona`
        path ignores persona allowlists. Same exposure, one more card.
        """
        with_allowlist = sorted(
            p.stem for p in PERSONAS.glob("*.json")
            if json.loads(p.read_text()).get("tools") is not None)
        assert with_allowlist == ["gwen", "gwen_dev"]


class TestDegradedModeFailsClosed:
    """The card is an OPTIONAL argument, and an optional authorization argument
    that defaults to skipping the check is worse than no check — it looks
    enforced at every call site that forgot it."""

    def test_omitting_the_card_still_gates_on_toolsets(self):
        """Without the card there is no `tools` key to honour, but `mcp_access`
        still resolves toolsets — so the degraded mode is MORE restrictive than
        the old behaviour, never less."""
        offered = get_tools_for_query(
            "anything", "someone", "common", mcp_access=[],
            precomputed_intent=QueryIntent.NEEDS_WEB_SEARCH)
        assert offered == []

    def test_omitting_the_card_does_not_hand_out_wallet_tools(self):
        offered = get_tools_for_query(
            "anything", "someone", "common", mcp_access=["brave_search"],
            precomputed_intent=QueryIntent.NEEDS_WALLET)
        assert offered == []

    def test_the_card_is_what_makes_an_allowlist_visible(self):
        """States the limitation out loud: a card rebuilt from scalars has no
        `tools` key, so callers that omit it get toolset gating only. This is
        why routes/chat.py passes `persona_card=card`."""
        scalars_only = get_tools_for_query(
            "anything", GWEN["key"], GWEN.get("rarity", "common"),
            mcp_access=GWEN.get("mcp_access"),
            precomputed_intent=QueryIntent.NEEDS_WEB_SEARCH)
        with_card = _offer(GWEN, QueryIntent.NEEDS_WEB_SEARCH)
        assert with_card == []
        assert _names(scalars_only) == ["brave_web_search"], (
            "degraded mode is expected to miss the allowlist — if this ever "
            "returns [] the fallback has become card-aware and this test, not "
            "the code, is what needs updating")


class TestExecutionLayerEnforcesTheAllowlist:
    """Filtering the OFFER is not an enforcement boundary.

    Function-calling models emit calls for tools that were never offered —
    reported on hosted APIs and on Ollama alike — so the offered list is a hint,
    not a control. OWASP LLM06 (Excessive Agency) puts the real check at the
    point of execution ("complete mediation"), which for this app is the ADR-004
    interceptor. Before this change the interceptor checked `mcp_access` only,
    i.e. TOOLSET granularity, so a persona scoped to image/video search was still
    permitted to execute `web_search` — both live in the `web` toolset.
    """

    @staticmethod
    def _ic():
        from src.coordinator.services.tool_interceptor import ToolCallInterceptor
        return ToolCallInterceptor()

    def test_a_withheld_tool_is_denied_at_execution(self):
        r = self._ic().validate("web_search", {"query": "weather"}, "gwen",
                                ["brave_search"], source="agent", persona_card=GWEN)
        assert not r.allowed
        assert "not granted" in r.reason

    def test_a_granted_tool_is_still_allowed(self):
        """The check must not cost the persona what she actually holds."""
        r = self._ic().validate("image_search", {"query": "red dress"}, "gwen",
                                ["brave_search"], source="agent", persona_card=GWEN)
        assert r.allowed

    def test_without_the_card_the_check_is_toolset_only(self):
        """Documents the limitation rather than hiding it: omitting the card
        leaves the pre-existing toolset-level behaviour, which cannot see a
        tool-level allowlist."""
        r = self._ic().validate("web_search", {"query": "weather"}, "gwen",
                                ["brave_search"], source="agent")
        assert r.allowed

    def test_an_unrestricted_persona_is_unaffected(self):
        r = self._ic().validate("web_search", {"query": "news"}, "nephilim_eeva",
                                ["brave_search", "solana_wallet"],
                                source="agent", persona_card=EEVA)
        assert r.allowed

    def test_the_two_layers_agree_for_every_shipped_persona(self):
        """Both layers resolve through `specs_for_persona`, so the offer can never
        contain a tool the interceptor would reject. Two independently maintained
        copies of "what may persona X do" is how they drift apart."""
        ic = self._ic()
        for path in sorted(PERSONAS.glob("*.json")):
            card = json.loads(path.read_text())
            offered = _names(_offer(card, QueryIntent.NEEDS_WEB_SEARCH))
            granted = {s.name for s in registry.specs_for_persona(card)}
            for name in offered:
                if not granted:
                    continue  # unrestricted persona: interceptor stays toolset-level
                r = ic.validate(name, {"query": "x"}, card.get("key", "x"),
                                card.get("mcp_access") or [], source="agent",
                                persona_card=card)
                assert r.allowed, (
                    f"{path.stem}: offered {name!r} but execution would deny it "
                    f"({r.reason}) — the two layers have drifted")


class TestNoneMeansUnknownNotUnrestricted:
    def test_no_card_and_no_mcp_access_denies(self):
        """The ambiguous case. Previously this fell through to the legacy rarity
        fallback, which hands any rare/epic/legendary persona a web tool on the
        strength of a rarity string — an authorization decision made from a
        cosmetic field."""
        assert get_tools_for_query("q", "x", "epic",
                                   precomputed_intent=QueryIntent.NEEDS_WEB_SEARCH) == []
        assert get_tools_for_query("q", "x", "legendary",
                                   precomputed_intent=QueryIntent.NEEDS_WALLET) == []
