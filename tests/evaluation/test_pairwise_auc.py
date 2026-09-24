# tests/evaluation/test_pairwise_auc.py
"""Mean pairwise AUC as the distinctiveness headline, and why 1/N could not be.

`attribution_accuracy` scores an argmax over N persona centroids, so its chance
level is 1/N. Three consequences, each demonstrated below against the SAME
synthetic data so the comparison is not an argument:

  * its headline is not comparable between galleries of different size, so adding
    a persona invalidates every prior baseline — the roadmap wants the swap done
    BEFORE a 9th persona for exactly this reason;
  * shrinking the gallery INFLATES it, because removing competitors makes the
    argmax easier while the personas are unchanged (a measured trap in this repo —
    the frozen-gallery mechanism exists to patch it);
  * it discards the margin, so a response that barely lands on the right centroid
    scores the same as one that lands overwhelmingly.

Pairwise AUC is 0.5 at chance for any N, averages over pairs rather than
competing within a set, and ranks rather than thresholds.

`test_shrinking_the_gallery_inflates_attribution_but_not_auc` is the load-bearing
one: it shows the two metrics disagreeing on identical data, with the old metric
moving for a reason that has nothing to do with voice.
"""
from __future__ import annotations

import random
import sys
import zlib
from pathlib import Path

import pytest

_PE = Path(__file__).parent / "persona_eval"
if str(_PE) not in sys.path:
    sys.path.insert(0, str(_PE))

import persona_metrics as pm  # noqa: E402

DIM = 8


def _seed(*parts: object) -> int:
    """Reproducible seed from arbitrary parts.

    NOT `hash()`: string hashing is randomized per process unless PYTHONHASHSEED is
    set, so test data built from it differs between runs. The first version of this
    file used `hash((p, j))` and produced a test that passed in isolation and failed
    in the full suite — the geometry it generated was a different geometry each
    process, so a pair planted as confusable was sometimes separable by luck.
    """
    return zlib.crc32(repr(parts).encode()) & 0xFFFFFFFF


def _vec(text: str) -> list[float]:
    """Deterministic pseudo-embedding. The test data encodes its own geometry in
    the response strings, so `embed_fn` needs no model and the expected answer is
    a property of the construction rather than of a checkpoint."""
    seed = int(text.split("#")[1])
    rng = random.Random(seed)
    return [rng.gauss(0, 1) for _ in range(DIM)]


def separated(personas: list[str], k: int = 4, jitter: float = 0.02) -> dict:
    """Each persona sits on its own axis; responses are that axis plus noise.
    Perfectly separable by construction."""
    out = {}
    for idx, p in enumerate(personas):
        rows = []
        for j in range(k):
            rng = random.Random(_seed(p, j))
            v = [jitter * rng.gauss(0, 1) for _ in range(DIM)]
            v[idx % DIM] += 1.0
            rows.append(v)
        out[p] = rows
    return out


def _embed_from(table: dict):
    """Turn a {persona: [vector]} table into (responses_by_persona, embed_fn)."""
    responses, lookup = {}, {}
    for p, vecs in table.items():
        names = []
        for i, v in enumerate(vecs):
            key = f"{p}#resp{i}"
            lookup[key] = v
            names.append(key)
        responses[p] = names
    return responses, lambda s: lookup[s]


EIGHT = ["eeva", "aegis", "solace", "nyx", "cipher", "aurora", "gwen", "gojo"]


def _null_mean(k: int, trials: int = 60, seed: int = 0) -> float:
    """Mean AUC for two personas drawn from the SAME distribution — true answer 0.5.

    Independent `random.Random` per trial. A shared generator makes trials
    correlated, which is how the first version of this helper produced a mean of
    0.371 at k=8 and made a correct metric look biased.
    """
    vals = []
    for t in range(trials):
        rng = random.Random(1000 * k + 7919 * seed + t)

        def draw(n, _r=rng):
            return [[1.0 + 0.3 * _r.gauss(0, 1) for _ in range(DIM)] for _ in range(n)]

        vals.append(pm.pairwise_auc(*_embed_from({"a": draw(k), "b": draw(k)}))["overall"])
    return sum(vals) / len(vals)


class TestChanceIsHalfRegardlessOfN:
    @pytest.mark.parametrize("n", [2, 3, 5, 8])
    def test_unstructured_voices_sit_near_one_half(self, n):
        """Random embeddings carry no persona signal, so AUC must land near 0.5 —
        and the TARGET must not move with n, which is the whole point."""
        table = {f"p{i}": [[random.Random(_seed(i, j, d)).gauss(0, 1)
                            for d in range(DIM)] for j in range(6)]
                 for i in range(n)}
        responses, fn = _embed_from(table)
        res = pm.pairwise_auc(responses, fn)
        assert res["random_baseline"] == 0.5
        assert 0.25 < res["overall"] < 0.75, res["overall"]

    def test_the_declared_baseline_is_constant_while_attributions_is_not(self):
        """Side by side. The old metric's own yardstick changes with the gallery."""
        for n in (2, 4, 8):
            responses, fn = _embed_from(separated(EIGHT[:n]))
            assert pm.pairwise_auc(responses, fn)["random_baseline"] == 0.5
            assert pm.attribution_accuracy(responses, fn)["random_baseline"] == round(1 / n, 4)


class TestPerfectAndInvertedSeparation:
    def test_distinct_voices_score_one(self):
        responses, fn = _embed_from(separated(EIGHT))
        res = pm.pairwise_auc(responses, fn)
        assert res["overall"] == pytest.approx(1.0, abs=1e-9)
        assert all(v == pytest.approx(1.0) for v in res["pairs"].values())

    def test_duplicated_response_sets_invert_to_zero_and_that_is_informative(self):
        """Two personas holding the SAME responses return 0.0, not 0.5, because each
        response is excluded from its own set while a copy survives in the other's.

        Documented rather than smoothed away: an AUC near 0 is not "very
        confusable", it is INVERTED, and for persona data that means the response
        sets are shared or the labels are swapped — a harness fault. Averaging it to
        0.5 would hide exactly the failure worth shouting about."""
        shared = [[1.0, 0, 0, 0, 0, 0, 0, 0], [0.9, 0.1, 0, 0, 0, 0, 0, 0],
                  [1.1, -0.1, 0, 0, 0, 0, 0, 0], [1.0, 0.05, 0, 0, 0, 0, 0, 0]]
        responses, fn = _embed_from({"a": [r[:] for r in shared],
                                     "b": [r[:] for r in shared]})
        assert pm.pairwise_auc(responses, fn)["overall"] == pytest.approx(0.0)

    def test_statistically_indistinguishable_personas_land_near_one_half(self):
        """The honest indistinguishability case — same distribution, DISTINCT draws.
        This is what a null actually looks like, and the case the first
        implementation got wrong (0.25 at k=4, see TestTheNullDoesNotCollapseAtSmallK).

        Asserted on the MEAN over independent trials, not on one draw. A single
        draw at k=8 spans 0.09-0.77 under the null, so a test written against one
        would be flaky for a reason that is a property of the metric rather than a
        fault — the first version of this test asserted a single shared-rng run and
        failed at 0.371."""
        seen = [_null_mean(8, trials=1, seed=s) for s in range(120)]
        assert 0.40 <= sum(seen) / len(seen) <= 0.56, sum(seen) / len(seen)


class TestTheNullDoesNotCollapseAtSmallK:
    """The defect the first implementation of this metric had, pinned.

    v1 scored `cos(v, centroid_A) - cos(v, centroid_B)`, holding v out of its own
    centroid only. A centroid of n-1 samples has different geometry from one of n,
    so the persona being scored was systematically handicapped — and with
    indistinguishable personas, which must sit at chance, it returned **0.25 at
    k=4**. The headline claim "chance is 0.5" was therefore false at every sample
    size this project actually runs.

    A mean of pairwise cosines is unbiased in the number of terms, which removes it.
    """

    @pytest.mark.parametrize("k", [3, 4, 6, 8, 20])
    def test_the_null_stays_near_one_half_at_every_sample_size(self, k):
        m = _null_mean(k)
        assert 0.40 <= m <= 0.58, f"k={k} null mean {m:.3f} — chance has moved"

    def test_it_does_not_drift_systematically_between_small_and_large_k(self):
        """The v1 signature was a null that FELL as k fell. A residual bias is
        acceptable; one that scales with sample size is not, because the same voices
        then score differently in a shorter run."""
        small, large = _null_mean(4), _null_mean(20)
        assert abs(small - large) < 0.12, (small, large)

    def test_the_residual_bias_is_downward_not_upward(self):
        """A null slightly BELOW 0.5 is conservative — it under-claims
        distinctiveness. Upward bias would manufacture it, which is the direction
        that matters."""
        assert _null_mean(6) < 0.52


class TestThePowerWarningIsCarriedNotRemembered:
    """The variance finding, which is the metric's real constraint.

    Measured: at k=4 a single pair's AUC spans 0.00-1.00 under the null — one pair
    can read a perfect 1.0 on pure noise. So `pairs` and `most_confusable_pair` are
    NOT usable for "which persona should I enrich" at the sample sizes this project
    runs, and an earlier version of the docstring offered them for exactly that.
    """

    def test_a_small_run_warns_that_no_single_pair_is_evidence(self):
        responses, fn = _embed_from(separated(EIGHT, k=4))
        res = pm.pairwise_auc(responses, fn)
        assert res["min_responses_per_persona"] == 4
        assert res["power_warning"] and "no individual entry" in res["power_warning"]
        assert res["single_pair_null_span"] == [0.0, 1.0]

    def test_a_large_run_does_not_warn(self):
        responses, fn = _embed_from(separated(EIGHT, k=20))
        assert pm.pairwise_auc(responses, fn)["power_warning"] is None

    def test_the_span_widens_as_k_shrinks(self):
        """Pinned so the table cannot be flattened into a single reassuring number."""
        wide = pm.pairwise_auc(*_embed_from(separated(EIGHT[:3], k=4)))
        narrow = pm.pairwise_auc(*_embed_from(separated(EIGHT[:3], k=40)))
        w = wide["single_pair_null_span"]
        n = narrow["single_pair_null_span"]
        assert (w[1] - w[0]) > (n[1] - n[0])

    def test_a_single_pair_can_read_one_point_zero_on_pure_noise(self):
        """The claim, demonstrated rather than asserted from the table. If this ever
        stops being true the warning can be relaxed — until then it stays."""
        hits = 0
        for s in range(200):
            rng = random.Random(4242 + s)

            def draw(n, _r=rng):
                return [[1.0 + 0.3 * _r.gauss(0, 1) for _ in range(DIM)] for _ in range(n)]

            res = pm.pairwise_auc(*_embed_from({"a": draw(4), "b": draw(4)}))
            if res["pairs"]["a|b"] >= 1.0:
                hits += 1
        assert hits > 0, "expected at least one perfect AUC from noise at k=4"


class TestTheSubsetShrinkTrap:
    """The reason the swap is worth doing."""

    @staticmethod
    def _partially_confusable() -> dict:
        """Six well-separated personas plus one pair deliberately blurred together,
        so there is real signal AND a real confusion to find."""
        table = separated(EIGHT[:6])
        base = [1.0, 1.0, 0, 0, 0, 0, 0, 0]
        for name, shift in (("gwen", 0.05), ("gojo", -0.05)):
            rows = []
            for j in range(4):
                rng = random.Random(_seed(name, j))
                v = [b + shift + 0.3 * rng.gauss(0, 1) for b in base]
                rows.append(v)
            table[name] = rows
        return table

    def test_shrinking_the_gallery_inflates_attribution_but_not_auc(self):
        """Identical personas, identical responses; only the SET of competitors
        changes. Attribution's headline moves because the argmax got easier.
        Pairwise AUC does not, because dropping a persona removes that persona's
        pairs rather than making the remaining comparisons easier."""
        full = self._partially_confusable()
        subset = {k: v for k, v in full.items() if k in EIGHT[:4]}

        r_full, fn_full = _embed_from(full)
        r_sub, fn_sub = _embed_from(subset)

        attr_full = pm.attribution_accuracy(r_full, fn_full)["overall"]
        attr_sub = pm.attribution_accuracy(r_sub, fn_sub)["overall"]
        auc_full = pm.pairwise_auc(r_full, fn_full)["overall"]
        auc_sub = pm.pairwise_auc(r_sub, fn_sub)["overall"]

        # The four surviving personas are perfectly separated, so on the subset the
        # old metric is at its ceiling while the full gallery pays for the blur.
        assert attr_sub > attr_full, (attr_sub, attr_full)
        # AUC on the subset only drops the blurred pairs; the surviving pairs score
        # exactly what they scored before.
        full_pairs = pm.pairwise_auc(r_full, fn_full)["pairs"]
        sub_pairs = pm.pairwise_auc(r_sub, fn_sub)["pairs"]
        for key, v in sub_pairs.items():
            assert full_pairs[key] == pytest.approx(v, abs=1e-9), key
        assert auc_sub >= auc_full  # only because the bad pairs left, not the task

    def test_a_per_pair_score_survives_the_gallery_changing(self):
        """The property that makes before/after enrichment possible: a pair's AUC
        is a statement about those two personas and nothing else, so it is
        comparable across runs with different galleries. No attribution number is."""
        full = self._partially_confusable()
        r_full, fn_full = _embed_from(full)
        dropped = {k: v for k, v in full.items() if k != "aurora"}
        r_drop, fn_drop = _embed_from(dropped)
        a = pm.pairwise_auc(r_full, fn_full)["pairs"]["gwen|gojo"]
        b = pm.pairwise_auc(r_drop, fn_drop)["pairs"]["gwen|gojo"]
        assert a == pytest.approx(b, abs=1e-9)


class TestItNamesWhatToFix:
    def test_the_confusable_pair_is_identified(self):
        """An overall number cannot tell an operator which persona to enrich; the
        pair matrix can, and this is the enrichment question."""
        full = TestTheSubsetShrinkTrap._partially_confusable()
        responses, fn = _embed_from(full)
        res = pm.pairwise_auc(responses, fn)
        assert set(res["most_confusable_pair"]["pair"].split("|")) == {"gwen", "gojo"}
        assert res["most_confusable_pair"]["auc"] < 0.9

    def test_per_persona_averages_over_that_personas_pairs(self):
        responses, fn = _embed_from(separated(EIGHT[:4]))
        res = pm.pairwise_auc(responses, fn)
        assert set(res["per_persona"]) == set(EIGHT[:4])
        assert res["n_pairs"] == 6  # C(4,2)


class TestLeaveOneOut:
    def test_a_response_is_never_in_the_centroid_it_is_scored_against(self):
        """Without hold-out a response pulls its own centroid toward itself and
        every AUC is optimistic. With k=2 the effect is largest, so a metric that
        forgot to hold out would score 1.0 on data that is genuinely ambiguous."""
        a = [[1.0, 0, 0, 0, 0, 0, 0, 0], [0, 1.0, 0, 0, 0, 0, 0, 0]]
        b = [[1.0, 0.01, 0, 0, 0, 0, 0, 0], [0.01, 1.0, 0, 0, 0, 0, 0, 0]]
        responses, fn = _embed_from({"a": a, "b": b})
        assert pm.pairwise_auc(responses, fn)["overall"] < 1.0

    def test_it_refuses_a_persona_with_one_response(self):
        responses, fn = _embed_from({"a": [[1.0] + [0.0] * 7], "b": separated(["b"])["b"]})
        with pytest.raises(ValueError, match="needs >=2"):
            pm.pairwise_auc(responses, fn)


class TestFrozenGallerySemanticsMatchAttribution:
    def test_a_frozen_persona_competes_but_is_not_scored(self):
        responses, fn = _embed_from(separated(EIGHT[:4]))
        res = pm.pairwise_auc(responses, fn, frozen_personas={"solace", "nyx"})
        assert res["frozen_personas"] == ["nyx", "solace"]
        assert set(res["per_persona"]) == {"eeva", "aegis"}
        # the frozen-frozen pair carries no scored side and is omitted
        assert "nyx|solace" not in res["pairs"] and "solace|nyx" not in res["pairs"]
        # but frozen-vs-active pairs are kept, so the competitors stay live
        assert any("solace" in k for k in res["pairs"])

    def test_all_frozen_is_an_error_not_an_empty_report(self):
        responses, fn = _embed_from(separated(EIGHT[:3]))
        with pytest.raises(ValueError, match="non-frozen"):
            pm.pairwise_auc(responses, fn, frozen_personas=set(EIGHT[:3]))

    def test_fewer_than_two_personas_is_an_error(self):
        responses, fn = _embed_from(separated(["only"]))
        with pytest.raises(ValueError, match=">=2 personas"):
            pm.pairwise_auc(responses, fn)


class TestItDoesNotDisturbTheExistingMetric:
    def test_attribution_accuracy_is_untouched(self):
        """Additive change. The old metric stays available for comparison against
        the frozen baselines that were measured with it."""
        responses, fn = _embed_from(separated(EIGHT))
        res = pm.attribution_accuracy(responses, fn)
        assert res["overall"] == 1.0
        assert res["random_baseline"] == round(1 / 8, 4)
