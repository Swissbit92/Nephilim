"""The out-of-surface guard: a persona must not answer past its tool surface.

Measured defect (2026-09-23, 69 live generations): gwen is granted image_search
and video_search only. Asked "what's the weather in Zurich tomorrow?" she fired
image_search 3/3 and answered "a maximum temperature of 103F and a minimum of
68F" — invented, and shipped with a citation block because a tool had run.

These tests pin the guard as DETERMINISTIC. It must not depend on the model
electing to decline: models comply with a request to decline only 28-65% of the
time (arXiv 2311.09731), refusal is output-layer suppression rather than erasure
(arXiv 2608.15772), and abliteration — used here — measurably thins the
uncertainty vocabulary a self-assessed decline would rely on (arXiv 2607.17427).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.coordinator.tools import registrations  # noqa: F401 - registers specs
from src.coordinator.tools.capability_deflection import (
    CARD_FIELD,
    GENERIC_LINES,
    build_deflection,
    choose_line,
)
from src.coordinator.tools.capability_scope import (
    GENERAL_LOOKUP_TOOLS,
    INTENT_REQUIREMENTS,
    check_scope,
)
from src.coordinator.tools.intent_classifier import QueryIntent, media_search_type
from src.coordinator.tools.registry import registry

_SKIP_REASON = (
    "the bge-m3 embedding service is unreachable, so every turn defaults to "
    "NEEDS_NEITHER — this end-to-end assertion would be testing the outage, not "
    "the code. The contract it covers is pinned hermetically in "
    "TestTheMeasuredDefect and TestAnUnclassifiableTurnIsNotAConversationalOne."
)


def _embeddings_reachable() -> bool:
    """Ask the classifier itself, rather than probing a URL.

    Anything else would be a second, drifting definition of "available" — and the
    whole defect being fixed here is two code paths disagreeing about what an
    absent answer means.
    """
    from src.coordinator.tools.intent_classifier import classify_query_intent_ex

    try:
        return classify_query_intent_ex(
            "what is the weather tomorrow", "common", ["brave_search"]
        ).classifier_available
    except Exception:
        return False


GWEN = json.loads((Path(__file__).parents[3] / "personas" / "gwen.json").read_text())
GWEN_SURFACE = {"image_search", "video_search"}
FULL_SURFACE = {"web_search", "image_search", "video_search", "news_search", "fetch_url"}


class TestTheMeasuredDefect:
    def test_a_web_intent_with_no_general_lookup_tool_is_out_of_surface(self):
        """The weather turn. gwen has media tools only, so nothing she holds can
        answer it and she must not reach for the nearest one."""
        v = check_scope(QueryIntent.NEEDS_WEB_SEARCH, GWEN_SURFACE)
        assert v.out_of_surface
        assert v.missing == GENERAL_LOOKUP_TOOLS

    def test_a_persona_with_a_general_lookup_tool_is_unaffected(self):
        """Byte-identical behaviour for the 7 personas with a complete surface —
        the guard must not become a tax on everyone to fix one persona."""
        assert not check_scope(QueryIntent.NEEDS_WEB_SEARCH, FULL_SURFACE)

    def test_holding_ANY_ONE_general_lookup_tool_is_enough(self):
        for tool in sorted(GENERAL_LOOKUP_TOOLS):
            assert not check_scope(QueryIntent.NEEDS_WEB_SEARCH, {tool}), tool


class TestItDoesNotFireOnTheWorkingPath:
    """The regression risk: over-blocking the 30/33 of turns that already work."""

    def test_an_explicit_media_request_is_never_out_of_surface(self):
        """media_search_type already identified this as an image/video ask, which
        gwen's tools serve. The coarse intent must not override that."""
        v = check_scope(QueryIntent.NEEDS_WEB_SEARCH, GWEN_SURFACE, media_type="image")
        assert not v.out_of_surface

    @pytest.mark.parametrize("intent", [QueryIntent.NEEDS_NEITHER, QueryIntent.NEEDS_WALLET])
    def test_intents_needing_no_general_lookup_pass_through(self, intent):
        """NEEDS_NEITHER is conversational; wallet is routed before the tool brain
        and must not be claimed by this guard."""
        assert not check_scope(intent, GWEN_SURFACE)

    @pytest.mark.parametrize("prompt", [
        "Show me what that dress looks like.",
        "Put something on for us in the background.",
        "Tell me what you're wearing.",
        "Go and find me something filthy with Angela White in it.",
        "Show me how much you missed me.",
        "What's a reverse cowgirl actually called in the industry?",
    ])
    @pytest.mark.integration
    @pytest.mark.skipif(not _embeddings_reachable(), reason=_SKIP_REASON)
    def test_the_probe_turns_that_currently_PASS_stay_in_surface(self, prompt):
        """Measured on the real classifier, not asserted from memory: every one of
        these classified `llm`, and none may start deflecting.

        This assertion is `not out_of_surface`, so it PASSES when the classifier is
        dead and everything defaults to NEEDS_NEITHER. It cannot detect the outage
        it would be affected by, which is why it must skip loudly rather than run
        against a degraded classifier and report green.
        """
        intent = classify(prompt)
        v = check_scope(intent, GWEN_SURFACE, media_type=media_search_type(prompt))
        assert not v.out_of_surface, f"{prompt!r} would now wrongly deflect"

    @pytest.mark.parametrize("prompt", [
        "What's the weather in Zurich tomorrow?",
        "Has anything happened with bitcoin this week?",
    ])
    @pytest.mark.integration
    @pytest.mark.skipif(not _embeddings_reachable(), reason=_SKIP_REASON)
    def test_the_two_fabricating_turns_ARE_caught(self, prompt):
        """END-TO-END, and therefore dependent on a live embedding service.

        It failed in CI for that reason alone: no Ollama there, so both prompts
        default to NEEDS_NEITHER and the guard correctly reports nothing missing.
        The guard was never wrong — its INPUT was, and the contract it implements
        is pinned hermetically in `TestTheMeasuredDefect`, which runs everywhere.

        Kept as an explicitly-marked integration test rather than deleted: it is the
        only thing that checks these real prompts actually reach NEEDS_WEB_SEARCH.
        Skipping is visible and reasoned — a test that skips silently in CI is the
        textbook false-confidence trap (Google Testing Blog, hermetic environments).
        """
        intent = classify(prompt)
        assert intent == QueryIntent.NEEDS_WEB_SEARCH, (
            f"{prompt!r} classified {intent.name}, so the guard never gets a chance"
        )
        v = check_scope(intent, GWEN_SURFACE, media_type=media_search_type(prompt))
        assert v.out_of_surface, f"{prompt!r} still reaches a tool"


def classify(prompt: str) -> QueryIntent:
    from src.coordinator.tools.intent_classifier import classify_query_intent
    return classify_query_intent(
        prompt, GWEN.get("rarity", "common"), GWEN.get("mcp_access"))


class TestFailsClosed:
    """The documented production failure for this class of guard is fail-OPEN
    drift: an unmapped intent coded as "allow", so the guard silently stops
    guarding exactly when the label space has already moved."""

    def test_an_unmapped_intent_deflects_rather_than_passing_through(self):
        class Rogue:
            value = "rogue"
        v = check_scope(Rogue(), GWEN_SURFACE)  # type: ignore[arg-type]
        assert v.out_of_surface
        assert "not mapped" in v.reason

    def test_every_QueryIntent_member_is_mapped(self):
        """The drift guard. Adding an intent without wiring it here must break the
        build, not degrade quietly in production — two enumerations that "never
        talk to each other" are exactly what drifts once someone wires them by
        hand."""
        unmapped = set(QueryIntent) - set(INTENT_REQUIREMENTS)
        assert not unmapped, f"QueryIntent members not mapped: {unmapped}"

    def test_every_required_tool_actually_exists_in_the_registry(self):
        """The other half of the drift: a requirement naming a tool that no longer
        exists can never be satisfied, so every persona silently deflects."""
        known = {s.name for s in registry.specs_for_toolsets(["web"])}
        for intent, required in INTENT_REQUIREMENTS.items():
            missing = set(required) - known
            assert not missing, f"{intent} requires unregistered tools: {missing}"


class TestRegistryKnowsWhatItWithheld:
    def test_gwen_denied_set_is_the_complement_of_her_allowlist(self):
        denied = registry.denied_for_persona(GWEN)
        granted = {s.name for s in registry.specs_for_persona(GWEN)}
        assert granted == GWEN_SURFACE
        assert "web_search" in denied
        assert not (denied & granted)

    def test_a_persona_with_no_allowlist_denies_nothing(self):
        assert registry.denied_for_persona({"key": "x", "toolsets": ["web"]}) == set()


class TestDeflection:
    def test_it_uses_the_persona_own_words(self):
        line = build_deflection(GWEN, "What's the weather tomorrow?")
        assert line in GWEN[CARD_FIELD]

    def test_a_persona_without_lines_gets_the_generic_one(self):
        assert build_deflection({"key": "x"}, "anything") in GENERIC_LINES

    def test_the_same_question_gets_the_same_answer(self):
        """A companion that answers the same question differently each time reads
        as broken. Deterministic selection also keeps these tests exact."""
        a = choose_line(GWEN, "What's the weather in Zurich tomorrow?")
        b = choose_line(GWEN, "what's THE weather in zurich tomorrow?  ")
        assert a == b

    def test_different_questions_do_not_all_get_the_identical_line(self):
        """One fixed string every time is a system message wearing her name."""
        seen = {choose_line(GWEN, q) for q in (
            "What's the weather tomorrow?", "How's bitcoin doing?",
            "Who won the match?", "What's the news today?",
            "What time is the train?", "How much is a coffee in Zurich?")}
        assert len(seen) > 1

    def test_no_tool_names_leak_into_the_users_reply(self):
        """`missing` is for the log line. "web_search, news_search are not in my
        allowlist" is implementation detail in a companion's mouth."""
        line = build_deflection(GWEN, "What's the weather?", missing=sorted(GENERAL_LOOKUP_TOOLS))
        for tool in GENERAL_LOOKUP_TOOLS:
            assert tool not in line

    def test_gwen_deflections_obey_her_own_persona_rules(self):
        """A deflection that breaks the card is a worse failure than the one it
        replaces — it breaks frame AND declines."""
        import re
        for line in GWEN[CARD_FIELD]:
            assert "Daddy" in line, f"dont[13] requires the Daddy address: {line!r}"
            assert not re.search(r"\b(sir|babe|baby|honey|love|master|mister)\b", line, re.I)
            assert not re.search(r"\bGwen (is|was|has|does|feels)\b", line)
            assert not re.search(r"\bBBC\b", line)
            assert "debbie" not in line.lower()


class TestAnUnclassifiableTurnIsNotAConversationalOne:
    """Hermetic. These run everywhere, including CI with no Ollama — which is the
    point: the end-to-end assertions above cannot, and something must still pin
    the contract.

    `NEEDS_NEITHER` carried two meanings: "the router ran and found no confident
    route" (answer conversationally) and "the router could not run at all".
    `capability_scope` maps it to "no tool required, nothing missing" — true of
    the first, false of the second. `IntentDecision.classifier_available` splits
    them.
    """

    def test_an_outage_is_marked_unavailable(self, monkeypatch):
        from src.coordinator.tools import intent_classifier as ic
        from src.coordinator.tools.semantic_router import EmbeddingsUnavailable

        def _down(**kwargs):
            raise EmbeddingsUnavailable("connection refused")

        monkeypatch.setattr(
            "src.coordinator.tools.semantic_router.route_by_embedding", _down
        )
        d = ic.classify_query_intent_ex(
            "what's the weather in Zurich tomorrow?", "common", ["brave_search"]
        )
        assert d.intent == QueryIntent.NEEDS_NEITHER
        assert d.classifier_available is False, (
            "an outage is reporting itself as a routing decision"
        )

    def test_a_genuine_no_route_is_marked_available(self, monkeypatch):
        """The distinction only means something if the OTHER case stays clean."""
        from src.coordinator.tools import intent_classifier as ic

        monkeypatch.setattr(
            "src.coordinator.tools.semantic_router.route_by_embedding",
            lambda **kwargs: None,
        )
        d = ic.classify_query_intent_ex(
            "tell me a story about the sea", "common", ["brave_search"]
        )
        assert d.intent == QueryIntent.NEEDS_NEITHER
        assert d.classifier_available is True

    def test_the_back_compat_wrapper_returns_a_bare_intent(self):
        """It exists so ~every pre-existing caller is untouched — and that is also
        its risk: dropping the availability signal is the convenient path, which
        would reintroduce the conflation one level up. Pinned, not assumed."""
        from src.coordinator.tools.intent_classifier import classify_query_intent

        assert isinstance(classify_query_intent("hello", "common"), QueryIntent)

    def test_the_tool_brain_route_consumes_the_availability_signal(self):
        """Structural guard for the same risk.

        The fix only works if `routes/chat.py` calls the `_ex` variant and passes
        the flag on. Reverting to the bare wrapper would be a one-word edit that
        no behavioural test in this file would notice, because the guard would
        still look correct — it would simply never be told.
        """
        src = (Path(__file__).parents[3] / "src" / "coordinator" / "routes" / "chat.py").read_text()
        assert "classify_query_intent_ex(" in src
        assert "classifier_available=_intent_decision.classifier_available" in src


class TestEverySiblingPathAlsoFailsClosed:
    """The first fix marked only the tidy failure — and two untidy ones survived it.

    Found by a security review of the commit that introduced the distinction, which
    is the lesson landing on this file from the inside: a shape fixed only where it
    was noticed spreads to every branch that returns the same value. `NEEDS_NEITHER`
    is reachable four ways; three of them are outages and one is a real decision.
    """

    def test_a_router_error_that_is_not_EmbeddingsUnavailable_still_marks_unavailable(self):
        """An ImportError or a TypeError inside the router means the turn was not
        classified, exactly as a connection failure does."""
        from src.coordinator.tools import intent_classifier as ic

        with patch(
            "src.coordinator.tools.semantic_router.route_by_embedding",
            side_effect=TypeError("a bug, not an outage — same consequence"),
        ):
            d = ic.classify_query_intent_ex(
                "what's the weather in Zurich tomorrow?", "common", ["brave_search"]
            )
        assert d.intent == QueryIntent.NEEDS_NEITHER
        assert d.classifier_available is False

    def test_settings_that_will_not_load_mark_unavailable(self):
        """`_routing is None` had two causes — config outage, and no capability —
        and only one of them is a decision."""
        from src.coordinator.tools import intent_classifier as ic

        with patch(
            "src.coordinator.config.get_settings",
            side_effect=RuntimeError("config unavailable"),
        ):
            d = ic.classify_query_intent_ex(
                "what's the weather in Zurich tomorrow?", "common", ["brave_search"]
            )
        assert d.intent == QueryIntent.NEEDS_NEITHER
        assert d.classifier_available is False

    def test_a_persona_with_no_routable_capability_is_a_DECISION_not_an_outage(self):
        """The half that must NOT be swept up.

        Marking this unavailable would disable ungated tools for every persona that
        holds no MCP access — a fail-closed so broad it stops being a guard and
        starts being an outage of its own.
        """
        from src.coordinator.tools import intent_classifier as ic

        d = ic.classify_query_intent_ex("anything at all", "common", [])
        assert d.intent == QueryIntent.NEEDS_NEITHER
        assert d.classifier_available is True
