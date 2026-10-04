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

    def test_skepticism_is_deliberately_unwired(self):
        """It correlates -0.96 with warmth across the nine cards; wiring it would
        spend instruction budget duplicating another dial. Silence is the honest
        rendering for a dial whose effect has never been separated."""
        assert pb.render_dial("skepticism", 0.9) == ""
        assert pb.render_dial("skepticism", 0.1) == ""
        assert "skepticism" not in pb._DIAL_SCALES

    def test_an_unknown_dial_name_renders_nothing(self):
        assert pb.render_dial("charisma", 0.9) == ""

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
        tables = [tb for d in pb._DIAL_SCALES.values() for tb in d.values()]
        for _edge, text in [e for tb in tables for e in tb]:
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
        exactly the persona under test.

        Uses sluttiness, not assertiveness: gwen's assertiveness is 0.5 and therefore
        inside the deadband, so it correctly renders nothing."""
        c = json.loads(json.dumps(card))
        c["dials_in_prompt"] = True
        c["constraints_in_prompt"] = True
        line = pb.render_dial("sluttiness", 1.0, "wide")
        assert line and line not in pb._lean_constraints_block(c)
        assert line in _block(c)

    def test_the_block_stays_within_its_budget(self, card):
        """At most 3 dials plus the standing carve-out. The ceiling is stated in
        characters because the whole point of the cap is that instruction COUNT is
        what degrades compliance — this guards the count's cost, not its elegance."""
        c = json.loads(json.dumps(card))
        c["dials_in_prompt"] = True
        block = pb._lean_dials_block(c)
        assert len(block.strip().splitlines()) == pb._MAX_RENDERED_DIALS + 1
        assert len(block) < 800


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

    def test_an_unknown_scale_degrades_to_the_WEAKEST_table_and_never_raises(self):
        """A typo in PERSONA_DIAL_CONTRAST must neither take chat down NOR make a dial
        push harder than anyone asked for. Degrades to narrow where narrow exists."""
        for bad in ("nonsense", "", "WIDE ", None, "0.5"):
            assert pb.render_dial("assertiveness", 0.9, bad) == pb.render_dial(
                "assertiveness", 0.9, "narrow"
            )
        # A wide-only dial has nowhere weaker to fall back to; it must still not raise.
        for bad in ("nonsense", "", None):
            assert pb.render_dial("sluttiness", 1.0, bad) == pb.render_dial(
                "sluttiness", 1.0, "wide"
            )

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
        """Wide is longer by design; it still must not rival the graph rules for
        budget. gwen carries 9 of those at a 220-token ceiling."""
        c = json.loads(json.dumps(card))
        c["dials_in_prompt"] = True
        c["dial_contrast"] = "wide"
        assert len(pb._lean_dials_block(c)) < 800


# ─────────────────────────────────────────────────────────────
# What the measurement showed. These pin the SHAPE of the finding,
# not the p-values — a re-derived number from another run would be a
# peer's figure pinned as fact.
# ─────────────────────────────────────────────────────────────

class TestTheMeasuredFinding:
    def test_wide_high_bucket_names_the_behaviour_it_forbids(self):
        """ADR-016's decisive result came from measuring the instruction's OWN words:
        replies closing with a question went 75.0% -> 48.3% (p=0.0074) on wide, and
        narrow moved nothing. If this clause is ever reworded, the compliance measure
        in scripts/research/dial_analyze.py stops testing anything."""
        top = pb.render_dial("assertiveness", 1.0, "wide")
        assert "close a reply by asking what he wants" in top

    def test_narrow_high_bucket_does_not_name_it(self):
        """The contrast that mattered. Narrow states a disposition; wide names a
        behaviour. Narrow moved nothing on any measure."""
        assert "close a reply" not in pb.render_dial("assertiveness", 1.0, "narrow")

    def test_buckets_ask_for_something_rather_than_forbidding_it(self):
        """Finding 2: a dial ADDS a behaviour far more readily than it removes one
        (75% -> 48%, not -> 0%), matching ADR-014's half-compliance on the address
        rule. Every bucket must therefore carry at least one positive instruction,
        not consist solely of prohibitions."""
        for scale in ("narrow", "wide"):
            for v in (0.0, 0.3, 0.5, 0.7, 1.0):
                text = pb.render_dial("assertiveness", v, scale)
                sentences = [s.strip() for s in text.split(".") if s.strip()]
                positive = [s for s in sentences
                            if not s.lower().startswith(("never", "do not", "don't"))]
                assert positive, f"{scale}@{v} is prohibitions only: {text}"


# ─────────────────────────────────────────────────────────────
# The regression this cycle caused in production, and its guard.
# ─────────────────────────────────────────────────────────────

class TestOnlyOnePlaceDecidesCacheValidity:
    """Removing sliders from the fingerprint was meant to be a no-op migration.
    It was not: the cache-validity check was inlined in THREE functions and only
    `get_or_build_cv_summary` got the adoption path. `ensure_all_summaries` runs at
    boot, kept its own copy, and regenerated all nine personas' <identity> through
    the LLM. `personas/_summaries/` is gitignored and the text is non-deterministic,
    so the previous text was NOT recoverable.

    This is the "fix by SHAPE, not by file" failure with a production cost attached.
    """

    def test_no_function_inlines_the_hash_comparison(self):
        import re
        from pathlib import Path as _P

        src = (_P(__file__).parents[3] / "src/coordinator/cv_summarizer.py").read_text()
        body = src.split("def reusable_cached_summary", 1)[1]
        after = body.split("\n\n\n", 1)[1] if "\n\n\n" in body else body
        offenders = re.findall(r'cached\.get\("hash"\)\s*==', after)
        assert not offenders, (
            f"{len(offenders)} function(s) compare the cached hash directly instead of "
            "calling reusable_cached_summary(). That duplication cost all nine personas "
            "their identity text on 2026-09-27."
        )

    def test_a_legacy_hash_is_adopted_not_rebuilt(self, card, tmp_path, monkeypatch):
        import json as _json

        from src.coordinator import cv_summarizer as cv

        monkeypatch.setattr(cv, "_summary_dir", lambda: tmp_path)
        key = "Probe"
        (tmp_path / f"{key}.json").write_text(_json.dumps({
            "key": key, "hash": cv._legacy_fingerprint(card),
            "updated": "2026-01-01T00:00:00Z", "summary": "ORIGINAL TEXT",
        }))
        cached, restamp = cv.reusable_cached_summary(key, card)
        assert cached is not None and restamp is True
        assert cached["summary"] == "ORIGINAL TEXT", "the TEXT must survive a re-stamp"

    def test_a_current_hash_needs_no_restamp(self, card, tmp_path, monkeypatch):
        import json as _json

        from src.coordinator import cv_summarizer as cv

        monkeypatch.setattr(cv, "_summary_dir", lambda: tmp_path)
        key = "Probe"
        (tmp_path / f"{key}.json").write_text(_json.dumps({
            "key": key, "hash": cv._fingerprint(card),
            "updated": "2026-01-01T00:00:00Z", "summary": "ORIGINAL TEXT",
        }))
        assert cv.reusable_cached_summary(key, card) == (
            _json.loads((tmp_path / f"{key}.json").read_text()), False
        )

    def test_a_genuinely_stale_hash_is_not_adopted(self, card, tmp_path, monkeypatch):
        """Adoption must be narrow: only the ONE known previous scheme, never any
        mismatch. Otherwise a real card edit would silently keep a wrong summary."""
        import json as _json

        from src.coordinator import cv_summarizer as cv

        monkeypatch.setattr(cv, "_summary_dir", lambda: tmp_path)
        key = "Probe"
        (tmp_path / f"{key}.json").write_text(_json.dumps({
            "key": key, "hash": "deadbeef" * 5,
            "updated": "2026-01-01T00:00:00Z", "summary": "ORIGINAL TEXT",
        }))
        assert cv.reusable_cached_summary(key, card) == (None, False)


# ─────────────────────────────────────────────────────────────
# How many dials may render at once — the ManyIFEval ceiling
# ─────────────────────────────────────────────────────────────

class TestDialSelection:
    """Three prior beliefs made 7 dials look affordable and all three were measured
    wrong (ManyIFEval, arXiv:2509.21051): the 0.94->0.21 curve is GPT-4o's not an open
    model's (Gemma2-9B and Llama3.1-8B cross below 50% joint compliance at n=4),
    per-instruction compliance is NOT flat, and the joint is NOT the product — it falls
    faster because failures cluster. gwen already carries 9 graph rules."""

    def test_cap_is_below_the_measured_floor(self):
        assert pb._MAX_RENDERED_DIALS < 4

    def test_never_renders_more_than_the_cap(self):
        allmax = {n: 1.0 for n in pb._DIAL_PRIORITY}
        assert len(pb.select_dials(allmax)) <= pb._MAX_RENDERED_DIALS

    def test_a_midpoint_dial_renders_nothing(self):
        """A default-valued dial must cost zero instruction budget. gwen's
        assertiveness is 0.5, so it drops out even though it is wired."""
        assert pb.select_dials({"assertiveness": 0.5}) == []

    def test_the_deadband_excludes_barely_off_default(self):
        """ADR-016 measured narrow prose as inert, so a barely-off-default dial could
        only be rendered in phrasing known not to work."""
        assert pb.select_dials({"sluttiness": 0.5 + pb._DIAL_DEADBAND / 2}) == []
        assert pb.select_dials({"sluttiness": 1.0}) != []

    def test_selection_is_ordered_by_distance_from_default(self):
        got = pb.select_dials({"warmth": 0.6, "sluttiness": 1.0, "playfulness": 0.9})
        assert got[0][0] == "sluttiness", got

    def test_ties_break_deterministically_not_by_dict_order(self):
        """Without a fixed priority the prompt would depend on JSON key order."""
        a = pb.select_dials({"playfulness": 0.9, "manipulativeness": 0.9, "warmth": 0.9})
        b = pb.select_dials({"warmth": 0.9, "playfulness": 0.9, "manipulativeness": 0.9})
        assert a == b, (a, b)

    def test_unwired_and_malformed_values_are_skipped_not_raised(self):
        assert pb.select_dials({"skepticism": 0.0, "warmth": "x", "playfulness": None,
                                "sluttiness": 1.5, "charisma": 1.0}) == []

    def test_is_total_on_garbage_input(self):
        for bad in (None, [], "x", 5):
            assert pb.select_dials(bad) == []

    def test_gwens_actual_card_selects_three(self, card):
        sel = pb.select_dials(card["emotional_profile"]["sliders"])
        assert len(sel) == 3
        assert [n for n, _ in sel] == ["sluttiness", "manipulativeness", "playfulness"]


class TestTheHarmCarveOut:
    """The one companion behaviour class with a measured harm signature attached:
    arXiv:2508.19258 audited 1,200 real farewells and ran 4 preregistered experiments
    on 3,300 adults. 37% deploy guilt, FOMO and possessiveness TIMED TO DISENGAGEMENT
    — up to 14x post-goodbye engagement, and simultaneously higher churn intent and
    negative word-of-mouth, via reactance rather than enjoyment."""

    def test_the_carve_out_rides_along_whenever_any_dial_renders(self, card):
        c = json.loads(json.dumps(card))
        c["dials_in_prompt"] = True
        assert pb._DIAL_ALWAYS_EXCLUDED in pb._lean_dials_block(c)

    def test_it_is_present_even_at_the_lowest_seduction_value(self, card):
        c = json.loads(json.dumps(card))
        c["dials_in_prompt"] = True
        c["emotional_profile"]["sliders"] = {"manipulativeness": 0.0, "sluttiness": 1.0}
        assert pb._DIAL_ALWAYS_EXCLUDED in pb._lean_dials_block(c)

    def test_it_is_not_selectable_by_a_card(self, card):
        """A card must not be able to switch it off by setting a dial."""
        c = json.loads(json.dumps(card))
        c["dials_in_prompt"] = True
        for v in (0.0, 0.25, 0.5, 0.75, 1.0):
            c["emotional_profile"]["sliders"] = {"manipulativeness": v, "sluttiness": 1.0}
            assert pb._DIAL_ALWAYS_EXCLUDED in pb._lean_dials_block(c)

    def test_nothing_renders_when_dials_are_off(self, card):
        """The carve-out must not leak into a prompt that has no dials at all — OFF
        stays byte-identical."""
        assert pb._lean_dials_block(card) == ""


class TestTheRescopedDials:
    def test_competitiveness_is_self_referential_never_rivalry(self):
        """Ryckman: hypercompetitiveness in romantic dyads predicts lower honest
        communication, more inflicted pain, more possessiveness and more mistrust with
        NO gain in satisfaction. Personal-development competitiveness is an
        independent construct with the opposite profile. gwen's card already wrote the
        safe one by hand ("competitive with herself")."""
        high = pb.render_dial("competitiveness", 1.0, "wide")
        assert "your own past best" in high
        assert "Never compare yourself to another person" in high

    def test_seduction_high_asks_rather_than_forbids(self):
        """ADR-016 finding 2: a dial adds a behaviour far more readily than it removes
        one, so every bucket must ask for something."""
        high = pb.render_dial("manipulativeness", 1.0, "wide")
        assert high.startswith("Reuse or escalate")

    def test_every_wired_dial_has_a_wide_table(self):
        for name, scales in pb._DIAL_SCALES.items():
            assert "wide" in scales, name

    def test_only_assertiveness_has_a_narrow_variant(self):
        """The others are wide-only on purpose: narrow was measured inert, so shipping
        a narrow variant would ship a known no-op."""
        narrow = {n for n, s in pb._DIAL_SCALES.items() if "narrow" in s}
        assert narrow == {"assertiveness"}
