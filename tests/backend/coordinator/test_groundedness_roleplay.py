"""The groundedness gate must not fire on fiction the character is writing.

The gate runs a second classifier on every draft that made no tool call —
which is every roleplay turn. Its exclusion list already covered a persona's
BACKSTORY, but not the scene the persona is narrating right now. A companion
writing "I'm on my knees, my hands trembling" is composing fiction in the first
person, yet the surface form is a specific, present-tense, falsifiable-sounding
claim about a current state: exactly the shape the flag clauses describe. The
gate fired mid-scene and replaced the reply with an offer to search
(observed 2026-08-23).

The tests here run with the gate explicitly ENABLED. The chat-route suite pins
`gate_enabled=False` in its shared fixture, so every gate path there is dark —
which is how this defect reached production with a green suite. The catch case
from the 2026-08-12 incident is regression-pinned below so that narrowing the
exclusion cannot quietly widen into a false negative.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.coordinator.config import get_settings
from src.coordinator.services.groundedness_gate_service import (
    GroundednessGateService,
    _classifier_system,
)


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def gate_enabled(monkeypatch):
    monkeypatch.setenv("GROUNDEDNESS_GATE_ENABLED", "true")
    get_settings.cache_clear()


def _svc(verdict="NO"):
    llm = MagicMock()
    llm.complete.return_value = verdict
    return GroundednessGateService(llm_client=llm), llm


# ─── the classifier prompt ───────────────────────────────────────────────────


def test_prompt_excludes_in_scene_narration():
    prompt = _classifier_system()
    assert "IN-SCENE NARRATION" in prompt
    assert "creative writing being composed" in prompt


def test_backstory_exclusion_is_kept_not_replaced():
    """The pre-existing lore clause still has work to do; the scene clause is
    additional coverage, not a swap."""
    prompt = _classifier_system()
    assert "backstory" in prompt
    assert "IN-SCENE NARRATION" in prompt


def test_live_state_clause_is_scoped_to_real_accounts():
    """Un-scoped, 'the user's own current state' reads as covering a character
    describing its own body. Naming money and accounts keeps the clause aimed
    at what it was added for."""
    prompt = _classifier_system(live_state=True)
    assert "REAL ACCOUNT OR PORTFOLIO STATE" in prompt
    assert "a character describing" in prompt


def test_live_state_clause_still_removable():
    """The A/B revert path from 2026-08-12 must survive this change."""
    assert "REAL ACCOUNT OR PORTFOLIO STATE" not in _classifier_system(live_state=False)
    assert "IN-SCENE NARRATION" in _classifier_system(live_state=False)


# ─── behaviour with the gate actually ON ─────────────────────────────────────


def test_gate_runs_on_a_no_tool_roleplay_turn(gate_enabled):
    """Not a no-op: the classifier really is consulted on every such draft,
    which is why its wording matters so much."""
    svc, llm = _svc("NO")
    svc.check(user_turn="come here", drafted_response="I lean against the doorway, watching you.")
    llm.complete.assert_called_once()


def test_scene_narration_passes_when_classifier_says_no(gate_enabled):
    svc, _ = _svc("NO")
    verdict = svc.check(
        user_turn="describe how you feel",
        drafted_response="My hands are trembling and my heart is racing.",
    )
    assert verdict.should_abstain is False


def test_real_live_state_claim_still_flagged(gate_enabled):
    """Regression pin for the 2026-08-12 incident. Narrowing the exclusion must
    not turn the gate off for the case it was built to catch: an unfetched
    claim about the user's actual portfolio."""
    svc, _ = _svc("YES")
    verdict = svc.check(
        user_turn="how am I doing?",
        drafted_response="Your position is currently unhedged and you're down 4% on the week.",
    )
    assert verdict.should_abstain is True


def test_the_classifier_sees_the_scene_exclusion(gate_enabled):
    """The exclusion is worthless if it never reaches the classifier call."""
    svc, llm = _svc("NO")
    svc.check(user_turn="sit with me", drafted_response="I sit down beside you.")
    sent = " ".join(str(a) for a in llm.complete.call_args.args) + " ".join(
        str(v) for v in llm.complete.call_args.kwargs.values()
    )
    assert "IN-SCENE NARRATION" in sent
