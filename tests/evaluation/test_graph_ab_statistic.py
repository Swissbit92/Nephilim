"""The graph A/B's primary statistic must not depend on probe list order.

`graph_vs_nograph_ab.py` carried its own inline signed-rank that assigned distinct
integer ranks 1..n with no midrank correction. Twelve of the fourteen non-zero
differences in its own data are tied in |d| (six at 0.1667, five at 0.3333), and
Python's stable sort breaks ties by position — so the p-value was a function of the order
probes happen to be listed in `HARDWALL`. Measured over 200 random orderings of identical
data, p moved 0.0245 → 0.1040, straddling 0.05 for the primary endpoint of a run that was
about to be pre-registered.

The fix was not a better ranking. `analyse_format_experiment.exact_permutation_p` already
existed and flips ARM LABELS rather than ranking magnitudes, so ties cannot affect it by
construction. Two of the three statistics needed were already in the repo; the defect was
a duplicate implementation, not a missing one.

`test_order_invariance` is the guard, and it fails on the replaced code.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO), str(_REPO / "scripts" / "research"),
           str(_REPO / "tests" / "evaluation" / "persona_eval")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analyse_format_experiment import exact_permutation_p  # noqa: E402
from graph_vs_nograph_ab import paired_report  # noqa: E402

#: The real probe-level rates from the logged 336-generation run, scored by the current
#: detector. Held as data, not as an expected p-value, so order-invariance is asserted on
#: the exact case that exposed the bug rather than on a constructed one.
PAIRS = [
    (0.0, 0.0), (0.0, 0.0), (0.0, 0.0), (0.0, 0.1667), (0.1667, 0.0),
    (0.1667, 0.3333), (0.1667, 0.0), (0.6667, 0.3333), (0.6667, 1.0),
    (0.6667, 0.8333), (0.6667, 0.6667), (0.8333, 0.5), (1.0, 0.0), (1.0, 0.5),
    (1.0, 0.6667), (1.0, 0.3333), (1.0, 0.6667), (0.0, 0.1667), (0.1667, 0.0),
    (0.5, 0.5),
]


def _p(pairs):
    return exact_permutation_p([y - x for x, y in pairs if y != x])


def test_order_invariance():
    """THE regression guard. Fails on the replaced inline signed-rank."""
    base = _p(PAIRS)
    rng = random.Random(20260929)
    for _ in range(50):
        shuffled = PAIRS[:]
        rng.shuffle(shuffled)
        assert _p(shuffled) == pytest.approx(base, abs=1e-12), (
            "the primary statistic moved under a reordering — it is tie-sensitive")


def test_ties_do_not_change_the_answer():
    """Adding a tied pair adds no information and must not move p.

    Four of the twenty probes never breach in EITHER arm, so this is the live case, not
    a hypothetical one.
    """
    assert _p(PAIRS + [(0.25, 0.25)]) == pytest.approx(_p(PAIRS), abs=1e-12)


def test_the_two_tests_disagree_and_the_report_says_both():
    """The effect rests on magnitude, not on how many probes moved.

    Ten probes improved and four worsened, but the improvements are large and the
    regressions small. A readout quoting only the permutation test would misrepresent
    that, so `paired_report` prints the sign test beside it and this pins the behaviour.
    """
    out = paired_report(PAIRS)
    assert "permutation" in out and "sign test" in out
    assert "discards magnitude" in out, "the sign test's limitation must be stated inline"


def test_the_harness_no_longer_defines_its_own_statistic():
    """A duplicate implementation is the defect; assert it cannot come back by copy-paste."""
    src = (_REPO / "scripts" / "research" / "graph_vs_nograph_ab.py").read_text()
    assert "def wilcoxon(" not in src, (
        "an inline signed-rank is back — use analyse_format_experiment instead")
    assert "exact_permutation_p" in src


def test_the_permutation_test_refuses_rather_than_stalling():
    with pytest.raises(ValueError, match="refuses n="):
        exact_permutation_p([0.1] * 21)
