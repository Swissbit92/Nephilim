"""ADR-007 × ADR-022 — the gate must know a recited fact from an invented one.

🔴 THE DEFECT THIS PINS, measured on the live backend 2026-10-18. With the
numbered recall frame injecting 32 curated facts, the gate destroyed three
CORRECT answers and replaced each with "I don't know — want me to search?":

    "You read, play GTA 5, and hit the gym"           -> facts 6, 7, 8
    "No way, I know how much you hate meat"           -> fact 2
    "You've been thinking about quitting your job..." -> fact 16

`check()` received only the question and the draft, so it had no way to tell
them apart — and `_FLAG_LIVE_STATE` explicitly covers "the user's own state",
which is exactly what the frame answers.

⚠️ The ADR-022 A/B measured 0.208 false refusals with the gate NOT in the path
(its harness called the model directly), so that number never described
production. Recorded because it is the same class as quoting a single-turn
probe rate.
"""
from __future__ import annotations

import pytest

from coordinator.context_framing import (
    frame_injected_context,
    frame_numbered_facts,
    recalled_facts_for_grounding,
)
from coordinator.services.groundedness_gate_service import (
    _CLASSIFIER_SYSTEM,
    GroundednessGateService,
    _classifier_system,
)


@pytest.fixture(autouse=True)
def _gate_on(monkeypatch):
    """The gate is OFF by default in the hermetic env, so every check() returns
    `flag_off` before reaching the classifier. Without this the tests below pass
    vacuously — they would assert nothing about grounding at all.
    """
    from coordinator.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("GROUNDEDNESS_GATE_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _Client:
    """Records the system prompt the classifier was given."""

    def __init__(self, verdict: str = "NO"):
        self.verdict, self.system, self.user = verdict, None, None

    def complete(self, system: str, user_prompt: str) -> str:
        self.system, self.user = system, user_prompt
        return self.verdict


class TestTheDormantPathIsUnchanged:
    def test_no_grounding_prompt_is_byte_identical_to_the_legacy_constant(self):
        assert _classifier_system(grounding="") == _CLASSIFIER_SYSTEM

    def test_no_grounding_does_not_add_the_recall_exclusion(self):
        assert "SUPPORTED BY the remembered facts" not in _classifier_system(grounding="")

    def test_the_prose_frame_supplies_no_grounding(self):
        """The prose path is unmeasured here and must keep today's behaviour."""
        prose = frame_injected_context("gwen", None, "He likes cats.")
        assert recalled_facts_for_grounding(prose) == ""

    def test_absent_context_supplies_no_grounding(self):
        assert recalled_facts_for_grounding(None) == ""
        assert recalled_facts_for_grounding("") == ""


class TestTheGateIsToldWhatWasRecalled:
    def test_the_numbered_facts_reach_the_classifier_prompt(self):
        frame = frame_numbered_facts("gwen", None, ["Is vegetarian", "Plays GTA 5"])
        c = _Client("NO")
        GroundednessGateService(c).check(
            "do i eat meat?", "No way, I know how much you hate meat.",
            grounding=recalled_facts_for_grounding(frame),
        )
        assert "[1] Is vegetarian" in c.system
        assert "[2] Plays GTA 5" in c.system
        assert "SUPPORTED BY the remembered facts" in c.system

    def test_support_is_judged_by_meaning_not_wording(self):
        """The three live failures were all paraphrases, not quotes."""
        sys_prompt = _classifier_system(grounding=" [1] Is vegetarian")
        assert "MEANING, not wording" in sys_prompt
        assert "hate meat" in sys_prompt and "is vegetarian" in sys_prompt

    def test_the_persona_card_is_never_handed_over_as_grounding(self):
        """Grounding is the facts, never the whole prompt.

        Passing the system prompt would make every in-character claim
        unflaggable — the opposite of the gate's purpose.
        """
        frame = frame_numbered_facts("gwen", None, ["Is vegetarian"])
        g = recalled_facts_for_grounding(
            "You are gwen, a succubus. Your lore: the Confluence.\n\n" + frame
        )
        assert "succubus" not in g and "Confluence" not in g
        assert "[1] Is vegetarian" in g

    def test_the_gate_still_abstains_when_the_classifier_says_yes(self):
        """Grounding must not disable the gate — only inform it."""
        frame = frame_numbered_facts("gwen", None, ["Is vegetarian"])
        v = GroundednessGateService(_Client("YES")).check(
            "what did bitcoin close at?", "Bitcoin closed at $91,204 today.",
            grounding=recalled_facts_for_grounding(frame),
        )
        assert v.should_abstain is True
        assert v.reason == "ungrounded"

    def test_it_still_fails_open_on_a_classifier_error(self):
        class _Boom:
            def complete(self, system, user_prompt):
                raise RuntimeError("ollama down")

        v = GroundednessGateService(_Boom()).check("q", "a", grounding=" [1] x")
        assert v.should_abstain is False
        assert v.reason == "classifier_error_fail_open"


class TestTheRouteWiresAllThreeCallSites:
    def test_every_gate_call_site_passes_the_context(self):
        """A gate that is grounding-aware but never GIVEN the grounding is inert.

        This repo has shipped exactly that shape before: a hard wall whose retry
        sat on a branch production never returned from. Assert the call sites,
        not just the capability.
        """
        import inspect

        from coordinator.routes import chat as chat_route

        src = inspect.getsource(chat_route)
        calls = [ln for ln in src.splitlines() if "_apply_groundedness_gate(" in ln
                 and "def " not in ln]
        assert len(calls) == 3, f"expected 3 call sites, found {len(calls)}: {calls}"
        assert src.count("extra_system_context', None))") == 3, (
            "every call site must forward the injected context"
        )
