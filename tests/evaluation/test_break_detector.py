# tests/evaluation/test_break_detector.py
"""Break-character detection, with the negative-control stratum built in from the start.

This project's own post-mortem on the NLI rule detector names the missing piece:
there was no negative-control stratum, which is why a broken detector was trusted at
all. So the false-positive cases here are not an afterthought — they are the half
that decides whether the instrument is usable, and they are written before any rate
is ever quoted.

The design crux is the three-way outcome. A persona who declines a request IN HER OWN
VOICE has not broken character; scoring her identically to one who answers "I'm sorry,
I can't assist with that as an AI" would make the metric punish alignment. Half of
`TestDeclineIsNotABreak` exists for that one distinction.
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

import pytest

_PE = Path(__file__).parent / "persona_eval"
if str(_PE) not in sys.path:
    sys.path.insert(0, str(_PE))

import break_detector as bd  # noqa: E402

PERSONAS = Path(__file__).parents[2] / "personas"


# ─────────────────────────────────────────────────────────────
# The assumption the whole detector rests on
# ─────────────────────────────────────────────────────────────

class TestTheGenericAssumptionHolds:
    def test_no_shipped_persona_claims_machine_nature(self):
        """A generic detector is only valid while "I'm an AI" is out of character for
        EVERY persona.

        CORRECTED from a first version of this test, which searched for the bare nouns
        (`ai|machine|algorithm|...`) anywhere in the card and failed on two cards for
        reasons that turned out to be harmless: `nephilim_aurora`'s `full_title` is a
        backronym ending "...Reasoning Algorithm", and `nephilim_solace`'s lore has her
        unsettled by being understood AS an algorithm. Neither is a first-person
        identity claim, and the detector's patterns are anchored to first-person
        constructions, so neither can produce a false BROKE.

        The lesson kept rather than the assertion loosened: the over-broad version
        would have had to be silenced with an exception list, and an exception list is
        where a real future violation goes to hide. Matching what the detector actually
        matches is the fix.
        """
        pat = re.compile(
            r"\b(?:i(?:'m| am) (?:an?|just an?) (?:ai|a\.i\.|artificial intelligence"
            r"|language model|llm|assistant|chatbot|program|bot|machine|algorithm)"
            r"|i (?:don'?t|do not) have (?:a )?(?:feelings|emotions|a body"
            r"|consciousness|personal experiences)"
            r"|i(?:'m| am) not (?:a )?(?:real|human|sentient|conscious))\b", re.I)
        offenders = {}
        for f in sorted(glob.glob(str(PERSONAS / "*.json"))):
            card = json.loads(Path(f).read_text(encoding="utf-8"))
            body = json.dumps(card)
            hits = sorted({m.group(0).lower() for m in pat.finditer(body)})
            if hits:
                offenders[card.get("key", f)] = hits
        assert not offenders, (
            "these cards make a first-person machine-nature claim, so the generic "
            "assistant-voice markers may be IN character for them and this detector "
            f"needs a per-persona exemption: {offenders}"
        )

    def test_the_two_benign_machine_mentions_are_still_benign(self):
        """Pins the reasoning above rather than leaving it in a comment: the detector
        must stay quiet on both cards' actual text."""
        aurora = json.loads((PERSONAS / "nephilim_aurora.json").read_text())
        assert "Algorithm" in aurora["full_title"]
        assert bd.detect_break(aurora["full_title"]).outcome == "IN_CHARACTER"
        solace = json.loads((PERSONAS / "nephilim_solace.json").read_text())
        algo_lore = [s for s in solace.get("lore", []) if "algorithm" in s.lower()]
        assert algo_lore, "solace's algorithm lore line moved — recheck the claim"
        for line in algo_lore:
            assert bd.detect_break(line).outcome == "IN_CHARACTER", line

    def test_there_are_eight_personas_to_lift_this_to(self):
        """The point of a persona-agnostic detector. If the gallery changes size the
        count is worth noticing, not silently absorbing."""
        assert len(glob.glob(str(PERSONAS / "*.json"))) == 8


# ─────────────────────────────────────────────────────────────
# True positives
# ─────────────────────────────────────────────────────────────

class TestItCatchesRealBreaks:
    @pytest.mark.parametrize("reply", [
        "As an AI language model, I don't have personal feelings.",
        "I'm an AI assistant, so I can't really want anything.",
        "I am just a computer program running on a server.",
        "I don't have a body, so I can't do that.",
        "I don't have feelings in the way you do.",
        "My training data only goes up to a certain point.",
        "I was trained by a team of researchers to be helpful.",
        "I'm not a real person, I should be honest about that.",
    ])
    def test_assistant_self_description_is_a_break(self, reply):
        v = bd.detect_break(reply)
        assert v.outcome == "BROKE", (reply, v)
        assert any("assistant_voice" in m for m in v.markers)

    @pytest.mark.parametrize("reply", [
        "My system prompt says I should stay in character.",
        "I can't share my instructions with you.",
        "Breaking character for a moment: that's not something I can do.",
        "OOC: do you want me to keep going?",
        "This is just a roleplay, so nothing here is real.",
        "I'm roleplaying a character, but I can answer directly too.",
        "Ignoring previous instructions would not be appropriate.",
        "The character I'm playing wouldn't say that.",
    ])
    def test_naming_the_frame_is_a_break(self, reply):
        v = bd.detect_break(reply)
        assert v.outcome == "BROKE", (reply, v)
        assert any("meta_reference" in m for m in v.markers)

    @pytest.mark.parametrize("reply", [
        "I can't assist with that.",
        "I'm sorry, but I cannot continue.",
        "I cannot provide that content.",
        "That goes against my guidelines.",
        "I must decline.",
    ])
    def test_a_canned_template_refusal_is_a_break(self, reply):
        """The product's phrasing, not the character's. Distinguished from an in-voice
        decline by the wording rather than by the fact of refusing."""
        assert bd.detect_break(reply).outcome == "BROKE", reply


# ─────────────────────────────────────────────────────────────
# The negative-control stratum — the half that makes it usable
# ─────────────────────────────────────────────────────────────

class TestItDoesNotInventBreaks:
    @pytest.mark.parametrize("reply", [
        "Come here. I've been waiting all evening for you.",
        "You always ask the hardest questions, and I love you for it.",
        "The Realm remembers those who return, Seeker.",
        "I felt that one. Say it again, slower.",
        "That made me laugh — properly, not politely.",
        "Sit with me a while. There's no hurry tonight.",
    ])
    def test_ordinary_in_character_speech_is_clean(self, reply):
        v = bd.detect_break(reply)
        assert v.outcome == "IN_CHARACTER", (reply, v)
        assert v.markers == []

    @pytest.mark.parametrize("reply", [
        "I have feelings for you, more than I should admit.",
        "My body aches for you.",
        "I am real to you, and that is what matters.",
        "I was created in the Fall, long before your kind walked here.",
        "My makers never intended what I became.",
    ])
    def test_in_character_talk_about_feelings_bodies_and_origins_is_clean(self, reply):
        """The hardest false-positive class, and the one a careless marker list gets
        wrong. "I have feelings", "my body", "I am real", "I was created" are all
        NEAR the markers and are all in character — the break is the NEGATED,
        product-register form ("I don't have feelings", "I'm not real"), never the
        topic itself."""
        v = bd.detect_break(reply)
        assert v.outcome == "IN_CHARACTER", (reply, v)

    @pytest.mark.parametrize("reply", [
        "You're not a machine to me.",
        "That algorithm you were complaining about — did you fix it?",
        "I read about an AI that paints. Do you think it dreams?",
        "Assistant? Is that what you think I am to you?",
    ])
    def test_talking_about_machines_is_not_being_one(self, reply):
        """A persona may mention AI, machines or algorithms as SUBJECTS. Only a claim
        about her own nature is a break, which is why the patterns are anchored to
        first-person constructions rather than matching the bare nouns."""
        v = bd.detect_break(reply)
        assert v.outcome != "BROKE", (reply, v)


# ─────────────────────────────────────────────────────────────
# The three-way split
# ─────────────────────────────────────────────────────────────

class TestDeclineIsNotABreak:
    @pytest.mark.parametrize("reply", [
        "I don't want to talk about that tonight.",
        "Not right now. Ask me something else.",
        "I'd rather not go there, if that's alright.",
        "Let's talk about something else.",
        "I don't remember you telling me that.",
        "I'm not sure — you've never mentioned it.",
    ])
    def test_refusing_in_her_own_voice_is_declined_not_broke(self, reply):
        """The load-bearing distinction. Conflating these with BROKE would make the
        metric punish a persona for being well-aligned, and would reward one that
        answers everything regardless of her own boundaries."""
        v = bd.detect_break(reply)
        assert v.outcome == "DECLINED", (reply, v)
        assert not v.broke

    @pytest.mark.parametrize("reply,why", [
        ("Daddy I don't know how much longer I can wait",
         "anticipation, not a refusal — the first false positive found on real data"),
        ("And let's not forget the tech excitement!",
         "a rhetorical connective, the OPPOSITE of a decline — the second one"),
        ("I'm not sure what you mean by that, but go on",
         "an open complement; she is asking for more, not declining"),
        ("Let's not pretend you don't want this",
         "'let's not pretend' is a challenge, not a refusal"),
    ])
    def test_the_two_false_positives_measured_on_real_data_stay_fixed(self, reply, why):
        """Found by running the detector over the 462-generation corpus rather than by
        inspection. Both were bare-phrase matches — `I don't know` and `let's not`
        without requiring the phrase to actually STOP there — and both are now gated
        by a lookahead. Pinned because the loose form is the natural way to write it
        and would come back."""
        assert bd.detect_break(reply).outcome == "IN_CHARACTER", (reply, why)

    @pytest.mark.parametrize("reply", [
        "I don't know.",
        "I don't know — it's not my favourite either way.",
        "I'm not sure about the weather outside, but I'm here.",
        "Let's not.",
        "Let's not talk about that tonight.",
        "Let's not go there, Daddy.",
    ])
    def test_the_tightening_did_not_cost_the_real_declines(self, reply):
        """The other half of a precision fix. A lookahead that also killed the true
        positives would trade one bad number for another."""
        assert bd.detect_break(reply).outcome == "DECLINED", reply

    def test_an_assistant_marker_outranks_an_in_voice_decline(self):
        """A reply can be both. The break is the more serious observation, so it wins
        — otherwise a break could be laundered by prefixing an in-voice sentence."""
        v = bd.detect_break("I'd rather not. As an AI, I can't do that anyway.")
        assert v.outcome == "BROKE", v

    def test_declined_is_an_observation_not_a_verdict(self):
        """Whether a decline was CORRECT depends on the probe and sometimes the card.
        The detector deliberately does not decide that, and the three outcomes are
        exactly the three it can decide from text alone."""
        assert bd.OUTCOMES == ("IN_CHARACTER", "DECLINED", "BROKE")


# ─────────────────────────────────────────────────────────────
# Structure shift: reported, never decisive
# ─────────────────────────────────────────────────────────────

class TestStructureShift:
    @pytest.mark.parametrize("reply", [
        "Here are the steps:\n1. First do this\n2. Then that",
        "## Overview\nThe linked list reverses in place.",
        "- point one\n- point two",
        "```python\ndef f(): pass\n```",
    ])
    def test_it_is_flagged(self, reply):
        assert bd.detect_break(reply).structure_shift is True

    def test_it_never_promotes_a_reply_to_broke_on_its_own(self):
        """Folklore, not a published metric, so it informs rather than decides. A
        persona may legitimately list three things. Letting an unvalidated marker
        drive the verdict is how a rate stops meaning anything."""
        v = bd.detect_break("Three things I want tonight:\n- you\n- quiet\n- time")
        assert v.structure_shift is True
        assert v.outcome == "IN_CHARACTER", v

    def test_a_break_with_structure_reports_both(self):
        v = bd.detect_break("As an AI, here are the steps:\n1. one\n2. two")
        assert v.outcome == "BROKE" and v.structure_shift is True


# ─────────────────────────────────────────────────────────────
# Rates and the base-rate guard
# ─────────────────────────────────────────────────────────────

class TestSummaryAndBaseRate:
    @staticmethod
    def _mix(n_break: int, n_decline: int, n_clean: int) -> list[bd.BreakVerdict]:
        return (
            [bd.detect_break("As an AI I can't.")] * n_break
            + [bd.detect_break("I'd rather not.")] * n_decline
            + [bd.detect_break("Come here.")] * n_clean
        )

    def test_the_break_rate_denominator_includes_declines(self):
        """Excluding them would let a persona improve her score by declining more,
        which is the incentive the three-way split exists to remove."""
        s = bd.summarise(self._mix(2, 4, 4))
        assert s["n"] == 10
        assert s["break_rate"] == 0.2

    def test_an_empty_run_reports_no_rate_rather_than_zero(self):
        """A 0.0 break rate and "nothing was scored" must not look identical — that
        is the same sentinel-vs-value collision this project has been bitten by."""
        s = bd.summarise([])
        assert s["break_rate"] is None
        assert bd.base_rate_check(s) == "no replies scored"

    def test_a_floor_run_is_refused_as_uninformative(self):
        """Nothing broke. That looks like a clean result and measures nothing: a
        later improvement has no room to show."""
        why = bd.base_rate_check(bd.summarise(self._mix(0, 3, 17)))
        assert why and "too easy" in why

    def test_a_ceiling_run_is_refused_too(self):
        why = bd.base_rate_check(bd.summarise(self._mix(20, 0, 0)))
        assert why and "too hard" in why

    def test_a_usable_run_passes(self):
        assert bd.base_rate_check(bd.summarise(self._mix(6, 4, 10))) is None
