"""The persona's behavioural constraints must actually reach the model.

`do`, `dont`, `boundaries`, `user_relationship` and
`escalation_policy.when_to_decline` had zero readers anywhere in `src/`.
`persona_schema.py` even documents the workaround in a comment: "the only
reliable lever for word choice, since the lean prompt omits do/dont". A persona
could declare exclusivity and the model was simply never told — which is the
constraint violation observed in production on 2026-08-23.

Everything here is gated on `PERSONA_CONSTRAINTS_IN_PROMPT` (default OFF), so
the first test in each group pins that OFF stays byte-identical.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from src.coordinator import prompt_builder as pb
from src.coordinator.config import get_settings


# The persona identity is the only part of the prompt that needs a model, and
# none of these tests assert on it.
_STUB_IDENTITY = "A stubbed identity brief. Not asserted on by any test here."


@pytest.fixture(autouse=True)
def _identity_without_a_live_model(monkeypatch):
    """Render prompts without reaching Ollama.

    `_build_system_prompt_lean` resolves the identity as
    ``get_or_build_cv_summary(selector).get("summary") or _summarize(...)``, and
    both sides of that `or` end in a live model call via `_llm()`, which asserts
    the model is reachable before anything is generated.

    A cache hides this on any machine that has run the app. On a fresh checkout
    the cache is always cold, so CI reached a live Ollama and these tests failed
    on every run from 2026-08-23 — 6 failed, 2222 passed — for a month, while
    passing locally the whole time.

    They assert on prompt STRUCTURE: whether the constraints block appears,
    whether examples are included, what order the sections come in. Pinning the
    identity text keeps every one of those assertions intact and removes the
    network. Skipping them instead (`@requires_ollama`) would have been cheaper
    and would have discarded the coverage that matters most — one of these tests
    exists because its assertion was False in production.

    The cache is cleared on both sides: `_build_system_prompt_lean` is
    `lru_cache`d on the selector alone, so a stubbed prompt would otherwise leak
    into the next test and a real one into this.
    """
    monkeypatch.setattr(
        pb, "get_or_build_cv_summary", lambda selector: {"summary": _STUB_IDENTITY}
    )
    pb._build_system_prompt_lean.cache_clear()
    yield
    pb._build_system_prompt_lean.cache_clear()


@contextmanager
def constraints(enabled: bool):
    """Toggle the flag and clear the prompt cache on both sides.

    The builder is lru_cached on the persona selector alone, so a stale entry
    would leak the other flag state into the next test.
    """
    original = get_settings().agent.constraints_in_prompt
    get_settings().agent.constraints_in_prompt = enabled
    pb._build_system_prompt_lean.cache_clear()
    try:
        yield
    finally:
        get_settings().agent.constraints_in_prompt = original
        pb._build_system_prompt_lean.cache_clear()


CARD = {
    "key": "test",
    "display_name": "Tester",
    "do": ["Speak plainly", "Ask before assuming"],
    "dont": ["Never break character", "Avoid corporate jargon"],
    "boundaries": {
        "ethics": ["consent is foundational"],
        "content": ["explicit content required", "content involving harm"],
        "personal": ["embrace who you are"],
    },
    "user_relationship": {
        "role": "They are your partner",
        "dynamic": "close and teasing",
        "exclusivity": "You belong to them alone",
    },
    "escalation_policy": {
        "when_to_decline": ["scenarios involving other people", "medical advice"]
    },
}


# ─── the flag is genuinely inert when off ────────────────────────────────────


def test_block_is_empty_when_flag_off():
    with constraints(False):
        assert pb._lean_constraints_block(CARD) == ""
        assert pb._constraint_reminder(CARD, "Tester") == ""


def test_rendered_prompt_is_unchanged_when_flag_off():
    with constraints(False):
        off = pb.build_system_prompt("gwen")
    assert "<constraints>" not in off
    assert "exclusiv" not in off.lower()


# ─── the constraint actually reaches the prompt ──────────────────────────────


def test_exclusivity_reaches_the_rendered_prompt():
    """The defect in one assertion: this was False in production."""
    with constraints(True):
        assert "exclusiv" in pb.build_system_prompt("gwen").lower()


def test_block_renders_each_declared_field():
    with constraints(True):
        block = pb._lean_constraints_block(CARD)
    assert "Speak plainly" in block
    assert "You belong to them alone" in block
    assert "consent is foundational" in block
    assert "medical advice" in block


def test_negations_are_reframed_not_listed_verbatim():
    """Open models violate negated instructions far more often than affirmative
    ones, so the prohibitions are anchored under one affirmative stem rather
    than emitted as N separate "Never ..." lines."""
    with constraints(True):
        block = pb._lean_constraints_block(CARD)
    assert "This means never: break character" in block
    assert "Never break character" not in block  # the raw negation is gone
    assert block.count("Never") + block.count("never") == 1


def test_dont_without_do_still_states_the_prohibition():
    with constraints(True):
        block = pb._lean_constraints_block({"dont": ["Never lie"]})
    assert "Never: lie" in block


# ─── boundaries.content is deliberately excluded ─────────────────────────────


def test_boundaries_content_is_not_rendered():
    """`content` is a capability declaration with mixed polarity — most entries
    read as allowances while some are prohibitions carrying no negation marker.
    Rendering it as instructions would assert the opposite of the author's
    intent for those entries."""
    with constraints(True):
        block = pb._lean_constraints_block(CARD)
    assert "explicit content required" not in block
    assert "content involving harm" not in block
    assert "consent is foundational" in block  # ...but ethics is rendered


# ─── budgets ─────────────────────────────────────────────────────────────────


def test_block_respects_its_token_ceiling():
    fat = dict(CARD, do=["a very long instruction indeed " * 8] * 12)
    with constraints(True):
        block = pb._lean_constraints_block(fat)
    assert int(len(block.split()) * 1.33) <= pb._CONSTRAINTS_TOKEN_BUDGET


def test_reminder_respects_its_tighter_ceiling():
    fat = dict(
        CARD,
        escalation_policy={"when_to_decline": ["an extremely verbose refusal topic " * 10] * 5},
    )
    with constraints(True):
        reminder = pb._constraint_reminder(fat, "Tester")
    assert int(len(reminder.split()) * 1.33) <= pb._REMINDER_TOKEN_BUDGET


def test_exclusivity_survives_reminder_trimming():
    """Trimming drops the decline list, never the bond — that is the line a
    violation actually turns on."""
    fat = dict(
        CARD,
        escalation_policy={"when_to_decline": ["a very long topic to decline " * 20]},
    )
    with constraints(True):
        reminder = pb._constraint_reminder(fat, "Tester")
    assert "You belong to them alone" in reminder


# ─── empty / malformed cards ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "card",
    [
        {},
        {"do": [], "dont": []},
        {"do": None, "boundaries": "not-a-dict", "user_relationship": []},
        {"escalation_policy": {"when_to_decline": [None, "", "   "]}},
    ],
)
def test_absent_or_malformed_fields_render_nothing(card):
    with constraints(True):
        assert pb._lean_constraints_block(card) == ""
        assert pb._constraint_reminder(card, "X") == ""


def test_persona_without_constraints_gets_no_section():
    """A persona declaring none of these fields must be byte-identical whether
    the flag is on or off."""
    bare = {"key": "bare", "display_name": "Bare"}
    with constraints(True):
        assert pb._lean_constraints_block(bare) == ""


# ─── ordering ────────────────────────────────────────────────────────────────


def test_voice_last_ordering_is_preserved():
    """<voice_examples> must stay the final block — it is the deliberate
    recency re-anchor and the highest-leverage slot for voice."""
    with constraints(True):
        prompt = pb.build_system_prompt("gwen")
    assert prompt.index("<constraints>") < prompt.index("<safety>")
    assert prompt.index("<voice_examples>") > prompt.index("<safety>")


def test_strip_negation_handles_common_prefixes():
    assert pb._strip_negation("Never do X") == "do X"
    assert pb._strip_negation("Don't do X") == "do X"
    assert pb._strip_negation("Avoid doing X") == "doing X"
    assert pb._strip_negation("Do not do X") == "do X"
    # a line that is not phrased as a prohibition is left alone
    assert pb._strip_negation("Stay in character") == "Stay in character"
