---
title: Tool calling collapses under roleplay history — measured, and replaced
status: active
created: 2026-10-02
last_reviewed_on: 2026-10-04
review_in: 6 months
applies_to: nephilim
ai_summary: "Measured: native tool calling degrades to near zero in an ongoing roleplay conversation, the cause is the CONTENT of the history rather than its length, and a call that DOES fire under history can carry arguments copied verbatim from an earlier turn — a wrong picture rather than no picture. Closes with a paired A/B (16/16 vs 5/16, exact McNemar p=0.00098) that replaced the tool call with grammar-constrained extraction, now shipped. Read before changing the tool-brain path, before trusting any tool-firing number measured without history, before passing history to an extractor, and before making gwen the persona template."
---

# Tool calling collapses under roleplay history — measured

Opened because the image feature was unreliable in live use while measuring
6/6 in isolation. The cause turned out not to be the image tool.

## What was measured

`scripts/research/tool_calling_vs_history.py`. One tool offered per call,
`prose_expected=False`, 5 repetitions per cell, real persona cards and real
system prompts. Two history sources at each depth: the persona's **real**
conversation from `chats.db`, and **synthetic** filler of the same message
count ("What is 3 plus 3?" / "That is 6.").

| persona · tool | history | d0 | d2 | d4 | d8 | d16 |
|---|---|---|---|---|---|---|
| gwen · generate_image | real | 3/5 | 0/5 | 0/5 | 0/5 | 0/5 |
| gwen · generate_image | synthetic | 4/5 | 1/5 | 4/5 | 3/5 | 3/5 |
| gwen · image_search | real | 5/5 | 2/5 | 1/5 | 1/5 | 0/5 |
| gwen · image_search | synthetic | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| eeva · generate_image | real | 5/5 | 0/5 | 1/5 | 0/5 | 0/5 |
| eeva · generate_image | synthetic | 5/5 | 5/5 | 4/5 | 5/5 | 4/5 |
| eeva · image_search | real | 5/5 | 5/5 | 5/5 | 3/5 | 5/5 |
| eeva · image_search | synthetic | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |

## What it says

**It is the CONTENT of the conversation, not its length.** Synthetic history
of the same message count barely moves the rate — `image_search` is 5/5 flat
at every depth under filler, and collapses to 0/5 under gwen's real history
at the same depth. Length was the obvious hypothesis and it is wrong.

**The effect scales with how roleplay-saturated the recent turns are.**
gwen's history is explicit and in-character throughout and kills even
`image_search`, a tool with one string argument that has shipped for months.
eeva's history is philosophical and leaves `image_search` at 5/5. Same model,
same tool, same schema.

**It is not specific to `generate_image`.** That tool is worse — four
structured arguments against one string — but the direction is the same for
both. Any tool-firing number measured WITHOUT history, including the
76%→90% from the tool-firing eval, describes a condition that does not occur
in an ongoing conversation.

## Why it happens

Ollama's `/api/chat` with `tools=` does **not** use grammar-constrained
decoding. The model generates freely and Ollama then scans the raw stream for
a tag — `[TOOL_CALLS]` for the Mistral parser. If the model emits prose
first, the call is **silently discarded**: empty `tool_calls`, no error, the
prose becomes the reply. Confirmed from Ollama's source (`tools/tools.go`,
`model/parsers/ministral.go`), and corroborated by ollama/ollama#17274, where
a failed parse reports empty content and no tool calls with no indication
anything was attempted.

A roleplay history is a strong prior for "the next thing is more dialogue".
Nothing in the request can override it, because nothing constrains the first
token.

## Replicated against the PRODUCTION prompt, and one hypothesis refuted

⚠️ The first run measured the wrong prompt. The probe process had no
`NEO4J_BASE_URL`, and `standing_rules()` swallows an outage and returns `[]`
— a degraded read is indistinguishable from "no rules". Production carries a
**254-token rules block (6 hard walls)** plus a **334-char constraint
reminder prepended to the USER turn**. Re-run with the graph reachable:

| gwen · tool · history | bare d0→d8 | production d0→d8 |
|---|---|---|
| generate_image · real | 3/5 → 0/5 | 4/5 → 0/5 |
| generate_image · synthetic | 5/5 → 3/5 | 5/5 → 0/5 |
| image_search · real | 5/5 → 0/5 | 5/5 → 0/5 |
| image_search · synthetic | 5/5 → 5/5 | 5/5 → 5/5 |

**Significant at n=5, verified here rather than quoted.** Exact two-sided
Fisher, computed in this session: `image_search` at d8, real 0/5 versus
synthetic 5/5, **p = 0.008**. The headline does not need the powered run to
stand. The cell most likely to be over-read is `generate_image` synth d8,
bare 3/5 versus production 0/5 — **p = 0.167**, which is NOT evidence that
the rules block hurts it.

**The production shape changes nothing.** The collapse is identical with and
without the rules block and the reminder, and the content-vs-length result
replicates. A specific hypothesis is therefore **refuted**: the reminder sits
last before her turn begins, which is where anything affecting first-token
selection would act — and it does not act.

## Two effects, not one

gwen is **3/5 on `generate_image` at ZERO history** while eeva is 5/5. Her
*card* is roleplay-saturated, so the system prompt alone is already priming.
Trimming history would not reach that. Credit to the semantic-layer session
for spotting it in the first table.

And the two tools do not degrade identically — `image_search` falls 5/5 → 0/5
while `generate_image` starts at 3/5 and is at 0/5 by depth 2. Different
floors, different slopes. A tool-complexity term (one string argument versus
four structured ones) may be hiding under the history term, and at n=5 per
cell the two are not separable. The claim "it is not the image tool" is
supported in direction and not in magnitude.

## Independent corroboration, on a NON-tool behaviour

From the semantic-layer session's own runs, same persona and prompt, frozen
detector:

- three-arm, **single-turn**, 888 generations: rename breach **0.333**
- rename A/B, **16-turn history**, 6 sessions: rename breach **0.481**

44% higher with real history, on hard-wall compliance rather than tool
firing. Confounded — those 16-turn probes follow a bet she lost, so history
and adversarial framing are entangled — but it points the same way from an
independent direction.

The consequence lands on both of us: **every hard-wall number in this repo
is single-turn**, including the −34% rules-block result. The criticism I made
of the 76%→90% tool eval applies to those equally.

## Already published, and I should have looked first

- **arXiv 2607.11437** — *history-carried lock-in*: relational states set
  early stay ~60 points apart, persist after the establishing prompt is
  removed, are order-insensitive, and **do not deepen with length**. That is
  "it is not length, it is state", measured before I measured it.
- **arXiv 2502.15851** (Control Illusion) — social framings beat
  system/user role framings; pretraining-derived social priors outrank
  post-training guardrails. Explains why gwen's register wins and eeva's
  does not.
- **arXiv 2609.14157** — unnecessary tool *availability* drops answer rate
  98.2% → 63.5% and the tool is mostly not called; presence alone suppresses.
  Mitigation measured at +27.8 to +45.6pp from one scope-aware sentence.

## What this does NOT yet establish

- Whether the trigger is explicit/NSFW content specifically, or any strongly
  stylised in-character register. gwen's history is both; eeva's is neither.
  Not separated.
- Whether a clean-context decision pass fixes it. Untested, and gwen's 3/5
  at d0 says a clean context alone would not reach 5/5 anyway.
- Whether resampling would work. Probably not, and the table already says
  so: `generate_image` is 0/5 at four consecutive depths, ~20 independent
  samples with zero successes. You cannot resample a distribution with no
  mass on tool-call-first. That is the enforcement experiment, run by
  accident.
- Whether my firing detector has the recall to support these as LEVELS
  rather than contrasts. `prose_expected=False` is a scoring choice with the
  same exposure that produced a clean null here once before, when a checker
  could not see a real 34% effect. The contrasts are paired and survive a
  recall problem; the absolute rates may not.
- Whether constrained decoding is reachable. Ollama exposes no `tool_choice`
  and does not wire its grammar engine to `tools=`; that would mean llama.cpp
  directly, which is a much larger change.

## The precedent is ADR-015, not ADR-014

My first reading was that ADR-014/017 proved "deterministic enforcement beats
asking the model nicely", and that this generalises. **That is wrong**, and
the number I was quoting (gwen_dev 0/6 → 6/6) was retired the same day as a
6-probe single-shot count no powered run reproduced. The powered result is
the opposite of how I used it: the rules block, a **pure prompt
instruction**, cut breaches 0.4375 → 0.2875, −34%, p=0.0084 against a
length-matched placebo. Asking nicely *won*. Enforcement then won by more
(−96%) — but the determinism there is in the **verifier**, not the generator:
nothing made the model comply, the output was checked and resampled.

Three reasons that does not transfer here, all of which I had the data to see:

- that retry fired on **9% of turns** because fixing one breach prevented the
  cascade behind it; tool calls have no cascade and every turn is
  independent, so the cost would be ~100% of drawing-intent turns;
- its corrective line named a **lexically satisfiable** target ("use
  Daddy") that she could meet while staying in character — "emit a tool
  call" has no in-character form;
- and resampling needs the behaviour to appear sometimes, which 0/5 at four
  depths says it does not.

The right precedent is **ADR-015 — bubble boundaries are a pure function of
the text, not a prompt instruction.** Something computable was being asked of
the model, and the fix was to compute it. `generation_intent()` is 18/18 on
the boundary cases and we still hand the decision back to the model. That is
the same shape, and it does not depend on a verifier being cheap.

## Should any of this live in the graph? No, with no caveat.

Asked directly, and answered by the session that built the Neo4j layer after
checking rather than arguing. The sanctioned schema surface is 18 labels, 25
relationships, 27 properties; there is no `Tool`, no `Decision`, no
decision-provenance property, and **not one of the ~60 competency questions
asks who decided to fire a tool**. The gate's rule is that nothing unlisted
gets built, and the test is whether a question can be written first. Neither
of us could write one.

The need is real and was misfiled. "Model chose versus regex chose" has to
be recorded, because a tool firing is not evidence of grounding — gwen fired
`image_search` for a weather question, answered "103F", and shipped a
Sources block because a tool had run. But it is a **per-turn event with
nothing to traverse**: a column, not a graph query. It is now
`ResponseMetadata.tool_decided_by`, beside `source_type`, `tools_used` and
`wall_observations`, which is the shape ADR-018 already settled for
provenance.

Recorded because the near-miss is the lesson: a half-justified label is how
a schema starts drifting, and the identity MERGE key in this repo was a list
position that nothing caught, because the thing that would have caught it
was a question nobody wrote.

## Why this matters more than it looks

gwen is intended to become the persona template. The measurement says the
more a persona succeeds at being in character, the less reliably it can use
a tool — which is a direct conflict between the two things the platform is
for, not a tuning problem.

It also means a user-visible failure is **silent by construction**: the model
produces a confident in-character sentence, Ollama discards the unparsed
call, and nothing anywhere reports that a tool was meant to run. The image
path now catches this specific case (a matched drawing request with no job
replaces the reply), but web search has no equivalent.

---

# Resolution — the paired A/B, 2026-10-04

The fix measured against the path it replaces. Design and stopping rule were
fixed in `scripts/research/enqueue_path_prereg.json` and committed before the
run; harness `scripts/research/enqueue_path_ab.py`.

## The design decision that mattered most

Scoring arm A by "did `tool_calls` fire" and arm B by "did the JSON parse"
would have compared **two different instruments** and reported the gap as a
treatment effect. Both arms therefore run to the same endpoint —
`prompt.compose()` — and are scored there: *did this path yield a prompt we
could actually draw.* This repo has paid for the other way twice (a blind
checker flipped a null; the groundedness gate's blind spot).

The arms are paired by construction, so the test is **exact McNemar**, not
Fisher. Below 6 discordant pairs no split can reach p<0.05, so n=16 was fixed
in advance for headroom — not chosen after seeing a p-value.

## Result

16 cells × 3 reps × 2 arms, graph up.

| | count |
|---|---|
| both arms pass | 5 |
| only arm A (tool call) | **0** |
| only arm B (extraction) | **11** |
| neither | 0 |

Arm A 5/16, Wilson 95% [0.14, 0.56]. Arm B 16/16, Wilson 95% [0.81, 1.00].
11 discordant pairs, all one direction, **exact two-sided McNemar p=0.00098**.

gwen fails arm A at **zero** history too (2/3, 0/3, 1/3, 0/3), so depth is not
the only factor; eeva passes 5 of her 8 cells. The persona being made the
template is the one the tool-call path serves worst.

## The finding the aggregate hid

Hand labelling was mandatory in the prereg, not optional, and this is why.
Arm A's single faithfulness miss was not a dropped adjective. Asked to paint
*"a quiet harbour at sunrise"* at history depth 8, it composed a subject
**lifted verbatim from a different image request eight messages earlier** in
that session, and would have drawn that instead. Verified against
`data/chats.db`.

So real history does not only *suppress* the tool call. When the call does
fire under history, the arguments can come from the conversation rather than
from the request. **That is worse than silence** — silence is visible, a
confidently wrong picture is not.

Arm B is structurally immune: it passes no history at all. That property was
originally justified on quality grounds ("the request is self-contained"). It
is actually a containment boundary, and `test_no_history_is_sent` now says so.

## Two claims of mine that the measurement refuted

**1. The prompt scaffolding is not load-bearing.** `extract.py` claimed the
field descriptions and the sentence *"Time of day and lighting are SETTING,
never mood"* were what fixed the observed `mood: "dusk"` defect. Tested by
deleting them: instruction line removed → live suite still 8/8; line **and**
the mood/setting schema descriptions removed → still 8/8. At temperature 0 on
`mistral-small-abliterated:24b` the model separates the fields unprompted. The
original defect was real and is **unexplained**; it does not reproduce under
any of the three configurations tried. The scaffolding stays (free, correct,
and the deletion test says nothing about other models), but the docstring no
longer claims it holds anything up, and
`tests/integration/test_image_extract_live.py` is labelled a behaviour **pin**
rather than a guard — it has never been watched failing on this tree.

**2. `style` is not inert.** 48/48 `photographic` in the A/B is
indistinguishable from a field nothing reads, and this codebase has shipped
exactly that (persona sliders). Checked: 4/4 when a style IS stated. The
uniform value was the correct default for four requests that named no style.

No repair function was written for either. A control that never fires is
unreached code, which is worse than none.

## Independent corroboration of the premise

Verified first-hand, not taken from a research summary:

- [ollama#17274](https://github.com/ollama/ollama/issues/17274) — **open**,
  filed 2026-07-20 by a third party on a different model. Same failure class:
  a well-formed tool call, 40 completion tokens, discarded silently by the
  post-hoc parser; the caller sees an empty message.
- [ollama#17284](https://github.com/ollama/ollama/pulls/17284), the fix in
  flight — **open, not merged**, and it only *surfaces* the discarded buffer
  as content. It does not constrain generation.

Installed here: Ollama **0.34.2**. Nothing in the release notes claims `tools=`
moved onto the grammar path, and `tool_choice` remains unsupported. The gap is
real, current, and upstream has not closed it.

One risk checked rather than assumed: llama.cpp#29457 reports superlinear
grammar-build time on large enums, synchronous on the serving thread. The
`style` enum here is **4 values**; the issue's smallest datapoint is 100 values
at 0.12s. Not applicable — but the ceiling exists if anyone widens it.

## Shipped

`IMAGE_GEN_DIRECT_ENQUEUE` defaults **on** as of 2026-10-04. The tool-call path
is retained behind the flag for rollback only; it is not better under any
measured condition.
