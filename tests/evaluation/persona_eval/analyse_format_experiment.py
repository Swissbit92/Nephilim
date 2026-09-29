# tests/evaluation/persona_eval/analyse_format_experiment.py
"""Scores the analytical-format experiment against analytical_format_prereg.json.

Written BEFORE the data landed, and it reads the pre-registration rather than
hard-coding the rule, so the endpoint and the decision cannot quietly move once
the numbers are visible. Three times in this programme a promising mean has
failed a paired test; the defence is to fix the rule first and let a script
apply it.

Pre-registered design:
  primary endpoint : causal_density, per-item mean over repeats, paired across items
  primary test     : EXACT item-level permutation over all 2^n sign flips
  confirmatory     : exact sign test on the item-level paired differences
  secondary        : descriptive only, excluded from the decision

Why the permutation is at ITEM level: repeats within a probe are correlated, so
pooling all 60 samples per arm as independent is pseudo-replication. Repeats
sharpen each item's estimate; they buy no degrees of freedom. The unit of
independence is the probe.

Pure scoring — unit-tested headless in test_analyse_format_experiment.py.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from itertools import product
from pathlib import Path
from statistics import mean
from typing import Dict, List, Sequence

_HERE = Path(__file__).parent
_PREREG = _HERE / "analytical_format_prereg.json"


def load_prereg(path: Path | str | None = None) -> dict:
    with open(Path(path) if path else _PREREG, encoding="utf-8") as f:
        return json.load(f)


def item_means(rows: List[dict], metric_fn) -> Dict[str, float]:
    """{probe_id: mean(metric over that probe's repeats)}.

    Errored/blank generations are dropped rather than scored as zero — an empty
    answer is a harness fault, not weak reasoning, and averaging it in would
    understate the arm that happened to hit one.
    """
    per: Dict[str, List[float]] = {}
    for r in rows:
        a = r.get("answer") or ""
        if not a or a.startswith("[ERROR"):
            continue
        per.setdefault(r["probe_id"], []).append(metric_fn(a))
    return {k: mean(v) for k, v in per.items() if v}


def paired_deltas(control: Dict[str, float], candidate: Dict[str, float]) -> List[float]:
    """candidate - control, over probes present in BOTH arms, in stable order."""
    return [candidate[k] - control[k] for k in sorted(set(control) & set(candidate))]


def exact_permutation_p(deltas: Sequence[float]) -> float | None:
    """Two-sided exact permutation test over all 2^n arm-label flips.

    Exhaustive, not sampled: at n=12 that is 4096 assignments, so there is no
    reason to approximate. Under the null the arm label is exchangeable within
    each item, so flipping a delta's sign is the null-consistent relabelling.
    """
    n = len(deltas)
    if n == 0:
        return None
    if n > 20:  # 2^20 ≈ 1M — guard against an accidental exhaustive blow-up
        raise ValueError(f"exhaustive permutation refuses n={n}; use a sampled test")
    observed = abs(mean(deltas))
    hits = 0
    for signs in product((1, -1), repeat=n):
        if abs(mean(s * d for s, d in zip(signs, deltas))) >= observed - 1e-12:
            hits += 1
    return round(hits / (2 ** n), 6)


def permutation_p(deltas: Sequence[float], draws: int = 50_000,
                  seed: int = 20260929) -> dict:
    """Two-sided sign-flip permutation p that survives n>20. Exact below the cap.

    `exact_permutation_p` refuses n>20 by design, and `contrast()` therefore returned
    `p: None` above it. That is correct behaviour and a dead end for this programme's
    next question: the 20-probe run's own pre-registration says the fix for an
    underpowered result is MORE PROBES, and 60 probes produce ~40 discordant pairs. A
    primary endpoint that silently stops producing a p-value exactly when the design is
    finally large enough is worse than no test.

    Conventions copied from `three_arm.omnibus_p` rather than invented, so the two
    permutation tests in this package report the same way:

      * exact by enumeration while 2**n stays under the cap, sampled beyond it
      * `mc_se` is always returned, because a sampled p of 0.04 with an SE of 0.01 is
        not the same claim as an exact 0.04 and reporting them identically invites
        reading noise as a result
      * add-one on the sampled path, so a sample never reports p=0
    """
    # Zeros are KEPT, matching exact_permutation_p. They are not "discordant pairs" to
    # be dropped the way signed-rank drops them: the statistic here is the mean delta,
    # and a zero is real evidence that pulls it toward zero. Filtering them turned a
    # genuinely FLAT design into "no data" and the decision rule read NO DATA where it
    # should have read KILL -- caught by test_a_flat_design_is_killed_not_called_
    # underpowered, which is exactly what a planted-ground-truth suite is for.
    d = list(deltas)
    n = len(d)
    if n == 0:
        return {"p": None, "n": 0, "method": "no data", "mc_se": None}

    observed = abs(mean(d))
    mag = [abs(x) for x in d]

    if n <= 20:
        hits = sum(
            1 for signs in product((1, -1), repeat=n)
            if abs(mean(s * m for s, m in zip(signs, mag))) >= observed - 1e-12
        )
        return {"p": round(hits / 2 ** n, 6), "n": n,
                "method": f"exact ({2 ** n} sign assignments)", "mc_se": 0.0}

    rng = random.Random(seed)
    hits = 0
    for _ in range(draws):
        if abs(mean(rng.choice((1, -1)) * m for m in mag)) >= observed - 1e-12:
            hits += 1
    p = (hits + 1) / (draws + 1)
    return {"p": round(p, 6), "n": n,
            "method": f"sampled ({draws} draws, seed={seed})",
            "mc_se": round(math.sqrt(p * (1 - p) / draws), 6)}


def exact_sign_test(deltas: Sequence[float]) -> dict:
    """Exact two-sided binomial sign test. Ties are dropped, as is conventional."""
    pos = sum(1 for d in deltas if d > 0)
    neg = sum(1 for d in deltas if d < 0)
    n = pos + neg
    if n == 0:
        return {"favour_candidate": pos, "favour_control": neg, "ties": len(deltas), "p": None}
    k = min(pos, neg)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return {
        "favour_candidate": pos,
        "favour_control": neg,
        "ties": len(deltas) - n,
        "p": round(min(1.0, 2 * tail), 6),
    }


def paired_cohens_d(deltas: Sequence[float]) -> float | None:
    """Cohen's d for a paired design: mean(delta) / sd(delta). None if undefined."""
    n = len(deltas)
    if n < 2:
        return None
    m = mean(deltas)
    var = sum((d - m) ** 2 for d in deltas) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None  # a constant difference has no scale — report it as undefined
    return round(m / sd, 4)


def decide(deltas: Sequence[float], sign: dict, p: float | None,
           d: float | None, min_items: int = 10, alpha: float = 0.05) -> str:
    """Apply the pre-registered rule. No judgement, no post-hoc metric choice."""
    if p is None:
        return "NO DATA"
    significant = p < alpha
    directional = mean(deltas) > 0
    enough_items = sign["favour_candidate"] >= min_items
    if significant and directional and enough_items:
        return "SHIP — candidate reasons more densely, and consistently across items"
    if significant and directional and not enough_items:
        return (
            f"INCONCLUSIVE — significant (p={p}) but only "
            f"{sign['favour_candidate']}/{len(deltas)} items favour the candidate; "
            "the pre-registered rule requires >=" f"{min_items}. A few large items "
            "can carry a mean without the effect being general."
        )
    if not significant and d is not None and abs(d) >= 0.5:
        return (
            f"INCONCLUSIVE — p={p} but d={d}. At n={len(deltas)} a large true "
            "effect still has only ~71-88% power, so this means the design "
            "cannot see it, NOT that it does not work. Follow-up = MORE ITEMS "
            "(n~18-20), not more repeats."
        )
    if not significant and (d is None or abs(d) < 0.3):
        return f"KILL — p={p}, d={d}: genuinely flat, not merely underpowered."
    return (
        f"INCONCLUSIVE — p={p}, d={d} sits between the pre-registered KILL "
        "(d<0.3) and INCONCLUSIVE (d>=0.5) bands."
    )


def analyse(control_rows: List[dict], candidate_rows: List[dict]) -> dict:
    import persona_metrics as pm

    primary = "causal_density"
    metrics = [primary, "words", "numeric_density", "contrastive_density"]
    out: dict = {"primary_endpoint": primary, "secondary_are_descriptive_only": True}

    for m in metrics:
        fn = (lambda k: (lambda t: pm.depth_profile(t)[k]))(m)
        c, k = item_means(control_rows, fn), item_means(candidate_rows, fn)
        deltas = paired_deltas(c, k)
        entry = {
            "n_items": len(deltas),
            "control_mean": round(mean(c.values()), 4) if c else None,
            "candidate_mean": round(mean(k.values()), 4) if k else None,
            "mean_delta": round(mean(deltas), 4) if deltas else None,
            "sign_test": exact_sign_test(deltas),
            "cohens_d": paired_cohens_d(deltas),
        }
        if m == primary:
            entry["permutation_p"] = exact_permutation_p(deltas)
            entry["VERDICT"] = decide(deltas, entry["sign_test"],
                                      entry["permutation_p"], entry["cohens_d"])
        out[m] = entry
    return out


def main() -> int:  # pragma: no cover - CLI
    import sys
    sys.path.insert(0, str(_HERE))
    ap = argparse.ArgumentParser(description="Score the pre-registered format experiment")
    ap.add_argument("--control", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--save")
    args = ap.parse_args()

    control = json.load(open(args.control, encoding="utf-8"))
    candidate = json.load(open(args.candidate, encoding="utf-8"))
    rep = analyse(control, candidate)
    prereg = load_prereg()

    prim = rep[rep["primary_endpoint"]]
    print(f"PRE-REGISTERED PRIMARY ENDPOINT: {rep['primary_endpoint']}")
    print(f"  control {prim['control_mean']} -> candidate {prim['candidate_mean']} "
          f"(delta {prim['mean_delta']})")
    print(f"  items favouring candidate: {prim['sign_test']['favour_candidate']}"
          f"/{prim['n_items']}  (sign p={prim['sign_test']['p']})")
    print(f"  exact permutation p = {prim['permutation_p']}   Cohen's d = {prim['cohens_d']}")
    print(f"\n  {prim['VERDICT']}\n")
    print("SECONDARY (descriptive only, excluded from the decision):")
    for m in ("words", "numeric_density", "contrastive_density"):
        e = rep[m]
        print(f"  {m:<22} {e['control_mean']} -> {e['candidate_mean']}  "
              f"(d={e['cohens_d']}, {e['sign_test']['favour_candidate']}/{e['n_items']} items)")
    print(f"\nvoice gate: {prereg['voice_gate']['rule']}")

    if args.save:
        json.dump(rep, open(args.save, "w"), indent=2)
        print(f"\nreport -> {args.save}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
