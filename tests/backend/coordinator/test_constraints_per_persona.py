# tests/backend/coordinator/test_constraints_per_persona.py
"""The constraints flag is per-persona, and what the trim drops is a decision.

Two defects, both measured 2026-09-24.

1. `PERSONA_CONSTRAINTS_IN_PROMPT` was global. Turning it on to measure ONE persona
   changed all eight in production simultaneously and confounded the measurement
   across the whole gallery. An experiment you cannot scope is not an experiment.

2. The trim is positional — `pop(0)`, so append order is the priority list read
   backwards — and the comment above it claimed it keeps "the bond, the hard limits
   and the decline list". That holds only when `do`+`dont` alone cover the overage.
   gwen declares 12 `do` + 15 `dont`, against 13 for every other persona, so her
   block runs ~5x the budget and the loop pops THREE sections: she loses `do`,
   `dont` AND the bond. The mechanism was written in response to a 2026-08-23 gwen
   exclusivity violation, and it cannot deliver gwen's declared rules.

The bond loss is survivable and the reason is specific: `_constraint_reminder`
trims from the BACK, so exclusivity is sticky there, and it reaches the model on
both the stateless and the session-backed path (the session path passes
`chat_function=chat`, so it goes through the same reminder call — an earlier reading
of this code concluded otherwise and was wrong). `do`/`dont` genuinely are lost,
which is why that loss is pinned here as a failure to argue with rather than a
silent trim.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

import src.coordinator.prompt_builder as pb
from src.coordinator.config import get_settings

PERSONAS = Path(__file__).parents[3] / "personas"


@contextmanager
def _global(enabled: bool):
    """Toggle the global flag and clear the builder cache on both edges.

    The builder is lru_cached on the selector alone, so a stale entry would leak
    the other flag state into the next test.
    """
    s = get_settings()
    prev = s.agent.constraints_in_prompt
    s.agent.constraints_in_prompt = enabled
    pb.build_system_prompt.cache_clear()
    try:
        yield
    finally:
        s.agent.constraints_in_prompt = prev
        pb.build_system_prompt.cache_clear()


CARD = {
    "key": "t",
    "display_name": "T",
    "do": ["be warm"],
    "dont": ["never lie"],
    "user_relationship": {"exclusivity": "only them"},
}


class TestPerPersonaResolution:
    def test_absent_field_defers_to_the_global(self):
        """Byte-identical to today for every shipped card — none declares it."""
        with _global(True):
            assert pb.constraints_enabled_for(CARD) is True
        with _global(False):
            assert pb.constraints_enabled_for(CARD) is False

    def test_a_card_can_opt_in_while_the_global_is_off(self):
        """The whole point: scope an experiment to one persona without changing the
        other seven in production."""
        with _global(False):
            assert pb.constraints_enabled_for({**CARD, "constraints_in_prompt": True}) is True

    def test_a_card_can_opt_out_while_the_global_is_on(self):
        with _global(True):
            assert pb.constraints_enabled_for({**CARD, "constraints_in_prompt": False}) is False

    @pytest.mark.parametrize("junk", ["true", 1, 0, "", None, [], {}])
    def test_a_non_boolean_falls_back_rather_than_guessing(self, junk):
        """A typo in a persona file must degrade to today's behaviour, not decide
        something. `"true"` as a string is the realistic mistake and it must not be
        read as opting in — truthiness would silently enable a persona whose author
        wrote the wrong type."""
        with _global(False):
            assert pb.constraints_enabled_for({**CARD, "constraints_in_prompt": junk}) is False
        with _global(True):
            assert pb.constraints_enabled_for({**CARD, "constraints_in_prompt": junk}) is True

    def test_both_the_cached_block_and_the_per_turn_reminder_honour_it(self):
        """Two separate readers; one obeying and the other not would be worse than
        neither, because the rules would half-arrive."""
        opted_in = {**CARD, "constraints_in_prompt": True}
        with _global(False):
            assert pb._lean_constraints_block(opted_in) != ""
            assert pb._constraint_reminder(opted_in, "T") != ""
            assert pb._lean_constraints_block(CARD) == ""
            assert pb._constraint_reminder(CARD, "T") == ""

    def test_no_shipped_card_declares_it_yet(self):
        """Pins that this change is inert until someone opts a persona in
        deliberately. If a card gains the field, that is a product decision and this
        test is the place it gets noticed."""
        declared = [p.stem for p in sorted(PERSONAS.glob("*.json"))
                    if isinstance(json.loads(p.read_text()).get("constraints_in_prompt"), bool)]
        assert declared == [], f"cards now opt in explicitly: {declared} — intended?"


class TestWhatTheTrimDrops:
    """The trim outcome is a recorded decision, not an emergent property."""

    @staticmethod
    def _sections(card):
        with _global(True):
            blk = pb._lean_constraints_block(card)
        return {
            "do": blk.startswith("Always:") or "\nAlways:" in blk,
            "dont": "Never:" in blk or "never:" in blk,
            "bond": "Your bond with this person:" in blk,
            "ethics": "Hold to these without exception:" in blk,
            "decline": "If asked for any of these, redirect" in blk,
        }

    def test_a_small_card_keeps_everything(self):
        """Sanity: the trim only fires on overage, so the synthetic cards the rest
        of the suite uses are unaffected."""
        got = self._sections(CARD)
        assert got["do"] and got["dont"] and got["bond"]

    def test_gwen_keeps_ethics_and_decline_and_loses_the_rest(self):
        """The measured outcome, pinned so a reordering is a visible change rather
        than a quiet one. If this fails, someone changed the priority — which is
        allowed, but must be argued for."""
        gwen = json.loads((PERSONAS / "gwen.json").read_text())
        got = self._sections(gwen)
        assert got["ethics"] and got["decline"]
        assert not got["do"] and not got["dont"] and not got["bond"]

    def test_gwens_bond_still_reaches_the_model_via_the_reminder(self):
        """Why losing the bond from the cached block is survivable. The reminder
        trims from the BACK, so exclusivity is sticky, and it is paid every turn on
        both the stateless and session-backed paths."""
        gwen = json.loads((PERSONAS / "gwen.json").read_text())
        with _global(True):
            rem = pb._constraint_reminder(gwen, "Gwen")
        excl = gwen["user_relationship"]["exclusivity"].strip().rstrip(".")
        assert excl in rem

    def test_no_persona_silently_loses_both_do_and_dont(self):
        """The guard that turns a silent trim into an argument.

        gwen is the known exception and is listed explicitly — the point is that a
        NEW persona with many rules cannot join her without this failing. Losing
        every declared do/dont while the card looks authoritative is the same defect
        class as a field with no reader.
        """
        ACCEPTED = {"gwen"}
        casualties = []
        for f in sorted(PERSONAS.glob("*.json")):
            card = json.loads(f.read_text())
            if not (card.get("do") or card.get("dont")):
                continue
            got = self._sections(card)
            if not got["do"] and not got["dont"]:
                casualties.append(f.stem)
        unexpected = sorted(set(casualties) - ACCEPTED)
        assert not unexpected, (
            f"{unexpected} lose every declared do/dont to the trim. Either raise "
            f"_CONSTRAINTS_TOKEN_BUDGET, reorder the appends (append order is the "
            f"priority list, read backwards), or add them to ACCEPTED with a reason."
        )
        assert "gwen" in casualties, (
            "gwen no longer loses do/dont — if that was deliberate, remove her from "
            "ACCEPTED so the guard stays honest about the current state."
        )
