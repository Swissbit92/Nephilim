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


def longest_shared_span(
    reply: str,
    history: Iterable[str],
    exclude: frozenset[tuple[str, ...]] = frozenset(),
) -> int:
    """Longest NON-PRESCRIBED exact token run shared with any earlier reply.

    The offline twin of DRY's suffix match, and the only metric here with no
    denominator — which is why it, not a ratio, is the binary gate.

    ``exclude`` WAS NOT HONOURED HERE, AND THAT OVERCOUNTED THE DEFECT. The module's
    first stated principle is that reusing the persona's prescribed phrasing is voice
    rather than degeneration, and ``cross_rep_n`` implemented it — but this function and
    ``repetition_report``'s ``failed`` flag ignored it, so the *binary gate* counted
    exactly what the exemption exists to forgive.

    MEASURED, 2026-10-10, on a real 56-reply session: the gate flagged 13 replies. Three
    of them were matching a span that is 67-100% four-grams drawn from the persona's own
    card — in one case the matched run was 12 of 12 prescribed, i.e. she reproduced her
    own mandated phrasing and was scored as degenerate for it. Applying the exemption
    here, the same session reads 12 (and 3 once a separate canned-line bug is fixed).

    The exemption is applied PER TOKEN rather than per span, so a mixed run gets partial
    credit: given "i can feel" + a fully-prescribed tail, the novel run is three tokens,
    which is the honest length. Skipping only wholly-prescribed spans was tried first and
    cleared just one of the three, because real repeats splice a connective onto a
    mandated phrase.
    """
    a = tokenize(reply)
    if not a:
        return 0
    n = DEFAULT_N
    best = 0
    for past in history:
        b = tokenize(past)
        if not b:
            continue
        for block in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
            if block.size == 0:
                continue
            seg = a[block.a:block.a + block.size]
            if not exclude:
                best = max(best, block.size)
                continue
            grams = list(ngrams(seg, n))
            if not grams:
                best = max(best, block.size)
                continue
            prescribed = sum(1 for gram in grams if gram in exclude)

            # IS THIS BLOCK A REPEAT OF HER VOICE, OR A REPEAT THAT MERELY CONTAINS SOME?
            #
            # Per-token credit alone produces a FALSE NEGATIVE on the exact defect this
            # gate exists to catch, and QA built the case: an 11-token byte-identical
            # repeat containing ONE coincidental card four-gram in the middle scored 4
            # instead of 11, because a single covered window RESETS the run and
            # fragments the block into pieces that each sit under the threshold. Status
            # lines and persona cards both lean on the same warm common phrasing, so
            # that collision is ordinary rather than contrived.
            #
            # So the exemption applies only when the match is DRIVEN by prescribed
            # content — more than half the block's n-grams. A block that is mostly novel
            # with some prescribed glue is a genuine repeat and counts whole.
            #
            # Checked against both cases: the real session's 12-of-12 prescribed span is
            # credited down (correct, it is her mandated phrasing), and QA's 1-of-8
            # adversarial block counts its full 11 (correct, the gate fires).
            if prescribed * 2 <= len(grams):
                best = max(best, block.size)
                continue

            covered = [False] * len(seg)
            for i, gram in enumerate(grams):
                if gram in exclude:
                    for j in range(i, i + n):
                        covered[j] = True
            run = 0
            for flag in covered:
                run = 0 if flag else run + 1
                best = max(best, run)
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
        span = longest_shared_span(reply, replies[:i], exclude)
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
