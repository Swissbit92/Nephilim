---
title: Invariants
status: active
created: 2026-10-02
last_reviewed_on: 2026-10-02
review_in: 12 months
applies_to: nephilim
---

# Invariants

Standing constraints that bind **all** work in this repo, not one change. Each is stated in
falsifiable form and wired to a check that exits non-zero on violation, so the workflow runs
it every milestone and nobody has to remember it.

That last part is the whole point, and this repo has paid for it repeatedly. A hard wall
broke seven times in live traffic while its retry sat on a branch production never returns
from. A red-team eval imported modules directly and proved logic rather than reachability,
validating a guard that was orphaned. In both cases the rule was written down and read, and
broken anyway — a constraint is least salient exactly when it is about to be violated, and
the thing being asked to remember is the thing doing the forgetting.

Run them all: `python3 <crucible>/scripts/invariants_run.py --repo .`

## Every chat-model transport goes through the arbiter

Status: active
Statement: No module under `src/coordinator/` may reach a CHAT-model transport without importing `guard_chat_model`, so a generation holding the machine cannot be undercut by a 17 GiB model load.
Falsifiable: WHEN a module matches `OllamaLLM(`, `ollama.Client(`, `/api/generate` or `/api/chat` and does not import `guard_chat_model` THE CHECK SHALL exit 1 and name the file.
Check: scripts/checks/chat_model_transports_guarded.py

The machine has 48 GB. The companion model holds 16-19 GiB pinned; a generation peaks
around 20 GB. Both at once does not fail cleanly — it swap-storms, and the symptom is a Mac
that stops responding for twenty minutes. Ollama cannot be told to refuse a request, so the
refusal has to live in the application.

There is no single socket to guard. Mapping the repo found **five** distinct transports:
`httpx` in `services/ollama_http.py`, `langchain_ollama.OllamaLLM` constructed in
`llm_completion_service.py` *and* separately in `prompt_builder.py` / `cv_summarizer.py`,
the `ollama` package in `tool_brain_service.py`, and raw health probes. Two of those were
not in the plan for this work and were found only by reading; a third,
`fact_extraction_worker`, loops forever and can start a load at an arbitrary moment with
nothing having asked it to.

So the rule is on the SHAPE, and it is a script rather than only a test because a test
protects the code that existed when it was written. The sixth transport will be added by
someone who has not read the test.

Embeddings are deliberately out of scope: `bge-m3` is 0.63 GiB against the chat model's
16-19 GiB and sits on the routing hot path, so gating it would cost a reload on every
routing decision to save about 1% of the memory.

⚠️ The check carries four documented exemptions (the evictor itself, settings prose,
`/api/tags`, `/api/version`). Adding a fifth is allowed and requires writing the reason it
cannot load a model — which is the point, because the exemption then has to be argued.

## Image generation is off unless switched on deliberately

Status: active
Statement: `IMAGE_GEN_ENABLED` and `IMAGE_GEN_DEV_ENDPOINT` default to `False` in the settings class.
Falsifiable: WHEN either field's declared default is anything other than `False`, or the field is absent, THE CHECK SHALL exit 1 and name it.
Check: scripts/checks/image_gen_defaults_off.py

A generation occupies the whole machine for ~331 seconds (measured, not estimated) and
unloads the companion model to do it. That must never begin because a default drifted
during a refactor, and a default is exactly the kind of thing that drifts without anyone
deciding to change it.

The check reads the field DEFAULTS, not the environment: the environment is the operator's
business, the defaults are the repo's. It also fails if a field disappears, because a
deleted switch reads as "nothing to check" to anything looking for a `True`.

## The image-prompt extractor is never given conversation history

Status: active
Statement: `extract()` in `src/coordinator/services/image_gen/extract.py` takes only (message, client, model, system_prompt), and forwards nothing history-shaped into the chat call.
Falsifiable: WHEN `extract()` declares any other parameter, or any name containing history/messages/context/transcript/turns, THE CHECK SHALL exit 1 and name it.
Check: scripts/checks/extractor_takes_no_history.py

A safety boundary, not a style rule, and it was measured rather than reasoned.

In the paired A/B of 2026-10-04, the native tool-call path was asked to paint "a quiet
harbour at sunrise" with 8 messages of real history, and composed a subject lifted
**verbatim from a different image request eight messages earlier** in that session. It
would have generated that picture instead. Confirmed against `data/chats.db`. So real
history does not merely suppress a tool call — when the call does fire under history, the
arguments can come from the conversation rather than from the request, and the user gets a
confidently wrong picture rather than a visible failure.

`extract()` is immune only because it passes no history at all. That immunity is one line
of *absence*, which is precisely the kind of property a refactor removes while the suite
stays green: adding `history=` would look like an improvement ("give it context so it
resolves pronouns"), the unit test that pins the message list would be updated to match,
and nothing would fail until someone received a picture of something they asked for last
week.

Hence the guard is on the SIGNATURE rather than on behaviour. A new parameter is then a
decision that has to be argued against this file instead of arrived at. The check was
watched failing on a tree with `history=None` added, and passing on the real one.

## Success is never inferred from an exit code

Status: active
Statement: A generation is reported successful only when the exit code is 0 AND the PNG chunk-walks clean AND the generator's own Pillow decode left its marker. Any one of the three alone is insufficient.
Falsifiable: Covered by `tests/backend/coordinator/test_image_jobs.py::test_reconcile_fails_an_exit_zero_with_no_image` and `test_image_supervisor.py::test_success_with_no_output_is_a_failure`. Deliberately no `Check:` line — see below.

mflux's save path swallows every exception and its CLI returns `None`, so it exits 0 having
written nothing. The same shape has now appeared three times in this ecosystem: Ollama
answers `{"done_reason": "unload"}` in 2 ms while the model is still fully resident, and a
failed KuCoin API call once rendered as `+0.00 funding` for six days because `0.0` was both
sentinel and valid value.

This one is listed without a check on purpose. `check` warns rather than errors on an entry
with no `Check:`, and inventing a grep that pattern-matches "treats rc as success" would
produce a check that is mostly false positives — which teaches people to bypass the runner,
which is worse than an honest gap. It is here because the next person wiring a generator
backend needs to read it, and because an unenforceable constraint stated plainly is more
use than one quietly omitted.
