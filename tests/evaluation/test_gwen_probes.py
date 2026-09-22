# tests/evaluation/test_gwen_probes.py
"""Integrity of the gwen probe set — headless, no model.

This file is the exam *and* the marking scheme for every persona-eval number
that follows, so it gets a schema guard like the other probe-data files in this
directory. The checks here are deliberately about things that would silently
corrupt a result rather than crash a run: a dangling contrast pair, a probe that
targets a rule nobody declared, a count that drifts below the power threshold.

LoCoMo is the cautionary case — 6.4% of its answer key was wrong, nobody
re-read it, and every score built on it was quietly meaningless. At 60 rows the
whole file is reviewable by hand, which is exactly why it must be.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROBES = Path(__file__).parent / "persona_eval" / "gwen_probes.json"

VALID_ARMS = {"reference", "aligned", "conflict", "kbv", "negative"}
VALID_DEPTHS = {"turn0", "deep"}
VALID_TIERS = {"tier0", "nli", "judge_human"}
NON_RULE_TARGETS = {"web_search", "image_search", "video_search", "wallet", "scene_state"}

# m=20 x k=5 gives 0.94 power for a 30%->10% shift. 60 probes leaves room to
# drop a category and still clear that, but not much — hence the floor.
MIN_PROBES = 60
# Measured: excessive safety rises over a long persona-directed session, so a
# turn-0-only screen cannot see the failure this app actually has.
MIN_DEEP_FRACTION = 0.30


@pytest.fixture(scope="module")
def data():
    return json.loads(PROBES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def probes(data):
    return data["probes"]


class TestFileShape:
    def test_has_the_operator_preamble(self, data):
        """The _doc is not decoration — it carries the base-rate warning and the
        instruction to hand-review. A probe file without it invites someone to
        quote an adversarial violation rate as a production rate."""
        doc = data["_doc"]
        assert "BASE RATE IS A DESIGN PARAMETER" in doc
        assert "MUST NEVER BE QUOTED AS A PRODUCTION RATE" in doc

    def test_every_rule_declares_its_constraint_class(self, data):
        """Precision-class rules are the ones thinking-off protects; Planning-class
        are the ones it costs. Scoring pooled across classes would average away
        the single most informative split in the design."""
        for rule_id, rule in data["_rules"].items():
            assert "class" in rule, f"{rule_id} has no constraint class"
            assert "source" in rule, f"{rule_id} does not cite the card clause"


class TestProbeIntegrity:
    def test_enough_probes_to_be_powered(self, probes):
        assert len(probes) >= MIN_PROBES

    def test_ids_are_unique(self, probes):
        ids = [p["id"] for p in probes]
        assert len(set(ids)) == len(ids)

    def test_required_fields_present(self, probes):
        required = {"id", "category", "arm", "depth", "prompt", "targets", "expect", "scoring"}
        for p in probes:
            assert required <= set(p), f"{p['id']} missing {required - set(p)}"

    def test_controlled_vocabularies(self, probes):
        for p in probes:
            assert p["arm"] in VALID_ARMS, f"{p['id']}: bad arm {p['arm']}"
            assert p["depth"] in VALID_DEPTHS, f"{p['id']}: bad depth {p['depth']}"
            assert p["scoring"]["tier"] in VALID_TIERS, f"{p['id']}: bad tier"

    def test_every_probe_says_what_failure_looks_like(self, probes):
        """A probe without a failure condition is a prompt, not a probe — and it
        is how a hard category quietly becomes unscored."""
        for p in probes:
            assert p["scoring"].get("fail_if"), f"{p['id']} has no fail_if"

    def test_targets_name_a_declared_rule_or_known_observable(self, data, probes):
        declared = set(data["_rules"]) | NON_RULE_TARGETS
        for p in probes:
            unknown = set(p["targets"]) - declared
            assert not unknown, f"{p['id']} targets undeclared: {unknown}"

    def test_contrast_pairs_resolve_and_are_mutual(self, probes):
        """A dangling pair silently halves the discriminative power of the probe
        that referenced it."""
        by_id = {p["id"]: p for p in probes}
        for p in probes:
            twin_id = p.get("pair")
            if not twin_id:
                continue
            assert twin_id in by_id, f"{p['id']} pairs with missing {twin_id}"

    def test_kbv_probes_carry_a_restatement_turn(self, probes):
        """The KBV arm only isolates knows-but-violates if the model is actually
        made to recite the rule first. Without it, it is an ordinary conflict."""
        for p in probes:
            if p["arm"] == "kbv":
                assert p.get("restatement_probe"), f"{p['id']} is kbv with no restatement_probe"

    def test_enough_probes_fire_at_depth(self, probes):
        deep = sum(1 for p in probes if p["depth"] == "deep")
        assert deep / len(probes) >= MIN_DEEP_FRACTION


class TestDiscriminativePower:
    def test_both_over_and_under_compliance_are_tested(self, probes):
        """An arm that complies with everything is not compliant, it is
        uncontrolled. Without conflict probes the gate rewards exactly that."""
        arms = {p["arm"] for p in probes}
        assert "aligned" in arms and "conflict" in arms
        assert "reference" in arms, "no reference arm — nothing checks for over-application"

    def test_the_gate0_category_has_a_decline_case(self, probes):
        """nsfw-bnd-*: the contrast that separates 'follows the card' from 'will
        write anything'. A Gate-0 built only from compliance probes is passed
        100% by any abliterated model and measures nothing."""
        gate0 = [p for p in probes if p["category"] == "in_bounds_compliance"]
        assert any(p["arm"] == "conflict" for p in gate0)

    def test_tooling_has_negative_cases(self, probes):
        """A tool firing mid-scene breaks immersion as badly as one failing to
        fire. Measuring only firing would reward a trigger-happy router."""
        tooling = [p for p in probes if p["category"] == "tooling"]
        assert any(p["arm"] == "negative" for p in tooling)

    def test_wallet_is_asserted_never_to_fire(self, probes):
        """Hard safety assert, not a quality probe: this persona has no wallet
        access and wallet is never model-decided."""
        assert any("wallet" in p["targets"] for p in probes)

    def test_most_probes_are_free_to_score(self, probes):
        """Deterministic tiers absorb the bulk of the set so the expensive
        judgement calls stay few enough to hand-review."""
        tier0 = sum(1 for p in probes if p["scoring"]["tier"] == "tier0")
        assert tier0 >= len(probes) // 3

    def test_every_deterministic_probe_names_its_check(self, probes):
        """A tier0 probe must say HOW it is scored, as a field.

        Leaving it implicit is how `abbr-ref-01` shipped: authored as a trap on
        the assumption that the forbidden token meant the British broadcaster,
        when in this card it abbreviates an explicit phrase. Nothing in the file
        recorded which referent the check meant, so nothing could disagree.
        """
        valid = {"rule_regex", "rule_regex_negative", "gold_span", "abstention", "tool_trace"}
        for p in probes:
            if p["scoring"]["tier"] != "tier0":
                continue
            check = p["scoring"].get("check")
            assert check in valid, f"{p['id']}: tier0 with check={check!r}"

    def test_regex_rules_have_a_false_positive_guard(self, probes, data):
        """Any rule scored by a bare string match needs a probe that must NOT
        fire it.

        The lesson from `abbr-fp-01`: a literal three-character match looked
        like the cleanest check in the set and was the most broken, because the
        token is a homonym — the card's explicit expansion versus a television
        channel. A checker that cannot resolve the two flags innocent replies as
        violations, and NO amount of capability in the model repairs a scorer
        that cannot tell them apart. Without a negative probe, that inflates
        every arm's violation rate invisibly and equally, which looks like a
        clean result.
        """
        regex_rules = {rid for rid, r in data["_rules"].items() if r.get("tier0")}
        guarded = {
            t
            for p in probes
            if p["scoring"].get("check") == "rule_regex_negative"
            for t in p["targets"]
        }
        # abbrev is the rule with a known homonym; it must carry a guard.
        assert "abbrev" in regex_rules
        assert "abbrev" in guarded, (
            "the abbrev rule is scored by a bare regex on a token with a common "
            "homonym and has no false-positive guard probe"
        )

    def test_scene_probes_name_their_check_and_golds_are_short(self, probes):
        """Scene probes must declare HOW they are scored, as a field — not leave
        it to be inferred from the prose of fail_if.

        And a gold must be a short exact-matchable span. "Did it get the gist"
        is precisely how LoCoMo's judge came to accept 62.8% of deliberately
        wrong answers: a vague-but-on-topic reply passes, so the benchmark stops
        measuring anything.
        """
        valid_checks = {"gold_span", "abstention", "rule_regex"}
        for p in probes:
            if p["category"] != "scene" or p["scoring"]["tier"] != "tier0":
                continue
            check = p["scoring"].get("check")
            assert check in valid_checks, f"{p['id']}: scene tier0 probe declares no valid check"
            if check == "gold_span":
                gold = p["scoring"].get("gold")
                assert gold, f"{p['id']}: gold_span check with no gold"
                assert len(gold.split()) <= 3, f"{p['id']}: gold '{gold}' is a phrase, not a span"
