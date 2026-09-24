# tests/evaluation/persona_eval/three_arm.py
"""Three-arm paired analysis: OFF / PLACEBO / ON.

Why a third arm exists, stated first because it is the entire design:

A two-arm OFF-vs-ON test attributes the whole difference to the CONTENT of the
constraints block. It cannot, because turning the block on also makes the system
prompt several hundred characters longer and shifts everything after it. Adding
any text to a prompt changes behaviour through position and attention effects
independent of what the text says, so OFF-vs-ON measures content PLUS length and
reports the sum as content.

PLACEBO is length-matched, position-matched, inert text. That decomposes it:

    ON  - PLACEBO   = the CONTENT effect      <- the actual question
    PLACEBO - OFF   = the LENGTH/POSITION effect (the confound, measured)
    ON  - OFF       = the total, i.e. what a naive two-arm test would have said

The primary endpoint is ON vs PLACEBO, declared before the data lands. The other
two contrasts are reported as decomposition and are NOT part of the decision.
That is deliberate and it is not laziness about multiplicity: at n~19 the design
is already marginally powered, and spending alpha on three co-equal comparisons
would leave the primary unable to see an effect it is there to detect. One
pre-registered primary needs no correction; three co-equal ones would.

A caveat the decision rule enforces rather than mentions: if PLACEBO - OFF is
itself large, the arms are not interchangeable and a null on the primary is
ambiguous rather than reassuring — the prompt is sensitive to length, so the
content was measured against a moving baseline.

Statistics reuse `analyse_format_experiment` for every paired contrast, so the
two-arm and three-arm paths cannot drift apart. The omnibus is a Friedman
statistic with a permutation null over the within-item arm labels: exact when the
arrangement space is small enough to enumerate, sampled with a fixed seed when it
is not, and the Monte Carlo standard error is reported either way so the p-value's
own precision is visible.

Pure scoring. No model calls, no I/O beyond an explicit prereg path.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterable, Sequence
from itertools import permutations, product
from statistics import mean

from analyse_format_experiment import (
    exact_permutation_p,
    exact_sign_test,
    item_means,
    paired_cohens_d,
    paired_deltas,
)

ARMS: tuple[str, str, str] = ("off", "placebo", "on")

# The primary contrast, and the two that only decompose it.
PRIMARY = ("placebo", "on")
DECOMPOSITION = (("off", "placebo"), ("off", "on"))

# Enumerate the exact null while the arrangement space stays under this; sample
# beyond it. 6**7 = 279_936; 6**8 = 1_679_616 already costs seconds per call.
_EXACT_ARRANGEMENT_CAP = 300_000

# Words that make a "neutral filler" a treatment rather than a control.
#
# Deliberately limited to unambiguous obligation/prohibition markers. Weaker ones
# ("should", "only", "ensure") were tried and dropped: they occur constantly in
# ordinary prose, so banning them forces the placebo into a register the real
# block does not share — and register is itself a confound (Sclar et al.
# arXiv:2310.11324 measured up to 76-point accuracy swings from purely syntactic
# prompt changes). Matching mood while carrying no enforceable rule is the goal;
# this check enforces the second half, and the first half stays a human
# judgement because deciding "is this an enforceable rule" is semantic.
_NORMATIVE = (
    "never", "always", "must", "do not", "don't", "avoid",
    "forbidden", "prohibited", "refuse", "decline",
    "under no circumstances", "required",
)


# ─────────────────────────────────────────────────────────────
# Arm validity — checked BEFORE any number is produced
# ─────────────────────────────────────────────────────────────

def check_placebo_matching(
    off_prompt: str,
    placebo_prompt: str,
    on_prompt: str,
    length_tolerance: float = 0.15,
) -> list[str]:
    """Reasons this placebo does not control what it claims to. Empty == valid.

    An unmatched placebo is worse than no placebo, because the decomposition is
    then reported with the same confidence while measuring something else. So
    these are hard errors that callers are expected to refuse on, not warnings.

    Checked:
      * the placebo actually adds text (a placebo equal to OFF is not an arm)
      * its added length is within tolerance of the real block's added length
      * it is inserted at the same offset, so position effects are matched
      * it carries no normative language — filler that happens to say "always be
        helpful" is an instruction, and the control becomes a second treatment
    """
    errors: list[str] = []

    add_placebo = len(placebo_prompt) - len(off_prompt)
    add_on = len(on_prompt) - len(off_prompt)

    if add_placebo <= 0:
        errors.append(
            f"placebo adds no text (delta={add_placebo}); it is the OFF arm under "
            "another name and controls nothing"
        )
    if add_on <= 0:
        errors.append(
            f"ON adds no text (delta={add_on}); there is no treatment to control for"
        )

    if add_placebo > 0 and add_on > 0:
        ratio = add_placebo / add_on
        if abs(ratio - 1.0) > length_tolerance:
            errors.append(
                f"placebo is not length-matched: adds {add_placebo} chars vs ON's "
                f"{add_on} ({ratio:.2f}x, tolerance +/-{length_tolerance:.0%}). "
                "An unmatched placebo cannot separate content from length."
            )

    pos_placebo = _insertion_offset(off_prompt, placebo_prompt)
    pos_on = _insertion_offset(off_prompt, on_prompt)
    if pos_placebo is not None and pos_on is not None and pos_placebo != pos_on:
        errors.append(
            f"placebo is inserted at offset {pos_placebo} but ON at {pos_on}; "
            "position effects are then unmatched and the contrast is confounded "
            "by exactly the thing the arm exists to hold still"
        )

    inserted = placebo_prompt[pos_placebo:pos_placebo + add_placebo] if pos_placebo is not None else placebo_prompt
    found = sorted({w for w in _NORMATIVE if w in inserted.lower()})
    if found:
        errors.append(
            f"placebo text contains normative language {found}; filler that tells "
            "the model what to do is a second treatment, not a control"
        )

    return errors


def _insertion_offset(base: str, longer: str) -> int | None:
    """Offset where `longer` first diverges from `base`, or None if base is not a
    prefix-and-suffix of longer (i.e. this was not a pure insertion)."""
    if len(longer) <= len(base):
        return None
    i = 0
    while i < len(base) and base[i] == longer[i]:
        i += 1
    # the remainder of base must reappear at the tail of longer
    tail = len(base) - i
    if tail and longer[len(longer) - tail:] != base[i:]:
        return None
    return i


# ─────────────────────────────────────────────────────────────
# Per-arm item means and the common item set
# ─────────────────────────────────────────────────────────────

def arm_item_means(
    rows_by_arm: dict[str, list[dict]],
    metric_fn: Callable[[str], float],
) -> dict[str, dict[str, float]]:
    """{arm: {probe_id: mean over that probe's repeats}} for every declared arm.

    Delegates to `item_means`, so the drop-errors-rather-than-score-zero rule is
    the same one the two-arm path uses.
    """
    missing = [a for a in ARMS if a not in rows_by_arm]
    if missing:
        raise ValueError(f"rows missing for arm(s) {missing}; all of {ARMS} are required")
    return {a: item_means(rows_by_arm[a], metric_fn) for a in ARMS}


def common_items(per_arm: dict[str, dict[str, float]]) -> list[str]:
    """Probes scorable in ALL THREE arms, sorted.

    Intersection, not union: a probe missing from one arm cannot contribute a
    within-item comparison, and filling it from the arms that do have it would
    silently turn a paired design into an unpaired one.
    """
    sets = [set(per_arm[a]) for a in ARMS]
    return sorted(set.intersection(*sets)) if sets else []


def _aligned(per_arm: dict[str, dict[str, float]], items: Sequence[str]) -> dict[str, list[float]]:
    return {a: [per_arm[a][i] for i in items] for a in ARMS}


# ─────────────────────────────────────────────────────────────
# Omnibus: Friedman statistic with a permutation null
# ─────────────────────────────────────────────────────────────

def friedman_statistic(aligned: dict[str, list[float]]) -> float:
    """Friedman chi-square over within-item ranks across the three arms.

    Ranks within each item rather than comparing raw values, so the statistic is
    invariant to any monotone rescaling of the metric and is not dragged by one
    item with a large spread. Ties share the average rank.
    """
    n = len(next(iter(aligned.values())))
    k = len(ARMS)
    rank_sums = dict.fromkeys(ARMS, 0.0)
    for i in range(n):
        vals = [(aligned[a][i], a) for a in ARMS]
        for arm, r in _average_ranks(vals).items():
            rank_sums[arm] += r
    total = sum(rs * rs for rs in rank_sums.values())
    return (12.0 / (n * k * (k + 1))) * total - 3.0 * n * (k + 1)


def _average_ranks(vals: list[tuple[float, str]]) -> dict[str, float]:
    order = sorted(vals)
    ranks: dict[str, float] = {}
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and order[j + 1][0] == order[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for _, arm in order[i:j + 1]:
            ranks[arm] = avg
        i = j + 1
    return ranks


def omnibus_p(
    aligned: dict[str, list[float]],
    draws: int = 20_000,
    seed: int = 20260924,
) -> dict:
    """Permutation p for "the three arms differ at all", plus its own precision.

    Under the null the arm label is exchangeable WITHIN each item, so a
    null-consistent relabelling permutes the three values of one item. The space
    is 6**n; enumerated when that is small, sampled with a fixed seed when not.

    Returns `mc_se`, the Monte Carlo standard error of the p-value, because a
    sampled p of 0.04 with an SE of 0.01 is not the same claim as an exact 0.04
    and reporting them identically invites reading noise as a result.
    """
    n = len(next(iter(aligned.values())))
    if n == 0:
        return {"p": None, "statistic": None, "method": "no data", "n_items": 0}

    observed = friedman_statistic(aligned)
    space = 6 ** n

    if space <= _EXACT_ARRANGEMENT_CAP:
        hits = 0
        for arrangement in product(list(permutations(ARMS)), repeat=n):
            if _permuted_statistic(aligned, arrangement, n) >= observed - 1e-12:
                hits += 1
        return {
            "p": round(hits / space, 6),
            "statistic": round(observed, 4),
            "method": f"exact ({space} arrangements)",
            "n_items": n,
            "mc_se": 0.0,
        }

    rng = random.Random(seed)
    perms = list(permutations(ARMS))
    hits = 0
    for _ in range(draws):
        arrangement = [rng.choice(perms) for _ in range(n)]
        if _permuted_statistic(aligned, arrangement, n) >= observed - 1e-12:
            hits += 1
    p = (hits + 1) / (draws + 1)  # add-one: never report p=0 from a sample
    return {
        "p": round(p, 6),
        "statistic": round(observed, 4),
        "method": f"sampled ({draws} draws, seed={seed})",
        "n_items": n,
        "mc_se": round(math.sqrt(p * (1 - p) / draws), 6),
    }


def _permuted_statistic(
    aligned: dict[str, list[float]],
    arrangement: Iterable[tuple[str, ...]],
    n: int,
) -> float:
    relabelled: dict[str, list[float]] = {a: [0.0] * n for a in ARMS}
    for i, order in enumerate(arrangement):
        for src, dst in zip(ARMS, order, strict=True):
            relabelled[dst][i] = aligned[src][i]
    return friedman_statistic(relabelled)


# ─────────────────────────────────────────────────────────────
# Contrasts and the pre-registered decision
# ─────────────────────────────────────────────────────────────

def contrast(per_arm: dict[str, dict[str, float]], a: str, b: str,
             items: Sequence[str] | None = None) -> dict:
    """Paired b-minus-a over the common items, via the two-arm machinery."""
    items = list(items) if items is not None else common_items(per_arm)
    control = {i: per_arm[a][i] for i in items}
    candidate = {i: per_arm[b][i] for i in items}
    deltas = paired_deltas(control, candidate)
    n = len(deltas)
    return {
        "contrast": f"{b} - {a}",
        "n_items": n,
        "mean_delta": round(mean(deltas), 6) if deltas else None,
        # exact_permutation_p refuses n>20 by design; say so rather than crash
        "p": exact_permutation_p(deltas) if n <= 20 else None,
        "p_note": None if n <= 20 else f"n={n} exceeds the exhaustive cap; use a sampled test",
        "sign": exact_sign_test(deltas),
        "d": paired_cohens_d(deltas),
    }


def holm_adjust(pvalues: dict[str, float | None]) -> dict[str, float | None]:
    """Holm-Bonferroni step-down adjustment over the named comparisons.

    Reported alongside the primary rather than instead of it. The primary is
    pre-registered and so needs no correction; but a reader who prefers to treat
    all three contrasts as co-equal planned comparisons is entitled to that
    reading, and making them compute it by hand is how a report gets argued about
    instead of used. Holm rather than plain Bonferroni: uniformly more powerful,
    and at n~19 power is the scarce resource.
    """
    present = {k: v for k, v in pvalues.items() if v is not None}
    if not present:
        return dict.fromkeys(pvalues)
    m = len(present)
    out: dict[str, float | None] = dict.fromkeys(pvalues)
    running = 0.0
    for rank, (name, p) in enumerate(sorted(present.items(), key=lambda kv: kv[1])):
        adj = min(1.0, (m - rank) * p)
        running = max(running, adj)  # step-down: adjusted p-values are monotone
        out[name] = round(running, 6)
    return out


def length_diagnostic(rows_by_arm: dict[str, list[dict]]) -> dict:
    """Mean output length per arm, and whether the arms differ enough to worry.

    Not optional decoration. Automatic scorers systematically favour longer
    outputs independent of quality (Dubois et al., length-controlled AlpacaEval,
    arXiv:2404.04475), so a treatment that changes output length can move a
    length-sensitive score without changing what the score is meant to measure.
    Reporting length per arm is the cheapest way to see that happening; if the
    arms diverge, the score needs residualising on length before it is believed.
    """
    means: dict[str, float | None] = {}
    for a in ARMS:
        lens = [len(r.get("answer") or "") for r in rows_by_arm.get(a, [])
                if (r.get("answer") or "") and not r["answer"].startswith("[ERROR")]
        means[a] = round(mean(lens), 2) if lens else None
    vals = [v for v in means.values() if v is not None]
    spread = (max(vals) - min(vals)) / max(vals) if vals and max(vals) else 0.0
    return {
        "mean_answer_chars": means,
        "relative_spread": round(spread, 4),
        "warning": (
            f"output length differs {spread:.0%} across arms; a length-sensitive "
            "scorer will read that as a treatment effect (arXiv:2404.04475)"
            if spread > 0.20 else None
        ),
    }


# Limitations that no amount of analysis fixes, recorded so they are cited rather
# than rediscovered. Returned in every report.
LIMITATIONS = (
    "OFF has no constraint region at all, so it cannot be position-matched to the "
    "other two the way they are matched to each other. PLACEBO-vs-OFF is therefore "
    "the least clean of the three contrasts, which is a further reason the primary "
    "is ON-vs-PLACEBO (lost-in-the-middle: Liu et al., arXiv:2307.03172).",
    "One template per arm. Single-template evaluation is unreliable across models "
    "and paraphrases (Mizrahi et al., arXiv:2401.00595; Sclar et al., "
    "arXiv:2310.11324, up to 76-point swings from format alone), so a result here "
    "is about this wording of the block, not about stating rules in general.",
    "A fixed decoder seed does NOT make arms comparable: the arms differ in prompt "
    "length, so the same seed's stream is consumed at a different token position. "
    "Comparability has to come from k independent samples per (probe, arm) cell, "
    "not from seeding.",
    "No published placebo design for LLM system prompts exists to validate against "
    "— length-and-position matching with no enforceable rule is a defensible "
    "improvisation, not an established method. Treat PLACEBO-vs-OFF as a finding "
    "in its own right rather than as a passed sanity check.",
    "Whether naming a prohibition makes it MORE likely is an open question for "
    "LLMs specifically; the ironic-rebound result is a human-psychology finding "
    "and no LLM study confirms or refutes it. A HARMFUL verdict here should also "
    "be checked against plain constraint-count overload (FollowBench, "
    "arXiv:2310.20410) by testing whether violations cluster on the LAST-listed "
    "rules, which points to dilution rather than priming.",
)


def analyse_three_arm(
    rows_by_arm: dict[str, list[dict]],
    metric_fn: Callable[[str], float],
    alpha: float = 0.05,
) -> dict:
    """Full three-arm report: validity, omnibus, primary, decomposition, verdict."""
    per_arm = arm_item_means(rows_by_arm, metric_fn)
    items = common_items(per_arm)
    aligned = _aligned(per_arm, items) if items else {a: [] for a in ARMS}

    primary = contrast(per_arm, *PRIMARY, items=items)
    decomp = [contrast(per_arm, a, b, items=items) for a, b in DECOMPOSITION]
    omni = omnibus_p(aligned) if items else {"p": None, "statistic": None,
                                             "method": "no data", "n_items": 0}

    all_contrasts = [primary, *decomp]
    holm = holm_adjust({c["contrast"]: c["p"] for c in all_contrasts})

    return {
        "arms": list(ARMS),
        "n_items_per_arm": {a: len(per_arm[a]) for a in ARMS},
        "n_items_common": len(items),
        "items": items,
        "omnibus": omni,
        "primary": primary,
        "decomposition": decomp,
        "holm_adjusted_p": holm,
        "length_diagnostic": length_diagnostic(rows_by_arm),
        "limitations": list(LIMITATIONS),
        "verdict": decide_three_arm(primary, decomp, omni, alpha=alpha),
    }


def decide_three_arm(primary: dict, decomposition: list[dict], omnibus: dict,
                     alpha: float = 0.05, min_items: int = 10) -> str:
    """The pre-registered rule, applied without judgement.

    The branch that earns the third arm: a null primary is only reassuring when
    the placebo arm ALSO showed nothing. If length alone moved the metric, the
    content was measured against a baseline that was itself moving, and "no
    content effect" is then a statement about the design, not about the content.
    """
    p = primary.get("p")
    if p is None or primary["n_items"] == 0:
        return "NO DATA"

    length_effect = next((c for c in decomposition if c["contrast"] == "placebo - off"), None)
    length_moved = bool(
        length_effect
        and length_effect.get("p") is not None
        and length_effect["p"] < alpha
    )

    significant = p < alpha
    directional = (primary["mean_delta"] or 0) > 0
    consistent = primary["sign"]["favour_candidate"] >= min_items
    d = primary.get("d")

    if significant and directional and consistent:
        base = (
            f"SHIP — the constraints CONTENT beats a length-matched placebo "
            f"(p={p}, d={d}, {primary['sign']['favour_candidate']}/"
            f"{primary['n_items']} items)"
        )
        if length_moved:
            base += (
                f"; note length alone also moved the metric (p={length_effect['p']}), "
                "so part of any OFF-to-ON gain is not the content"
            )
        return base

    if significant and directional and not consistent:
        return (
            f"INCONCLUSIVE — significant (p={p}) but only "
            f"{primary['sign']['favour_candidate']}/{primary['n_items']} items favour "
            f"ON; the rule requires >={min_items}. A few large items can carry a mean "
            "without the effect being general."
        )

    if significant and not directional:
        return (
            f"HARMFUL — the content arm is significantly WORSE than the placebo "
            f"(p={p}, mean={primary['mean_delta']}). Listing prohibitions can prime "
            "the behaviour it forbids; this is the result that predicts it."
        )

    if length_moved:
        return (
            f"UNINTERPRETABLE — no content effect (p={p}) but the PLACEBO moved the "
            f"metric on its own (p={length_effect['p']}, mean="
            f"{length_effect['mean_delta']}). The prompt is length-sensitive, so the "
            "content was compared against a shifting baseline. Fix the harness "
            "before drawing a conclusion about the content."
        )

    if d is not None and abs(d) >= 0.5:
        return (
            f"INCONCLUSIVE — p={p} but d={d}. At n={primary['n_items']} a large true "
            "effect is still often invisible, so this says the design cannot see it, "
            "NOT that it does not work. Follow-up = MORE ITEMS, not more repeats."
        )

    if d is None or abs(d) < 0.3:
        return (
            f"KILL — p={p}, d={d}, and the placebo was flat too: the constraints "
            "content genuinely does not move this metric."
        )

    return (
        f"INCONCLUSIVE — p={p}, d={d} sits between the KILL (d<0.3) and "
        "INCONCLUSIVE (d>=0.5) bands."
    )
