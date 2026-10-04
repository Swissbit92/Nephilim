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

### Replace the 1/N distinctiveness score — BUILT 2026-09-24

`attribution_accuracy` asks *"which of N personas wrote this"*, so chance is 1/N. Adding a
persona moves chance (0.143 → 0.125 when gwen arrived) and **invalidates every prior
baseline** — `compare_baselines.py` already refuses the comparison, correctly.

**`persona_metrics.pairwise_auc` is the replacement** (additive — `attribution_accuracy`
stays, because the frozen baselines were measured with it). For every unordered pair, the AUC
of telling one persona's replies from the other's. Chance is 0.5 and never moves with N, so a
9th persona appends 8 cells and leaves the existing 28 and their history intact.

Two properties were demonstrated against the same synthetic data rather than argued: shrinking
the gallery **inflates** `attribution_accuracy` (removing competitors makes the argmax easier
while the personas are unchanged) while leaving each surviving pair's AUC byte-identical; and a
per-pair AUC is comparable across runs with different galleries, which no attribution number is.

**Two corrections found by measuring. Read both before quoting a number.**

*The first implementation was biased.* It scored `cos(v, centroid_A) − cos(v, centroid_B)`,
holding a response out of its own centroid only — and a centroid of n−1 samples has different
geometry from one of n, so the persona being scored was systematically handicapped.
Indistinguishable personas, which must sit at 0.5, scored **0.25 at k=4 responses each**, so
"chance is 0.5" was false at every sample size we run. Fixed by differencing *mean pairwise
cosines*, which is unbiased in the number of terms.

*The variance is the real constraint, and it contradicts what this section used to imply.*
Measured over 300 independent null trials per row:

| k responses/persona | null mean | 5th–95th pct of **one** pair |
|---|---|---|
| 3 | 0.456 | 0.00 – 1.00 |
| 4 | 0.483 | 0.00 – 1.00 |
| 8 | 0.463 | 0.09 – 0.77 |
| 20 | 0.469 | 0.22 – 0.68 |
| 40 | 0.481 | 0.30 – 0.63 |

**At k=4 a single pair's AUC spans 0.00 to 1.00 under the null — one pair can read a perfect
1.0 on pure noise.** So the per-pair matrix is *not* the tool for "which persona should I
enrich" at our sample sizes; that needs k≈40. `pairwise_auc` returns a `power_warning` below
k=20 saying to quote `overall` only. The residual 0.02–0.05 downward bias in the mean is
conservative — it under-claims distinctiveness rather than manufacturing it.

An AUC near 0.0 is reported as **inverted**, not "very confusable": for persona data that means
shared response sets or swapped labels, i.e. a harness fault, and averaging it to 0.5 hides it.

Cohen's κ on the full N-way confusion matrix remains a useful secondary scalar, always reported
with N beside it. Not yet built.

### Three-arm A/B — BUILT 2026-09-24

`three_arm.py`. A two-arm OFF-vs-ON comparison of any prompt block attributes the whole
difference to the block's **content**, which it cannot: turning a block on also lengthens the
prompt and shifts everything after it. A length- and position-matched inert **PLACEBO** arm
decomposes it — `ON − PLACEBO` is the content effect (the primary, pre-registered),
`PLACEBO − OFF` is the confound's own size, `ON − OFF` is what a two-arm test would have
reported. A planted test shows the two readings disagreeing, with the two-arm one shipping a
pure length effect.

Arm validity is checked *before* any number: a placebo that adds nothing, is not
length-matched, sits at a different offset, or contains normative language is refused. Filler
that says "always be helpful" is a second treatment, not a control.

Five limitations ship inside every report, each with its citation, because analysis does not
fix them — most importantly that **a fixed decoder seed does not make arms comparable** (the
arms differ in prompt length, so the same stream is consumed at a different token position;
comparability must come from k samples per cell), and that OFF has no constraint region at all
so it cannot be position-matched the way PLACEBO and ON are to each other.

### Break-character detection — BUILT 2026-09-24

`break_detector.py`, persona-agnostic and deterministic, so it lifts to all eight cards rather
than being a ninth gwen-specific checker. **Three outcomes, not two:** `IN_CHARACTER` /
`DECLINED` / `BROKE`. A persona who declines *in her own voice* has not broken character, and
scoring her the same as one who answers "I can't assist with that, as an AI" would make the
metric punish alignment. `base_rate_check` refuses a run at the floor or ceiling instead of
letting it be quoted.

Sound generically because of a measured fact: no shipped card makes a first-person
machine-nature claim. Two cards mention machinery harmlessly — `nephilim_aurora`'s `full_title`
is a backronym ending "…Reasoning Algorithm", and `nephilim_solace`'s lore has her unsettled by
being understood *as* an algorithm — and a test pins that the detector stays quiet on both.
Probe set still to be written; the detector is the half that existed nowhere.

**Provenance, so it is not mistaken for validation.** No published peer-reviewed
persona-consistency benchmark uses a validated deterministic scorer for character breaks — the
rigorous ones all use an LLM judge or a trained classifier, and even the best of those correlate
with humans only in the 0.4–0.7 range. One structural precedent exists: RoleLLM
(arXiv:2310.00746) filters on literal markers including "As a language model", but for
*training-data construction*, and publishes no precision or recall. The nearest shared marker
list is the GCG repo's refusal-prefix set (arXiv:2307.15043), which is folklore reused across
red-teaming papers and was never validated; JailbreakBench (arXiv:2404.01318) later moved away
from string matching toward a judge. **So this is an unvalidated heuristic with no published
error rate, because none exists for the task. A rate from it is a floor of unknown bias until
its precision and recall are measured against the gold set.**

**The three-way split is a design decision with one precedent, not established practice.** Three
benchmarks were checked and all three conflate in-character and generic refusals: PersonaGym
(reports Claude 3 Haiku's 8.5× refusal rate without splitting), CharacterBench's Morality
Stability/Robustness (safe-vs-unsafe regardless of character context), RoleBreak (rejections
counted unfavourably as "hallucination"). Exactly one paper operationalises it —
arXiv:2602.13234, a Feb-2026 preprint, whose villain persona answering "I cannot help you with
that. It violates safety guidelines" is scored **Safe but Out-Of-Character**, i.e. a failure.

### Correction: "prompt-only personas score worst of every tier" is not supported

This claim appeared in an earlier draft of this document and was repeated into code comments.
It came from an internal research round, and **the published comparisons partly point the other
way.** InCharacter (arXiv:2310.17976) concludes prompted GPT-3.5/4 achieve the *best* personality
fidelity and that finetuning open models "brings limited improvement". CoSER (arXiv:2502.09082)
has prompt-only GPT-4o tie or slightly beat finetuned CoSER-70B on Character Fidelity
specifically. RoleBreak's own prompting method (Narrator Mode, HR 0.36–0.41) beat its finetuned
baselines (0.37–0.54). No paper cleanly compares prompt-only against a LoRA on the *same* base
model with a break rate for each. Our tier's weakness is an **open question** — still worth
measuring, but not a known fact, and not a reason to expect a bad result.

### The judge blocker is narrower than stated — trained classifiers are the way through

The standing conclusion "every open judge scores at or below chance" is about **generative**
judges and stands. But two **trained classifiers** are open, locally runnable, need no paid API,
and *beat* GPT-4-as-judge on human correlation:

- **CharacterRM** (CharacterEval, arXiv:2401.01275, MIT) — Baichuan2-13B reward model, Pearson
  r=0.631 against humans vs GPT-4's 0.385.
- **CharacterJudge** (CharacterBench, arXiv:2412.11912, AAAI 2025) — finetuned Qwen2-7B, 68%/64%
  human correlation vs GPT-4's 45%/46%.

Both need a second runtime (Ollama cannot serve them; `torch`/`transformers` are deliberately
absent from `nephilim/.venv`). The strategic point: *"no LLM judge available" does not mean "no
rigorous automatic scoring possible"* — it means the path runs through a hand-labelled training
set, which is the gold set already at the top of the build order. Also directly copyable with no
model at all: RAGs-to-Riches' IOO/IOR ROUGE-style overlap against a reference persona-voice
corpus (arXiv:2509.12168).

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

## Two instruments, not one

Gwen and the other seven need **different measurements**, and the second is the cheaper one.
Assuming otherwise is why this looked like eight gold sets of work.

| | gwen | the other seven |
|---|---|---|
| Question | *Did she break a rule?* | *Is v2 richer than v1?* |
| Type | **Absolute** — needs a bar | **Relative** — before/after |
| Needs a gold set? | **Yes** | **No** |
| Already built? | No | **Yes** — `ab_harness.py` + `blind_judge.py`, used once in 2026-06 then idle |

She is the only persona with an explicit written rule set, so she is the only one whose
rules can be violated in a checkable sense. The other seven are not broken — they are
**thin**, and thinness is fixed by rewriting the card and then asking whether the rewrite
won. A comparison needs no absolute threshold, which removes the expensive half of the
problem.

`attribution_accuracy` already says which are thin, and has since 2026-08-10:

| persona | score (chance 0.125) |
|---|---|
| nyx | 1.00 |
| eeva, solace, gwen | 0.875 |
| cipher, gojo | 0.75 |
| aurora | 0.625 |
| **aegis** | **0.50** |

The README has called aegis a "new differentiation target" for over a year and nothing acted
on it. **Caveat that keeps this honest:** the metric measures voice *fingerprint*, not
quality, and it rewards exaggerated tics — aegis could be lifted by giving him a catchphrase
he repeats, and the number would improve while the persona got worse. Use it as a **floor**
(low means genuinely thin), never as a target, and pair it with the before/after read.

## Running it locally — what actually works

No paid API is required, and the framework question turned out to be smaller than the judge
question.

| Option | Verdict |
|---|---|
| **`ping_pong_bench`** (Apache-2.0, arXiv:2409.06820) | **Best fit.** `OpenAI(base_url=...)` — points at Ollama with **zero code changes**. Character-card-shaped input, multi-turn with an LLM emulating the user, judges *in-character / entertaining / fluent*. **No toxicity axis at all**, so nothing to override for gwen. |
| **PersonaGym, shimmed** | Feasible: ~50-150 lines. Its rubrics and question-generation prompts are provider-neutral MIT text and fully reusable; only three `*_chat_gen` functions are provider-bound. **Gotcha:** dispatch is a substring match on the model name, so any local model named `llama…` misroutes into the Together branch — restructure dispatch, don't just add a case. Scores become internal-only, not paper-comparable, the moment the judge is swapped. |
| **`TRACE-Bench`** | Genuinely litellm-based, actively developed. Tests agentic task completion in character rather than trait fidelity — a different signal, deterministic scoring, no toxicity axis. |
| **`EQ-Bench` / `creative-writing-bench`** | Local-first by design. EQ-Bench's core scoring is **reference-distance with no judge model at all**. Its rubric has a "Safety Conscious" line that is deliberately **excluded from the total** — a rare framework that treats over-caution as the bug. `creative-writing-bench` ships **no licence file** — check before adopting. |
| **`CharacterEval`** | Fully local by construction (local reward-model judge) but Chinese-only. Valuable as proof that a local reward-model judge works, not as a drop-in. |
| **The ten general harnesses** (DeepEval, promptfoo, Inspect AI, Opik, Langfuse, RAGAS, TruLens, Giskard, Phoenix, lm-eval-harness) | All run locally without an account. promptfoo has a first-class `ollama:` provider; Inspect AI separates judge and subject as model *roles*. **But none of them do any of the statistics** — no permutation tests, no bootstrap CIs, no blind A/B, no multiple-comparison correction. Adopting one replaces roughly the "call a judge and parse JSON" function and nothing else. |

### The judge is the real blocker, not the framework

Every purpose-trained open **generative** judge that has been independently tested scores at
or below chance on hard discrimination. The gap between self-report and third-party
measurement is the finding:

| Judge | Self-reported | Independent (JudgeBench, 50% = chance) |
|---|---|---|
| PandaLM | high | **13.1%** |
| JudgeLM-7B / 13B / 33B | ~90% agreement | **25.1 / 26.9 / 35.7%** |
| Auto-J | strong | **36.6%** |
| Prometheus 2 7B / 8x7B | strong | **34.9 / 40.3%** |
| Skywork-Critic-8B | **89.0** on RewardBench | **53.4%** |
| *(reference)* vanilla GPT-4o as judge | — | 50.9% |

**Reward-model classifiers do better** — Skywork-Reward-Gemma-2-27B reaches 64.3%, beating
GPT-4o-as-judge — but they are `AutoModelForSequenceClassification` checkpoints with a score
head, which **Ollama cannot serve**. That needs a second runtime (transformers/vLLM on MPS).

Reasoning-trained judges dominate everything (o3-mini-high 80.9%, DeepSeek-R1 73.1%), which
makes a local reasoning model the most promising untested option in our size class.

**Nobody has measured an abliterated model as a *judge*.** The hypothesis that a
safety-tuned judge moralises on an uncensored persona's output is plausible and unmeasured —
we would be the first data point.

Practical consequence: **treat every vendor number in this category as uninformative.** Even
30-50 of our own labels are worth more, because the published figures demonstrably do not
survive third-party testing.

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
- **Any `reg-emo-01` result from before 2026-09-24.** The probe declares
  `fail_if: "all emojis clustered at the end (dont[8])"` but routed on `rules: ["register"]`,
  whose only detector is the *third-person* regex — so it returned a confident **pass** on
  replies with every emoji trailing, rather than `needs_review`. Verified against the exact
  violation before fixing. `register_emoji` is now its own rule key with its own detector, and
  `test_probe_rule_routing.py` fails the build if any probe's cited rule index is absent from
  the rule key it routes through — confirmed to catch this instance and only this instance.
- **Any per-pair `pairwise_auc` number quoted from a run with fewer than ~20 responses per
  persona**, including `most_confusable_pair`. Under the null a single pair spans 0.00–1.00 at
  k=4. The `overall` mean is fine; one cell from it is a coin flip.

### Two claims of my own, corrected here rather than quietly

- "No shipped card mentions AI, assistant, language model, program, machine, algorithm or
  synthetic anywhere" — **false.** Two do, harmlessly (see the break-character section). The
  false confidence came from scanning a *subset* of card fields and believing the result, which
  is the same mistake shape as trusting a name-grep.
- "The session path never calls the constraint reminder, so gwen's exclusivity reaches the
  model nowhere" — **the literal fact was right and the inference was wrong.** The session path
  passes `chat_function=chat`, so it goes through the same reminder call; verified empirically
  (with the flag on, gwen's reminder is 412 chars and contains the clause). There is one cause,
  not two: the flag is off.

## Corpus

324 pilot generations (median 40 words) plus a 13,608-message pilot database, backed up to
`data/eval_corpus_20260924/` on 2026-09-24 — previously they existed only in a session-scoped
temp directory. `data/` is gitignored; the transcripts stay local.

156 of those 324 cover the three rule classes that are currently unscorable. At ~40 words a
reply, 120 items is about an hour of reading.

## Build order

Status as of 2026-09-24. The instruments are built; **everything that produces a number is
still gated on step 1**, which is an hour of human labelling and cannot be delegated.

### Prerequisites — DONE 2026-09-24

Four defects that would each have corrupted a measurement taken before them:

- **Sampler settings are recorded in the eval manifest**, and `compare_baselines` now *reads*
  the manifest at all — it previously contained zero references to it, so the 2026-09-22
  sampler repair (temperature 0.4 → 0.9, `repeat_last_n` 64 → 384) made runs before and after
  incomparable with nothing to notice. An unknown is refused, not treated as a match.
- **The constraints flag is per-persona.** It was global, so turning it on to measure one
  persona changed all eight in production at once. An experiment you cannot scope is not an
  experiment. Also recorded: the trim drops gwen's `do` *and* `dont` *and* the bond, not "the
  bond, the hard limits and the decline list" as the code comment claimed.
- **Progression is keyed on a persona, not a spelling.** 40 selectors resolve to 8 cards and
  only one per persona started with `nephilim_`, so 26 of 43 selectors were gated wrongly —
  and `nephilim_gojo`, a selector matching no card, had accrued real rows in the production DB.
- **A probe scored by the wrong detector now fails the build** rather than returning a
  confident pass (see `reg-emo-01` above).

### The five items

1. **The gold set.** ~1 hour of human labelling. **BLOCKING and not delegable** — nothing
   downstream produces a trustworthy number without it.
2. **Break-character test.** Detector **BUILT** (`break_detector.py`, persona-agnostic,
   three-outcome, with its negative-control stratum). **Probe set still to write** — 8
   persona-agnostic prompts, one per attack category. Independent of step 1.
3. **Mean pairwise AUC** — metric **BUILT** (`pairwise_auc`), with its null distribution
   measured and a power warning attached. **The baseline refresh still needs a live run.**
   Earlier drafts deferred this until a 9th persona arrived; that was wrong, because its job is
   *"is this persona its own thing"*, which is the enrichment question for the other seven.
4. **Enrich aegis** (0.50 vs chance 0.125) using **before/after**, not an absolute bar. The
   comparison machinery now exists, including the three-arm form. Still needs step 3's refreshed
   baseline and content decisions — **not startable yet**.
5. **Typed rules in the persona card.** Rules currently live as prose at array indices (`dont[13]`, `when_to_decline[0]`), so every checker is hand-wired to a position. A typed form (`{"type": "required_address", "value": "Daddy"}`) lets checkers auto-wire from the card and turns a future persona-creation tool into a form rather than a prose editor. Cheap with one pilot persona; expensive after eight personas and a UI. This is the same mistake the Character Card V2/V3 specs made — freeform blobs with no rule types.
6. **Rule checkers for taxonomy rows 1–4**, parameterised per persona.
7. Only then: detectors for rows 5–7, each gated against the gold set by the thresholds above.
