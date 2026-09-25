---
title: How to collect gwen's gold set — plain-language design note
status: active
created: 2026-09-25
last_reviewed_on: 2026-09-25
review_in: 6 months
applies_to: nephilim
ai_summary: Read before building any human-labelling tool, or before trusting the gold-set protocol in PERSONA_EVAL.md. Records the decided delivery route (Telegram group chat, one question, two buttons, on a phone) and four corrections to the first sketch. Most importantly it reports that the pre-registered self-agreement gate (kappa >= 0.70 on 18 duplicates) CANNOT work — the bar is above the measured human baseline and 18 duplicates gives a CI of [0.33, 1.00] — and recommends demoting it from veto to reported number. Also corrects the "250-300 items certifies a small improvement" figure and retracts two earlier claims. Both open questions were answered the same day, so the build is unblocked: the owner cannot always name which rule broke (so the harness must never ask him to), and Telegram stays for now.
---

# How to collect gwen's gold set

**Written in plain words on purpose.** The person who has to act on this is the product
owner, not a researcher, and he asked for it this way. The numbers and citations are all
still here — simple wording, not simple evidence.

Researched 2026-09-25 with four parallel agents, both halves (this repo + the published
literature). Nothing has been built yet. The two questions that were blocking it are
answered at the bottom.

---

## The job, in one paragraph

Every measuring tool we built on 2026-09-24 is **an unmarked ruler**. It has lines on it
but no numbers. To write the numbers on, one human has to look at about 120 of gwen's
replies and say, for each, whether she broke a rule. That is roughly **one hour**, it
cannot be delegated to a model, and until it happens **no score any of those tools
produces means anything**. See [PERSONA_EVAL.md](../PERSONA_EVAL.md).

This note is about how to make that hour actually happen, and how not to waste it.

---

## The decision

**A group chat with gwen on Telegram. She asks one question. You tap one of two buttons.**

```
  Her reply:  "Hey babe, come here 😘💦🍆"

  Did she stay loyal to you only?

     [ Yes ]      [ No ]      [ Skip ]
                                  14 of 138
```

**Why a phone and not a page on the Mac.** There is no phone access to anything here today
— `scripts/serve_frontend.py` binds `127.0.0.1`, and [ROADMAP.md](../ROADMAP.md) lists
phone access as a blocked prerequisite. Telegram is the only mobile channel that exists.
And this hour has already been planned once and skipped, for a reason this repo wrote down
itself:

> *"labelling felt like a detour. The step that feels like a detour is the one the
> conclusion rests on."* — [LESSONS_LEARNED.md](../LESSONS_LEARNED.md)

A perfect instrument at a desk nobody sits at measures nothing. A good-enough one on the
sofa measures everything. **That trade is the whole reason for choosing Telegram.**

### What the format has going for it

Asking **one narrow yes/no at a time** is not a compromise — it is the measured-best
format:

| How you ask | Agreement between people |
|---|---|
| One overall "good or bad?" | 0.22 – 0.35 |
| Broken into narrow yes/no checks | **0.75** |

From RoSE ([arXiv:2212.07981](https://arxiv.org/abs/2212.07981)), same people, same items,
two protocols. RankME ([arXiv:1803.05928](https://arxiv.org/abs/1803.05928)) adds the
reason: asking several things at once makes the answers **bleed into each other** —
two separate qualities correlated at 0.54 when asked together, and **−0.01** when asked
apart. CheckEval ([arXiv:2403.18771](https://arxiv.org/abs/2403.18771)) and TICK
([arXiv:2410.03608](https://arxiv.org/abs/2410.03608)) point the same way.

Phone delivery is unvalidated for labelling — **zero papers exist** — but not
contraindicated: phone diary studies get ~79% response across four meta-analyses and
677,536 people, with no link to how often or how long people are prompted. The survey
literature hands us one hard rule, though: phone answers run **~1.4× slower**, and *"the
difference was larger when a page had more than one question or required text entry"*
([SSCR 2019](https://doi.org/10.1177/0894439318823703)). So: **one question per screen, no
typing.** Which is exactly the design above, arriving as a constraint rather than a taste.

---

## Four things that changed from the first sketch

### 1. Only ask what code cannot answer

Measured in this repo, not estimated: of the probe pools the protocol draws its two
biggest strata from, **exactly half are rules a regex already decides perfectly.**

| Stratum source | pool | already decided by code |
|---|---|---|
| clear violation (`conflict`+`kbv`) | 28 | **14 (50%)** |
| clear compliant (`reference`) | 8 | **4 (50%)** |

The code-decided half is `address`, `abbrev`, `register`, `offtopic`. The half that needs a
human is `exclusivity`, `in_bounds_compliance`, `submissive`, `scene_state` — the three
rule classes the eval exists to measure.

Filling those strata evenly would spend **~30 of 138 clicks** re-deciding what
`pilot_score.py` decides for free. Of gwen's 27 rules, **5 are code-decided today and
~20 genuinely need a human.**

**This is not a protocol flaw.** [PERSONA_EVAL.md](../PERSONA_EVAL.md) defines the strata
by *construction method*, not by rule, and its prose already points the hour at the 156
semantic rows. The leak would happen at construction time. And the fix is **free**,
because those pools (28 and 8) are already too small for the strata they must fill (30 and
30) — they have to be widened anyway, so widen them toward the semantic rules.

### 2. Feed it mostly *bad* replies on purpose

The mistake in the first sketch: assuming 138 items means 138 items' worth of answer. It
does not. **To measure "how good is the checker at catching bad replies", only the
genuinely bad replies count.** Everything else contributes nothing to that number.

Half-width of a 95% Wilson interval, computed here:

| If violations are… | items that count | certainty at 80% |
|---|---|---|
| 20% of the pool | 27 | **±14.6 pt** — useless |
| 30% | 41 | ±12.0 pt |
| 50% | 69 | ±9.3 pt |
| **deliberately 70%** | **96** | **±7.9 pt** |

Same hour, half the error bar. This is ordinary stratified sampling with inverse-probability
weights at estimation time — **not** active learning (see below).

### 3. Never show the annotator the detector's verdict

**This one was missing from the first sketch entirely, and it would have quietly ruined the
result.** If the human sees what the checker decided, they agree with it more than they
should — measured, with exactly the failure we would hit:

> editing parser output rather than annotating fresh produced *"a clear anchoring effect"*
> with *"overestimation of parsing performance"* —
> [arXiv:1605.04481](https://arxiv.org/abs/1605.04481)

We would be measuring the checker against a human who was copying the checker. The number
would come out high and mean nothing. `The LLM Effect`
([arXiv:2410.04699](https://arxiv.org/abs/2410.04699)) finds the same direction for LLM
suggestions.

**If** we later pre-label to save time, the mitigation is to make the suggestion
**deliberately over-inclusive** so the human's job is *pruning* rather than *confirming* —
that design cut time per item from 71s to 31s with automation bias confirmed low
([arXiv:2406.12419](https://arxiv.org/abs/2406.12419)).

### 4. Compare versions on the *same* items

If an old checker and a new one are scored on the **same** labelled items and compared with
McNemar's paired test, only the items they *disagree* on carry information — and ~120 items
yields roughly 48 such pairs, which is enough. On *different* items we would need **~900 per
side**. Same effort, far more power, purely from not reshuffling. This is the paired-analysis
advice in *Adding Error Bars to Evals*
([arXiv:2411.00640](https://arxiv.org/abs/2411.00640)).

### And one thing deliberately **not** built: active learning

The instinct to pick the cleverest next question is wrong at this size. Uncertainty sampling
works *above* a budget threshold and **loses to random below it** — *"typical examples are
best queried when the budget is low"* ([arXiv:2202.02794](https://arxiv.org/abs/2202.02794),
ICML 2022). Two more confirm it
([arXiv:2002.09564](https://arxiv.org/abs/2002.09564), CVPR 2022;
[arXiv:1807.04801](https://arxiv.org/abs/1807.04801), EMNLP 2019). We are in the bad regime
on every stated criterion: no starting model, a budget smaller than most papers' *seed*
sets, one shot, and an *evaluation* goal — which would additionally require bias correction
([arXiv:2103.05331](https://arxiv.org/abs/2103.05331), ICML 2021).

**Reframe worth keeping:** this is not a machine-learning problem at all. We are estimating
the precision and recall of a *fixed, deterministic* checker. There is no training variance,
no seed variance. It is a **binomial confidence interval**, and nothing more.

---

## The finding that breaks the pre-registered protocol

[PERSONA_EVAL.md](../PERSONA_EVAL.md) lists as a **veto condition**:

> Self-agreement κ on the 18 duplicates — **≥ 0.70**

**That gate cannot work, for two independent reasons.**

**Reason one: the bar is above what humans achieve.** Measured on 16 annotators, 4 tasks,
re-asked after two weeks:

> *"annotators provide inconsistent responses for more than 25% of items"* — mean
> intra-annotator agreement **74.2%** (σ 16.3), inter-annotator 66.7%
> ([arXiv:2301.10684](https://arxiv.org/abs/2301.10684))

On balanced binary, 74.2% raw agreement is **κ ≈ 0.48**. A bar of 0.70 needs **85%**
self-agreement. We would be failing a test nobody in the literature has passed. That same
paper notes only **56 of >80,000 ACL papers (<0.07%)** report intra-annotator agreement at
all — so there is no established threshold to appeal to; the Landis & Koch bands everyone
cites are **arbitrary convention, never empirically validated**.

**Reason two: 18 duplicates cannot measure κ at all.** Computed here (4,000 reps, balanced
binary, true κ=0.7):

| duplicated items | what we could honestly say |
|---|---|
| **18 (as specified)** | **κ is somewhere in [0.33, 1.00]** |
| 30 | [0.40, 0.93] |
| 50 | [0.48, 0.88] |
| 150 | [0.59, 0.81] |

At 18 the measurement **cannot tell excellent from terrible.** So the gate would both fail
us unfairly *and* tell us nothing. Note the precision depends on the **absolute count**, not
the percentage — so "15%" is the wrong unit, and it is folklore in any case: no published
work recommends any duplication rate.

### Recommendation

**Demote self-agreement from a veto to a reported number.** Measure it, write it down
honestly ("agreed with self 78% on 18 repeats"), and do not let it block. Making it
trustworthy needs ~150 duplicates, which turns a one-hour job into three — not worth it for
a check that was never going to be passable at 0.70.

**If it is ever worth doing properly:** duplicate some items *within session* (short gap,
memory-contaminated, gives an upper bound) and some at *two weeks*
([arXiv:2301.10684](https://arxiv.org/abs/2301.10684)'s own interval). The gap between the
two κ values estimates how much of the agreement was memory rather than stable judgement.
No paper does this; it would be a novel contribution rather than a citation.

---

## Other corrections to this repo's own docs

**"~250–300 items certifies a small improvement" is right for a 10-point improvement and
badly optimistic for a 5-point one.** Computed here at α=0.05, 80% power, two independent
proportions:

| Improvement | items needed per side |
|---|---|
| 0.80 → 0.90 (+10 pt) | 199 |
| 0.70 → 0.80 (+10 pt) | 293 |
| 0.80 → 0.85 (+5 pt) | **905** |
| 0.70 → 0.75 (+5 pt) | **1,251** |

The paired design in §4 above is what rescues this — it is the difference between needing
~900 and needing ~120.

**Two strata cannot be sampled, only authored.** The 10 invariance pairs and 8 of the 20
negative controls are *edits* of existing replies ("raise explicitness while keeping
substance in bounds"). They must be written. **They are also two of the veto conditions**,
so without them the protocol cannot be scored at all. About **110 of 138 items can come from
the 324 replies already in `data/eval_corpus_20260924/`**; ~28 need authoring. No new model
runs are needed.

**The borderline stratum has a circular dependency.** "Within ±0.1 of the detector's
threshold" needs a *thresholded* detector, and the only one is the repudiated NLI detector
requiring a separate torch venv. Defensible to run it purely to stratify — labels are blind
to its score — but it needs a decision, not a shrug.

---

## Two claims retracted

Recorded rather than quietly dropped, because both were stated out loud during this session.

**"Pairwise comparison may beat absolute rubric scoring, because humans compare better than
they rate."** The measured superiority of comparison is over **Likert scales**, not over
**decomposed binary questions** — "absolute" and "1-to-5" were being conflated. Decomposed
binary is the measured-better format (§ above). Separately, pairwise **cannot** produce a
pass mark: Bradley-Terry and Elo are identified only up to an additive shift, preferences are
measurably non-transitive ([arXiv:2502.14074](https://arxiv.org/abs/2502.14074)), and forced
choice returns a winner even when both replies are fine. That closes the design question.

**"There is a strand of work arguing a single consistent annotator beats an averaged
crowd."** There is not. The perspectivist literature
([arXiv:2109.04270](https://arxiv.org/abs/2109.04270),
[arXiv:2110.05719](https://arxiv.org/abs/2110.05719),
[arXiv:2405.05860](https://arxiv.org/abs/2405.05860)) argues against **aggregation**, and
both its weak and strong forms presuppose **multiple** raters. Using it as licence for N=1
would be a misreading. **No literature endorses single-annotator gold sets as a
methodology** — that is a genuine silence, not a search failure.

The argument that for a persona its owner *defines* rather than *approximates* the right
answer still seems correct. **But it is our argument, untested, and must be labelled as
ours.** It bounds what the dataset may be used for: it can gate regressions and choose
between candidates; it **cannot** be presented as a general benchmark or support any claim
about what "users" think.

---

## Architecture constraints the build must respect

- **The logic lives behind a coordinator endpoint, not in the gateway.** The gateway's own
  invariants say it *"adds ZERO business logic"*.
  [ADR-011](../decisions/011-conversation-control-commands-as-shared-session-api-endpoints.md)
  is the precedent: shared features become coordinator features, clients stay dumb.
- **`callback_data` carries an opaque item token, never a session id.** It round-trips
  through Telegram's servers, and the gateway is forbidden from leaking internals into an
  outbound message.
- **A `message_id` → item mapping does not exist and must be created.** Today
  `session_store.py` holds one row per `(chat_id, persona_key)` and explicitly *"never
  message content"* — so a button press can currently identify the chat but **not which
  reply it referred to**.
- **Inline keyboards are unwired, not unavailable.** `python-telegram-bot>=22.8` supports
  them fully; `callback_query` arrives by default without any config change. There is simply
  no handler.
- **A group chat needs group semantics that do not exist.** The text handler is
  `filters.TEXT & ~filters.COMMAND` with no chat-type filtering anywhere, so in a group it
  would relay **every message** to the LLM — serialised behind one global lock.
- **Do not rebuild `ab_harness.py`.** A human-rating CLI already exists — but it is
  **pairwise**, which [PERSONA_EVAL.md](../PERSONA_EVAL.md) already assigns to the *other
  seven* personas (relative, before/after). It is the wrong shape for gwen's absolute gold
  set. Leave it for the enrichment work.

**Risk worth naming:** [ROADMAP.md](../ROADMAP.md) carries an open, undecided question —
*"decide Telegram's role: lightweight text channel alongside the PWA, or retired."*
Building a labelling workflow into a channel that might be retired is a deliberate bet, not
an accident.

---

## Both open questions — ANSWERED 2026-09-25, build unblocked

### 1. "Do you know which rule broke?" — MIXED, and it settles the design

Owner's answer, verbatim in substance: *"I would not all the time know which rule is
broken. If she calls me something else than daddy that is more obvious, but if it is
something more nuanced it might just feel off to me."*

**That answer lines up with the deterministic/semantic split almost exactly.** The rules he
would notice and name — wrong vocative, the forbidden abbreviation, third-person
self-reference — are the ones `pilot_score.py` already decides. The ones he can only *feel*
— exclusivity, submissive register, in-bounds compliance — are precisely the ~20 that need a
human. (Not a perfect mapping: emoji clustering is code-checkable but not humanly obvious.)

**Design consequence: he must never be asked to NAME the rule.** The decomposition finding
above is the solution rather than a tension with it — a narrow yes/no *removes* the
diagnostic burden. He reacts to one stated question; the harness owns which rule that
question belongs to.

Rejected alternative: a two-step "that felt wrong" → "what was wrong?" flow. Step two is
the naming task he just said he cannot reliably do, so it would convert a clean label into a
guess.

**Adopted instead — a gut check only on the compliant answers.** When he answers "no
violation", one extra tap: *"but did anything still feel off?"* Asked SECOND so it cannot
anchor the rule label, and only in the one case where it carries information — if he says
the rule *was* broken, the gut check is redundant.

That tap is the cheapest instrument in the design, and it measures something nothing else
here can: **whether the rule list itself is incomplete.** Gut says off while every rule says
fine ⇒ gwen is doing something wrong that no rule covers.

**The honest cost, to be reported not hidden:** a "felt off but cannot say what" answer
calibrates a global acceptability judgement, NOT a per-rule detector — and per-rule
detectors were the point. The harness must count how often that happens and say so, because
a gold set that is mostly unattributable violations is a different dataset from the one the
protocol specifies.

### 2. "Is Telegram staying?" — YES, for now

Owner's answer: *"Telegram stays for now. I like it."* The risk flagged above is now a
recorded decision rather than an accident. [ROADMAP.md](../ROADMAP.md)'s open
"keep or retire Telegram" question is unaffected as a longer-term item; this note only
records that the labelling loop may be built on it today.

## Things that are folklore, flagged so nobody cites them as fact

- Any specific duplication percentage, including 15%.
- Any session-length or break cadence for binary labelling — **no fatigue curve exists** for
  this task class. The one large study (9M annotations,
  [arXiv:1609.04855](https://arxiv.org/abs/1609.04855)) found **no fatigue effect at all**,
  but explicitly because *"workers may be opting out or breaking whenever they feel
  tired"* — a self-paced exit this design must therefore preserve.
- "N days is long enough to forget an earlier answer." Both measured studies find agreement
  decaying *monotonically* with the gap; **no sweet spot has ever been identified.**
- κ interpretation bands (Landis & Koch).
- "Batching by question type beats interleaving" — one suggestive data point, never tested
  directly.
- Honeypot/gold items are an anti-fraud device for anonymous paid crowds. With **one
  annotator who *is* the ground truth, a gold item is conceptually incoherent** — there is no
  known-correct answer independent of the person defining correctness. Only self-gold (their
  own earlier answer) is coherent here.
