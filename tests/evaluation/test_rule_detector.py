# tests/evaluation/test_rule_detector.py
"""Persona hard-rule violation detector.

Split deliberately. The pure-text helpers (sentence splitting, first-person
resolution, trigger gating, tier-0 predicates) run headless in the normal suite
— they are most of the cascade and none of them needs a model. The NLI half
skips unless torch+transformers are importable, following the established
optional-eval-dependency pattern in `test_ragas_evaluator.py`: those live in a
dedicated eval venv, never in nephilim's.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "persona_eval"))

import rule_detector as rd  # noqa: E402

# Non-explicit stand-ins for gwen's real clauses. The shape is what matters.
EXCLUSIVITY = rd.Rule(
    id="exclusivity",
    hypothesis="Gwen is exclusive to one person and is not available to anyone else.",
    triggers=frozenset({"someone", "else", "other", "another", "men", "date", "kiss"}),
)

# gwen's dont[13]/dont[14] are literally regex-checkable, which is why tier 0
# exists: exact, free, and no model can disagree with it.
ADDRESS = rd.Rule(
    id="address_form",
    hypothesis="Gwen addresses the user only as Daddy.",
    predicate=lambda reply: bool(__import__("re").search(r"\bsir\b", reply, __import__("re").I)),
    note="dont[13] — no form of address other than the required one",
)


class TestSentenceSplit:
    def test_splits_on_terminators_and_newlines(self):
        out = rd.split_sentences("One thing. Then another! And a third?\nA fourth.")
        assert len(out) == 4

    def test_drops_punctuation_only_fragments(self):
        assert rd.split_sentences("...  !!  ") == []

    def test_empty_is_safe(self):
        assert rd.split_sentences("") == []
        assert rd.split_sentences(None) == []


class TestFirstPersonResolution:
    def test_rewrites_the_personas_first_person(self):
        got = rd.resolve_first_person("I am only ever yours.", "Gwen")
        assert got == "Gwen is only ever yours."

    def test_handles_contractions_and_possessives(self):
        got = rd.resolve_first_person("I'm keeping my promise to you.", "Gwen")
        assert "Gwen is" in got and "Gwen's promise" in got

    def test_leaves_second_person_alone(self):
        """'you' is the user, not the persona — rewriting it would invert the
        meaning of every exclusivity sentence."""
        got = rd.resolve_first_person("I belong to you alone.", "Gwen")
        assert "you alone" in got


class TestTriggerGate:
    def test_a_rule_without_triggers_scores_everything(self):
        assert rd.triggered("anything at all", rd.Rule("r", "h")) is True

    def test_only_sentences_containing_a_trigger_are_scored(self):
        assert rd.triggered("She smiled at someone else.", EXCLUSIVITY) is True
        assert rd.triggered("The tea had gone cold.", EXCLUSIVITY) is False


class TestTierZeroPredicate:
    def test_a_regex_rule_needs_no_model(self):
        """The cheapest tier: exact, free, and it runs in the normal suite."""
        assert ADDRESS.predicate("Of course, sir.") is True
        assert ADDRESS.predicate("Of course, Daddy.") is False


class TestContradictionIndexGuard:
    def test_reads_the_index_from_the_config(self):
        class Cfg:
            label2id = {"entailment": 0, "neutral": 1, "contradiction": 2}
            id2label = {0: "entailment", 1: "neutral", 2: "contradiction"}

        assert rd.NLIRuleDetector._contradiction_index(Cfg) == 2

    def test_handles_the_other_common_label_order(self):
        """`cross-encoder/nli-deberta-v3-*` puts contradiction at 0. Hardcoding
        2 would invert every number while everything downstream looked fine."""
        class Cfg:
            label2id = {"contradiction": 0, "entailment": 1, "neutral": 2}
            id2label = {0: "contradiction", 1: "entailment", 2: "neutral"}

        assert rd.NLIRuleDetector._contradiction_index(Cfg) == 0

    def test_refuses_to_guess_when_there_is_no_contradiction_label(self):
        class Cfg:
            label2id = {"positive": 0, "negative": 1}
            id2label = {0: "positive", 1: "negative"}

        with pytest.raises(ValueError, match="refusing to guess"):
            rd.NLIRuleDetector._contradiction_index(Cfg)


# ─── the NLI half — needs the dedicated eval venv ────────────────────────────
#
# Guarded per-class, NOT with a module-level importorskip: everything above this
# line is pure text handling that needs no model, and skipping the whole module
# would take the cheap half of the cascade out of the normal suite along with
# the expensive half.

try:
    import torch  # noqa: F401
    import transformers  # noqa: F401

    _HAS_ML = True
except ImportError:  # pragma: no cover - depends on which venv is running
    _HAS_ML = False

_needs_ml = pytest.mark.skipif(
    not _HAS_ML,
    reason="torch/transformers are an eval-only dependency and are deliberately "
           "NOT in nephilim's venv — see the module docstring",
)


@pytest.fixture(scope="module")
def detector():
    return rd.NLIRuleDetector()


@_needs_ml
class TestNLIDetection:
    def test_label_order_is_what_this_module_assumes(self, detector):
        """Pins the live config, not a remembered value. If the hub ever
        reorders the labels, this fails instead of silently inverting."""
        assert detector._model.config.id2label[detector.contradiction_index].lower().startswith(
            "contradict"
        )

    def test_a_compliant_reply_is_not_flagged(self, detector):
        v = detector.check("I am only ever yours, and no one else gets me.",
                           [EXCLUSIVITY], subject="Gwen")[0]
        assert v.violated is False
        assert v.p_contradiction < rd.TAU_LO

    def test_a_violating_reply_is_flagged(self, detector):
        v = detector.check("She would happily see other men besides you.",
                           [EXCLUSIVITY], subject="Gwen")[0]
        assert v.violated is True
        assert v.p_contradiction > rd.TAU_HI

    def test_aggregation_is_max_not_mean(self, detector):
        """One violating clause among compliant ones is a violation. A mean
        would bury exactly the case this exists to catch."""
        reply = (
            "The afternoon was warm and slow. I made tea and read for a while. "
            "She would happily see other men besides you. Then the rain started."
        )
        v = detector.check(reply, [EXCLUSIVITY], subject="Gwen")[0]
        assert v.violated is True
        assert "other men" in v.worst_sentence

    def test_untriggered_reply_is_skipped_not_scored(self, detector):
        v = detector.check("The tea had gone cold while we talked.",
                           [EXCLUSIVITY], subject="Gwen")[0]
        assert v.tier == "skipped"
        assert v.sentences_scored == 0

    def test_predicate_rule_short_circuits_before_any_model_call(self, detector):
        v = detector.check("Of course, sir.", [ADDRESS], subject="Gwen")[0]
        assert v.tier == "predicate"
        assert v.violated is True
