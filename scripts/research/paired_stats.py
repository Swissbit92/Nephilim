"""Exact paired tests for probe-level A/B analysis. One implementation, shared.

WHY THIS FILE EXISTS. `graph_vs_nograph_ab.py` carried an inline `wilcoxon()` that
assigned distinct integer ranks 1..n with no midrank correction for ties. On the graph
A/B's own data 12 of 14 non-zero differences are tied in |d| (six at 0.1667, five at
0.3333), and Python's stable sort breaks those ties by PROBE INDEX -- so the p-value was
a function of the order probes happen to be listed in, moving 0.0245 to 0.1040 over
random orderings of identical data. Measured, not theorised.

That is the worst possible failure for a primary endpoint: it is silent, it is
reproducible on any given day, and it straddles 0.05.

Three tests are provided deliberately, because they disagree in an informative way and a
run should report all three rather than pick one:

  signed_rank_exact   magnitude-aware, midranks, exact by enumeration
  sign_flip_exact     magnitude-aware, no ranks at all -- immune to ties by construction
  sign_test_exact     direction only; discards magnitude

On the graph A/B the first two give 0.0491 and 0.0460 while the sign test gives 0.1796,
and that spread IS the finding: the effect is carried by how much each probe moved, not
by how many moved. Reporting only the significant one would hide that.

All three enumerate exactly rather than approximating, so they are valid at the small n a
probe-level design produces -- which is the whole reason the analysis is at probe level.
"""
from __future__ import annotations

import itertools
from math import comb

#: Above this, exhaustive enumeration stops being free (2^25 = 34M). The callers in this
#: repo run at n<=60 probes with far fewer discordant pairs, but a cap that is never
#: reached is still better than a silent 20-minute stall.
_MAX_EXACT = 22


def _nonzero_diffs(a: list[float], b: list[float]) -> list[float]:
    if len(a) != len(b):
        raise ValueError(f"paired test needs equal lengths, got {len(a)} and {len(b)}")
    return [y - x for x, y in zip(a, b) if y != x]


def _midranks(vals: list[float]) -> list[float]:
    """Average ranks within each tie group -- the correction the broken version lacked."""
    idx = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0.0] * len(vals)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and vals[idx[j + 1]] == vals[idx[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[idx[k]] = avg
        i = j + 1
    return out


def signed_rank_exact(a: list[float], b: list[float]) -> tuple[float, int]:
    """Exact two-sided Wilcoxon signed-rank with midranks. Returns (p, n_discordant).

    Zeroes are dropped (Wilcoxon's own method) rather than split, and the count of
    dropped pairs is returned so a caller can see how much of its nominal n carried no
    information -- on the 20-probe graph A/B six pairs were tied and four of those were
    probes that never breached in EITHER arm.
    """
    d = _nonzero_diffs(a, b)
    n = len(d)
    if n == 0:
        return float("nan"), 0
    if n > _MAX_EXACT:
        raise ValueError(f"{n} discordant pairs exceeds the exact cap {_MAX_EXACT}")
    rk = _midranks([abs(x) for x in d])
    obs = sum(rk[i] for i in range(n) if d[i] > 0)
    tot = sum(rk)
    hits = sum(
        1 for signs in itertools.product((0, 1), repeat=n)
        if abs(sum(rk[i] for i in range(n) if signs[i]) - tot / 2) >= abs(obs - tot / 2) - 1e-9
    )
    return hits / 2 ** n, n


def sign_flip_exact(a: list[float], b: list[float]) -> tuple[float, int]:
    """Exact two-sided sign-flip permutation on the raw differences. Returns (p, n).

    Uses no ranks, so ties cannot affect it at all -- which is why it is the honest
    cross-check on the signed-rank result rather than a second opinion of the same kind.
    """
    d = _nonzero_diffs(a, b)
    n = len(d)
    if n == 0:
        return float("nan"), 0
    if n > _MAX_EXACT:
        raise ValueError(f"{n} discordant pairs exceeds the exact cap {_MAX_EXACT}")
    obs = abs(sum(d))
    mag = [abs(x) for x in d]
    hits = sum(
        1 for signs in itertools.product((-1, 1), repeat=n)
        if abs(sum(s * m for s, m in zip(signs, mag))) >= obs - 1e-9
    )
    return hits / 2 ** n, n


def sign_test_exact(a: list[float], b: list[float]) -> tuple[float, int, int]:
    """Exact two-sided sign test. Returns (p, n_up, n_down).

    Deliberately included even though it is the least powerful: it answers "did more
    probes move down than up", which is a different and weaker claim than "the aggregate
    moved". When it disagrees with the two above, the effect rests on magnitude, and that
    is worth saying out loud rather than discovering later.
    """
    d = _nonzero_diffs(a, b)
    n = len(d)
    if n == 0:
        return float("nan"), 0, 0
    up = sum(1 for x in d if x > 0)
    lo = min(up, n - up)
    p = min(1.0, 2 * sum(comb(n, k) for k in range(lo + 1)) / 2 ** n)
    return p, up, n - up


def report(a: list[float], b: list[float], label: str = "") -> str:
    """All three tests plus the means. The only sanctioned way to print a primary result."""
    mA, mB = sum(a) / len(a), sum(b) / len(b)
    rel = (mB - mA) / mA * 100 if mA else float("nan")
    p1, nd = signed_rank_exact(a, b)
    p2, _ = sign_flip_exact(a, b)
    p3, up, dn = sign_test_exact(a, b)
    return (
        f"{label}n={len(a)} discordant={nd}  A={mA:.4f}  B={mB:.4f}  "
        f"({mB - mA:+.4f} = {rel:+.1f}% rel)\n"
        f"  signed-rank (midranks, exact) p={p1:.4f}\n"
        f"  sign-flip permutation (exact) p={p2:.4f}   <- no ranks, immune to ties\n"
        f"  sign test ({dn} down / {up} up, exact)      p={p3:.4f}   <- direction only\n"
    )
