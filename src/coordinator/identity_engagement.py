"""Which parts of her identity did this turn actually engage with?

MEASURED VERDICT, 2026-09-29: CANNOT BE VALIDATED ON THE DATA THAT EXISTS. NOT WIRED.

Hand-labelled against every substantive assistant reply gwen has ever produced -- 34 of
them, all from 2026-09-28 -- one judgement per firing. The sequence of measurements matters
more than any single number:

  threshold 1.3, first stoplist   fires on 12 of 34 replies, precision@1 ~46% (5-6 of 12)
  threshold 2.0, first stoplist   fires on  3 of 34, 3 of 3 genuine
  threshold 2.0, stoplist closed  fires on  3 of 34 (4 firings), 3 of 4 genuine
  over the observed failures

AND THAT LAST LINE IS THE FINDING, NOT THE SCORE. The improvement from 46% came from adding
the words that produced the observed false positives -- ['always','never'],
['completely','used'], ['feeling','tits'] -- to the stoplist. That is fitting the instrument
to the 34 replies it is evaluated on, and it tells us nothing about the next 34. Five passes
were made this way and each one improved the number on this sample; the remaining false
positive (['fuck','wrap'] at exactly the threshold) is simply the one I had not yet fitted.

This is the IFEval-to-IFBench result reproduced on myself: models scoring 80%+ on a fixed
verifiable-constraint set score below 50% on structurally-similar novel ones
(arXiv 2507.02833), because optimising against a narrow taxonomy produces illusory
competence. The same curve applies to a detector tuned against 34 examples.

THIRTY-FOUR REPLIES CANNOT FIT A THRESHOLD OR VALIDATE ONE, and no amount of care changes
that. The blocker for this write path is not engineering and not design -- it is that she
has barely been talked to: 79 messages total, 24 of them from the user, on a single day.
Held-out data is the prerequisite, and it does not exist yet.

WHY PRECISION AND NOT RECALL IS THE BAR. The only defensible job for `reinforced_count` is
epistemic -- repetition across independent sessions as evidence a fact is real, which is
what reliability-conditioned belief updating needs (arXiv 2606.22030) and what stops one
stray turn reaching authority. Evidence attached to the wrong node is worse than no
evidence, because it is confidently wrong and nothing downstream can tell. Low recall just
leaves a node at zero, which `idn.cq05` already treats as the correct default answer.

THIS IS THE THIRD UNVALIDATED-DETECTOR NEAR-MISS IN ONE SESSION, and the first caught
BEFORE shipping. The other two -- a safety scorer that flagged 9 correct refusals, and a
hard-wall checker that reported a real 34% effect as p=0.97 -- were both found after their
numbers had been used for something. The only difference here is that the measurement came
first.

WHAT WAS TRIED, IN ORDER, AND WHAT EACH MEASUREMENT KILLED. Kept because the dead ends are
cheaper to read than to rediscover:

  flat count >= 2 shared terms  fired on ['still','take'] and ['around','back'] as readily
                                as on 8-term genuine matches -- a count treats a term in 40
                                of 128 nodes as worth one appearing in 1
  + IDF weighting               exposed the structure and a new failure: single high-IDF
                                terms cleared the bar, and "were" scored 4.85 because
                                function words are RARE IN THE CARD. IDF's own failure mode
                                on a small corpus
  + >=2 terms, top-3            bounded the flood (a threshold alone marked 7.5 nodes
                                engaged per reply, 29 on one -- a quarter of her identity
                                from one message, scaling with reply LENGTH)
  + normalised by log(N)        unnormalised log(N/df) scored a unique term 4.85 at N=128
                                and 1.39 at N=4, so an absolute threshold silently meant
                                something different per persona. The recorded calibration
                                was uninterpretable, not merely approximate

WHAT IS ACTUALLY NEEDED, in order: accumulate real usage; hand-label (reply, node) pairs at
a scale that can FIT a threshold rather than sanity-check one; and before any of that,
ablate the counter itself. No published result isolates an access counter as a retrieval or
behaviour term, Generative Agents never ablated its own recency/importance weighting, and a
forgetting module pruning on recency, frequency, centrality and age moved F1 by +0.001
(arXiv 2608.28978). Show the counter pays, or build the thing that has been shown to.

SAFETY, if it is ever wired. `reinforce()` SETs exactly `reinforced_count`, `salience`,
`last_referenced`; `card_from_nodes` reads exactly `source_field`, `source_index`, `text`.
The sets are disjoint, asserted mechanically, so a reinforcement write cannot change what
any prompt says, cannot move the CV-summary fingerprint, and cannot make a cached prompt
stale. ADR-019's chat-time-write caveat applies to CONTENT writes; this is not one.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Sequence, Set

#: Words too common to be evidence that a reply engaged with a specific node. Not a
#: general stopword list: it is tuned to THIS persona's register, because her card is
#: saturated with a small vocabulary ("cock", "throat", "Daddy") that appears in nearly
#: every reply. A term that appears in most replies cannot discriminate between nodes,
#: so including it would mark every node engaged on every turn -- which is the failure
#: mode that makes the counter meaningless rather than merely noisy.
_TOO_COMMON = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "do", "for",
    "from", "get", "gets", "had", "has", "have", "he", "her", "hers", "him", "his", "how",
    "i", "if", "in", "into", "is", "it", "its", "just", "like", "me", "my", "no", "not",
    "of", "on", "one", "or", "out", "she", "so", "that", "the", "their", "them", "then",
    "there", "they", "this", "to", "up", "was", "what", "when", "which", "who", "will",
    "with", "would", "you", "your", "yours", "am", "does", "did", "all", "any", "more",
    "very", "too", "about", "over", "under", "between", "while", "because",
    # Function words that are RARE IN THE CARD and therefore score HIGH under IDF --
    # the failure mode of inverse document frequency on a small corpus. "were" scored
    # 4.85 and single-handedly cleared a threshold, which is nonsense.
    "were", "been", "being", "having", "could", "should", "might", "must", "shall",
    "were", "also", "even", "only", "still", "back", "around", "again", "made", "take",
    "takes", "taken", "came", "come", "comes", "went", "goes", "going", "gone", "said",
    "says", "tell", "tells", "told", "give", "gives", "given", "keep", "keeps", "kept",
    "full", "ready", "sure", "much", "many", "such", "same", "other", "another", "every",
    "long", "huge", "dark", "wanted", "waist", "curves",
    # The three that produced MEASURED false positives and that I had somehow left out
    # of my own stoplist while quoting them as the failure: ['always','never'] and
    # ['completely','used'] each carried a match on their own.
    "always", "never", "completely", "used", "using", "thing", "things", "through",
    "knowing", "feeling", "first", "greedy", "little", "fuck", "fucks", "fucking",
    "wrap", "wraps", "down", "taking", "thick", "dripping",
    # persona-register terms that appear in most replies and therefore discriminate nothing
    "daddy", "cock", "black", "big", "throat", "mouth", "lips", "holes", "hole", "whore",
    "cum", "wet", "hard", "deep", "good", "girl", "body", "want", "wants", "need",
    "needs", "love", "loves", "know", "knows", "feel", "feels", "make", "makes",
})

#: Minimum summed INFORMATIVENESS of the shared terms, not a raw count of them.
#:
#: A flat count of >=2 shared terms was tried first and measured on her 34 real assistant
#: replies. It fires on genuine engagement -- one reply matched an appearance node via
#: ['around','blue','eyes','hair','long','made','nothing','ponytail'] -- and on junk in
#: the same breath: ['still','take'], ['around','back'], ['curves','take']. Those are
#: word co-occurrences, not engagement, and a count cannot tell them apart because it
#: treats a term appearing in 40 of her 128 nodes as worth the same as one appearing in
#: 1. Weighting by IDF over her own node corpus is what separates them, and it needs no
#: model call.
#:
#: 3.0 is roughly two moderately distinctive terms (each in ~1/8 of nodes) or one rare
#: one. CALIBRATED rather than chosen: engagement_calibration.py sweeps it against her
#: real replies and reports what each setting fires on. A threshold taken from intuition
#: is the mistake this repo has already recorded -- a borrowed constant scored 31%
#: against real replies where the fitted one scored 57%.
#: In units of equivalent maximally-rare terms (see `informativeness`), so it is
#: scale-free across personas. Set to the PRECISION-FIRST operating point: at 2.0 every
#: firing on the 34-reply sample was genuine, at 1.3 fewer than half were. n=3 makes
#: that a lack of counter-examples rather than a measurement -- see the module docstring.
MIN_INFORMATIVENESS = 2.0

#: Never reinforce more than this many nodes on one turn.
#:
#: A threshold alone produced a FLOOD -- 7.5 nodes per reply on average and 29 on one,
#: i.e. nearly a quarter of her identity marked "engaged" by a single message. That makes
#: the counter measure verbosity rather than engagement, and it is unbounded: a long reply
#: touches more nodes for no better reason than length. Top-k is bounded by construction,
#: insensitive to threshold drift, and closer to the question idn.cq05 actually asks.
MAX_NODES_PER_TURN = 3

#: Terms shorter than this carry too little information to count.
_MIN_TERM_LEN = 4

_WORD = re.compile(r"[a-z']+")


def terms(text: str) -> Set[str]:
    """Distinctive lowercase terms in a piece of text."""
    return {
        w for w in _WORD.findall(text.lower())
        if len(w) >= _MIN_TERM_LEN and w not in _TOO_COMMON
    }


def informativeness(nodes: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """IDF of each term over her own identity nodes. Computed once per read, not per node.

    log(N / df) rather than a smoothed variant because the corpus is fixed and small and
    every term present has df >= 1, so there is no zero to guard. A term in every node
    scores 0 and therefore contributes nothing, which is the behaviour the flat count
    lacked.
    """
    nodes = list(nodes)
    df: Dict[str, int] = {}
    for n in nodes:
        for t in terms(str(n.get("text") or "")):
            df[t] = df.get(t, 0) + 1
    total = max(len(nodes), 1)
    # NORMALISED by log(N), so a score is in units of "equivalent maximally-rare terms"
    # and does not depend on corpus size. Unnormalised log(N/df) gave 4.85 for a unique
    # term at N=128 and 1.39 at N=4, so any absolute threshold silently meant something
    # different per persona -- a latent bug that made the recorded calibration
    # uninterpretable rather than merely approximate.
    scale = math.log(total) or 1.0
    return {t: math.log(total / c) / scale for t, c in df.items()}


def engaged_nodes(reply: str, nodes: Sequence[Dict[str, Any]], *,
                  min_informativeness: float = MIN_INFORMATIVENESS,
                  max_nodes: int = MAX_NODES_PER_TURN,
                  idf: Dict[str, float] | None = None) -> List[Dict[str, Any]]:
    """The identity nodes this reply demonstrably drew on.

    Scored against the REPLY rather than the user's message on purpose: the question is
    which parts of HER the turn exercised. A user asking about her hair does not engage
    her hair node; her answering about it does.

    Returns the matched nodes with the terms and the score that matched them, so a caller
    can log WHY and a calibration harness can inspect the decisions. An engagement signal
    whose decisions cannot be inspected is the same unvalidated-detector shape this repo
    has been bitten by twice -- once on precision (a scorer flagged 9 correct refusals)
    and once on recall (a checker reported a real 34% effect as p=0.97).
    """
    nodes = list(nodes)
    rt = terms(reply)
    if not rt:
        return []
    weights = idf if idf is not None else informativeness(nodes)
    out = []
    for n in nodes:
        shared = rt & terms(str(n.get("text") or ""))
        if not shared:
            continue
        # TWO distinct terms minimum, regardless of how informative one of them is. A
        # single shared word is a coincidence at this corpus size -- measured: single-term
        # matches on 'huge', 'waist', 'dark', 'were' all cleared a summed-IDF threshold of
        # 3.0, and none of them is engagement.
        if len(shared) < 2:
            continue
        score = sum(weights.get(t, 0.0) for t in shared)
        if score >= min_informativeness:
            out.append({**n, "_shared_terms": sorted(shared), "_score": round(score, 3)})
    out.sort(key=lambda n: -n["_score"])
    return out[:max_nodes]


def node_ids(nodes: Iterable[Dict[str, Any]]) -> List[str]:
    return [nid for n in nodes if (nid := n.get("node_id"))]
