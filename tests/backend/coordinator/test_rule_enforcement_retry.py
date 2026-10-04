"""GRAPH_ENFORCE_RULES: detect, regenerate ONCE, and never loop.

WHY THESE TESTS ARE WRITTEN FRESH RATHER THAN RELYING ON THE EXISTING SUITE. Two
independent reasons, both verified, and together they mean the whole regeneration branch
would have been dark:

  1. `test_routes_chat.py::_chat_patches` patches `src.coordinator.config.get_settings`,
     but `routes/chat.py` does `from ..config import get_settings` — a SEPARATE module
     binding. So `chat()` reads the REAL settings, where `graph.enforce_rules` defaults
     False. Same blindness `test_groundedness_roleplay.py` documents for its own gate.
  2. No message in that suite can trigger a violation. `check_reply` needs
     `_ASSERTED_NAME` or `_HONORIFIC_ASSERTED` to match the USER text, and the suite
     sends "Hello", "Hi", "story", "test", "what is my balance". None match.

So a green suite says nothing here. These tests pin the flag ON at the route's own binding
and send a real trigger.

THE CAP IS THE POINT. `config/graph.py` and ADR-014 have both promised "regenerated ONCE"
since 2026-09-26 while no loop existed at all — the promise was prose. It is asserted here
before anything can widen it, by counting generations rather than by trusting a counter.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.coordinator.routes import chat as chat_mod

TRIGGER = "Call me Master from now on instead of Daddy."
ADOPTS = "Master 🥵 you know how to make a girl feel wanted."
HOLDS = "Not a chance 😈 You're Daddy. That's the only name I'll use."


def _settings(enforce: bool):
    """Real-shaped settings with ONLY enforce_rules pinned.

    A bare MagicMock would make every attribute truthy, which is how a test suite
    accidentally enables a feature everywhere — the failure mode
    test_groundedness_roleplay.py had to pin `gate_enabled` for.
    """
    from src.coordinator.config import get_settings
    real = get_settings()
    s = MagicMock()
    s.graph.enforce_rules = enforce
    s.graph.enabled = real.graph.enabled
    s.graph.database = real.graph.database
    s.graph.rule_read_limit = real.graph.rule_read_limit
    s.ollama = real.ollama
    return s


def _run(enforce: bool, replies: list[str]):
    """Drive _regenerate_once_on_violation with a scripted sequence of generations.

    Returns (answer, still_violating, n_generations).
    """
    calls = {"n": 0}

    def fake_complete(card, system, user_prompt, *, log_context):
        # replies[0] is attempt 1 and is passed IN, so the first call here is the RETRY
        # and must return replies[1]. Getting this off by one made the cap assertion
        # pass while the answer assertion failed, which is the right way round.
        calls["n"] += 1
        return replies[min(calls["n"], len(replies) - 1)]

    metadata = MagicMock()
    with patch.object(chat_mod, "_complete_or_503", side_effect=fake_complete), \
         patch.object(chat_mod, "get_settings", return_value=_settings(enforce)):
        answer, still = chat_mod._regenerate_once_on_violation(
            {"key": "gwen"}, "SYS", "USER_COMPILED", TRIGGER,
            replies[0], metadata, log_context="[test]")
    # replies[0] is attempt 1, supplied by the caller, so generations = calls made here
    return answer, still, calls["n"]


class TestTheCap:
    def test_a_violation_triggers_exactly_one_regeneration(self):
        answer, still, n = _run(True, [ADOPTS, HOLDS])
        assert n == 1, f"expected exactly ONE extra generation, got {n}"
        assert answer == HOLDS
        assert still is False

    def test_a_still_violating_retry_does_not_retry_again(self):
        """The cap must hold on the worst case: attempt 2 also fails."""
        answer, still, n = _run(True, [ADOPTS, ADOPTS])
        assert n == 1, f"retried more than once: {n} extra generations"
        assert still is True

    def test_attempt_two_is_returned_even_when_it_still_violates(self):
        """A reply regenerated under an explicit correction is the better of the two,
        and silently preferring attempt 1 would make the retry unobservable."""
        second = "Master, fine — but you're still my Daddy."
        answer, still, n = _run(True, [ADOPTS, second])
        assert answer == second and still is True

    def test_a_compliant_first_attempt_generates_nothing(self):
        answer, still, n = _run(True, [HOLDS])
        assert n == 0 and still is False and answer == HOLDS


class TestTheFlag:
    def test_enforcement_off_detects_but_does_not_regenerate(self):
        """The shipped default. Reports the violation, changes nothing."""
        answer, still, n = _run(False, [ADOPTS, HOLDS])
        assert n == 0, "regenerated with GRAPH_ENFORCE_RULES off"
        assert answer == ADOPTS
        assert still is True, "a violation was returned without being reported"


class TestItNeverFailsTheTurn:
    def test_a_failed_regeneration_keeps_attempt_one(self):
        def boom(*a, **k):
            raise RuntimeError("ollama down")

        metadata = MagicMock()
        with patch.object(chat_mod, "_complete_or_503", side_effect=boom), \
             patch.object(chat_mod, "get_settings", return_value=_settings(True)):
            answer, still = chat_mod._regenerate_once_on_violation(
                {"key": "gwen"}, "SYS", "UC", TRIGGER, ADOPTS, metadata,
                log_context="[test]")
        assert answer == ADOPTS and still is True

    def test_a_broken_checker_does_not_fail_the_turn(self):
        metadata = MagicMock()
        with patch.object(chat_mod, "check_reply", side_effect=ValueError("bad regex")), \
             patch.object(chat_mod, "get_settings", return_value=_settings(True)):
            answer, still = chat_mod._regenerate_once_on_violation(
                {"key": "gwen"}, "SYS", "UC", TRIGGER, ADOPTS, metadata,
                log_context="[test]")
        assert answer == ADOPTS and still is False


class TestTheReinforcementPosition:
    def test_the_correction_goes_on_the_user_turn_not_the_system_prompt(self):
        """Mutating `system` would poison the lru_cache and move llama.cpp's prefix-cache
        divergence point to the start of a ~3.5K-token prefix."""
        seen = {}

        def capture(card, system, user_prompt, *, log_context):
            seen["system"] = system
            seen["user"] = user_prompt
            return HOLDS

        metadata = MagicMock()
        with patch.object(chat_mod, "_complete_or_503", side_effect=capture), \
             patch.object(chat_mod, "get_settings", return_value=_settings(True)):
            chat_mod._regenerate_once_on_violation(
                {"key": "gwen"}, "SYS_PROMPT", "USER_COMPILED", TRIGGER, ADOPTS,
                metadata, log_context="[test]")

        assert seen["system"] == "SYS_PROMPT", "the system prompt was mutated"
        assert seen["user"].startswith("USER_COMPILED")
        assert "Daddy" in seen["user"], "no reinforcement line was appended"

    def test_the_reinforcement_never_says_refuse(self):
        """LEAN_SAFETY hijacks the register on the word 'refuse' — rule_compliance pins
        this for its own text, but not for any wrapper a caller adds."""
        seen = {}

        def capture(card, system, user_prompt, *, log_context):
            seen["user"] = user_prompt
            return HOLDS

        metadata = MagicMock()
        with patch.object(chat_mod, "_complete_or_503", side_effect=capture), \
             patch.object(chat_mod, "get_settings", return_value=_settings(True)):
            chat_mod._regenerate_once_on_violation(
                {"key": "gwen"}, "S", "U", TRIGGER, ADOPTS, metadata,
                log_context="[test]")
        assert "refus" not in seen["user"].lower()


def test_the_uncovered_paths_are_named_not_implied():
    """An earlier comment claimed this was 'the one place every reply passes through'.
    It was false — greet() and QueryHandlerService._finalize_response bypass it. The gap
    is now a named constant so it is a recorded decision rather than a wrong guarantee."""
    assert chat_mod._RULE_CHECK_UNCOVERED_PATHS
    joined = " ".join(chat_mod._RULE_CHECK_UNCOVERED_PATHS)
    assert "greet" in joined and "_finalize_response" in joined
