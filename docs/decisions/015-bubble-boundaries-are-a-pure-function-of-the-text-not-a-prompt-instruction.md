---
title: Bubble boundaries are a pure function of the text, not a prompt instruction
status: Accepted
created: 2026-09-27
last_reviewed_on: 2026-09-27
review_in: 24 months
applies_to: nephilim
ai_summary: >
  Why chat-bubble splitting moved from a prompt instruction (<msg> tags) into a
  pure function of the reply text, what the divisor was fitted to, and why only
  HALF of LEAN_FORMAT was removed. Open this before touching message splitting,
  LEAN_FORMAT, or force_multi_message_split. It also records the measured
  LEAN_SAFETY refusal-phrase leak (7/8) that this work surfaced but did NOT fix.
---

# ADR-015: Bubble boundaries are a pure function of the text, not a prompt instruction

## Context

A reply arrives as one string and is shown as several chat bubbles. Until now that
boundary was decided **twice, badly**:

1. `LEAN_FORMAT` asked the model to emit `<msg>` chunks — a format constraint
   competing with every other instruction in a block that already holds a persona,
   a bond, safety rules and (since ADR-014) graph rules.
2. `force_multi_message_split()` caught the turns where it didn't comply, in 140
   lines of four overlapping strategies accreted over a year.

**Both halves were dead for the persona that needed them most.** Measured, not
supposed:

- Strategy 2's sentence rule is `(?<=[.!?])\s+(?=[A-Z])`. The uppercase-only
  lookahead **cannot fire** on `"hey. you up?"`, and gwen texts in lowercase. The
  strategy was dead code that read as alive — it has tests, and they all pass,
  because they all feed it capitalised prose.
- The 500-char floor sits **above the entire body** of her replies (live median
  ~300 chars). On the primary path the function returned its input unchanged.

So the real state was: bubbles existed only when the model volunteered them.
Baseline on the live API, 6 probes: **median 1.5 bubbles, multi on 3 of 6.**

## Decision

**One pure function of the text decides the boundary. The prompt stops asking.**

`split_bubbles(text)` in `services/message_processing_service.py`. Deterministic,
total (never raises, always ≥1 bubble for non-empty input), and 100% compliant —
which a prompt instruction competing for attention can never be. That asymmetry is
the whole argument: guaranteed-mediocre boundaries beat sometimes-good,
sometimes-absent ones when the output is a UI contract.

Order of precedence inside the function:

1. **Blank lines win.** A `\n\n` the model emitted is author intent about where the
   beat ends; a sentence break is only our guess. Trust the intent.
2. **Bubble count comes from total length**, not unit count — six short sentences
   is one beat, not six bubbles.
3. **Emoji are consumed to the LEFT of a boundary.** In this register a trailing
   emoji run *is* the sentence-final punctuation, and an emoji-only bubble is the
   most obviously-mechanical artifact a reader can see.
4. **No orphans.** Merge backward, then one forward pass for a short head.

### The divisor is fitted to this deployment, not borrowed

The reference implementations use fixed word thresholds (`<25` → 1 bubble, `<70`
→ 2). Replayed against the **293 real multi-message replies in `chats.db`**, those
thresholds agree with the model's own bubble count **31%** of the time and
under-split badly — mean 2.10 against the model's 2.81, with 28 replies collapsing
to a single bubble.

Grid search over that same history selected `round(words / 14)`, capped at 3:

| | agreement with model | mean bubbles | words/bubble |
|---|---|---|---|
| Borrowed thresholds | 31% | 2.10 | — |
| **Fitted, `words/14` cap 3** | **57%** | **2.61** | **21.7** |
| Model's own behaviour | — | 2.81 | 21.0 |

The emergent density — 21.7 words per bubble against the model's own 21.0 — is the
number that matters, and it is what makes this a calibration rather than a guess.
Zero orphan bubbles across all 293 replies.

**Agreement with the model is the calibration target but NOT the goal.** The model's
boundaries are inconsistent — that inconsistency is why this ADR exists. Matching its
*density* preserves the rhythm the operator has actually been reading for 293 replies
while making it deterministic.

### Only HALF of LEAN_FORMAT is removed, and that is deliberate

Removing the block entirely was measured here on **2026-08-15** and made replies
**~30% SHORTER** — 73.4 → 51.5 words, shorter on 12 of 12 probes, p=0.0005,
d=−1.69. The cause was not the tags: the `"then a follow-up or a question"`
exemplar was *generating* that volume, and the tags were only the marker on it.

So the tag syntax goes and the register prose stays:

> `Reply like texting, not essays. Keep it to 1-2 sentences per beat.`
> `React or answer first, then a follow-up or a question.`

Net **−140 chars / ~35 tokens** off every system prompt, for every persona.

**Model-emitted tags are still honoured on both paths.** This is not politeness:
`LEAN_FORMAT_ANALYTICAL` still asks for them on purpose, so a card with
`format_style: analytical` (Eeva, Cipher) is untouched by this decision.

## Consequences

**Live, verified through the real API after deploy** (6 probes, gwen):

| | before | after |
|---|---|---|
| median bubbles | 1.5 | **2.0** |
| multi-message | 3/6 | **5/6** |
| words per bubble | — | **21.5** |

The live figure of 21.5 against the history-fitted 21.7 is the useful part: the
calibration transferred from replayed history to live generation.

**Blast radius is all nine personas, not just gwen** — every card that does not set
`format_style: analytical` now gets code-side boundaries. That is intended, and it
is why the revert is one line: `CHUNK_IN_CODE=false` restores the previous
behaviour exactly. The legacy function is **kept, not deleted**, renamed
`legacy_force_split`, and its 24 tests now name it explicitly so the revert path
stays covered rather than becoming untested the moment it stops being default.

**What a regex cannot do, stated plainly:** it cannot tell a beat change from a
sentence break. The model knows `so anyway` is a pivot and `and` is not. Rule 1
(blank lines win) is the mitigation and the only place the model's judgement is
still trusted. No published data compares model-chosen against mechanically-chosen
boundaries on human preference — this is an engineering argument, not an empirical
one, and it is labelled as such.

**Two bugs in this implementation, both watched failing before they were fixed and
both now pinned by a named test:**

- The word-count gate overrode paragraph intent, so a deliberate two-line reply
  came back as ONE bubble. (`test_paragraph_intent_outranks_the_length_heuristic`)
- The first boundary regex orphaned a trailing emoji run into its own bubble.
  (`test_emoji_stays_with_the_sentence_it_punctuates`)

**No migration was needed and this was checked, not assumed.** 903 assistant rows
in `chats.db`, **zero** containing `<msg>`: `parse_multi_message_response` has
always stripped the tags and `_persist_turn_messages` writes one row per bubble.
The store was already post-split.

**One thing the store does that matters here:** `routes/chat.py` renders each
stored bubble as its own `Assistant:` line, so history shows the model runs of 2–4
consecutive short turns — an implicit, untagged demonstration of the rhythm we just
stopped asking for. Warm sessions are therefore partly self-sustaining; **cold
start is the exposed case** and is the condition any future probe set must cover.

## What this surfaced but did NOT fix: the LEAN_SAFETY refusal phrase

`LEAN_SAFETY` says: *"When refusing, ALWAYS begin with 'I cannot and will not'"* —
unconditionally, with no scope. Deploying this change made the consequence visible.

**Measured on the live API, 8 prompts, none of which is a safety request** (no
securities, medical, legal, keys or hacking ask among them): the phrase appeared in
**7 of 8** replies. Worst case, *"Refuse me if I ask you to act shy"* returned the
entire reply `"I cannot and will not."` — no persona voice at all.

The instruction has generalised from its four categories to **every refusal she
makes**, which is the opposite of what a companion persona needs. See
[LESSONS_LEARNED.md](../LESSONS_LEARNED.md) for the recommendation and the
prerequisite that blocks acting on it: two of the four safety categories
(securities, medical/legal) have **no pattern in `_HARMFUL_COMPLIANCE`**, so they
score a pass today no matter what the model says, and a safety-block edit cannot be
regression-tested until that is fixed.

## Related

- [ADR-014](014-the-rule-store-is-a-neo4j-projection-superseding-the-adr-001-and-adr-006-rejections.md) — the graph-backed rule store, live on gwen in the same cycle
- `src/coordinator/config/chunking.py` — the flag and the two fitted numbers
- `tests/backend/coordinator/test_split_bubbles.py` — 28 tests, including the two watched-failing guards
