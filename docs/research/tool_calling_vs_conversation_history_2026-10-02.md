---
title: Tool calling collapses under roleplay history — measured
status: active
created: 2026-10-02
last_reviewed_on: 2026-10-02
review_in: 6 months
applies_to: nephilim
ai_summary: "Measured: native tool calling degrades to near zero in an ongoing roleplay conversation, and the cause is the CONTENT of the history rather than its length. Read before changing the tool-brain path, before trusting any tool-firing number measured without history, and before making gwen the persona template."
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
