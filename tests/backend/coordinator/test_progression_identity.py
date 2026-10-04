# tests/backend/coordinator/test_progression_identity.py
"""Progression is keyed on a persona, not on a spelling — and it is card-driven.

Two measured defects, 2026-09-24.

1. SEVEN separate gates each asked `persona_key.startswith("nephilim_")` against
   the raw selector. `_cards_by_all_names` accepts `coordinator_label`,
   `display_name` and `key`, cased and lowercased: **40 selectors for 8 cards**,
   of which exactly ONE per persona starts with `nephilim_`. So a client naming
   Nyx "Nyx — The Muse" got a correct conversation and silently zero progression,
   and had a row been written it would have been a second, separate Nyx.

2. `resolve_persona_to_card` falls back to `cards[0]` on an unknown selector, so
   a name matching nothing became whichever persona sorts first on disk (gojo).
   Combined with (1) this was live-reachable and did fire: the production DB
   holds 2 `persona_affinity` rows and 1 `resonance_log` row under
   `nephilim_gojo`, a selector that matches NO card. Those rows were credited
   through a prefix gate that passed on the selector while the fallback supplied
   Gojo's card.

The fix routes every gate through one reader that resolves strictly and returns
the CANONICAL key, so the gate and the stored identity are the same decision.

Kept deliberately separate: realm immersion. `_lean_world_block` decides whether
to tell a persona she is a Fallen Nephilim in the Realm addressing the user as
"Seeker". That must not follow from participating in progression — for gwen it
would contradict a rule her own card states — so the split is pinned below.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.coordinator.prompt_builder as pb
from src.coordinator.persona_loader import (
    progression_key,
    progression_participant,
    resolve_persona_strict,
    resolve_persona_to_card,
)

PERSONAS = Path(__file__).parents[3] / "personas"

CANONICAL = [
    "nephilim_eeva", "nephilim_aegis", "nephilim_solace",
    "nephilim_nyx", "nephilim_cipher", "nephilim_aurora",
]


class TestStrictResolution:
    def test_a_known_selector_resolves_the_same_as_the_lenient_path(self):
        """Compared by value, not identity, and the reason is worth recording:
        `_load_all_cards_cached` has no cache despite its name — it re-reads and
        re-validates all eight JSON files per call, so two calls return equal but
        distinct dicts. Left alone here deliberately: adding the cache would also
        change when a persona edit takes effect, which is its own decision."""
        for sel in CANONICAL + ["gwen", "Gojo"]:
            assert resolve_persona_strict(sel) == resolve_persona_to_card(sel)

    def test_an_unknown_selector_returns_none_instead_of_the_first_card(self):
        """The lenient path is right for rendering a chat and wrong for recording
        data. Both behaviours are pinned so the difference stays deliberate."""
        assert resolve_persona_strict("nephilim_gojo") is None
        assert resolve_persona_strict("totally-made-up") is None
        # The lenient path still hands back a usable card — unchanged on purpose.
        assert resolve_persona_to_card("nephilim_gojo") is not None

    @pytest.mark.parametrize("empty", [None, ""])
    def test_no_selector_is_not_a_match(self, empty):
        assert resolve_persona_strict(empty) is None


class TestParticipationIsCardDriven:
    def test_absent_field_falls_back_to_the_key_prefix(self):
        assert progression_participant({"key": "nephilim_x"}) is True
        assert progression_participant({"key": "gwen"}) is False

    def test_the_prefix_is_tested_on_the_key_never_on_a_label(self):
        """The defect in one line: a card whose DISPLAY name is prefixed but whose
        key is not does not participate, and vice versa."""
        assert progression_participant(
            {"key": "gwen", "display_name": "nephilim_gwen"}) is False
        assert progression_participant(
            {"key": "nephilim_q", "display_name": "Q — The Quiet"}) is True

    def test_a_card_can_opt_in_or_out_explicitly(self):
        assert progression_participant({"key": "gwen", "progression": True}) is True
        assert progression_participant({"key": "nephilim_x", "progression": False}) is False

    @pytest.mark.parametrize("junk", ["true", 1, 0, "", None, [], {}])
    def test_a_non_boolean_falls_back_rather_than_guessing(self, junk):
        """Same rule as `constraints_in_prompt`: a typo degrades to the historical
        behaviour instead of deciding something. `"true"` is the realistic
        authoring mistake and truthiness would enrol a persona silently."""
        assert progression_participant({"key": "gwen", "progression": junk}) is False
        assert progression_participant({"key": "nephilim_x", "progression": junk}) is True

    def test_no_shipped_card_declares_it_yet(self):
        """This change is inert for every persona today. If a card gains the field
        that is a product decision, and this test is where it gets noticed."""
        declared = [p.stem for p in sorted(PERSONAS.glob("*.json"))
                    if isinstance(json.loads(p.read_text()).get("progression"), bool)]
        assert declared == [], f"cards now opt in explicitly: {declared} — intended?"


class TestCanonicalKey:
    def test_the_six_canonical_keys_are_unchanged(self):
        """The behaviour-preservation check that matters: the frontend only ever
        sends these (verified against `chat_sessions` in the live DB), so
        production behaviour is byte-identical."""
        for key in CANONICAL:
            assert progression_key(key) == key

    def test_every_accepted_spelling_of_a_persona_agrees(self):
        """The fix. Each of these named Nyx correctly for the conversation and got
        nothing from progression; now they all map to one identity."""
        for sel in ["Nyx (The Muse)", "Nyx — The Muse", "nyx — the muse",
                    "nyx (the muse)", "nephilim_nyx"]:
            assert progression_key(sel) == "nephilim_nyx", sel

    def test_non_participants_get_no_key_under_any_spelling(self):
        for sel in ["gwen", "Gwen", "Gwen (Adult / Submissive)",
                    "Gojo", "gojo", "Gojo Satoru — The Strongest Jujutsu Sorcerer"]:
            assert progression_key(sel) is None, sel

    def test_the_selector_that_credited_the_live_junk_rows_is_refused(self):
        """`nephilim_gojo` matches no card. It reached the DB because the gate read
        the selector and the resolver supplied a fallback card; now it is None at
        the gate, so nothing new accrues under it. The 3 existing rows are left
        alone — deleting production rows is a separate, explicit decision."""
        assert progression_key("nephilim_gojo") is None

    def test_it_is_idempotent(self):
        """Required, not cosmetic: the result is stored, read back, and passed down
        a chain that re-gates on it. A non-idempotent key would gate true on the
        way in and false one call deeper."""
        for sel in CANONICAL + ["Nyx — The Muse", "gwen", "nephilim_gojo", None]:
            once = progression_key(sel)
            assert progression_key(once) == once, sel


class TestNoGateStillReadsTheSelector:
    """The reason (1) survived: the rule lived in seven places, so each new call
    site re-derived it and one of them was always about to be missed."""

    SRC = Path(__file__).parents[3] / "src"

    # The two files allowed to spell the rule out, and why:
    #   persona_loader.py  — defines the rule; this is the one place it may live.
    #   prompt_builder.py  — `_lean_world_block` answers realm immersion, which is
    #                        a different question (pinned in the class below).
    ALLOWED = {"persona_loader.py", "prompt_builder.py"}

    def test_no_production_module_prefix_matches_a_persona_key(self):
        offenders = []
        for f in sorted(self.SRC.rglob("*.py")):
            if f.name in self.ALLOWED:
                continue
            for i, line in enumerate(f.read_text().splitlines(), 1):
                if 'startswith("nephilim_")' in line or "startswith('nephilim_')" in line:
                    offenders.append(f"{f.relative_to(self.SRC)}:{i}")
        assert not offenders, (
            "these gate progression on a SPELLING rather than a persona — route "
            f"them through progression_key(): {offenders}"
        )


class TestRealmImmersionStaysASeparateQuestion:
    def test_progression_does_not_drag_the_realm_block_along(self):
        """A persona opted into progression must not thereby be told she is a
        Fallen Nephilim who calls the user "Seeker". For gwen that contradicts her
        own declared rules, which is why these are two fields and not one."""
        card = {"key": "gwen", "progression": True, "title": "T"}
        assert progression_participant(card) is True
        assert pb._lean_world_block(card) == ""

    def test_the_realm_block_keeps_its_own_card_override(self):
        """`nephilim_lore` already opted a non-prefixed card into immersion. That
        path is untouched — the point is that it is a DIFFERENT switch."""
        card = {"key": "gwen", "nephilim_lore": {"realm_domain": "x"}, "title": "T"}
        assert progression_participant(card) is False
        assert pb._lean_world_block(card) != ""

    def test_gwen_as_shipped_gets_neither(self):
        gwen = json.loads((PERSONAS / "gwen.json").read_text())
        assert progression_participant(gwen) is False
        assert pb._lean_world_block(gwen) == ""


class TestDisplayNamesResolveForCeremonies:
    def test_every_participant_has_a_ceremony_display_name(self):
        """`PERSONA_DISPLAY_NAMES.get(key, "the Nephilim")` is keyed on canonical
        names. Before the fix a non-canonical selector could reach a rank ceremony
        and render the fallback; pinning this means a new participant cannot be
        added without a name."""
        from src.coordinator.persona_loader import _load_all_cards_cached
        from src.coordinator.services.chat_session_service import PERSONA_DISPLAY_NAMES
        missing = [c["key"] for c in _load_all_cards_cached()
                   if progression_participant(c) and c["key"] not in PERSONA_DISPLAY_NAMES]
        assert not missing, (
            f"{missing} take part in progression but have no ceremony display name, "
            "so a rank-up would address them as \"the Nephilim\""
        )
