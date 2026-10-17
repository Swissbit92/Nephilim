---
title: Invariants
status: active
created: 2026-10-02
last_reviewed_on: 2026-10-17
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

## A lifetime cache is only for a line said while the model is gone

Status: active
Statement: `persona_lines._MODEL_UNREACHABLE` — the situations whose first generation is kept for the process lifetime — must contain exactly the situations the image worker warms immediately before `unload()`. Every other situation regenerates when its permutation cycle drains.
Falsifiable: WHEN the two sets differ in either direction THE CHECK SHALL exit 1 and name the direction.
Check: scripts/checks/lifetime_cache_only_when_model_is_gone.py

Measured 2026-10-10 over gwen's live corpus: 9 of 127 comparable turns tripped an 8-token
whole-history repetition gate, and **9 of 9 were byte-identical replays of a cached status
line**, not model repetition — which was **0/127**. A single cached generation accounted
for 6 of them. So the defect everyone would have reached for a sampler to fix was a cache
with no expiry.

**The edit that undoes this looks like an optimisation.** Recycling costs one LLM call per
cycle, so adding a situation to `_MODEL_UNREACHABLE` makes a latency graph better and
restores the defect exactly. Nothing fails. The lines stay in voice, in character and
grammatical — they are simply the same lines forever, which is invisible to every test
that asks whether *a* line was produced rather than whether it differed from last time.

The rule is therefore tied to the one fact that licenses an exception, rather than to a
judgement about which situations "feel" cacheable: `image_gen/worker.py` warms a line
immediately before evicting the chat model, because that line is said exactly when the
model is gone. If a situation is cached for the lifetime it must be warmed there; if it is
warmed there it must be cached. The two lists are one claim written twice, and the check is
what keeps them one claim. `image_ready` is the case that matters — it renders in
`routes/notifications.py` *after* `worker.py`'s `finally: repin(...)` has restored the
model, so it was paying for a constraint it does not have.

Related and deliberately not a check, because no grep decides it: **a cap whose only reset
is the operation it gates is a latch, not a cap.** The first version of the recycle budget
stopped after two consecutive failures and could only be cleared by a successful recycle —
which is the thing it was blocking. Two transient draws reverted a situation to the
lifetime cache for the rest of the process's uptime, logged at INFO. Bound the *rate*
(exponential backoff) rather than the total whenever the failure may be transient.

## Every declared term carries a definition

Status: active
Statement: Every member of every controlled vocabulary registered in `scripts/checks/every_declared_term_has_a_definition.py` has a definition of at least 25 characters, and no definition exists for a term that is not in its vocabulary.
Falsifiable: WHEN a vocabulary gains a term with no definition, OR a definition is shorter than 25 characters, OR a definition names a term absent from its vocabulary, THE CHECK SHALL exit 1 and name the term. A vocabulary it cannot read exits 2, which is not a pass.
Check: scripts/checks/every_declared_term_has_a_definition.py

**Semantics are mandatory from 2026-10-17.** This repo enforced its vocabularies strictly
long before it defined them: an out-of-vocabulary value has always raised at the write
path, while not one of the 55 terms said what it MEANT. The meaning lived in Python
comments and ADR prose — unqueryable, and invisible to whatever assigns the next value.

**Why that is a data problem and not a documentation problem.** `TRAIT_KINDS` holds
`behaviour`, `psychological`, `emotional`, `style` and `tic`; **34 live nodes are tagged
`behaviour` and 20 `psychological`**, and nothing stated the difference. The same shape
has already cost this repo real data: `has_opinion_on` in the fact store had an
unconstrained range, and **half of everything the extractor ever wrote landed in the two
vaguest predicates** — three of twelve live rows were facts about the *assistant's own
reasoning*. Noy & McGuinness name an unconstrained range ("do not make the range THING")
as the modelling defect, and **the literature bounds relation GENERICITY, not relation
count.** A vague term is where data goes to die.

**A definition must DISTINGUISH, not restate.** "behaviour: a behaviour" satisfies no
purpose; the useful form says what belongs here *and what goes next door instead*. The
25-character floor exists because a shorter string is almost always a restatement — the
check was watched rejecting `"tic": "a tic"`.

⚠️ **DEFINITIONS ARE NOT PROMPT MATERIAL, and this is the constraint most likely to be
violated by someone acting in good faith.** Two separate measurements forbid it:

- Adding identity content to her prompt is a **closed change**: voice distinctiveness
  0.804 with injection off, 0.625 on, and three deliberate reframings at 0.708 / 0.542 /
  0.500 — every one below the off-baseline (ADR-006).
- The extractor prompt is capped by a different result: schema pruning beat the full
  schema at **~6x fewer tokens**, and small models "degrade sharply with schema bloat".
  The extractor currently receives the 33 predicates as a bare comma-joined list of
  names. Appending 33 definitions to it is precisely that bloat.

So definitions serve the human reading the code and this check. If they ever reach a
model, they reach it **pruned to the few terms a given call could plausibly use**.

**What this invariant deliberately does NOT require.** No SKOS, RDFS, OWL, PROV-O or
SHACL; no `broader`/`narrower` hierarchy; no external vocabulary (schema.org, FOAF,
Dublin Core, OWL-Time, Wikidata, BFO). Researched 2026-10-17 and declined on evidence,
not taste: those solve interoperability between parties who cannot talk to each other, at
term counts no one person can hold — thesauri exist for 40k–400k terms and this is 55.
A hierarchy would also make extraction *worse*, turning one decode decision into two.
The ecosystem's own ontology research reached the same verdict one level up ("adopt none
of the candidates wholesale") and gates the formal apparatus behind a stated trigger: **a
second person or consumer.** That trigger governs here too.

**Also deliberately not required: that definitions improve accuracy.** Measured
elsewhere: 13B models reach 83–94% ontology *conformance* alongside **22–38% F1** — the
model reliably picks a valid predicate while getting the fact wrong. Definitions buy
conformance and human clarity. Any future claim that they buy correctness needs its own
measurement.

**Adding a vocabulary to the registry is how it becomes governed, and the registry audits
itself.** A vocabulary absent from `GOVERNED` would be invisible to this check — so the
check first enumerates every `frozenset` in the governed files and fails on any that is
neither governed nor listed in `NOT_A_VALUE_VOCABULARY` **with a stated reason**, the same
discipline `identity_from_card.EXCLUDED_FIELDS` uses and for the same reason: "excluded"
without a reason is how something gets quietly dropped to make a check go green.

That audit earned its place immediately. Reviewing this work before opening its PR, it
found **two real misses** in the first registry I wrote — `ORIGINS` and
`RULE_TYPE_SOURCES`, both genuine value vocabularies in the rule store, both silently
unchecked. The docstring had warned about exactly that hole and the first version shipped
with it anyway. 55 terms became 60 across 10 vocabularies.

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
