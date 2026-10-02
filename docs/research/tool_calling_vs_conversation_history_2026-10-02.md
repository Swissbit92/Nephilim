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

## What this does NOT yet establish

- Whether the trigger is explicit/NSFW content specifically, or any strongly
  stylised in-character register. gwen's history is both; eeva's is neither.
  Not separated.
- Whether a clean-context decision pass fixes it. Untested — the d0 column
  says the model CAN call the tool with the same prompt and samplers, which
  is suggestive, but a two-pass design has its own costs (TB6 costed the
  second generation at ~16 tok/s) and has not been measured end to end.
- Whether constrained decoding is reachable. Ollama exposes no `tool_choice`
  and does not wire its grammar engine to `tools=`; that would mean llama.cpp
  directly, which is a much larger change.

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
