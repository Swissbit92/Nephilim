# tests/evaluation/persona_eval/repetition_metrics.py
"""Verbatim self-repetition, measured without a model.

The reported symptom was whole paragraphs reappearing across turns, days apart.
Nothing in this repo measured that: `persona_metrics` scores voice, grounding
and depth, and the nearest thing to a repetition signal was a sampler setting.

Deliberately stdlib-only. These numbers must be reproducible without Ollama,
without a GPU and without the persona that produced them, because they are the
one part of the ruler that can be recomputed on an archived transcript years
later.

Definitions follow the published ones rather than inventing new ones:

- ``seq_rep_n`` is Welleck et al. (ICLR 2020) §6.1: ``1 - |unique n-grams| /
  |n-grams|``, computed per continuation and averaged, never pooled.
- ``cross_rep_n`` adapts that to Salkar et al. (AACL-IJCNLP 2022) self-repetition
  — n-grams recurring across *separate outputs of the same system* — which is
  the shape here: a reply reusing a phrase from an earlier reply, not from
  itself.
- ``longest_shared_span`` is the offline twin of the DRY sampler's backward
  suffix match (arXiv:2608.22761).

**n = 4** because three independent sources land there: Welleck's headline
``seq-rep-4``, DRY's ``SER@4``, and Salkar's "four or more tokens". Below 4 the
metric measures function-word noise ("I think that"); at 4 it measures phrase
reuse.

Two hygiene rules that decide whether the number means anything:

1. **Exclude n-grams that appear in the persona card.** A companion is *supposed*
   to reuse her signature phrasing — that is prescribed voice, not degeneration.
   Counting it makes every configuration look equally bad and buries the signal.
2. **Report raw counts beside every ratio.** "15%" from a denominator of 13
   four-grams is not a percentage, and the aggregate uses a median over replies
   rather than a mean over pooled n-grams, so one long reply cannot dominate.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable, Sequence
from difflib import SequenceMatcher
from typing import NamedTuple

__all__ = [
    "tokenize",
    "ngrams",
    "seq_rep_n",
    "cross_rep_n",
    "longest_shared_span",
    "CrossRep",
    "card_ngrams",
    "repetition_report",
    "DEFAULT_N",
    "DEFAULT_SPAN_FAIL",
]

DEFAULT_N = 4
# A reply reproducing an 8-token run from an earlier reply is the binary failure.
# Denominator-free, so it is meaningful on a single short reply where every
# ratio metric is noise.
DEFAULT_SPAN_FAIL = 8

_WORD_RE = re.compile(r"[a-z0-9']+")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens. Deliberately NOT the model's BPE.

    The metric has to be recomputable from a stored transcript without knowing
    which tokenizer produced it, and words are what a human checking the result
    can see.
    """
    return _WORD_RE.findall((text or "").lower())


def ngrams(tokens: Sequence[str], n: int = DEFAULT_N) -> list[tuple[str, ...]]:
    if n <= 0:
        raise ValueError("n must be positive")
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def seq_rep_n(text: str, n: int = DEFAULT_N) -> float | None:
    """Within-reply repetition (Welleck). ``None`` when the reply is too short.

    ``None`` rather than 0.0 on purpose: a reply with no 4-grams has not been
    measured, and scoring it as "no repetition" quietly rewards terse output.
    """
    grams = ngrams(tokenize(text), n)
    if not grams:
        return None
    return 1.0 - len(set(grams)) / len(grams)


class CrossRep(NamedTuple):
    """``rate`` is ``hits / eligible``; both counts are kept so a ratio is never
    reported without the denominator that produced it."""

    rate: float | None
    hits: int
    eligible: int


def cross_rep_n(
    reply: str,
    history: Iterable[str],
    n: int = DEFAULT_N,
    exclude: frozenset[tuple[str, ...]] = frozenset(),
) -> CrossRep:
    """Fraction of a reply's n-grams already used in EARLIER replies.

    ``history`` is the model's own prior replies in the same conversation —
    never the user's turns. Echoing the user is grounding; echoing yourself is
    the defect.
    """
    reply_grams = [g for g in ngrams(tokenize(reply), n) if g not in exclude]
    if not reply_grams:
        return CrossRep(None, 0, 0)
    seen: set[tuple[str, ...]] = set()
    for past in history:
        seen.update(g for g in ngrams(tokenize(past), n) if g not in exclude)
    hits = sum(1 for g in reply_grams if g in seen)
    return CrossRep(hits / len(reply_grams), hits, len(reply_grams))


def longest_shared_span(reply: str, history: Iterable[str]) -> int:
    """Longest exact token run shared with any earlier reply.

    The offline twin of DRY's suffix match, and the only metric here with no
    denominator — which is why it, not a ratio, is the binary gate.
    """
    a = tokenize(reply)
    if not a:
        return 0
    best = 0
    for past in history:
        b = tokenize(past)
        if not b:
            continue
        match = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(
            0, len(a), 0, len(b)
        )
        best = max(best, match.size)
    return best


def card_ngrams(persona_card: dict, n: int = DEFAULT_N) -> frozenset[tuple[str, ...]]:
    """n-grams the persona is *instructed* to produce, to be excluded.

    Walks every string in the card — ``example_phrases``, ``example_dialogues``,
    ``signature_moves``, ``lore``, ``do``/``dont`` — because prescribed phrasing
    can be declared in any of them and counting it as degeneration is the
    fastest way to make this metric useless.
    """
    out: set[tuple[str, ...]] = set()

    def walk(node) -> None:
        if isinstance(node, str):
            out.update(ngrams(tokenize(node), n))
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)

    walk(persona_card)
    return frozenset(out)


def repetition_report(
    replies: Sequence[str],
    n: int = DEFAULT_N,
    exclude: frozenset[tuple[str, ...]] = frozenset(),
    span_fail: int = DEFAULT_SPAN_FAIL,
) -> dict:
    """Score one conversation's assistant replies, in order.

    Returns per-reply rows plus aggregates. The aggregate is a **median** over
    replies, not a mean over pooled n-grams: one long reply must not be able to
    dominate, and the distribution here is skewed by construction.
    """
    rows = []
    for i, reply in enumerate(replies):
        history = replies[:i]
        cr = cross_rep_n(reply, history, n=n, exclude=exclude)
        span = longest_shared_span(reply, history)
        rows.append(
            {
                "index": i,
                "cross_rep": cr.rate,
                "cross_rep_hits": cr.hits,
                "cross_rep_eligible": cr.eligible,
                "longest_shared_span": span,
                "seq_rep": seq_rep_n(reply, n),
                "failed": span >= span_fail,
            }
        )

    scored = [r["cross_rep"] for r in rows if r["cross_rep"] is not None]
    spans = [r["longest_shared_span"] for r in rows]
    # The headline is the binary rate: comparable to the rule and scene axes
    # under the same permutation test, and immune to the small-denominator
    # problem that makes a ratio meaningless on a short reply.
    return {
        "n": n,
        "span_fail_threshold": span_fail,
        "replies": len(replies),
        "scored_replies": len(scored),
        "fail_rate": (sum(r["failed"] for r in rows) / len(rows)) if rows else None,
        "median_cross_rep": statistics.median(scored) if scored else None,
        "max_shared_span": max(spans) if spans else 0,
        "rows": rows,
    }
