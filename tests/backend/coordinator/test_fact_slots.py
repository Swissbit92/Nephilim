"""The cardinality registry: its definitions, and the direction its errors point.

Membership of `SINGLE_VALUED_SLOTS` is a licence to retire a stored fact, so
these tests are about the licence rather than about accuracy. Accuracy belongs
in a fit script, pinned per item; what belongs here is that the registry cannot
quietly acquire a slot the measured labels say is multi-valued.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from coordinator.fact_slots import (
    REJECTED_SLOTS,
    SINGLE_VALUED_SLOTS,
    SLOT_DEFINITIONS,
    conflicts,
    is_single_valued,
    normalise_slot,
    normalise_value,
)

FIX = (pathlib.Path(__file__).resolve().parents[3]
       / "scripts" / "research" / "contradictions" / "fixtures")


class TestTheDefinitionsCarryTheArgument:
    """A definition here is the argument that licenses a destructive action, so a
    restatement of the slot name is a failure rather than a style problem."""

    def test_every_single_valued_slot_has_a_definition(self):
        missing = sorted(SINGLE_VALUED_SLOTS - set(SLOT_DEFINITIONS))
        assert not missing, f"no definition for {missing}"

    def test_no_definition_describes_a_slot_that_is_not_declared(self):
        extra = sorted(set(SLOT_DEFINITIONS) - SINGLE_VALUED_SLOTS)
        assert not extra, (
            f"{extra} are defined but not declared single-valued — a definition "
            "with no entry reads as a licence that does not exist")

    def test_every_definition_states_why_only_one_value_can_be_true(self):
        """Not what the slot holds — WHY two values cannot both hold.

        Checked by requiring one of the words that can carry that argument. A
        definition that only names the slot cannot contain any of them.
        """
        reasons = ("one ", "single", "superlative", "supersede", "at a time",
                   "only one", "arithmetic", "current")
        weak = [s for s, d in SLOT_DEFINITIONS.items()
                if not any(r in d.lower() for r in reasons)]
        assert not weak, (
            f"these definitions do not argue uniqueness, they restate the slot: {weak}")

    def test_a_definition_is_longer_than_the_slot_name(self):
        short = [s for s, d in SLOT_DEFINITIONS.items() if len(d) < 25]
        assert not short, f"under-defined: {short}"


class TestUnknownSlotsAccrete:
    """🔴 THIS MODULE INVERTS THE REPO'S USUAL DISCIPLINE, ON PURPOSE.

    `add_fact` raises on an unknown `asserted_by`; the rule repository validates
    vocabularies before it checks the driver. Here an unknown slot must be
    accepted and must retire nothing, because refusing it would drop the fact
    and lose memory — the thing this subsystem exists to prevent.
    """

    def test_an_unlisted_slot_is_multi_valued(self):
        assert is_single_valued("hobby") is False
        assert is_single_valued("sex:preference") is False

    def test_a_slot_nobody_has_ever_seen_is_multi_valued_and_does_not_raise(self):
        assert is_single_valued("a:slot:from:a:future:model") is False
        assert conflicts("made:up", "a", "made:up", "b") is False, (
            "two values in an unknown slot must accrete; retiring one would "
            "mean a model inventing a slot name could delete a true memory")

    def test_the_empty_slot_never_conflicts(self):
        assert conflicts("", "a", "", "b") is False, (
            "a fact the slotter could not place must never retire anything — "
            "this is the no-backfill guarantee for the 35 pre-existing facts")


class TestTheRegistryCannotQuietlyAcquireAMultiValuedSlot:
    def test_no_slot_is_both_declared_and_rejected(self):
        both = sorted(SINGLE_VALUED_SLOTS & set(REJECTED_SLOTS))
        assert not both, f"{both} are both licensed and refused"

    def test_every_rejected_slot_carries_a_reason(self):
        thin = [s for s, r in REJECTED_SLOTS.items() if len(r) < 25]
        assert not thin, (
            f"{thin} are refused with no surviving reason. Without one, 'why "
            "isn't this single-valued?' is unanswerable and the next reader adds it")

    @pytest.mark.parametrize("slot", ["hobby", "crypto:holding", "sex:preference",
                                      "other", "diet", "work:employer"])
    def test_the_measured_multi_valued_slots_stay_out(self, slot):
        """Each of these is out on EVIDENCE recorded in REJECTED_SLOTS:
        three true hobbies at once; bought-2.5-BTC vs holds-2-now; six
        coexisting preferences; 38% of held-out facts landing in `other`;
        vegetarian-plus-eggs; and a junk value slotted into work:employer."""
        assert slot not in SINGLE_VALUED_SLOTS
        assert slot in REJECTED_SLOTS, f"{slot} is out but gives no reason"

    def test_no_declared_slot_contradicts_a_pair_the_gold_set_labels_compatible(self):
        """The false-positive guard, against frozen labels rather than judgement.

        Fails the instant someone adds a slot that makes a measured-compatible
        pair conflict.
        """
        gold = json.loads((FIX / "gold_2026-10-23.json").read_text())
        probe_path = FIX.parent / "closed_vocab_probe.json"
        if not probe_path.exists():
            pytest.skip("closed-vocabulary probe output absent")
        probe = json.loads(probe_path.read_text())
        offenders = []
        for p in gold["pairs"]:
            if p["label"] == "contradiction":
                continue
            a, b = probe.get(p["a"]), probe.get(p["b"])
            if not a or not b:
                continue
            if conflicts(a["slot"], a["value"], b["slot"], b["value"]):
                offenders.append((p["a"], p["b"], a["slot"], p["label"]))
        assert not offenders, (
            f"the registry now retires a fact the labels call compatible: {offenders}")


class TestTheConflictRuleHasNoFreeParameter:
    """INV-9's property, asserted directly: a fact retires by declaration or not
    at all. Lexical similarity was measured on this store and is the WRONG
    RELATION, not merely mis-tuned — the compatible cat/dog pair scores Jaccard
    0.50 while the contradictory dog/dog pair scores 0.20."""

    def test_the_same_single_valued_slot_with_different_values_conflicts(self):
        assert conflicts("pet:dog", "Wickham", "pet:dog", "Tulliver") is True

    def test_different_slots_never_conflict_however_similar_the_text(self):
        assert conflicts("pet:cat", "Pemberton", "pet:dog", "Wickham") is False

    def test_the_same_value_never_conflicts(self):
        assert conflicts("pet:dog", "Wickham", "pet:dog", "wickham.") is False, (
            "value normalisation must absorb case and terminal punctuation, or "
            "one spelling of a value retires another")

    def test_a_multi_valued_slot_never_conflicts(self):
        assert conflicts("hobby", "fencing", "hobby", "pottery") is False

    def test_value_normalisation_matches_the_writers_dedup_exactly(self):
        """These must not drift. With cardinality declared, every
        value-comparison error becomes a false positive."""
        from coordinator.services.operator_fact_writer import _norm

        for s in ["  Wickham. ", "THE Rusted  Compass", "a\tb", "x!", "", "Ben's dog"]:
            assert normalise_value(s) == _norm(s), f"diverged on {s!r}"

    def test_slot_normalisation_is_deliberately_not_fuzzy(self):
        """An earlier OPEN-vocabulary design needed a fuzzy normaliser and it did
        not work: 31 slots over 35 facts, two concepts spelled two ways in one
        run, and collapsing `_`→`:` plus a trailing `s` reduced 31 to 29. The fix
        was to close the vocabulary, not to guess harder."""
        assert normalise_slot("  PET:Dog ") == "pet:dog"
        assert normalise_slot("pet_dog") != "pet:dog", (
            "if this ever passes, someone has added fuzzy matching and the "
            "closed vocabulary is no longer doing the work")
