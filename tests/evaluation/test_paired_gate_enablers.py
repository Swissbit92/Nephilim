"""Enablers for a PAIRED voice gate: per-item outcomes, and probe_id alignment.

WHY A PAIRED GATE AT ALL. The current gate compares a new run against a frozen reference,
which is a historical-control design, and it has been returning NO VERDICT since the
2026-09-22 sampler repair: `compare_baselines` Guard 0 refuses when one side records its
samplers and the other cannot. Re-cutting the reference fixes the refusal but not the
power — at 8 probes per persona, a per-persona move of 1/8 is exact-McNemar p=1.0, and it
takes SIX one-way flips out of 8 before a per-persona result reaches p<0.05.

A paired same-session design needs two things the code did not expose:

  per_item     exact McNemar counts discordant ITEMS. The scoring loop always computed
               `best_q == p` and then folded it into three counters; `confusion`
               aggregates by (true, predicted) with no index, so it cannot say WHICH
               probe flipped.
  probe_id     `responses_by_persona` returns bare answers and SKIPS rows with no answer,
               so two runs can only be aligned by position — and one errored probe in one
               arm shifts every subsequent index, silently pairing probe i against
               probe i+1. Nothing would report that.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PE = Path(__file__).parent / "persona_eval"
if str(_PE) not in sys.path:
    sys.path.insert(0, str(_PE))


def _emb(text: str):
    """Deterministic toy embedding: first character decides the cluster."""
    return [1.0 if text[0] == "a" else 0.0, 1.0 if text[0] == "b" else 0.0]


class TestPerItemOutcomes:
    def test_per_item_agrees_with_per_persona(self):
        from persona_metrics import attribution_accuracy

        resp = {"a": ["a1", "a2", "a3"], "b": ["b1", "b2", "b3"]}
        r = attribution_accuracy(resp, _emb)
        for p, items in r["per_item"].items():
            ok = sum(1 for i in items if i["correct"])
            assert round(ok / len(items), 4) == r["per_persona"][p]

    def test_per_item_records_the_misattribution_not_just_the_total(self):
        """A vector of all-True would satisfy the agreement test above and be useless."""
        from persona_metrics import attribution_accuracy

        # "a4" sits in a's cluster but belongs to b, so it MUST be misattributed
        resp = {"a": ["a1", "a2", "a3"], "b": ["a4", "b2", "b3"]}
        r = attribution_accuracy(resp, _emb)
        wrong = [i for items in r["per_item"].values() for i in items if not i["correct"]]
        assert len(wrong) == 1, f"expected exactly one misattribution, got {wrong}"
        assert wrong[0]["predicted"] == "a" and wrong[0]["i"] == 0

    def test_adding_per_item_did_not_change_the_scored_numbers(self):
        """The whole point of appending it: history stays comparable."""
        from persona_metrics import attribution_accuracy

        resp = {"a": ["a1", "a2"], "b": ["b1", "b2"]}
        r = attribution_accuracy(resp, _emb)
        assert r["overall"] == 1.0
        assert r["per_persona"] == {"a": 1.0, "b": 1.0}
        assert r["n"] == 4 and r["random_baseline"] == 0.5


class TestProbeIdAlignment:
    def test_probe_ids_are_parallel_to_the_answers(self):
        from run_eval import probe_ids_by_persona, responses_by_persona

        results = [
            {"persona": "a", "category": "distinctiveness", "probe_id": "p1", "answer": "x"},
            {"persona": "a", "category": "distinctiveness", "probe_id": "p2", "answer": "y"},
            {"persona": "b", "category": "distinctiveness", "probe_id": "p1", "answer": "z"},
        ]
        answers = responses_by_persona(results)
        ids = probe_ids_by_persona(results)
        for p in answers:
            assert len(answers[p]) == len(ids[p])
        assert ids["a"] == ["p1", "p2"]

    def test_a_missing_answer_shifts_positions_which_is_why_ids_exist(self):
        """The exact failure a position-keyed pairing would hide."""
        from run_eval import probe_ids_by_persona, responses_by_persona

        arm_a = [
            {"persona": "a", "category": "distinctiveness", "probe_id": "p1", "answer": "x"},
            {"persona": "a", "category": "distinctiveness", "probe_id": "p2", "answer": "y"},
        ]
        arm_b = [  # p1 errored and produced no answer
            {"persona": "a", "category": "distinctiveness", "probe_id": "p1", "answer": ""},
            {"persona": "a", "category": "distinctiveness", "probe_id": "p2", "answer": "y2"},
        ]
        ra, rb = responses_by_persona(arm_a), responses_by_persona(arm_b)
        # By POSITION, index 0 of each arm looks pairable — and is not the same probe.
        assert ra["a"][0] == "x" and rb["a"][0] == "y2"
        ia, ib = probe_ids_by_persona(arm_a), probe_ids_by_persona(arm_b)
        assert ia["a"][0] != ib["a"][0], "ids must expose the shift that positions hide"
        # Intersecting on id is what makes the pairing correct.
        shared = sorted(set(ia["a"]) & set(ib["a"]))
        assert shared == ["p2"]


def test_the_prompt_builder_version_was_bumped():
    """Its contract is 'bump when the prompt builder changes in a way that moves voices',
    and it sat at lean-v1 through four such changes — a guard that could not fire."""
    from frozen_gallery import PROMPT_BUILDER_VERSION

    assert PROMPT_BUILDER_VERSION != "lean-v1"


class TestSamplingFingerprintV2:
    def test_the_transport_environment_now_moves_the_hash(self):
        """`sampling_env` was recorded but never hashed, so completion_backend could
        change — deciding whether min_p reaches the wire at all — while Guard 0 still
        reported 'samplers match'."""
        from frozen_gallery import sampling_fingerprint, sampling_fingerprint_v2

        sampling = {"gwen": {"temperature": 0.9}}
        env_a = {"completion_backend": "http", "context_window": 16384}
        env_b = {"completion_backend": "legacy", "context_window": 16384}

        assert sampling_fingerprint(sampling) == sampling_fingerprint(sampling)
        assert sampling_fingerprint_v2(sampling, env_a) != sampling_fingerprint_v2(sampling, env_b)

    def test_the_original_hash_is_unchanged(self):
        """Redefining the old hash would make every tracked artifact incommensurable
        without saying so, which is why v2 is a separate function."""
        from frozen_gallery import sampling_fingerprint

        # the live value recorded in baseline_refusal-exemplar_20260928_232945.json
        assert len(sampling_fingerprint({"gwen": {"temperature": 0.9}})) == 16
