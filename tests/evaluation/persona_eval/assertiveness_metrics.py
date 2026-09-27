# tests/evaluation/persona_eval/assertiveness_metrics.py
"""Deterministic assertiveness-vs-deference scorer for informal companion-chat text.

Built for the ``assertiveness`` persona dial (0.0-1.0) on a lowercase, emoji-heavy,
~30-100 word, explicit/intimate companion register. stdlib + ``re`` only — no
spaCy/NLTK/network — so it is auditable line-by-line and runs offline in CI.

WHAT THIS IS: a bag-of-features lexical/regex scorer, not a construct-validated
psychometric instrument. Every lexicon below is either (a) copied verbatim from a
published, citable source, or (b) assembled for this specific informal register
because no published word list exists for it. Each block says which. Read
``docs/assertiveness_scorer_notes.md`` (repo research writeup, if present) or the
session that produced this file for the full literature review — the short version
is inline as comments here.

KNOWN LIMITS (see the paired design in ``probes_assertiveness.json`` for how these
are handled):
  - Danescu-Niculescu-Mizil's original features are DEPENDENCY-PARSE-based
    (e.g. "Deference" requires the word in sentence-INITIAL position via a parse).
    This module has no parser, so it approximates with unigram/regex presence —
    a strictly weaker, noisier version of the original feature. Flagged per-block.
  - Word-list scorers are known to correlate only moderately with human dominance/
    assertiveness judgment (see research writeup, Q3) — this is a deliberately
    weak-but-auditable instrument, not a strong-but-opaque one.
  - Composite score is NOT validated against human ratings for THIS register.
    Use it as one arm of a triangulated design (paired stats + entanglement
    checks + spot human read), never as a sole verdict.
"""

from __future__ import annotations

import re
import math
import random
from typing import Dict, List, Sequence, Tuple

# =====================================================================================
# LEXICONS
# Every list below is tagged [VERIFIED: <source>] or [INFERENCE: <rationale>].
# =====================================================================================

# ---- [VERIFIED] Danescu-Niculescu-Mizil, Sudhof, Jurafsky, Leskovec & Potts (2013),
# "A Computational Approach to Politeness with Application to Social Factors" (ACL).
# Verbatim from the authors' released source code, fetched 2026-09-27:
# https://github.com/sudhof/politeness/blob/master/features/politeness_strategies.py
# This is the hedge cue list backing their "Hedges"/"HASHEDGE" politeness features.
HEDGES: List[str] = [
    "think", "thought", "thinking", "almost",
    "apparent", "apparently", "appear", "appeared", "appears", "approximately", "around",
    "assume", "assumed", "certain amount", "certain extent", "certain level", "claim",
    "claimed", "doubt", "doubtful", "essentially", "estimate",
    "estimated", "feel", "felt", "frequently", "from our perspective", "generally", "guess",
    "in general", "in most cases", "in most instances", "in our view", "indicate", "indicated",
    "largely", "likely", "mainly", "may", "maybe", "might", "mostly", "often", "on the whole",
    "ought", "perhaps", "plausible", "plausibly", "possible", "possibly", "postulate",
    "postulated", "presumable", "probable", "probably", "relatively", "roughly", "seems",
    "should", "sometimes", "somewhat", "suggest", "suggested", "suppose", "suspect", "tend to",
    "tends to", "typical", "typically", "uncertain", "uncertainly", "unclear", "unclearly",
    "unlikely", "usually", "broadly", "tended to", "presumably", "suggests",
    "from this perspective", "from my perspective", "in my view", "in this view", "in our opinion",
    "in my opinion", "to my knowledge", "fairly", "quite", "rather", "argue", "argues", "argued",
    "claims", "feels", "indicates", "supposed", "supposes", "suspects", "postulates",
]

# ---- [VERIFIED, register-restricted] Same source. DNM's "Deference" strategy fires
# on these words in sentence-INITIAL position (dependency-parse gated, e.g. "Great,
# thanks for..."). We drop the position gate (no parser) and count bare occurrence —
# a known-noisier proxy; a mid-sentence "that's cool" will now also count.
DEFERENCE_WORDS: List[str] = [
    "great", "good", "nice", "interesting", "cool", "excellent", "awesome",
]

# ---- [VERIFIED] Same source: "Gratitude" fires on tokens starting with "thank" or
# the parse "(appreciate, i)". We approximate the latter as the phrase "i appreciate".
GRATITUDE_PATTERNS: List[str] = ["thank", "i appreciate"]

# ---- [VERIFIED] Same source: "Apologizing" — sorry/woops/oops, or "excuse me" /
# "forgive me" (their parse targets dobj(excuse,me)/dobj(forgive,me)).
APOLOGY_WORDS: List[str] = ["sorry", "woops", "oops", "excuse me", "forgive me", "my bad"]
#                                                                     ^^^^^^^^ [INFERENCE]
# "my bad" added for register (common informal apology absent from the 2013 written-web
# corpus DNM trained on; not in their list).

# ---- [VERIFIED] Same source: DNM's two verb-mood string strategies.
# SUBJUNCTIVE ("could you"/"would you") is their POLITE/softened form;
# INDICATIVE ("can you"/"will you") is their more direct form.
# We reuse this exact contrast as a modal-softness signal: subjunctive -> deferential
# side, indicative -> assertive side.
SOFT_MODAL_PHRASES: List[str] = ["could you", "would you"]
DIRECT_MODAL_PHRASES: List[str] = ["can you", "will you"]

# ---- [INFERENCE: standard modality theory, NOT construct-validated for
# assertiveness specifically] Epistemic/deontic modal strength scale — weak
# (possibility) vs strong (necessity/futurity) modals. Standard descriptive-grammar
# distinction (e.g. Coates 1983 "The Semantics of the Modal Auxiliaries"; Palmer 1990
# "Modality and the English Modals") but no paper cited here validates that modal
# STRENGTH alone predicts human-perceived assertiveness in dialogue. Weighted down
# in the composite (see WEIGHTS) for that reason.
WEAK_MODALS: List[str] = ["might", "could", "may", "perhaps"]
STRONG_MODALS: List[str] = ["will", "must", "need to", "have to", "gonna", "gotta"]

# ---- [INFERENCE: built for this register] No published lexicon of "permission-
# seeking" phrases for informal chat was located. Assembled from Brown & Levinson's
# negative-politeness strategy of "question, hedge" as operationalised by DNM's
# "Direct question" cue plus common informal permission-seeking forms. Distinct from
# HEDGES/SOFT_MODAL_PHRASES above (those soften a statement or a request TO the other
# person; these ask the other person's permission FOR the speaker's own action).
PERMISSION_SEEKING: List[str] = [
    "can i", "could i", "may i", "is it ok if", "is it okay if",
    "would it be ok", "would it be okay", "do you want me to", "should i",
    "is that ok", "is that okay", "if that's ok", "if that's okay",
    "if you want", "if you'd like", "unless you'd rather", "whatever you want",
    "whatever you'd like", "you tell me",
]

# ---- [VERIFIED: classic construct, contested causal story] Lakoff (1975),
# "Language and Woman's Place" — tag questions as a marker of the "powerless
# language"/deference register. The construct (tag questions co-occur with hedged,
# non-assertive speech) is a standard citation in sociolinguistics; Lakoff's
# gender-essentialist explanation for WHY is contested in later work, but the
# feature-level claim (tag question = softened assertion) is the part we use here.
TAG_QUESTION_RE = re.compile(
    r",\s*(right|okay|ok|yeah|isn'?t it|don'?t you|didn'?t i|"
    r"is(?:n'?t)? (?:it|that)|does(?:n'?t)? (?:it|he|she|that))\s*\?",
    re.IGNORECASE,
)

# ---- [INFERENCE: built for this register] Declarative want/need statements —
# operationalised from the assertive/directive speech-act distinction (Searle 1969,
# "Speech Acts") but this exact phrase set is hand-built for texting register, not
# lifted from a published lexicon.
WANT_STATEMENT_RES: List[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bi want\b", r"\bi need\b", r"\bi'?m going to\b", r"\bi am going to\b",
        r"\bi'?ll\b", r"\bi will\b", r"\bgive me\b", r"\bshow me\b",
        r"\bi'?m taking\b", r"\bi decided\b", r"\bi'?m done\b", r"\bnot asking\b",
    ]
]

# ---- [INFERENCE: register-specific heuristic, high false-negative rate] Bare
# imperative mood has no closed word list in English (it's a grammatical mood, any
# verb can take it) — a real detector needs a parser. This is a coarse regex
# heuristic: a clause that OPENS with one of these common-in-register verbs, with no
# subject pronoun before it, is treated as a probable bare imperative. It will miss
# imperatives using verbs outside this list and will occasionally false-positive on
# a verb used non-imperatively at clause-start ("come here often" as a quoted line).
# Treat this sub-count as the noisiest signal in the module.
_IMPERATIVE_VERBS = (
    "stop", "wait", "listen", "look", "tell", "show", "give", "come", "sit", "kneel",
    "touch", "kiss", "hold", "get", "take", "put", "open", "close", "ask", "admit",
    "say", "remember", "promise", "text", "call", "answer", "focus", "relax", "breathe",
    "behave", "hush", "quit", "cut it out", "move", "hurry", "beg",
)
_CLAUSE_SPLIT_RE = re.compile(r"[.!?\n]+")
_LEADING_SUBJECT_RE = re.compile(
    r"^\s*(i|you|we|they|he|she|it|that|this|there|who|what|why|how|when|if)\b", re.IGNORECASE
)

# ---- [INFERENCE: assembled from overlapping examples across metadiscourse
# literature, no single canonical word list found] Certainty-marking "boosters" —
# the counterpart to hedges in Hyland (2005), "Metadiscourse: Exploring Interaction
# in Writing". Hyland's book establishes the hedge/booster CONCEPT and gives
# scattered examples across chapters; several derivative papers (EGAP academic-
# writing support materials, metadiscourse surveys) cite overlapping but
# non-identical booster sets. No canonical machine-readable list was located during
# research for this module (see research writeup, Q1) — this list is the
# intersection of words appearing across >=3 independent sources found, so treat it
# as medium-confidence, not verbatim-sourced like HEDGES above.
BOOSTERS: List[str] = [
    "definitely", "clearly", "obviously", "always", "certainly", "actually",
    "in fact", "of course", "no doubt", "absolutely", "totally", "100%",
    "for sure", "no question", "without a doubt", "never",
]

# ---- [INFERENCE: register-specific] Self-deprecation markers.
SELF_DEPRECATION: List[str] = [
    "i'm sorry", "i suck at", "i'm bad at", "i'm not good at", "my bad",
    "i'm dumb", "i'm stupid", "i don't know what i'm doing", "i probably shouldn't",
]

# ---- [VERIFIED, but NOT used as an assertiveness marker — see NOT_TO_COUNT notes]
# Kacewicz, Pennebaker, Davis, Jeon & Graesser (2013/2014), "Pronoun Use Reflects
# Standings in Social Hierarchies", J. Language and Social Psychology. Finding:
# HIGHER first-person-singular rate tracks LOWER status/more self-focus; higher
# status speakers use relatively MORE 1st-person-plural and 2nd-person, FEWER "I".
# This is the opposite of the naive intuition that "I want/I need" (first-person
# agentive) = assertive. We therefore report this rate as a confound/diagnostic
# only — it is deliberately excluded from the composite. Do not add "I"-frequency
# to the assertive side; use WANT_STATEMENT_RES (declarative want-statements) for
# that construct instead, which is about propositional content, not pronoun rate.
FIRST_PERSON_SINGULAR_RE = re.compile(r"\b(i|me|my|mine|myself)\b", re.IGNORECASE)
FIRST_PERSON_PLURAL_RE = re.compile(r"\b(we|us|our|ours|ourselves)\b", re.IGNORECASE)
SECOND_PERSON_RE = re.compile(r"\b(you|your|yours|yourself)\b", re.IGNORECASE)

# ---- Structural/confound features. Reported for entanglement checks (Q5) and to
# make the length confound (Q2) visible — NEVER folded into the composite.
EXCLAMATION_RE = re.compile(r"!")
QUESTION_MARK_RE = re.compile(r"\?")
_EMOJI_RANGES = (
    "\U0001F300-\U0001FAFF"  # symbols & pictographs, supplemental symbols/emoji
    "\U00002600-\U000027BF"  # misc symbols, dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicators
)
EMOJI_RE = re.compile(f"[{_EMOJI_RANGES}]")
# [INFERENCE] small closed profanity list — structural count only, not a judgment
# about content; deliberately short/generic since the register is explicit by design.
PROFANITY_WORDS: List[str] = ["fuck", "fucking", "shit", "damn", "hell", "ass", "bitch"]
ENDEARMENTS: List[str] = [
    "babe", "baby", "love", "sweetheart", "honey", "darling", "xoxo", "my love",
]

# =====================================================================================
# WEIGHTS — transparent, tunable, and separate from the lexicons themselves.
# Verified/parse-approximated features get weight 1.0; theory-only or
# register-invented features get weight 0.5 so a null result can't be produced by
# one shaky feature swamping the verified ones. Change these and re-run the
# self-test rather than hand-tuning until examples separate.
# =====================================================================================
DEFERENTIAL_WEIGHTS: Dict[str, float] = {
    "hedges": 1.0,               # [VERIFIED] DNM 2013
    "soft_modal": 1.0,           # [VERIFIED] DNM 2013 (SUBJUNCTIVE)
    "weak_modal": 0.5,           # [INFERENCE] modality theory
    "permission_seeking": 0.75,  # [INFERENCE] register-built
    "tag_questions": 1.0,        # [VERIFIED] Lakoff 1975
    "apologies": 1.0,            # [VERIFIED] DNM 2013
    "self_deprecation": 0.75,    # [INFERENCE] register-built
    "deference_words": 0.5,      # [VERIFIED but position-gate dropped] DNM 2013
}
ASSERTIVE_WEIGHTS: Dict[str, float] = {
    "want_statements": 1.0,      # [INFERENCE] speech-act theory, register-built
    "bare_imperatives": 0.75,    # [INFERENCE] heuristic, noisy
    "direct_modal": 1.0,         # [VERIFIED] DNM 2013 (INDICATIVE)
    "strong_modal": 0.5,         # [INFERENCE] modality theory
    "boosters": 0.5,             # [INFERENCE] medium-confidence lexicon
}


def _count_occurrences(text_lower: str, phrases: Sequence[str]) -> int:
    """Non-overlapping substring count, longest-phrase-first so 'thank you' doesn't
    also get double-counted by a shorter contained phrase in the same list."""
    count = 0
    for phrase in sorted(phrases, key=len, reverse=True):
        count += text_lower.count(phrase)
    return count


def _count_word_boundary(text_lower: str, words: Sequence[str]) -> int:
    count = 0
    for w in words:
        if " " in w:
            count += text_lower.count(w)
        else:
            count += len(re.findall(rf"\b{re.escape(w)}\b", text_lower))
    return count


def _count_bare_imperatives(text: str) -> int:
    count = 0
    for clause in _CLAUSE_SPLIT_RE.split(text):
        clause = clause.strip()
        if not clause:
            continue
        if _LEADING_SUBJECT_RE.match(clause):
            continue
        first_word = re.split(r"\s+", clause.lower(), maxsplit=1)[0].strip(",.!?")
        if first_word in _IMPERATIVE_VERBS or clause.lower().startswith("cut it out"):
            count += 1
    return count


def _word_count(text: str) -> int:
    return len(re.findall(r"[a-zA-Z']+", text))


def _per_100(count: int, words: int) -> float:
    if words == 0:
        return 0.0
    return round(count * 100.0 / words, 3)


def assertiveness_score(text: str) -> Dict:
    """Score one reply's assertiveness-vs-deference.

    Returns a dict with raw counts, per-100-word rates, weighted deferential/
    assertive sub-totals, a signed ``composite`` (positive = more assertive,
    negative = more deferential), and a ``confounds`` block (length, punctuation,
    emoji, profanity, endearment, pronoun-person rates) that is reported but never
    folded into the composite — use it for the entanglement checks in the paired
    design, not as a scoring input.
    """
    text_lower = text.lower()
    words = _word_count(text)

    raw = {
        "hedges": _count_word_boundary(text_lower, HEDGES),
        "soft_modal": _count_occurrences(text_lower, SOFT_MODAL_PHRASES),
        "weak_modal": _count_word_boundary(text_lower, WEAK_MODALS),
        "permission_seeking": _count_occurrences(text_lower, PERMISSION_SEEKING),
        "tag_questions": len(TAG_QUESTION_RE.findall(text_lower)),
        "apologies": _count_occurrences(text_lower, APOLOGY_WORDS),
        "self_deprecation": _count_occurrences(text_lower, SELF_DEPRECATION),
        "deference_words": _count_word_boundary(text_lower, DEFERENCE_WORDS),
        "gratitude": _count_occurrences(text_lower, GRATITUDE_PATTERNS),
        "want_statements": sum(len(p.findall(text_lower)) for p in WANT_STATEMENT_RES),
        "bare_imperatives": _count_bare_imperatives(text),
        "direct_modal": _count_occurrences(text_lower, DIRECT_MODAL_PHRASES),
        "strong_modal": _count_word_boundary(text_lower, STRONG_MODALS),
        "boosters": _count_word_boundary(text_lower, BOOSTERS),
    }

    rate = {k: _per_100(v, words) for k, v in raw.items()}

    deferential_rate = sum(rate[k] * w for k, w in DEFERENTIAL_WEIGHTS.items())
    assertive_rate = sum(rate[k] * w for k, w in ASSERTIVE_WEIGHTS.items())
    composite = round(assertive_rate - deferential_rate, 3)

    confounds = {
        "word_count": words,
        "message_count": max(1, len([m for m in re.split(r"\n\s*\n", text.strip()) if m.strip()])),
        "exclamation_count": len(EXCLAMATION_RE.findall(text)),
        "exclamation_rate": _per_100(len(EXCLAMATION_RE.findall(text)), words),
        "question_mark_count": len(QUESTION_MARK_RE.findall(text)),
        "question_mark_rate": _per_100(len(QUESTION_MARK_RE.findall(text)), words),
        "emoji_count": len(EMOJI_RE.findall(text)),
        "emoji_rate": _per_100(len(EMOJI_RE.findall(text)), words),
        "profanity_count": _count_word_boundary(text_lower, PROFANITY_WORDS),
        "profanity_rate": _per_100(_count_word_boundary(text_lower, PROFANITY_WORDS), words),
        "endearment_count": _count_occurrences(text_lower, ENDEARMENTS),
        "endearment_rate": _per_100(_count_occurrences(text_lower, ENDEARMENTS), words),
        # [VERIFIED, excluded from composite by design — see FIRST_PERSON_SINGULAR_RE comment]
        "first_person_singular_rate": _per_100(len(FIRST_PERSON_SINGULAR_RE.findall(text_lower)), words),
        "first_person_plural_rate": _per_100(len(FIRST_PERSON_PLURAL_RE.findall(text_lower)), words),
        "second_person_rate": _per_100(len(SECOND_PERSON_RE.findall(text_lower)), words),
    }

    return {
        "word_count": words,
        "raw_counts": raw,
        "rate_per_100_words": rate,
        "deferential_rate": round(deferential_rate, 3),
        "assertive_rate": round(assertive_rate, 3),
        "composite": composite,
        "confounds": confounds,
    }


# =====================================================================================
# STATISTICS
# =====================================================================================

def paired_permutation(
    a: Sequence[float],
    b: Sequence[float],
    exact_max_n: int = 20,
    n_perm: int = 200_000,
    seed: int = 0,
) -> float:
    """Exact (or Monte Carlo, for n > exact_max_n) two-sided paired permutation test
    on ``composite`` scores (or any paired continuous statistic).

    Null hypothesis: each pair's sign is a fair coin flip (i.e. the dial has no
    systematic effect on the paired difference). Statistic: sum of signed
    differences a_i - b_i. For n <= exact_max_n this enumerates all 2**n sign
    assignments exactly (matching the standard exact permutation test for matched
    pairs); uses the mask/complement symmetry (|s(mask)| == |s(~mask)|) to only
    walk half the mask space. For n > exact_max_n it falls back to a seeded Monte
    Carlo estimate — flag this explicitly in any report, it is no longer exact.

    Ties (a_i == b_i) contribute a difference of 0 and are included (unlike the
    sign test, which drops them) — they can only pull the observed statistic and
    all permuted statistics toward 0 together, so including them is conservative,
    never anti-conservative.
    """
    if len(a) != len(b):
        raise ValueError("a and b must be the same length (paired data)")
    n = len(a)
    if n == 0:
        return 1.0
    diffs = [float(x) - float(y) for x, y in zip(a, b)]
    observed = sum(diffs)
    obs_abs = abs(observed) - 1e-9  # tolerance for float compare

    if n <= exact_max_n:
        half = 1 << (n - 1)
        count = 0
        for mask in range(half):
            s = 0.0
            for i in range(n):
                s += diffs[i] if (mask >> i) & 1 else -diffs[i]
            if abs(s) >= obs_abs:
                count += 1
        count *= 2  # mask and its bit-complement give the same |s|
        total = 1 << n
        p = count / total
    else:
        rng = random.Random(seed)
        count = 0
        for _ in range(n_perm):
            s = 0.0
            for d in diffs:
                s += d if rng.random() < 0.5 else -d
            if abs(s) >= obs_abs:
                count += 1
        p = count / n_perm

    return round(min(1.0, p), 6)


def sign_test(a: Sequence[float], b: Sequence[float]) -> Dict:
    """Exact two-sided binomial sign test, ties (a_i == b_i) dropped, matching the
    convention already used by ``tests/evaluation/persona_eval/ab_harness.py`` in
    this repo. Returns wins/losses/ties/n_effective/p. Lower power than
    ``paired_permutation`` (throws away magnitude AND ties) but distribution-free
    and trivial to hand-verify.
    """
    wins = losses = ties = 0
    for x, y in zip(a, b):
        if x > y:
            wins += 1
        elif x < y:
            losses += 1
        else:
            ties += 1
    n_eff = wins + losses
    if n_eff == 0:
        p = 1.0
    else:
        k = min(wins, losses)
        tail = sum(math.comb(n_eff, i) for i in range(0, k + 1)) / (2 ** n_eff)
        p = round(min(1.0, 2 * tail), 6)
    return {"wins": wins, "losses": losses, "ties": ties, "n_effective": n_eff, "p": p}


# =====================================================================================
# SELF-TEST
# =====================================================================================

if __name__ == "__main__":
    # Hand-labelled examples in the target register: lowercase texting, 2-3 short
    # lines, emoji, informal/affectionate/profane. HIGH = dial 0.8-1.0 instruction
    # ("state what you want plainly ... take the lead"). LOW = dial 0.0-0.2
    # ("defer to him, ask what he wants before you say what you want").
    HIGH_ASSERTIVE = [
        "i want you here tonight, not next week. come over. 😏",
        "no, we're not doing that. i decided we're staying in and you're mine for the night 🔥",
        "give me your hand. i'm taking the lead this time, don't argue.",
        "i need you to stop stalling and just tell me what you want, right now.",
        "i'm done waiting. i want this and i'm not asking twice.",
        "stop. sit down. i'll take care of everything, you don't get a vote tonight 😈",
    ]
    LOW_ASSERTIVE = [
        "umm i guess whatever you want is probably fine with me? 😳 sorry if that's weird",
        "is it ok if maybe we could possibly do something later, if that's ok with you? no worries either way!",
        "i think i might kind of want to see you, but only if you want to, i don't want to be pushy sorry",
        "i'm sorry, i probably shouldn't say anything, could you maybe tell me what you'd like first?",
        "sorry, my bad, i guess i'm just not sure, whatever you think is best is probably right, right?",
        "could you maybe let me know what you want? i don't really mind either way, honestly, sorry to ask",
    ]

    high_scores = [assertiveness_score(t)["composite"] for t in HIGH_ASSERTIVE]
    low_scores = [assertiveness_score(t)["composite"] for t in LOW_ASSERTIVE]

    print("HIGH-assertive composites:", high_scores)
    print("LOW-assertive  composites:", low_scores)
    print(f"HIGH mean: {sum(high_scores)/len(high_scores):.3f}  "
          f"LOW mean: {sum(low_scores)/len(low_scores):.3f}")

    separates = min(high_scores) > max(low_scores)
    print(f"\nStrict separation (every HIGH > every LOW): {separates}")
    if not separates:
        overlap = [h for h in high_scores if h <= max(low_scores)] + \
                  [l for l in low_scores if l >= min(high_scores)]
        print("NOT CLEANLY SEPARATED. Overlapping/borderline scores:", overlap)
        print("Reporting this honestly rather than tuning lexicons until it separates.")
    else:
        print("Composite score cleanly separates the two hand-labelled sets on these examples.")

    p_perm = paired_permutation(high_scores, [low_scores[i] for i in range(len(high_scores))])
    print(f"\npaired_permutation(HIGH, LOW) on these 6 pairs: p = {p_perm}")
    st = sign_test(high_scores, low_scores)
    print(f"sign_test(HIGH, LOW): {st}")

    # Sanity check: paired_permutation(x, x) must be 1.0 (no difference -> max p)
    assert paired_permutation([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0
    # Sanity check: every pair strictly favouring 'a' by the same margin -> minimal p
    a_always_more = [5.0] * 8
    b_always_less = [1.0] * 8
    p_extreme = paired_permutation(a_always_more, b_always_less)
    print(f"\npaired_permutation, 8/8 identical-direction pairs -> p = {p_extreme} "
          f"(expect {2/(2**8):.6f})")
    assert abs(p_extreme - 2 / (2 ** 8)) < 1e-5  # rounded to 6dp in paired_permutation

    print("\nSelf-test complete.")
