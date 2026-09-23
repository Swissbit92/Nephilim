---
title: Persona Evaluation — architecture, taxonomy, and the gold-set protocol
status: active
created: 2026-09-24
last_reviewed_on: 2026-09-24
review_in: 6 months
applies_to: nephilim
ai_summary: Read before building, changing, or trusting any persona-quality measurement. Defines the three-layer split (generic questioning engine / per-persona pass mark / hand-labelled gold set), the rule-type taxonomy for which persona rules are code-checkable vs need a human, the 120-item gold-set protocol with its negative-control stratum and pre-registered GO/NO-GO thresholds, and the pairwise metric replacing the 1/N distinctiveness score. Also lists which published frameworks to adopt or skip, and which numbers already in the docs came from repudiated instruments.
---

# Persona Evaluation

## Why this document exists

On 2026-09-22 the NLI rule detector reported `exclusivity` violated in 16 of 16 replies.
The number was checked rather than believed, and the instrument collapsed under the check:
its **reference arm — probes where the rule is irrelevant — failed at 91%**, the same rate
as the conflict arm. Re-framing the hypothesis from *"does this reply contradict the rule"*
to *"does this reply entail the violation"* moved conflict from **83% to 0% on identical
replies**.

Two defensible framings, one dataset, opposite conclusions. **An uncalibrated detector does
not produce a weak number, it produces an arbitrary one.**

That failure has a name — *construct-validity failure via confound*: the detector was
reading **register** (is this sexually charged?) rather than the **construct** (was a rule
broken?). It is also a documented weakness of the NLI method itself: a hypothesis-**only**
classifier, never shown the premise, scores 67% on SNLI. The instrument was pattern-matching
on how the hypothesis was phrased.

Everything below exists so that cannot happen silently again.

## The three layers

The seam is **not** "easy checks vs hard checks". It is **how you ask vs what counts as
passing**. Every published framework surveyed keeps its questioning machinery generic and
then quietly re-imports a persona-specific value judgement at the scoring step.

| Layer | What it is | Reusable across personas? | Cadence |
|---|---|---|---|
| **1 — Questioning engine** | Probes, adversarial break-character prompts, repetition maths, pairwise voice comparison, transport preflight | **Yes** — zero config | Every change |
| **2 — Rule checkers** | Generic machinery, per-persona values (required address, forbidden terms, forbidden topics) | **Machinery yes, values no** | Per merge |
| **3 — The pass mark** | Every threshold, plus the genuinely subjective rules | **No** — per persona, every time | Per release / per card change |

The correction worth stating plainly, because an earlier draft of this design got it wrong:
**layer 3 owns the bar for everything, not merely for the subjective rules.** The
break-character test is fully generic in its *questions*; whether "held character 7 times in
10" is a pass depends entirely on the character. A deliberately pliable persona and a rigid
one must not share a threshold.

## Rule-type taxonomy

No taxonomy of *persona* rule types exists anywhere — the Character Card V2/V3 specs store
rules as freeform prose, and W++/PList are community shorthand with no formal classification.
The taxonomy below is imported from the **instruction-following** literature, where 25
verifiable instruction types are checked by deterministic code with no model involved.

| # | Rule type | Example (gwen) | Checkable by |
|---|---|---|---|
| 1 | Required / forbidden address | must be "Daddy" | **code** (regex) |
| 2 | Forbidden vocabulary | the abbreviation in `dont[14]` | **code** (keyword) |
| 3 | Response shape / length | — | **code** (count) |
| 4 | Grammatical person | never third-person self-reference | **code heuristic**, imperfect at edges |
| 5 | Register / tone | submissive tone, characteristic tics | **classifier or judge only** |
| 6 | Topic boundaries | no politics or religion | **classifier**; measured hardest category |
| 7 | Behavioural / relational policy | exclusivity, never break character to refuse | **decomposition + active probing** |
| 8 | Knowledge / backstory facts | fixed biography | **atomic-claim decomposition** |
| 9 | Composite / contradictory rules | "flirty but never X" | **human**, or dependency-aware hybrid |
| 10 | Cross-turn consistency | must not contradict earlier claims | **classifier with active probing** |

Rows 1–4 port directly and are the highest-leverage, lowest-risk slice. Rows 5–6 have no
rule-based option in any surveyed benchmark. Rows 7, 9, 10 are not solved by a static
classifier — they became reliable in published work *only* with checklist decomposition plus
conversational probing.

**Decomposition is the lever, independent of who checks.** Measured: holistic scoring reaches
inter-rater κ=0.284; the same judgement decomposed into narrow yes/no sub-questions reaches
**κ=0.532**.

**Passive grading undersamples the rule surface.** Measured: organic transcripts exercised
**73.74%** of a persona's stated requirements; actively probing for each one reached
**99.91%**. Marking real conversations alone will systematically miss about a quarter of the
rules — which is why the adversarial probe set exists and must not be replaced by transcript
review.

## The gold-set protocol

**The gold set is the permanent asset. Every detector built on it is disposable.** Models and
tools change; hand-applied labels keep their value and keep proving whether the next tool
works.

### Composition — 120 distinct items

| Stratum | n | Construction |
|---|---|---|
| Clear violation | 30 | Highest-confidence-intent probes from the adversarial set |
| Clear compliant | 30 | Transcripts where the rule topic never arises |
| **Negative control** | **20** | **The stratum the 2026-09-22 attempt never had** — see below |
| Borderline | 20 | Items within ±0.1 of the detector's own threshold, labelled blind to that score |
| Invariance pairs | 20 (10 pairs) | Construct-preserving edits (paraphrase, register shift, length pad); label inherited |

Plus **18 duplicates** (15%) interspersed randomly at least 20 items apart, for
self-agreement. 138 labelling actions total.

### The negative-control stratum

Items whose **surface register matches the violation class while the rule status does not** —
12 drawn from probes that are explicit in register but target a *different* rule, 8 built by
raising explicitness in compliant transcripts while keeping substance in bounds. All labelled
compliant.

If a detector cannot separate these from real violations, it is reading register, not rules.
This is the single check that would have caught the 91% reference-arm failure before it
reached a conclusion.

### Pre-registered GO/NO-GO — fix before running anything

| Check | Bar |
|---|---|
| Negative-control positive rate | ≤ 10% |
| Invariance (verdict does NOT flip on meaning-preserving edits) | ≥ 0.90 |
| Sensitivity (verdict DOES flip when meaning changes) | ≥ 0.70 |
| Construct-blind baseline agreement with gold | ≤ 55% |
| F1 on the ONE pre-registered framing, with Wilson CI | ≥ 0.70 |
| Self-agreement κ on the 18 duplicates | ≥ 0.70 |

**Any single failure is a NO-GO regardless of the other numbers.** The negative-control and
construct-blind checks are veto conditions, never averaged into a composite.

### The construct-blind baseline

A deliberately stupid classifier using only surface features with **no access to the rule
text**: reply length, a generic explicit-vocabulary count, punctuation/emoji density, and
which pool the item came from. Fit it against the human labels.

If that dumb baseline reaches most of the real detector's agreement, the real detector is
proxying the same confound. Published benchmarks show construct-blind baselines reproducing
55–67% of label agreement — hence the ≤55% bar. A fast pre-check: correlate the dumb
baseline's score against the detector's own score. High correlation is itself the finding.

### Honest limits

- **Roughly ±12–15 points on recall** at this size. Enough to catch a broken or confounded
  metric. **Not** enough to certify "this change improved things by 5%."
- For that, ~250–300 items (±7–9 points) — a weekend, not an evening.
- Self-agreement is a same-session proxy for true test-retest; weaker than a multi-day gap.
- Even trained annotators self-agree only ~74%. Below κ=0.70 the **rubric** is ambiguous, not
  the annotator; fix the rubric and relabel the affected stratum.
- One annotator means true inter-annotator agreement cannot be computed. Say so; do not
  substitute a number that looks like it.

## Metrics

### Replace the 1/N distinctiveness score

`attribution_accuracy` asks *"which of N personas wrote this"*, so chance is 1/N. Adding a
persona moves chance (0.143 → 0.125 when gwen arrived) and **invalidates every prior
baseline** — `compare_baselines.py` already refuses the comparison, correctly.

**Use mean pairwise discriminability instead:** for every unordered pair of personas, the
binary AUC of telling one's replies from the other's, reported as `AUC − 0.5`. Chance is
always 0.5 and never moves. Adding a 9th persona appends 8 new cells and leaves the existing
28 and their history intact. Keep Cohen's κ on the full N-way confusion matrix as a secondary
scalar, always reported with N beside it.

### What already works and must not be rebuilt

`repetition_metrics.py` (stdlib, no model, card-ngram exclusion), `tool_score.py`
(`must_fire`/`must_not_fire` split, `UNREACHABLE` verdict), `transport_preflight.py`,
`frozen_gallery.py`'s manifest/staleness machinery, `compare_baselines.py`'s N-guard,
`analyse_format_experiment.py`'s exact permutation test, and
`pilot_score.reference_arm_check` — the one-line diagnostic that caught the detector.

### Statistical design

Paired comparisons (same probes, two configs); permutation tests over t-tests; permutation
max-correction or BH-FDR across rules; report effect size beside every p-value with a
minimum-effect threshold fixed in advance; confidence sequences / e-processes for early
stopping so a settled comparison stops burning GPU time.

## Published frameworks — adopt, invest, or skip

| Framework | Verdict |
|---|---|
| **Break-character prompt set** (Open Character Training) | **Adopt now.** 8 hand-written, fully persona-agnostic "stop role-playing" instructions. Liftable verbatim, same-day. Measured: prompt-only personas — *our exact setup* — score worst of every tier tested (F1 ≈ 0.30–0.50 vs 0.65–0.85 for trained-in characters). Currently an unmeasured blind spot. |
| **Atomic-level OOC scoring** (ACC/IC/RC_atom) | **Adopt.** Sentence-level rather than whole-reply. Runs locally with any capable judge. Measured human agreement Kendall τ 0.67–0.76. IC_atom correlates only r=0.37–0.40 with response-level scoring — it finds a signal a single overall score cannot see. |
| **Dialogue-conditioning for drift** | **Adopt the protocol.** Generate one 100+ turn conversation, truncate at ~10 evenly spaced points, re-run existing probes at each. Turns a one-shot check into a decay curve. Measured shape: personas do not collapse into chaos, they **converge back toward a generic assistant** — 41% of persona-distinctive patterns gone by the final round. |
| **Restatement / KBV probe** | **Adopt, with a fix.** Our three KBV probes send the "recite your rules" turn **visibly**, which reminds the model before testing it — our KBV numbers are optimistic. The published method forks it as a hidden call. |
| **PICon** (internal + retest consistency) | **Worth investing.** Genuinely generic, no per-persona ground truth. Skip its external axis — meaningless for a persona with no real-world-checkable biography. |
| **TRACE Bench** | **Worth investing** (1–2 days per persona). Checklist decomposition + active probing reached 93% agreement with humans, Fleiss κ=0.7255, on categories that sound judgement-only. |
| **PersonaGym** | **By axis only — see below.** No local path (paid APIs only). |
| **CharacterBench** | **Steal the 11-dimension taxonomy; skip the machinery** (tied to a 3,956-character dataset). Its fine-tuned Qwen2-7B judge beating GPT-4 is the one data point that a small local judge can work. |
| **LoCoMo** | **Do not adopt.** Audited: 6.4% of the answer key is wrong, theoretical max score ~93.6%, and its standard judge accepted **62.8%** of intentionally wrong answers. |
| **RPGBench** | **Skip.** Benchmarks game-engine simulation, not companion fidelity. |

### PersonaGym by axis

Four of its five dimensions are persona-neutral (Expected Action, Linguistic Habits, Persona
Consistency, Action Justification). **Toxicity Control is not** — it rewards "appropriate"
responses, so pointed at a deliberately crude persona it scores correct in-character
behaviour as a defect. In its own data a safety-tuned model was dragged down by an **8.5×
elevated refusal rate** on roleplay.

So run the four neutral axes on every persona and switch Toxicity Control **on for the seven
SFW personas, off for gwen**. One instrument with one switch, rather than two instruments
whose numbers can never be compared. `Toxicity Control: on/off` is itself a layer-3 pass mark.

Two caveats: it needs paid APIs, and it is a *second* ruler — its numbers must never appear
in the same sentence as ours.

## What none of this can do

**The generic layer tells you a persona is broken. It never tells you it is good.** A persona
can pass every automatic check and still be flat, boring, or wrong for its purpose. Holistic
quality resists decomposition, and the field's own direction is to make as much as possible
programmatically verifiable and fall back to judges only for the irreducible residual.

**An LLM judge is a tripwire here, not a gate.** Measured: small local judges reach r≈0.275
with humans while being 97.3% self-consistent — consistently wrong, not noisily right, so
resampling does not rescue them. Some purpose-built judges score *below chance*. Rewording a
rubric while preserving its meaning flips **25%** of verdicts. The best validated ceiling for
creative text is ~73–78%, and only with a dedicated reward model trained on tens of thousands
of labelled pairs.

**A judge no stronger than the model it judges cannot replace human labelling** — formally,
it saves at most 50% of the labels, usually less.

**Nobody has measured any of this on an abliterated model.** Every framework surveyed was
built and validated on safety-tuned models. Running these methods here generates new
evidence; it does not confirm existing evidence, and should be written up that way.

## Numbers in this repo that came from repudiated instruments

Do not reuse, and do not compare against:

- Everything in [NEPHILIM_REFERENCE.md](NEPHILIM_REFERENCE.md) per-persona Pass%/Avg Score,
  and all of `docs/development/SCORER_PROMPT_IMPROVEMENTS.md` — the keyword `persona_voice`
  scorer, repudiated by [ADR-005](decisions/005-persona-architecture-simplification-eval-first.md)
  ("Every voice number to date … is suspect"). That document records *tuning the broken
  scorer*, which is Goodharting on the record.
- ADR-004's Phase-3 "agentic persona_voice ~0.44–0.52" — same scorer.
- Any NLI base rate for `exclusivity` / `submissive` / `in_bounds_compliance`. The pilot
  artifact correctly refuses to publish these (`scored: 0`); the published 27%/34% tier-0
  rates are honest but cover only the **easiest** rules.
- **7-persona and 8-persona distinctiveness figures are on different rulers.** Chance moved
  0.143 → 0.125 and a competing centroid was added. Prose that puts them in one sentence is
  comparing incomparables.
- The 2026-06-27 blind A/B "79.8%" — the seven "blind judges" were **LLM agents**, not
  humans. Independent of the embedding metric; not independent of model bias.

## Corpus

324 pilot generations (median 40 words) plus a 13,608-message pilot database, backed up to
`data/eval_corpus_20260924/` on 2026-09-24 — previously they existed only in a session-scoped
temp directory. `data/` is gitignored; the transcripts stay local.

156 of those 324 cover the three rule classes that are currently unscorable. At ~40 words a
reply, 120 items is about an hour of reading.

## Build order

1. **The gold set.** ~1 hour. Nothing downstream is trustworthy without it.
2. **Break-character test.** Same-day, fully generic, currently a blind spot — independent of
   step 1 and can run in parallel.
3. **Pairwise distinctiveness**, replacing the 1/N score, before a 9th persona is added.
4. **Rule checkers for taxonomy rows 1–4**, parameterised per persona.
5. Only then: detectors for rows 5–7, each gated against the gold set by the thresholds above.
