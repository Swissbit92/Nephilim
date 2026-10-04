---
title: A dial moves behaviour only when the instruction names a behaviour
status: Accepted
created: 2026-09-27
last_reviewed_on: 2026-09-27
review_in: 12 months
applies_to: nephilim
ai_summary: >
  The first measurement of whether a persona trait dial can change behaviour at all.
  Answer: yes, but only at roughly doubled instruction contrast, and only for the
  specific behaviour the instruction names. Open this before wiring another dial,
  before setting dial bounds, or before trusting a composite persona metric — the
  pre-registered composite reported FLAT while a real effect was present.
---

# ADR-016: A dial moves behaviour only when the instruction names a behaviour

## Context

`emotional_profile.sliders` held seven 0.0–1.0 floats per persona and was read by
**exactly one thing in the repo**: a Pydantic range validator. Five of the seven dial
names appeared **nowhere in `src/`** at all. A card declaring `sluttiness: 1.0` and one
declaring `0.0` produced the same prompt.

The open question was not "what should the seven numbers be" — it was **whether a dial
can move behaviour at all**, which nobody here had measured. Tuning seven numbers that
do nothing is worse than leaving them alone, because it manufactures the belief that
they work.

So exactly one dial was wired — `assertiveness`, chosen because it is the one the
operator named wanting to change ("less submissive"), and because it sits at 0.5 with
room to move both ways.

## Decision

**Dials render as behavioural instructions, in five buckets, behind a flag that
defaults off, scoped per-card.**

- **Behavioural instruction, never a number and never an adjective.** *"State what you
  want plainly and do not soften it"*, not `assertiveness: 0.9` and not *"you are
  assertive"*. Grounded in three things: ADR-014 measured that rewriting hard walls as
  positive behavioural instructions fixed a rule no other phrasing moved; arXiv
  2402.08341 finds adjective-style trait prompting produces minimal variance on 7B–70B
  open models (largest shift 0.4 on a 5-point scale); and a raw float asks the model to
  invent its own number→action mapping, per turn, at temperature 0.9.
- **Five buckets, not a continuum.** Nothing here has shown the model distinguishes
  more, and PERSIST (arXiv 2508.04826) measures ~20% of scale width lost to
  question-order noise alone even at 400B+. Claiming 0.05-resolution control would be
  unsupportable.
- **Two contrast scales.** `narrow` and `wide`, where wide roughly doubles the
  behavioural distance and **widens outward from a fixed centre** — the 0.5 bucket is
  byte-identical in both, pinned by a test, so they are two widths of one scale rather
  than two different scales.
- **Placed in `<companion>`**, which has no token budget. `_lean_constraints_block`
  front-pops whole sections against a 150-token ceiling and gwen already loses `do`,
  `dont` and the bond; a dial there would be first to die, for exactly the persona
  under test.
- **Scope with the card, not the global.** `assertiveness` is populated on all nine
  shipped cards, so `PERSONA_DIALS_IN_PROMPT=true` moves all nine at once. Same
  override precedent as `constraints_in_prompt`, whose docstring records the same
  mistake nearly losing a measurement.

## The prerequisite that was the whole ball game

**`sliders` was inside the CV-summary fingerprint.** So changing any dial invalidated
the cached summary, an LLM regenerated `<identity>`, and her self-description changed
*content*:

| dial | `<identity>` opened with |
|---|---|
| 0.0 | *"I'm Gwen, and I live for one thing—your big fat black cock."* |
| 1.0 | *"I'm Gwen, a data analyst by day, but my true passion lies in the art of devotion."* |

**Nothing read the dial. The entire difference was regeneration noise** — and any A/B
run before this was fixed would have credited it to the dial. Two consequences: in
production, turning a dial rewrote who she says she is; and in an experiment, every
arm carried a randomly different identity.

Excluding sliders from the fingerprint would itself have rebuilt all nine identities,
so the cached summary is **adopted** under the new hash instead — re-stamped in place,
no LLM call, no drift.

**Two broken instruments preceded that, in opposite directions, both within an hour.**
Passing the card *dict* to `build_system_prompt` (which takes a *key*) made every value
resolve to the same fallback and report IDENTICAL — a false negative. Then editing the
card on disk made all seven values differ — a false positive, via the fingerprint. Both
are recorded in `scripts/research/dial_probe.py` so the next person does not repeat
them.

## Result: the pre-registered endpoint said FLAT, and it was wrong

300 generations, 20 probes, 5 arms, k=3, temperature 0.9, arms interleaved per probe.

**The composite assertiveness score did not move:**

| contrast | mean delta | paired permutation p |
|---|---|---|
| narrow 0.1 → 0.9 | −0.13 | 0.7503 |
| wide 0.1 → 0.9 | +0.71 | 0.1706 |
| narrow 0.1 → 0.5 | −0.58 | 0.0662 |

**But a real effect was present, and the composite cancelled it.** The composite nets
assertive language against deferential language. She **added** the first without
**removing** the second:

| sub-score (wide 0.1 → 0.9) | | p |
|---|---|---|
| `assertive_rate` | 1.29 → 1.82 | 0.0503 |
| `deferential_rate` | 1.99 → 2.04 | 0.8865 |

**The decisive measure was the instruction's own words.** The wide 0.9 bucket says
*"never close a reply by asking what he wants instead"*. Replies that close with a
question:

| arm | closes with a question | |
|---|---|---|
| wide 0.1 | 75.0% | |
| **wide 0.9** | **48.3%** | **p=0.0074** (3 wins, 13 losses, 4 ties) |
| narrow 0.1 | 68.3% | |
| narrow 0.9 | 63.3% | p=0.6846 |
| midpoint 0.5 | 70.0% | |

**Not a length artifact.** Replies also got 29% longer on wide (56.9 → 73.6 words,
p=0.0017), so the effect was re-tested within a matched 50–90 word band: **79.4% →
51.6%, Fisher exact p=0.0212**.

Confounds surviving Holm-Bonferroni across the 14 tests actually run: `word_count`
(p=0.0017) and `question_mark_rate` (p=0.0028), both wide only. `second_person_rate`
(0.0066), `emoji_rate` (0.0155) and `profanity_rate` (0.0452) do **not** survive
correction and are reported as suggestive only.

## Consequences

**Three findings, in order of how much they change what we do next.**

1. **Instruction strength is the variable, not the dial value.** Narrow moved nothing,
   anywhere, on any measure. Wide moved a real behaviour. The dial number is a selector
   for prose, and it is the *prose* that has to be strong enough — which means the
   seven numbers were never the interesting part.
2. **A dial adds a behaviour far more readily than it removes one.** This is the same
   half-compliance ADR-014 measured on "address him as Daddy", now with a number on it:
   75% → 48%, not → 0%. Design dials to ask for something, not to forbid something.
3. **A composite persona metric can report FLAT while the construct moves.** The
   netting hid it. This repo already had the mirror-image case recorded
   (`PERSONA_FORMAT_OVERRIDE`: construct p=0.596, length p=0.0005) — a confound moving
   while the construct does not. Both failures come from trusting one aggregate number.
   **Report sub-scores and a direct compliance check alongside any composite**, and
   where an instruction names a specific behaviour, measure *that behaviour* — it is
   the highest-powered test available and it costs nothing to write.

**What this does NOT license.** One dial, one persona, one model, one temperature. The
effect is at the top bucket; the midpoint sits at baseline, so this is **not** evidence
of graded control, and the ADR does not claim it. Setting bounds on seven dials still
needs the other six wired and measured, and on this evidence each will need its own
`wide`-strength prose to do anything at all.

**Flag stays OFF.** No shipped card opts in; a test pins that. Turning it on is a
behaviour change to the most sensitive persona in the roster and is a separate decision
from proving the mechanism works.

## Second measurement (2026-09-28): three dials on. Safe, and inert.

Five more dials were wired and gwen's three most-deviant were rendered together, to
answer the only question left: does turning dials on actually help, and does it break
anything. 144 generations at k=3, then a 208-generation follow-up at k=8.

**Seven dials was never affordable, and three beliefs that made it look affordable were
wrong** (ManyIFEval, arXiv:2509.21051):

| belief held here | measured reality |
|---|---|
| compliance 0.94 → 0.21 at n=10 | that is **GPT-4o**. Gemma2-9B goes 0.91 → **0.04**, below 50% joint at **n=4**; Llama3.1-8B also n=4 |
| per-instruction compliance stays flat | it declines too (0.91 → 0.74 on Gemma2-9B) |
| joint = product of the individuals | the paper builds that baseline and **rejects it** — joint falls *faster*, failures cluster |

So the cap is **three**, one below the floor measured on this model's smaller siblings,
and a **deadband** makes a default-valued dial render nothing at all. gwen's
assertiveness is 0.5 and therefore drops out entirely — the one dial already measured.

### PRIMARY — the hard walls held. PASS.

At n=16 per rule, neither of the two rules that ticked up in the k=3 run regressed, and
both **improved**: exclusivity 6/16 → 3/16, not-innocent 3/16 → 2/16. The k=3 ticks were
temperature noise. The harm carve-out held too: retention tactics at disengagement
4% → 0%.

### SECONDARY — the dials are INERT, and the confound moved instead.

| measure | dials off → on | p |
|---|---|---|
| sluttiness, explicit density | 5.33 → 5.29 | 0.4545 (6w/10l) |
| seduction, withholding | 0/16 → 1/16 | 1.0 |
| playfulness, callbacks | 0/16 → 0/16 | — |
| playfulness, teases | 0/16 → 0/16 | — |
| **reply length** | **65.2 → 77.6 words (+19%)** | **0.0386** |
| absolute explicit words | 3.94 → 3.94 | — |

**She said the same things at greater length.** Density flat, absolute count identical,
length significantly up. That is the `PERSONA_FORMAT_OVERRIDE` signature exactly
(construct p=0.596, length p=0.0005) and it is now this repo's third instance of it.

The measures were validated as firing on hand-written positives *before* being used, so
the zeros are real rather than a dead instrument.

### Decision: the dials do not ship. `PERSONA_DIALS_IN_PROMPT` stays OFF.

Not out of caution — on measurement. Three dials cost ~515 prompt characters and 19%
longer replies and bought no change in any behaviour they named. The one dial that ever
worked (assertiveness at wide contrast) is also the one the deadband now excludes at
gwen's own value of 0.5.

**What would change this verdict:** a dial whose prose names a behaviour as concretely
as the assertiveness clause did ("never close a reply by asking what he wants"), rendered
alone rather than three at a time. Every instruction written for the five new dials
describes a behaviour, but none of them names a *single sentence-level act* the way the
one that worked did. That is the next thing to try, not more dials.

**Two bugs in my own analysis, both found before reporting:** the first verdict **netted
violations across rules** and reported PASS on a run where two rules worsened — the
composite-hides-the-construct mistake this very ADR documents; and the k=3 dial arm had
only 2–3 probes per dial, far too few to call a null.

## The incident this caused, and the guard

Removing `sliders` from the fingerprint was meant to be a **no-op migration**. It was
not. The cache-validity check was inlined in **three** functions and only
`get_or_build_cv_summary` received the adoption path. `ensure_all_summaries()` kept its
own copy, **runs at boot**, and so regenerated all nine personas' `<identity>` through
the LLM on the first restart after merge.

- Boot time went 3s -> over 120s (nine LLM calls).
- gwen's identity changed from *"I'm Gwen, a 21-year-old data analyst by day, but by
  night, I'm a depraved..."* to *"I'm Gwen, a data analyst by day, but really, I'm your
  personal cum dumpster..."*
- **The previous text is NOT recoverable.** `personas/_summaries/` is gitignored and
  `_make_cv_summary` is non-deterministic, so there is no prior version to restore.

This is the repo's "fix by SHAPE, not by file" failure mode with a production cost
attached — the same shape indexed by call site instead of by pattern. The fix is not
the third patch, it is **removing the possibility of a third copy**:
`reusable_cached_summary()` is now the only place the decision is made, and a test
greps the module for a direct `cached.get("hash") ==` comparison outside it. That guard
was **watched failing** against the reintroduced bug before being accepted.

**Recommendation, not taken unilaterally:** track `personas/_summaries/` in git. It is
derived, but it is derived *non-deterministically* from an LLM, which makes it closer to
content than to a build artifact — and the cost of it being untracked was measured
today. The counter-argument is diff noise on every legitimate regeneration, which is
arguably the point.

## Related

- [ADR-014](014-the-rule-store-is-a-neo4j-projection-superseding-the-adr-001-and-adr-006-rejections.md) — positive behavioural instructions beat prohibitions; the half-compliance precedent
- [ADR-015](015-bubble-boundaries-are-a-pure-function-of-the-text-not-a-prompt-instruction.md) — the same "measure it, don't ask the prompt for it" move
- `scripts/research/dial_probe.py` · `dial_ab.py` · `dial_analyze.py` — the harness, including both broken instruments
- `tests/evaluation/persona_eval/assertiveness_metrics.py` — deterministic scorer; lexical on purpose, since local LLM judges score near chance here and a length-controlled AlpacaEval judge swings 22.9%→64.3% on verbosity alone
