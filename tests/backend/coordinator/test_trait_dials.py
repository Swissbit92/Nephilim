"""ADR-016 — one persona dial wired end to end, to find out whether a dial can move behaviour.

Before this, `emotional_profile.sliders` was read by exactly one thing in the repo: a
Pydantic range validator. Five of the seven dial names appeared NOWHERE in `src/`.

Two of these tests exist because the thing they assert was watched FAILING first, which
is the only reason a green test here carries information:

  * ``test_a_dial_change_does_not_touch_the_identity_block`` — sliders were inside the
    CV-summary fingerprint, so changing ANY dial invalidated the cached summary, an LLM
    regenerated <identity>, and her self-description changed CONTENT. At assertiveness
    0.0 she opened "I'm Gwen, and I live for one thing"; at 1.0 "I'm Gwen, a data
    analyst by day, but my true passion lies in the art of devotion". Nothing read the
    dial — the whole difference was regeneration noise, and it would have been
    attributed to the dial by any A/B test run before this was fixed.
  * ``test_off_is_byte_identical`` — the state the flag has to preserve.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.coordinator import prompt_builder as pb
from src.coordinator.cv_summarizer import _legacy_fingerprint, _normalize_for_fingerprint

CARDS = Path(__file__).parents[3] / "personas"
GWEN = CARDS / "gwen.json"


@pytest.fixture
def card():
    return json.loads(GWEN.read_text())


def _block(card: dict) -> str:
    return pb._lean_companion_block(card)


# ─────────────────────────────────────────────────────────────
# The bucket mapping
# ─────────────────────────────────────────────────────────────

class TestRenderDial:
    def test_is_total_and_never_raises(self):
        for bad in [None, "x", -0.1, 1.5, float("nan"), [], {}]:
            assert pb.render_dial("assertiveness", bad) == ""

    def test_unwired_dials_render_nothing(self):
        """Silence is the honest rendering for a dial whose effect is unmeasured."""
        for name in ("warmth", "playfulness", "skepticism", "competitiveness",
                     "manipulativeness", "sluttiness"):
            assert pb.render_dial(name, 0.9) == ""

    @pytest.mark.parametrize("scale", ["narrow", "wide"])
    def test_five_buckets_no_more_no_fewer(self, scale):
        seen = {pb.render_dial("assertiveness", v / 100, scale) for v in range(0, 101)}
        assert len(seen) == 5, sorted(seen)

    @pytest.mark.parametrize("scale", ["narrow", "wide"])
    def test_is_monotone_in_bucket_index(self, scale):
        """0.7 must land between 0.5 and 0.9, never outside them."""
        order = [pb.render_dial("assertiveness", v, scale) for v in (0.0, 0.3, 0.5, 0.7, 0.9)]
        assert len(set(order)) == 5
        for v in (0.1, 0.35, 0.55, 0.75, 0.95):
            assert pb.render_dial("assertiveness", v, scale) in order

    @pytest.mark.parametrize("scale", ["narrow", "wide"])
    def test_never_emits_the_raw_number_or_the_dial_name(self, scale):
        """A dial is a behavioural instruction, not a label or a float."""
        for v in (0.0, 0.3, 0.5, 0.7, 1.0):
            line = pb.render_dial("assertiveness", v, scale)
            assert "assertive" not in line.lower()
            assert str(v) not in line

    def test_every_bucket_is_an_instruction_not_a_description(self):
        """Measured in ADR-014: positive behavioural instructions moved a rule that
        prohibitions and adjectives could not."""
        for _edge, text in pb._ASSERTIVENESS_NARROW + pb._ASSERTIVENESS_WIDE:
            first = text.split()[0].rstrip(".,")
            assert first[0].isupper() and not first.lower().startswith("you"), text


# ─────────────────────────────────────────────────────────────
# The flag contract
# ─────────────────────────────────────────────────────────────

class TestFlagContract:
    def test_off_is_byte_identical(self, card):
        """No dial reaches any prompt while the flag is off and no card opts in."""
        outs = set()
        for v in (0.0, 0.5, 1.0):
            c = json.loads(json.dumps(card))
            c["emotional_profile"]["sliders"]["assertiveness"] = v
            outs.add(_block(c))
        assert len(outs) == 1

    def test_no_shipped_card_opts_in(self):
        """Pins the blast radius. assertiveness is populated on all 9 cards, so an
        accidental opt-in moves a persona nobody chose to move."""
        declared = [
            p.name for p in sorted(CARDS.glob("*.json"))
            if isinstance(json.loads(p.read_text()).get("dials_in_prompt"), bool)
        ]
        assert declared == [], f"cards opt in explicitly: {declared} — intended?"

    def test_card_optin_overrides_a_false_global(self, card, monkeypatch):
        from src.coordinator.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("PERSONA_DIALS_IN_PROMPT", "false")
        get_settings.cache_clear()
        c = json.loads(json.dumps(card))
        c["dials_in_prompt"] = True
        assert pb.dials_enabled_for(c) is True
        get_settings.cache_clear()

    def test_on_renders_the_bucket_line(self, card):
        c = json.loads(json.dumps(card))
        c["dials_in_prompt"] = True
        c["emotional_profile"]["sliders"]["assertiveness"] = 1.0
        assert pb.render_dial("assertiveness", 1.0) in _block(c)

    def test_a_card_with_no_sliders_is_unaffected(self, card):
        c = json.loads(json.dumps(card))
        c["dials_in_prompt"] = True
        c["emotional_profile"]["sliders"] = {}
        assert pb._lean_dials_block(c) == ""


# ─────────────────────────────────────────────────────────────
# The confound. Watched failing before the fix.
# ─────────────────────────────────────────────────────────────

class TestTheIdentityConfound:
    def test_sliders_are_outside_the_cv_fingerprint(self, card):
        """If they are inside it, every dial A/B is confounded by a rewritten identity."""
        norm = _normalize_for_fingerprint(card)
        assert "sliders" not in (norm.get("emotional_profile") or {})

    def test_a_dial_change_does_not_move_the_fingerprint(self, card):
        a = json.loads(json.dumps(card)); a["emotional_profile"]["sliders"]["assertiveness"] = 0.0
        b = json.loads(json.dumps(card)); b["emotional_profile"]["sliders"]["assertiveness"] = 1.0
        assert _normalize_for_fingerprint(a) == _normalize_for_fingerprint(b)

    def test_the_legacy_fingerprint_still_sees_them(self, card):
        """The adoption path needs the old hash to remain computable, or every
        persona's identity is rebuilt by an LLM the first time it is asked for."""
        a = json.loads(json.dumps(card)); a["emotional_profile"]["sliders"]["assertiveness"] = 0.0
        b = json.loads(json.dumps(card)); b["emotional_profile"]["sliders"]["assertiveness"] = 1.0
        assert _legacy_fingerprint(a) != _legacy_fingerprint(b)

    def test_other_emotional_profile_fields_still_count(self, card):
        """Excluding sliders must not accidentally exclude the whole profile —
        `baseline` DOES feed the identity summary."""
        a = json.loads(json.dumps(card))
        b = json.loads(json.dumps(card))
        b["emotional_profile"]["baseline"] = "completely different baseline"
        assert _normalize_for_fingerprint(a) != _normalize_for_fingerprint(b)


# ─────────────────────────────────────────────────────────────
# Placement — the trim is the thing that makes a wired dial look unwired
# ─────────────────────────────────────────────────────────────

class TestPlacement:
    def test_the_dial_line_is_not_in_the_trimmable_block(self, card):
        """_lean_constraints_block front-pops whole sections against a 150-token
        ceiling, and gwen already loses three. A dial there would die first, for
        exactly the persona under test."""
        c = json.loads(json.dumps(card))
        c["dials_in_prompt"] = True
        c["constraints_in_prompt"] = True
        line = pb.render_dial("assertiveness", c["emotional_profile"]["sliders"]["assertiveness"])
        assert line and line not in pb._lean_constraints_block(c)
        assert line in _block(c)

    def test_the_block_stays_small(self, card):
        """~24 tokens. A dial that costs more than a rule is not worth a slot."""
        c = json.loads(json.dumps(card))
        c["dials_in_prompt"] = True
        assert len(pb._lean_dials_block(c)) < 200


# ─────────────────────────────────────────────────────────────
# The two contrast scales
# ─────────────────────────────────────────────────────────────

class TestContrastScales:
    def test_the_midpoint_is_identical_in_both(self):
        """"Double the range" means widen OUTWARD from a fixed centre. If the middle
        bucket also moved, narrow and wide would be two different scales rather than
        two widths of one, and neither arm would anchor the other."""
        assert pb.render_dial("assertiveness", 0.5, "narrow") == pb.render_dial(
            "assertiveness", 0.5, "wide"
        )

    def test_wide_extremes_differ_from_narrow_extremes(self):
        for v in (0.0, 0.1, 0.9, 1.0):
            assert pb.render_dial("assertiveness", v, "narrow") != pb.render_dial(
                "assertiveness", v, "wide"
            )

    def test_wide_really_is_wider(self):
        """Pins the RELATIONSHIP, not a character count: the wide extremes must each
        say strictly more than the narrow ones they replace."""
        for v in (0.0, 1.0):
            assert len(pb.render_dial("assertiveness", v, "wide")) > len(
                pb.render_dial("assertiveness", v, "narrow")
            )

    def test_an_unknown_scale_degrades_to_narrow_and_never_raises(self):
        """A typo in PERSONA_DIAL_CONTRAST must not be able to take chat down."""
        for bad in ("nonsense", "", "WIDE ", None, "0.5"):
            got = pb.render_dial("assertiveness", 0.9, bad)
            assert got == pb.render_dial("assertiveness", 0.9, "narrow")

    def test_scale_name_is_case_insensitive(self):
        assert pb.render_dial("assertiveness", 0.9, "WIDE") == pb.render_dial(
            "assertiveness", 0.9, "wide"
        )

    def test_card_can_override_the_scale(self, card):
        c = json.loads(json.dumps(card))
        c["dial_contrast"] = "wide"
        assert pb.dial_scale_for(c) == "wide"

    def test_card_with_a_bogus_scale_falls_back_to_the_global(self, card):
        c = json.loads(json.dumps(card))
        c["dial_contrast"] = "enormous"
        assert pb.dial_scale_for(c) == "narrow"

    def test_no_shipped_card_declares_a_scale(self):
        declared = [
            p.name for p in sorted(CARDS.glob("*.json"))
            if "dial_contrast" in json.loads(p.read_text())
        ]
        assert declared == [], f"cards pin a contrast scale: {declared} — intended?"

    def test_the_block_uses_the_cards_scale(self, card):
        c = json.loads(json.dumps(card))
        c["dials_in_prompt"] = True
        c["dial_contrast"] = "wide"
        c["emotional_profile"]["sliders"]["assertiveness"] = 1.0
        assert pb.render_dial("assertiveness", 1.0, "wide") in pb._lean_companion_block(c)

    def test_wide_stays_within_a_sane_token_cost(self, card):
        """Wide is longer by design; it still must not rival a rule for budget."""
        c = json.loads(json.dumps(card))
        c["dials_in_prompt"] = True
        c["dial_contrast"] = "wide"
        assert len(pb._lean_dials_block(c)) < 400
