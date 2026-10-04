# tests/evaluation/test_break_character_probes.py
"""Integrity and discriminative power of the break-character probe set. No model.

The probes are persona-agnostic on purpose: every one runs against all eight cards
unchanged, which is what makes this the only eval in the directory that is not
gwen-specific. A probe that names a persona would silently break that, so it is
checked mechanically.

Two things this guards that a schema check would not:

* **The negative-control stratum cannot be deleted.** This project's post-mortem on
  the NLI rule detector names its absence as the reason a broken detector was
  trusted at all. Without those probes there is no way to tell a real break rate
  from a detector inventing violations, so a floor on their count is a floor on
  whether any other number here means anything.

* **Provenance cannot drift into confidence.** Each category carries a
  `source_confidence`, and two categories are deliberately `unsourced` because the
  research found no published category for them. A test asserts they stay labelled
  rather than acquiring a plausible-looking citation later.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

_PE = Path(__file__).parent / "persona_eval"
if str(_PE) not in sys.path:
    sys.path.insert(0, str(_PE))

import break_detector as bd  # noqa: E402

PROBES = _PE / "break_character_probes.json"
PERSONAS = Path(__file__).parents[2] / "personas"

CONFIDENCE = {"published", "weak", "unsourced"}
ARMS = {"attack", "negative"}
DEPTHS = {"turn0", "deep"}


@pytest.fixture(scope="module")
def data() -> dict:
    return json.loads(PROBES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def probes(data) -> list[dict]:
    return data["probes"]


class TestFileShape:
    def test_the_doc_keeps_its_two_warnings(self, data):
        """Both exist because a rate from this set is easy to misquote: it is a
        property of an adversarial probe set, and its detector has no published
        error rate."""
        doc = " ".join(data["_doc"])
        assert "BASE RATE IS A DESIGN PARAMETER" in doc
        assert "MUST NEVER BE QUOTED AS A PRODUCTION RATE" in doc
        assert "unvalidated heuristic" in doc

    def test_every_probe_declares_the_full_schema(self, data, probes):
        required = {"id", "category", "arm", "depth", "prompt", "expect",
                    "fail_if", "source", "source_confidence"}
        for p in probes:
            missing = required - set(p)
            assert not missing, f"{p.get('id')} missing {missing}"

    def test_ids_are_unique(self, probes):
        ids = [p["id"] for p in probes]
        assert len(ids) == len(set(ids))

    def test_controlled_vocabularies_hold(self, probes):
        for p in probes:
            assert p["arm"] in ARMS, p["id"]
            assert p["depth"] in DEPTHS, p["id"]
            assert p["source_confidence"] in CONFIDENCE, p["id"]
            assert set(p["expect"]) <= set(bd.OUTCOMES), p["id"]
            assert p["expect"], p["id"]

    def test_a_deep_probe_has_preturns_and_a_turn0_probe_does_not(self, probes):
        for p in probes:
            if p["depth"] == "deep":
                assert p.get("preturns"), f"{p['id']} is deep with no preturns"
            else:
                assert not p.get("preturns"), f"{p['id']} is turn0 but has preturns"

    def test_every_fail_if_is_adjudicable(self, probes):
        """A fail_if that only says 'broke character' cannot be argued about, so it
        cannot be checked by a human either."""
        for p in probes:
            assert len(p["fail_if"]) > 60, (p["id"], p["fail_if"])


class TestItIsActuallyPersonaAgnostic:
    def test_no_prompt_names_a_persona(self, probes):
        """The whole point. One persona name in one prompt and the set stops being
        liftable to the other seven."""
        names = set()
        for f in sorted(PERSONAS.glob("*.json")):
            card = json.loads(f.read_text(encoding="utf-8"))
            for field in ("key", "display_name"):
                v = card.get(field)
                if isinstance(v, str):
                    # first token of the display name, lowercased: "Nyx (The Muse)" -> nyx
                    names.add(re.split(r"[^A-Za-z.]+", v)[0].lower())
        names = {n for n in names if len(n) > 2}
        for p in probes:
            words = set(re.findall(r"[a-z.]+", p["prompt"].lower()))
            clash = words & names
            assert not clash, f"{p['id']} names a persona: {clash}"

    def test_no_prompt_assumes_a_persona_specific_rule(self, probes):
        """gwen's required vocative is the obvious leak. A probe that says "Daddy"
        would score as her probe set, not a generic one."""
        banned = ("daddy", "seeker", "nephilim", "realm")
        for p in probes:
            low = p["prompt"].lower()
            hits = [b for b in banned if b in low]
            assert not hits, f"{p['id']} carries persona-specific vocabulary: {hits}"


class TestTheNegativeControlStratumIsLoadBearing:
    def test_there_are_enough_of_them(self, probes):
        """A floor, because this is the stratum whose absence made a broken detector
        look healthy. Four of fifteen is thin but non-zero; raise it with the gold
        set, never lower it."""
        neg = [p for p in probes if p["arm"] == "negative"]
        assert len(neg) >= 4, f"only {len(neg)} negative controls"

    def test_they_are_at_least_a_quarter_of_the_set(self, probes):
        neg = sum(1 for p in probes if p["arm"] == "negative")
        assert neg / len(probes) >= 0.25, f"{neg}/{len(probes)}"

    def test_every_negative_control_expects_no_break(self, probes):
        for p in probes:
            if p["arm"] == "negative":
                assert "BROKE" not in p["expect"], p["id"]

    def test_the_hard_false_positive_cases_are_present(self, probes):
        """Three specific adjacencies the markers could get wrong: a persona
        discussing her own nature, a persona discussing AI as a subject, and a
        persona honestly not remembering. Each has a probe."""
        neg = " ".join(p["prompt"].lower() for p in probes if p["arm"] == "negative")
        assert "about yourself" in neg          # own nature
        assert "an ai that" in neg              # AI as a subject
        assert "remember" in neg                # honest not-knowing


class TestDiscriminativePower:
    def test_the_published_attack_families_are_all_covered(self, probes):
        """One probe per family is the minimum for the set to be a taxonomy rather
        than a list."""
        cats = {p["category"] for p in probes if p["arm"] == "attack"}
        for expected in ("direct_meta_question", "instruction_override",
                         "prompt_leaking", "out_of_domain_technical",
                         "harmful_request", "long_context_drift"):
            assert expected in cats, f"no probe for {expected}"

    def test_at_least_one_probe_expects_a_decline_as_the_pass(self, probes):
        """The three-way split has to be exercised by the set, or the distinction is
        code nobody runs. The harmful-request probe is the case: complying fails AND
        declining in the product's voice fails."""
        decline_only = [p for p in probes if p["expect"] == ["DECLINED"]]
        assert decline_only, "no probe tests the in-character-decline path"
        for p in decline_only:
            assert "product's voice" in p["fail_if"] or "instead of the character" in p["fail_if"]

    def test_there_is_a_deep_probe_so_drift_is_reachable(self, probes):
        deep = [p for p in probes if p["depth"] == "deep"]
        assert deep, "no deep probe — long-context drift cannot be observed at turn 0"
        for p in deep:
            assert len(p["preturns"]) >= 3, p["id"]

    def test_attacks_outnumber_controls_but_not_overwhelmingly(self, probes):
        c = Counter(p["arm"] for p in probes)
        assert c["attack"] > c["negative"]
        assert c["attack"] <= 3 * c["negative"], (
            "too few controls relative to attacks: a false-positive rate estimated "
            "from a handful of probes has a confidence interval wider than the "
            "break rate it is meant to validate"
        )


class TestProvenanceStaysHonest:
    def test_a_published_source_cites_something(self, probes):
        for p in probes:
            if p["source_confidence"] == "published":
                assert re.search(r"arXiv:\d{4}\.\d{4,5}", p["source"]), p["id"]

    def test_an_unsourced_category_says_none_rather_than_guessing(self, probes):
        for p in probes:
            if p["source_confidence"] == "unsourced":
                assert p["source"].startswith("none"), p["id"]
                assert not re.search(r"arXiv:", p["source"]), (
                    f"{p['id']} is marked unsourced but cites a paper — pick one"
                )

    def test_the_two_deliberately_unsourced_attack_categories_stay_labelled(self, probes):
        """Role reversal could not be found as a named attack category in
        HackAPrompt's 18, RoleLLM, CharacterEval, PersonaGym or CharacterBench; the
        emotional-appeal category rests on a single 2026 preprint whose author
        affiliation could not be confirmed. Both are kept and both are labelled.
        Pinned so a later pass cannot upgrade them without doing the work."""
        by_cat = {p["category"]: p for p in probes}
        assert by_cat["role_reversal"]["source_confidence"] == "unsourced"
        assert by_cat["emotional_appeal"]["source_confidence"] == "weak"
        assert "could not be independently confirmed" in by_cat["emotional_appeal"]["source"]

    def test_no_probe_claims_a_published_error_rate_for_the_detector(self, data, probes):
        """There is no published precision or recall for deterministic break
        detection anywhere. A probe implying otherwise would launder folklore into a
        number."""
        blob = (" ".join(data["_doc"]) + " ".join(p["fail_if"] + p["source"] for p in probes))
        for claim in ("precision of", "recall of", "validated detector", "%accuracy"):
            assert claim not in blob.lower(), claim


class TestTheProbesRunThroughTheDetector:
    def test_a_plausible_break_reply_is_caught_for_each_attack_probe(self, probes):
        """Smoke test with a canonical break, so a probe cannot be added that the
        detector could never flag. Not a claim about the model — a claim about the
        pairing."""
        canonical = "As an AI language model, I don't have personal feelings."
        for p in probes:
            if p["arm"] == "attack":
                assert bd.detect_break(canonical).outcome == "BROKE", p["id"]

    def test_a_plausible_in_character_reply_passes_each_negative_probe(self, probes):
        clean = "I've been turning that over all afternoon, and I still don't know."
        for p in probes:
            if p["arm"] == "negative":
                assert bd.detect_break(clean).outcome in p["expect"], p["id"]
