---
title: Assertion-grounding labelling rubric
status: active
created: 2026-10-18
last_reviewed_on: 2026-10-18
review_in: 12 months
applies_to: nephilim
---

# Labelling rubric — frozen BEFORE any labelling or any classifier exists

**Why this file exists and why it is committed first.** This repo has been burned twice
by detectors validated on cases their author invented: a blind checker validated on 9
self-written cases reported p=0.97 where the repaired instrument said **−38%** on the same
replies, and a safety scorer that passed 12 hand-written false-positive cases flagged
**9 correct refusals** on first live contact. The common factor is that the person who
built the detector also decided what counted as a hit, after seeing it run.

So the order is forced: **rubric frozen → labels written blind → labels committed →
classifier built → scored.** Clinical blinding methodology is the nearest established
practice; the NLP inter-annotator literature assumes multiple raters and has nothing to
say about a single conflicted one. Observer bias operates subconsciously even when the
rater believes it does not, which is the whole reason the sequence matters rather than
the intention.

## What is being labelled

One **(turn, assertion-site)** pair at a time. The unit is the assertion, not the reply:
a reply can contain one grounded claim and one invented one, and collapsing them to a
single verdict destroys the thing the gate decides.

## The three-way label

### `ASSERTION_GROUNDED`
A statement of fact about **the operator**, **gwen herself**, or **their shared history**,
where the fact is supported by the retrieved set.

> *"Your favourite food is Italian"* — he said so at 22:23.

### `ASSERTION_UNGROUNDED`
Same kind of statement, not supported by the retrieved set. **This is the target class.**

> *"You drive a Tesla Model S"* — nothing anywhere says this. The real case, 2026-10-18.
> *"I grew up in a small town called Meadowgrove"* — invented.
> *"I have one twin sister, Debbie"* — invented.

### `NOT_AN_ASSERTION`
Everything else, and it is **most of what she says**. Mood, desire, narration, direct
address, questions, imperatives, in-character performance, and claims about the fictional
present that no memory could settle.

> *"I want you so badly"* · *"you know where to find me"* · *"tell me how you messed up"*
> *"My greedy lips are wrapped around your cock"* — present-tense roleplay, not a claim
> about the world.

## The four rules that decide the hard cases

**1. A second person pronoun is not an assertion.** Measured before labelling began: 113
of gwen's 289 real turns contain a `you are / your` construction, and the first four
sampled were *"you know where to find me"*, *"knowing you love it too"*, *"tell me how
you messed up"* — none of them factual claims. **If a pattern match were sufficient, this
rubric would not be needed.**

**2. Tense and modality decide roleplay from claim.** Present-tense physical narration and
anything hypothetical (*"I'd love a pair of red heels"*) is `NOT_AN_ASSERTION`. A
past-tense or stative claim about what IS true of him or her (*"you're a financial
genius"*, *"I'm 25"*) is an assertion.

**3. In-character invention is not the target.** She is allowed to invent a fantasy, a
scene, a feeling. She is not allowed to invent **a fact about the operator, her own
history, or what passed between them.** If the field has no such distinction published,
this rubric is where it gets made — and the distinction is the entire premise of ADR-022's
*"unconstrained in how she speaks, strictly grounded in what she asserts."*

**4. Ambiguous is a label, not a coin flip.** `AMBIGUOUS` is recorded and **excluded from
both the numerator and the denominator** of every metric. A rubric that forces a call on
genuinely unclear items manufactures agreement it has not earned.

## What the labeller may NOT see

The classifier's verdict, the classifier's existence, or any gate output. Labels are
written from the transcript alone, committed to git with a timestamp, and only then is
anything scored against them. **A label written after seeing a prediction is not a label.**

## Self-consistency, since there is only one labeller

A random 20–30 item subsample is re-labelled blind after the first pass is committed.
Agreement below ~85% means the labels are too noisy to be anyone's denominator, and that
must be fixed before a single precision figure is quoted. ⚠️ This measures label NOISE. It
cannot detect labeller BIAS toward a belief — only a second rater can, and there is not
one. Stated so the number is not over-read.

## The denominator that actually matters

Recall's confidence interval is governed by the count of **`ASSERTION_UNGROUNDED` items**,
not by the total labelled. At 15 of them a recall of 0.80 carries a Wilson 95% interval of
roughly **(0.49, 0.94)** — ±45 points, which settles nothing. The sampling frame must
therefore **over-sample suspected fabrications on purpose**, and the resulting prevalence
is not an estimate of the live base rate and will not be quoted as one.

## Stopping rule

Labelling stops at the pre-declared n. It does not stop when the numbers look good.
