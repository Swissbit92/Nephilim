#!/usr/bin/env python3
"""Score and test the dial A/B (ADR-016 M2).

Reports THREE things, and the second two are the point:

  1. The headline paired test per arm contrast.
  2. Two NAMED failure conditions, decided before the data existed:
       FLAT       — no arm contrast clears p<0.05 on either scale.
       CARICATURE — the extreme contrast clears it but the midpoint is
                    indistinguishable from the low arm, i.e. a jump not a gradient.
     A two-arm read passes on both of those, which is why they are separate lines.
  3. The confounds, next to the result rather than in a footnote. This repo has a
     recorded precedent (PERSONA_FORMAT_OVERRIDE) where the target construct did
     not move (p=0.596) while reply LENGTH did (p=0.0005) — a confound moving while
     the construct does not is the expected outcome here, not the surprising one.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "evaluation" / "persona_eval"))

from assertiveness_metrics import assertiveness_score, paired_permutation, sign_test  # noqa: E402

CONFOUNDS = ["word_count", "emoji_rate", "endearment_rate", "exclamation_rate",
             "profanity_rate", "first_person_singular_rate", "second_person_rate",
             "question_mark_rate"]


def cell_key(row) -> tuple:
    return (row["scale"], row["dial"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="/tmp/dial_ab_raw.json")
    args = ap.parse_args()
    rows = json.loads(pathlib.Path(args.raw).read_text())

    # Median of the k draws per (probe, arm). Median, not mean: at k=3 one wild
    # temp-0.9 draw would drag a mean and cannot drag a median.
    comp = defaultdict(dict)
    conf = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        s = assertiveness_score(r["reply"])
        comp[cell_key(r)].setdefault(r["probe_id"], []).append(s["composite"])
        for c in CONFOUNDS:
            v = s["confounds"].get(c)
            if v is not None:
                conf[cell_key(r)][c].setdefault(r["probe_id"], []).append(v)
    med = {a: {p: st.median(v) for p, v in d.items()} for a, d in comp.items()}
    medc = {a: {c: {p: st.median(v) for p, v in pd.items()} for c, pd in d.items()}
            for a, d in conf.items()}

    arms = sorted(med.keys(), key=lambda a: (a[0], a[1]))
    probes = sorted(set().union(*[set(d) for d in med.values()]))
    print(f"{len(rows)} generations | {len(probes)} probes | {len(arms)} arms\n")
    print(f"{'arm':<14} {'mean':>8} {'median':>8} {'n':>4}")
    for a in arms:
        vals = [med[a][p] for p in probes if p in med[a]]
        print(f"{a[0]+':'+str(a[1]):<14} {st.mean(vals):>8.2f} {st.median(vals):>8.2f} {len(vals):>4}")

    def contrast(lo, hi, label):
        pair = [(med[lo][p], med[hi][p]) for p in probes if p in med[lo] and p in med[hi]]
        a = [x for x, _ in pair]
        b = [y for _, y in pair]
        p_perm = paired_permutation(b, a)
        s = sign_test(b, a)
        d = st.mean(b) - st.mean(a)
        print(f"\n{label}")
        print(f"  mean delta (hi-lo) = {d:+.2f}   paired permutation p = {p_perm:.4f}")
        print(f"  sign test: {s['wins']} wins, {s['losses']} losses, {s['ties']} ties "
              f"(n_eff={s['n_effective']}) p = {s['p']:.4f}")
        sig = p_perm < 0.05
        print(f"  -> {'SIGNIFICANT' if sig else 'not significant'} at p<0.05")
        return sig, d, p_perm

    results = {}
    for scale in ("narrow", "wide"):
        lo, hi = (scale, 0.1), (scale, 0.9)
        if lo in med and hi in med:
            results[scale] = contrast(lo, hi, f"=== {scale.upper()}: dial 0.1 vs 0.9 ===")
    mid = ("narrow", 0.5)
    grad = None
    if mid in med and ("narrow", 0.1) in med:
        grad = contrast(("narrow", 0.1), mid, "=== GRADIENT: dial 0.1 vs 0.5 (narrow) ===")

    print("\n" + "=" * 62)
    print("NAMED FAILURE CONDITIONS (declared before the data existed)")
    print("=" * 62)
    any_sig = any(v[0] for v in results.values())
    print(f"  FLAT       : {'YES — no contrast moved the score' if not any_sig else 'no'}")
    caric = bool(any_sig and grad is not None and not grad[0])
    print(f"  CARICATURE : {'YES — extremes differ but the midpoint does not (a jump, not a gradient)' if caric else 'no'}")
    if "narrow" in results and "wide" in results:
        n_sig, n_d, _ = results["narrow"]
        w_sig, w_d, _ = results["wide"]
        print(f"  CONTRAST PAYS OFF: narrow delta {n_d:+.2f} ({'sig' if n_sig else 'ns'}) "
              f"vs wide delta {w_d:+.2f} ({'sig' if w_sig else 'ns'})")

    print("\n" + "=" * 62)
    print("CONFOUNDS — did something OTHER than assertiveness move?")
    print("=" * 62)
    for scale in ("narrow", "wide"):
        lo, hi = (scale, 0.1), (scale, 0.9)
        if lo not in medc or hi not in medc:
            continue
        print(f"\n  {scale}:")
        for c in CONFOUNDS:
            pair = [(medc[lo][c][p], medc[hi][c][p]) for p in probes
                    if p in medc[lo].get(c, {}) and p in medc[hi].get(c, {})]
            if not pair:
                continue
            a = [x for x, _ in pair]
            b = [y for _, y in pair]
            p_perm = paired_permutation(b, a)
            flag = "  <-- MOVED" if p_perm < 0.05 else ""
            print(f"    {c:<28} {st.mean(a):>8.2f} -> {st.mean(b):>8.2f}  p={p_perm:.4f}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
