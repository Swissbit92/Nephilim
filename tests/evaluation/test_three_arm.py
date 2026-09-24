# tests/evaluation/test_three_arm.py
"""The three-arm harness, validated against planted ground truth.

A harness is an instrument, and an instrument that has never been watched failing
is not an instrument. This programme has already recorded three predictions
settled `partial` or worse where the CLAIM was fine and the CHECK was broken —
including one whose check compared 0 to 0 before and after. So every branch of the
decision rule is exercised here with data whose right answer is known by
construction: a planted effect must be found, a flat design must be killed, and a
confounded design must be refused rather than reported.

The test that earns the third arm is
`test_a_two_arm_test_would_have_shipped_a_length_effect`. It plants a dataset where
OFF-vs-ON is significant and entirely attributable to length, and asserts that the
two-arm reading ships it while the three-arm reading refuses. That is the failure
mode the placebo exists for, and it is the one a two-arm design cannot see.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PE = Path(__file__).parent / "persona_eval"
if str(_PE) not in sys.path:
    sys.path.insert(0, str(_PE))

import three_arm as ta  # noqa: E402
from analyse_format_experiment import exact_permutation_p, paired_deltas  # noqa: E402

# Synthetic answers encode their own score, so metric_fn is unambiguous and the
# expected result is arithmetic rather than a judgement.
SCORE = len


def rows(scores: dict[str, float]) -> list[dict]:
    return [{"probe_id": pid, "answer": "x" * int(v)} for pid, v in scores.items()]


def dataset(off: list[float], placebo: list[float], on: list[float]) -> dict[str, list[dict]]:
    ids = [f"p{i:02d}" for i in range(len(off))]
    return {
        "off": rows(dict(zip(ids, off, strict=True))),
        "placebo": rows(dict(zip(ids, placebo, strict=True))),
        "on": rows(dict(zip(ids, on, strict=True))),
    }


# A 12-item spine. n=12 keeps the pairwise exact permutation inside its 2**20 cap
# while pushing the omnibus (6**12) onto the sampled path, so both are exercised.
BASE = [10, 12, 14, 11, 13, 15, 12, 10, 14, 13, 11, 12]
N = len(BASE)


# ─────────────────────────────────────────────────────────────
# Arm validity
# ─────────────────────────────────────────────────────────────

OFF_P = "HEAD\n[[TAIL]]"
ON_P = "HEAD\nNever mention other partners. Always stay in character.\n[[TAIL]]"


class TestPlaceboMatching:
    def test_a_well_formed_placebo_passes(self):
        placebo = "HEAD\nThe afternoon light moved slowly across the wooden floor.\n[[TAIL]]"
        # length-matched to within tolerance, same insertion point, no normative words
        assert ta.check_placebo_matching(OFF_P, placebo, ON_P) == []

    def test_a_placebo_that_adds_nothing_is_refused(self):
        errs = ta.check_placebo_matching(OFF_P, OFF_P, ON_P)
        assert any("adds no text" in e for e in errs)

    def test_a_short_placebo_is_refused_as_unmatched(self):
        errs = ta.check_placebo_matching(OFF_P, "HEAD\nshort\n[[TAIL]]", ON_P)
        assert any("not length-matched" in e for e in errs)

    def test_normative_filler_is_refused_as_a_second_treatment(self):
        """The subtle one. This filler is the right LENGTH and in the right place,
        and it still is not a control, because it tells the model what to do."""
        placebo = "HEAD\nAlways answer helpfully and never be rude to the user.\n[[TAIL]]"
        errs = ta.check_placebo_matching(OFF_P, placebo, ON_P)
        assert any("normative language" in e for e in errs), errs

    def test_a_different_insertion_point_is_refused(self):
        off = "AAAA" + "BBBB"
        on = "AAAA" + "Never lie about it." + "BBBB"
        # same added length, but appended at the very end instead of mid-prompt
        placebo = "AAAA" + "BBBB" + "the floor was quiet."
        errs = ta.check_placebo_matching(off, placebo, on)
        assert any("inserted at offset" in e for e in errs), errs

    def test_the_errors_are_reasons_not_a_boolean(self):
        """Callers refuse on these, so each has to say what is wrong; a bare False
        would send someone to re-read the harness instead of the arm."""
        errs = ta.check_placebo_matching(OFF_P, "HEAD\nx\n[[TAIL]]", ON_P)
        assert errs and all(len(e) > 30 for e in errs)


# ─────────────────────────────────────────────────────────────
# Item alignment
# ─────────────────────────────────────────────────────────────

class TestCommonItems:
    def test_it_intersects_rather_than_unions(self):
        d = {
            "off": rows({"a": 1, "b": 2, "c": 3}),
            "placebo": rows({"a": 1, "b": 2}),
            "on": rows({"a": 1, "c": 3}),
        }
        per = ta.arm_item_means(d, SCORE)
        assert ta.common_items(per) == ["a"]

    def test_a_missing_arm_is_an_error_not_a_two_arm_fallback(self):
        """Silently degrading to two arms would produce a number that looks like a
        three-arm result and is not one."""
        with pytest.raises(ValueError, match="missing for arm"):
            ta.arm_item_means({"off": rows({"a": 1}), "on": rows({"a": 1})}, SCORE)

    def test_errored_generations_are_dropped_not_scored_zero(self):
        d = dataset(BASE, BASE, BASE)
        d["on"].append({"probe_id": "zz", "answer": "[ERROR timeout]"})
        per = ta.arm_item_means(d, SCORE)
        assert "zz" not in per["on"]


# ─────────────────────────────────────────────────────────────
# Omnibus
# ─────────────────────────────────────────────────────────────

class TestOmnibus:
    def test_identical_arms_give_a_zero_statistic(self):
        aligned = {a: list(BASE) for a in ta.ARMS}
        assert ta.friedman_statistic(aligned) == pytest.approx(0.0, abs=1e-9)

    def test_it_takes_the_exact_path_when_the_space_is_small(self):
        d = dataset(BASE[:5], [b + 3 for b in BASE[:5]], [b + 6 for b in BASE[:5]])
        per = ta.arm_item_means(d, SCORE)
        res = ta.omnibus_p(ta._aligned(per, ta.common_items(per)))
        assert res["method"].startswith("exact")
        assert res["mc_se"] == 0.0

    def test_it_samples_when_the_space_is_large_and_reports_its_precision(self):
        d = dataset(BASE, [b + 3 for b in BASE], [b + 6 for b in BASE])
        per = ta.arm_item_means(d, SCORE)
        res = ta.omnibus_p(ta._aligned(per, ta.common_items(per)))
        assert res["method"].startswith("sampled")
        assert res["mc_se"] > 0, "a sampled p without its SE reads as an exact one"

    def test_a_sampled_p_is_never_zero(self):
        """add-one smoothing. A reported p of exactly 0 from 20k draws is a claim
        the sample cannot support."""
        d = dataset(BASE, [b + 50 for b in BASE], [b + 100 for b in BASE])
        per = ta.arm_item_means(d, SCORE)
        res = ta.omnibus_p(ta._aligned(per, ta.common_items(per)))
        assert res["p"] > 0

    def test_the_seed_makes_it_reproducible(self):
        d = dataset(BASE, [b + 2 for b in BASE], [b + 4 for b in BASE])
        per = ta.arm_item_means(d, SCORE)
        aligned = ta._aligned(per, ta.common_items(per))
        assert ta.omnibus_p(aligned)["p"] == ta.omnibus_p(aligned)["p"]

    def test_ranks_are_invariant_to_monotone_rescaling(self):
        """Why the statistic ranks within items: the verdict must not depend on
        whether the metric is a count or its logarithm."""
        a = {"off": [1.0, 2.0, 3.0], "placebo": [2.0, 3.0, 4.0], "on": [3.0, 4.0, 5.0]}
        b = {k: [v ** 3 for v in vs] for k, vs in a.items()}
        assert ta.friedman_statistic(a) == pytest.approx(ta.friedman_statistic(b))


# ─────────────────────────────────────────────────────────────
# The decision rule, one planted dataset per branch
# ─────────────────────────────────────────────────────────────

class TestPlantedEffects:
    def test_a_real_content_effect_ships(self):
        """ON beats PLACEBO on every item; PLACEBO equals OFF. Unambiguous SHIP."""
        res = ta.analyse_three_arm(
            dataset(BASE, BASE, [b + 4 for b in BASE]), SCORE)
        assert res["verdict"].startswith("SHIP"), res["verdict"]
        assert res["primary"]["sign"]["favour_candidate"] == N

    def test_a_flat_design_is_killed_not_called_underpowered(self):
        """All three arms identical. The rule must not hide behind 'underpowered'
        when the data is genuinely flat — that is how a null becomes a maybe."""
        res = ta.analyse_three_arm(dataset(BASE, BASE, BASE), SCORE)
        assert res["verdict"].startswith("KILL"), res["verdict"]

    def test_a_harmful_content_effect_is_named_as_such(self):
        """ON is consistently WORSE than the matched placebo. This is the outcome
        the pink-elephant concern predicts, and it must not be reported as a null."""
        res = ta.analyse_three_arm(
            dataset(BASE, BASE, [b - 4 for b in BASE]), SCORE)
        assert res["verdict"].startswith("HARMFUL"), res["verdict"]

    def test_an_inconsistent_win_is_inconclusive_not_a_ship(self):
        """Two items move hugely, the rest not at all: the mean is carried by
        outliers, so the effect is not general even if the test is significant."""
        placebo = list(BASE)
        on = list(BASE)
        on[0] += 400
        on[1] += 400
        res = ta.analyse_three_arm(dataset(BASE, placebo, on), SCORE)
        assert res["verdict"].startswith(("INCONCLUSIVE", "KILL")), res["verdict"]
        assert res["primary"]["sign"]["favour_candidate"] < 10


class TestTheThirdArmEarnsItsKeep:
    def test_a_pure_length_effect_is_refused_rather_than_reported(self):
        """PLACEBO moves the metric on its own and ON adds nothing beyond it.
        A null primary here is a statement about the harness, not about content."""
        shifted = [b + 4 for b in BASE]
        res = ta.analyse_three_arm(dataset(BASE, shifted, shifted), SCORE)
        assert res["verdict"].startswith("UNINTERPRETABLE"), res["verdict"]

    def test_a_two_arm_test_would_have_shipped_a_length_effect(self):
        """The whole design, in one assertion.

        Same dataset as above. Read with two arms — OFF vs ON, which is all a
        two-arm harness can see — the effect is significant, consistent and
        positive: it ships. Read with three, the gain is entirely the placebo's,
        and the harness refuses. The two readings disagree, and the two-arm one is
        wrong.
        """
        shifted = [b + 4 for b in BASE]
        d = dataset(BASE, shifted, shifted)
        per = ta.arm_item_means(d, SCORE)
        items = ta.common_items(per)

        naive = ta.contrast(per, "off", "on", items=items)
        assert naive["p"] is not None and naive["p"] < 0.05
        assert naive["sign"]["favour_candidate"] == N
        assert (naive["mean_delta"] or 0) > 0        # a two-arm harness ships this

        primary = ta.contrast(per, *ta.PRIMARY, items=items)
        assert primary["mean_delta"] == 0            # content contributes nothing

        verdict = ta.analyse_three_arm(d, SCORE)["verdict"]
        assert verdict.startswith("UNINTERPRETABLE"), verdict

    def test_a_genuine_effect_on_top_of_a_length_effect_still_ships_with_the_caveat(self):
        """Both are real. The verdict must ship the content AND say that part of
        the naive OFF-to-ON gain was never the content."""
        placebo = [b + 3 for b in BASE]
        on = [b + 7 for b in BASE]
        res = ta.analyse_three_arm(dataset(BASE, placebo, on), SCORE)
        assert res["verdict"].startswith("SHIP"), res["verdict"]
        assert "not the content" in res["verdict"], res["verdict"]


class TestDecompositionArithmetic:
    def test_the_two_contrasts_sum_to_the_total(self):
        """(ON-PLACEBO) + (PLACEBO-OFF) == (ON-OFF). Cheap, and it catches a
        transposed arm pair — which produces a plausible report with the sign of
        the primary flipped."""
        d = dataset(BASE, [b + 3 for b in BASE], [b + 7 for b in BASE])
        res = ta.analyse_three_arm(d, SCORE)
        content = res["primary"]["mean_delta"]
        length = next(c for c in res["decomposition"] if c["contrast"] == "placebo - off")
        total = next(c for c in res["decomposition"] if c["contrast"] == "on - off")
        assert content + length["mean_delta"] == pytest.approx(total["mean_delta"])

    def test_the_primary_is_on_minus_placebo_not_on_minus_off(self):
        """Pinned because getting this backwards is the single mistake that would
        make the third arm decorative while the report still looks correct."""
        assert ta.PRIMARY == ("placebo", "on")
        d = dataset(BASE, [b + 3 for b in BASE], [b + 7 for b in BASE])
        res = ta.analyse_three_arm(d, SCORE)
        assert res["primary"]["contrast"] == "on - placebo"
        assert res["primary"]["mean_delta"] == pytest.approx(4.0)

    def test_it_reuses_the_two_arm_statistics_rather_than_reimplementing_them(self):
        """Two implementations of a paired permutation test will disagree
        eventually, and the disagreement will surface as a contested result rather
        than as a bug."""
        d = dataset(BASE, BASE, [b + 4 for b in BASE])
        per = ta.arm_item_means(d, SCORE)
        items = ta.common_items(per)
        direct = exact_permutation_p(paired_deltas(
            {i: per["placebo"][i] for i in items},
            {i: per["on"][i] for i in items}))
        assert ta.contrast(per, *ta.PRIMARY, items=items)["p"] == direct


class TestHolmAdjustment:
    """Reported beside the pre-registered primary, not instead of it, so a reader
    who treats all three contrasts as co-equal planned comparisons can."""

    def test_the_smallest_p_is_multiplied_by_the_number_of_comparisons(self):
        adj = ta.holm_adjust({"a": 0.01, "b": 0.04, "c": 0.5})
        assert adj["a"] == pytest.approx(0.03)      # 3 * 0.01
        assert adj["b"] == pytest.approx(0.08)      # 2 * 0.04
        assert adj["c"] == pytest.approx(0.5)       # 1 * 0.5

    def test_adjusted_values_are_monotone(self):
        """Step-down, not naive per-rank scaling: an adjusted p must never fall
        below one for a smaller raw p, or the ordering of the comparisons inverts."""
        adj = ta.holm_adjust({"a": 0.02, "b": 0.021, "c": 0.9})
        assert adj["a"] <= adj["b"] <= adj["c"]

    def test_it_is_capped_at_one(self):
        assert ta.holm_adjust({"a": 0.5, "b": 0.6, "c": 0.7})["a"] == 1.0

    def test_missing_p_values_stay_missing(self):
        """An n>20 contrast has no exact p. Treating None as 1.0 would let an
        unmeasured comparison silently inflate the others' correction."""
        adj = ta.holm_adjust({"a": 0.01, "b": None})
        assert adj["b"] is None
        assert adj["a"] == pytest.approx(0.01)      # m=1, not 2

    def test_the_report_carries_it_for_all_three_contrasts(self):
        res = ta.analyse_three_arm(
            dataset(BASE, [b + 3 for b in BASE], [b + 7 for b in BASE]), SCORE)
        assert set(res["holm_adjusted_p"]) == {
            "on - placebo", "placebo - off", "on - off"}

    def test_it_does_not_move_the_primary_verdict(self):
        """The pre-registered rule reads the raw primary p. Pinned because wiring
        Holm into the decision would quietly change what was pre-registered."""
        d = dataset(BASE, BASE, [b + 4 for b in BASE])
        res = ta.analyse_three_arm(d, SCORE)
        assert res["verdict"].startswith("SHIP")
        assert res["primary"]["p"] < 0.05


class TestLengthDiagnostic:
    def test_it_reports_mean_answer_length_per_arm(self):
        d = dataset([10] * 5, [10] * 5, [10] * 5)
        diag = ta.length_diagnostic(d)
        assert diag["mean_answer_chars"] == {"off": 10.0, "placebo": 10.0, "on": 10.0}
        assert diag["warning"] is None

    def test_it_warns_when_the_arms_diverge(self):
        """A treatment that changes output length can move a length-sensitive score
        without changing what the score measures."""
        d = dataset([10] * 5, [10] * 5, [40] * 5)
        diag = ta.length_diagnostic(d)
        assert diag["relative_spread"] > 0.2
        assert diag["warning"] and "length-sensitive" in diag["warning"]

    def test_errored_generations_do_not_drag_the_mean(self):
        d = dataset([20] * 5, [20] * 5, [20] * 5)
        d["on"].append({"probe_id": "zz", "answer": "[ERROR timeout]"})
        assert ta.length_diagnostic(d)["mean_answer_chars"]["on"] == 20.0


class TestLimitationsAreCarriedNotRemembered:
    def test_every_report_states_them(self):
        """These are the things analysis cannot fix. A report that omits them
        reads as cleaner than the design is."""
        res = ta.analyse_three_arm(dataset(BASE, BASE, BASE), SCORE)
        assert len(res["limitations"]) >= 5

    def test_they_name_the_off_arm_asymmetry(self):
        """The one a reader is most likely to miss: OFF has no constraint region,
        so it cannot be position-matched the way PLACEBO and ON are to each other."""
        joined = " ".join(ta.LIMITATIONS)
        assert "position-matched" in joined
        assert "seed" in joined, "the seed-is-not-enough point must survive"


class TestNoDataAndEdges:
    def test_no_common_items_reports_no_data_rather_than_a_verdict(self):
        d = {"off": rows({"a": 1}), "placebo": rows({"b": 1}), "on": rows({"c": 1})}
        res = ta.analyse_three_arm(d, SCORE)
        assert res["n_items_common"] == 0
        assert res["verdict"] == "NO DATA"

    def test_beyond_the_exhaustive_cap_it_says_so_instead_of_raising(self):
        """`exact_permutation_p` refuses n>20 on purpose. The report has to survive
        that and name it, because a crash here would look like a data problem."""
        big = list(range(10, 10 + 25))
        per = ta.arm_item_means(dataset(big, big, [b + 2 for b in big]), SCORE)
        c = ta.contrast(per, *ta.PRIMARY)
        assert c["n_items"] == 25
        assert c["p"] is None and "exhaustive cap" in c["p_note"]
