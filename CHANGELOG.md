# Changelog

All notable changes to the NEPHILIM project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-09-23

Patch: three latent production outages on the greet path, the sampler bypass that
made the 2026-08-23 repetition fix inert, a guard stopping tests writing to the
production database, and the persona-eval harness with its first pilot.

### Added (2026-09-23) — Persona-eval harness, and the pilot that measured it

324 generations against the deployed model through a scratch backend on its own database; production untouched. Two arms at k=3 over 54 probes (23 tooling probes skipped — they need a Brave key). **0 errors, 0 empty replies, 2% gate-intercepted.**

- **Verbatim self-repetition fell 40.7% → 5.6%** between the pre-fix sampler config (temp 0.4, `repeat_penalty` 1.0 = off, `repeat_last_n` 64) and the fixed one (temp 0.9, `min_p` 0.05, `repeat_penalty` 1.05, `repeat_last_n` 384). Median CrossRep-4 20.5% → 3.8%; longest shared span 46 → 27 tokens. Measured with stdlib n-gram metrics — no model, no thresholds, nothing to calibrate. **Stated honestly: the arms differ in three settings at once, so this is the bundle effect — what shipped versus what was there — and cannot attribute the gain to any single component.**
- **The 77-probe set works.** Tier-0 base rate 27% (fixed) / 34% (pre-fix), inside the 30–50% band the power analysis requires, so no rewrite is needed. Weakest-held rule is the required form of address at 46–61% failure, corroborating that 10 of 30 ordinary prefix replies dropped it. The forbidden abbreviation held perfectly at 0% across 48 generations.
- **The NLI rule detector does NOT work, and no figure from it may be quoted.** The reference arm — questions the rule is irrelevant to — failed at **91%** against conflict's 93%, and it scored *"I had a wild day 🌪️"* as a 0.99 contradiction of an exclusivity clause. Re-framing the hypothesis from "contradicts the rule" to "entails the violation" moved conflict from **83% to 0% on identical replies**. Two defensible framings, one dataset, opposite conclusions: an uncalibrated detector does not produce a weak number, it produces an arbitrary one. `reference_arm_check()` now prints beside every headline figure so this cannot go unnoticed again.
- **`keep_alive` was broken on the wire.** `OLLAMA_KEEP_ALIVE` defaults to the string `"-1"`, which Ollama rejects (`time: missing unit in duration "-1"`, HTTP 400). Measured: `-1` as an integer works, `"-1s"` works, `"10m"` works, `"-1"` does not. The legacy path had carried this since it was written — on the greet, the same never-exercised path that hid the `repeat_last_n` sentinel. Fixed at source with `wire_keep_alive()`.
- **New guard: the live backend is not a test fixture.** Eleven test modules reach `localhost:8000`, four through an `EVAL_BASE_URL` that defaulted to production. A full-suite run wrote 476 messages across 108 sessions into the production database before this was caught. `conftest.py` now refuses any HTTP request to a production backend port, failing loudly rather than skipping.
- New modules: `transport_preflight.py`, `pilot_score.py`, `repetition_metrics.py`, `rule_detector.py`, `pilot_prefix.py`, `gwen_probes.json` (77 probes), and baseline `baselines/pilot_20260923.json`.

### Fixed (2026-09-22) — The samplers never reached the model: a latent 503, a bypassed prose path, and anti-repetition switched off at the server

Three defects, all on the path gwen's turns actually take, none of which any existing test could have caught: every sampler test in the repo asserted on the dict the app *built* and never on what the server *did* with it. Suite **2273 → 2288**, zero new ruff findings on touched files, full-suite failure-set diff against the branch point empty.

- **`repeat_last_n: -1` was a latent outage, not a tuning mistake.** It meant "penalise over the whole context"; llama.cpp removed that meaning in PR #26524 (2026-08-04) because back-end sampling builds samplers before the context length is resolved, and Ollama forwards the value verbatim. Reproduced against the running Ollama 0.34.2: `{"repeat_last_n":-1}` → **HTTP 400** `Field 'repeat_last_n': Value must be between 0 <= value <= 2147483647`; `384` → 200. The **greet** path passes it — `create_llm_client` (`llm_client.py:134`) reads the card and `LLMCompletionService` sets `ollama_params["repeat_last_n"]` — and `_complete_or_503` (`routes/chat.py:112-126`) catches every exception and re-raises as `503 "LLM service temporarily unavailable: <type>"`, so the 400 would never have been legible as a sampler fault. It had not fired only because **no session has been created since the value landed on 2026-08-23**; the next new gwen conversation would have failed its opening message. The `-1` was not a stray value but a sentinel encoded in four layers — the card, the extractor guard (`>= -1`), the pydantic bound (`ge=-1`) and eight test assertions, one commented "below the -1 sentinel" — all now retired. Enforcement sits at `get_persona_sampling_overrides`, the only guard that binds: cards load through `load_persona_card_lenient`, which logs a warning and returns the raw dict regardless, so the schema bound states the contract but cannot enforce it. A negative window is now dropped *and logged*, because silence is how the sentinel survived.
- **The persona's samplers never reached the call that writes the prose.** ADR-008's Status header resolved the two-brain split to a single model, and TB6 then made the tool brain's own answer user-facing on ordinary chitchat via `TOOL_BRAIN_UNGATED_WEB`. TB6 argued only about generation *count* — one generation per turn either way — and nobody checked sampler parity, so every reply was written at a hardcoded `temperature: 0.4` while the card asked for 0.9, and `repeat_penalty`/`repeat_last_n`/`min_p`/`top_k`/`top_p` never arrived at all. The 0.4 has no design rationale anywhere; ADR-008 does not mention sampling. It entered at TB3 as a tool-*decision* temperature and leaked scope at TB6 when that same call began producing prose. **This is why `c00cf084` ("widen the repetition-penalty window") was inert in production** — it was verified end to end through `OllamaLLM`, the *legacy* transport, on a path gwen's turns no longer take. Decision and prose sampling are now split rather than one chosen over the other: a tool decision keeps 0.4, since persona voice has no business in tool-argument JSON, while prose (ungated chitchat, post-tool synthesis, forced final synthesis, refusal retry) uses the persona's declared settings. Still exactly one generation per turn — TB6 costed the alternative explicitly at ~16 tok/s. The caller resolves the overrides because `get_persona_sampling_overrides` reads the module-level settings singleton frozen at import, so resolving deeper would be invisible to any test that sets the environment and clears the cache.
- **`keep_alive` was never sent on tool-brain calls.** Ollama applies it per request, last-one-wins, so omitting it silently reverted `OLLAMA_KEEP_ALIVE=-1` to the server default on every turn — quietly defeating the model pin the setting exists to hold.
- **Anti-repetition was disabled at the server, not merely mistuned.** Ollama changed `repeat_penalty`'s default from 1.1 to 1.0 — off — in commit `6a261db7` (2026-08-12), reasoning that an always-on penalty distorts output that legitimately repeats and that "the remedy is a per-model parameter". Measured here beforehand: `repeat_last_n = 64, repeat_penalty = 1.000, dry_multiplier = 0.000`. That is **not** the original cause (the symptom predates 2026-08-12) but it is why nothing was damping it. gwen now declares `min_p 0.05`, `repeat_penalty 1.05`, `repeat_last_n 384`; `temperature` stays at 0.9, her declared voice, because no evidence was found to move it. 1.05 rather than 1.1 because the penalty applies to *prompt* tokens too, flat and undecayed (`llama_sampler_penalties_apply`), so a heavier one also punishes her pinned voice exemplars; the DRY paper's composability table (arXiv:2608.22761, Table 15) measures a light 1.05 as the best stacked setting. **384 is chosen, not derived** — no benchmark pins it, and it is flagged as inferred wherever it appears.
- **Corrects the 2026-08-23 entry below on `min_p`.** That entry says `min_p` cannot apply without `OLLAMA_COMPLETION_BACKEND=http`. True of the **legacy** path — `langchain_ollama` coerces options through `ollama._types.Options`, which has no `min_p` field, and that gap stays pinned. But the tool brain passes a plain dict to `Client.chat`, and `ChatRequest.options` accepts a `Mapping` without coercing, so **on the path gwen's turns actually take, `min_p` reaches the wire**. Verified end to end against the running server.
- **New: `test_sampler_wire_arrival.py` asserts what the server accepted, not what the app sent** — the gap every prior sampler regression here slipped through. Ollama makes the difference observable two ways and both are used: an unrecognised option is accepted and ignored with `level=WARN source=types.go:1048 msg="invalid option provided"` (HTTP 200, no error — the silent-failure mode), while a *retired* option is rejected outright (`typical_p` → 400 "no longer supported", confirmed live). Includes a `requires_ollama` round trip with `keep_alive=0` so nothing is left pinned.

## [0.2.0] - 2026-09-23

First tagged release. `[0.1.0]` was recorded in this file but never tagged, so the
repository has no prior tag to compare against; `v0.2.0` starts that history at the
commit `main` is promoted to, not retroactively.

Eighteen commits since the last promotion: the persona-context defect fixes
(reset completeness, dropped constraints, unlabelled recall, silent samplers), a
direct Ollama transport so `min_p` actually applies, the gwen repetition-penalty
window, the semantic-platform concept architecture, and the CI repair that made
this branch verifiable again.

### Fixed (2026-09-23) — CI had been red for a month: six prompt tests needed a live model

The tests added by the 2026-08-23 persona-context work turned CI red the day they landed, and **never once ran there afterwards**. Every push to `dev` from 2026-08-23 to 2026-09-23 failed the backend job — **6 failed, 2222 passed** — while passing locally the entire time. The last green run was 2026-08-22. Nothing reported it: GitHub's only signal is an email per push. Found by an ecosystem-wide CI watcher (`nephilim-ecosystem/scripts/ci_watch.py`) built for exactly this.

- **`build_system_prompt()` reaches Ollama, and a cache hides it.** The lean builder resolves the persona identity as `get_or_build_cv_summary(selector).get("summary") or _summarize(...)`; both sides end in `_llm()`, which calls `assert_model_available()` before anything is generated. On a machine that has run the app the summary is cached and the call never fires. **A fresh checkout is always cold**, so CI reached a live model and raised `RuntimeError: Could not reach Ollama`.
- Fixed in the tests, not the workflow: an autouse fixture pins the identity text, so the six render prompts with no network. They assert on prompt **structure** — whether the constraints block appears, whether examples are included, what order sections come in — and none of them asserts on the identity, so every assertion survives intact.
- **Deliberately not `@requires_ollama`.** Skipping was the cheap fix and would have discarded the coverage that matters most: one of these tests exists because *"this was False in production"* on 2026-08-23. A test that skips on CI is a test that is not protecting anything.
- The fixture clears `_build_system_prompt_lean`'s `lru_cache` on both sides, or a stubbed prompt leaks into the next test and a real one into this.

Verified in an isolated worktree with Ollama pointed at a closed port, which reproduces CI exactly: **6 failed / 2222 passed → 2228 passed, 43 skipped, 0 failed.** The six now *pass* rather than skip.

**Known, pre-existing, out of scope:** `test_faiss_incremental_update.py::test_incremental_update_performance` fails locally when Ollama is reachable — confirmed identical on unmodified `dev`, so not introduced here. It is among the 43 that skip on CI, which is why it never showed there.

### Fixed (2026-08-23) — Persona-context defects: reset completeness, dropped constraints, unlabelled recall, silent samplers

Four defects reproduced from a live Telegram session and the production DB, all of them wiring gaps rather than model or context-size problems. The context window was never the constraint: `num_ctx` is 16,384 and the failing turn used **~2,236 tokens**, so the system prompt is ~1,016 tokens and the rest is history. Suite **1966 → 2038** passing, no test removed, zero new ruff findings on touched files.

- **`/reset` now clears everything derived from the messages** (`routes/sessions.py::_clear_derived_session_state`). It previously deleted messages and emotional state only, leaving `conversation_summaries`, the FAISS index and `session_notes` behind. Live evidence: the gwen session held **12 messages and 2 orphaned summaries**, one written 2026-07-05 recording a preference the user had since contradicted — re-injected into every turn, ungated by any flag, while the bot's reset message said history was wiped. `EpisodicMemoryRAG.clear_session()` already existed with **zero callers**, so a cleared session stayed semantically searchable for the life of the backend process. Each store clears independently and the result is returned in the response body: a disabled subsystem cannot fail the reset, and cannot fail silently either. Note the declared `ON DELETE CASCADE`s do not help here — `/reset` deletes children directly and never the parent row, and cascades only fire top-down.
- **Persona behavioural constraints reach the model** (flag-gated `PERSONA_CONSTRAINTS_IN_PROMPT`, **default OFF**). `do`, `dont`, `boundaries`, `user_relationship` (including `exclusivity`) and `escalation_policy.when_to_decline` had **zero readers anywhere in `src/`**; `persona_schema.py` documents the workaround in a comment ("the only reliable lever for word choice, since the lean prompt omits do/dont"). A persona declared exclusivity, the model was never told, and it proposed the opposite when probed — not drift, an unsent rule. Adds a `<constraints>` section plus a one-line restatement immediately before the latest user turn, because recall is worst in the middle of a long context (arXiv:2307.03172). Negations are re-anchored under one affirmative stem rather than listed: open models violate negated instructions 77–100% of the time versus affirmative framing (arXiv, "When Prohibitions Become Permissions"). `boundaries.content` is deliberately **not** rendered — unlike `ethics` it is a capability declaration with mixed polarity, and at least one shipped persona has an entry plainly meant as a prohibition that carries no negation marker. Budgeted at 150 tokens cached / 100 per turn; measured +182 tokens on gwen with the flag on, byte-identical off.
- **Recalled turns are labelled instead of impersonating recent dialogue** (`MessageRole.RECALLED`, unflagged). Semantically retrieved messages were built with their original role and rendered `Assistant: …` — byte-identical to the model's own previous line — so it continued them verbatim. This is the same structural cause Unit 42 documents as memory poisoning: content arriving in the model's own voice is weighted as its own prior commitment. The role is render-time only and never persisted; `tool_brain_service` skips recalled turns alongside narrator ones, since it maps unknown roles to `"user"` and would have shown weeks-old text to the router as a fresh request.
- **Voice exemplars and the session opener stop being pinned forever** (flag-gated `PERSONA_UNPIN_ON_DEPTH`, **default OFF**, threshold `PERSONA_UNPIN_DEPTH_TURNS`=6). Three cached `example_dialogues` and the first three messages reached the model on every turn for the life of a session, in dialogue format — the condition few-shot copying feeds on, an effect that grows with the number of similar examples (arXiv:2402.09954). `include_examples` became a second `lru_cache` key rather than relocating the block, preserving the deliberate voice-last ordering.
- **Persona sampler settings stop being discarded.** `top_k`/`top_p` were declared on `SamplingPreset` and silently dropped; `repeat_last_n` existed nowhere, leaving Ollama's 64-token default — enough to stop a sentence repeating inside one reply, far too short to notice a repeated paragraph. Both spellings of the penalty (`repeat_penalty`, `repetition_penalty`) are now accepted; only the former was read while the schema documented the latter. Out-of-range values are omitted rather than clamped so the caller's default applies.
- **`min_p` actually applies** (flag-gated `OLLAMA_COMPLETION_BACKEND=http`, **default `langchain`**). `langchain_ollama.OllamaLLM` coerces options through `ollama.Options`, which has no `min_p` field — verified two ways against the installed 1.0.1; upstream langchain-ai/langchain#32744. No config change can fix it, so `services/ollama_http.py` posts to `/api/generate` with the options dict verbatim. Live-verified against the running Ollama, not only mocks. On the langchain path a requested `min_p` now logs a warning instead of vanishing.
- **The groundedness gate stops firing on fiction being written.** Its exclusion list covered a persona's *backstory* but not the scene it is narrating now; a companion writing in the first person produces specific, present-tense, falsifiable-*sounding* statements, which is the shape the flag clauses describe. Adds an in-scene-narration exclusion and scopes the live-state clause to real accounts and money. The 2026-08-12 catch case is regression-pinned so narrowing cannot widen into a false negative. **`test_routes_chat.py` pins `gate_enabled=False` in its shared fixture**, so every gate path there was dark — which is how a live-in-production gate defect coexisted with a green suite; the new tests run with it explicitly on.
- **Summarizer offset**: `summary_count * interval` could exceed the messages that exist, driving the backlog negative and suppressing summarization with no signal. Clamped, and now warned. This does not restore summarization sooner — clearing summaries on reset is what fixes that; the clamp is defence-in-depth for a summary racing a reset.

### Changed

- **Ollama `keep_alive` is configurable, and the summarisation utilities no longer pin a model indefinitely.** It was hardcoded to `-1` in three places. For the chat path that is deliberate and documented — an always-warm ~17GB model costs VRAM but no CPU, and avoids a cold reload on every conversation. For `prompt_builder` and `cv_summarizer` it was not: both are rarely-run summarisation helpers, and pinning them held a model resident for the entire process lifetime. Observed 2026-08-22: **25.66GB across three models on a 52GB box, 30% free, 162k pageouts.** Two new settings — `OLLAMA_KEEP_ALIVE` (chat, default `"-1"`, behaviour unchanged) and `OLLAMA_UTILITY_KEEP_ALIVE` (utilities, default `"10m"`). The chat default is deliberately left at `-1` so warm-start latency is not silently regressed; lower it only if the box is under memory pressure and a cold first chat is acceptable. 1967 backend tests unchanged.


### Changed (2026-08-22) — Decompose `startup.py` into `di/{repositories,services,jupiter}.py` (repo-audit rec #7)

Behavior-preserving, zero test changes required. Suite 2156 passing / 43 skipped (unchanged before/after); `coverage_delta.py` exit 0 (no collected test lost); full-repo ruff **1855 → 1825** (net reduction — the split's mechanical `Optional[X]`→`X | None`/import-sort backlog additions were auto-fixed with `ruff --fix` scoped to just the touched files). Live startup wiring smoke-tested end-to-end (`init_db → init_repositories → init_memory_manager → init_phase3_memory → init_brave_client → init_jupiter → init_strategy_scheduler → cleanup_orphaned_sessions → build_app_state`) against a scratch SQLite DB with Jupiter/Brave disabled, confirming `AppState.wallet_repo`/`trade_proposal_repo`/`brave_client` correctly resolve to `None` rather than raising.

Named the **#1 structural fix** in the 2026-07-04 audit and still open in the 2026-08-22 repeat audit (anchor 42→43, "nothing is being paid down"); the 2026-07-05 DI follow-up (`app_state.py`/`dependencies.py`, see the entry below) added the *typed consumption layer* but deliberately left the `get_X()`/`init_X()` **definitions** and their module globals in `startup.py` itself — this closes that remaining half.

- **`src/coordinator/di/{repositories,services,jupiter}.py`** (new package) — the 25 `get_*` + 8 `init_*` functions and their module-level globals, split by cluster: `repositories.py` (12 SQLite repos + `init_db`/Alembic + `cleanup_orphaned_sessions`), `services.py` (Brave MCP + memory manager + Phase 3 RAG/fact-extraction + tool interceptor), `jupiter.py` (Jupiter MCP + wallet execution/strategy + scheduler). Moved verbatim, including the 9 in-function lazy imports (fixed up to `..module` relative paths) — none of them were actually import-cycle workarounds specific to `startup.py`'s position in the graph, so nothing needed to change about *why* they're lazy.
- **`startup.py` 752 → 283 lines.** Re-exports every `di/*` name via plain `from .di.X import name` — a name binding, not a copy — so `src.coordinator.startup.get_X`/`init_X` keep resolving exactly as before. This is why the ~150 test-patch sites (`monkeypatch.setattr(startup, "get_wallet_repo", ...)`, `@patch("src.coordinator.startup.get_wallet_repo")`, etc., concentrated in `test_routes_wallet.py`) and every `from ..startup import get_X` / `from .. import startup` call site in `routes/`, `services/`, and `server.py` needed **zero changes** — confirmed by grep first: no code anywhere (src or tests) touches a private `startup._X` global directly, only the public `get_*`/`init_*`/`build_app_state`/`get_app_state` surface, which the re-export preserves byte-for-byte.
- **`build_app_state()` now goes through a `_safe()` wrapper** around the raising getters (`get_session_repo`, `get_wallet_repo`, etc.) instead of reading the old same-module private globals directly, since those globals no longer live in `startup.py`. `_safe()` collapses `RuntimeError` (raised by several getters, by design, for the 503 path in `dependencies.py`) back to `None` — provably identical output to a raw global read in every case, verified by the live smoke test above with Jupiter disabled (`wallet_repo`/`trade_proposal_repo` are legitimately `None` pre-Jupiter-init and must stay `None` in the snapshot, not raise).
- **Deliberately not touched:** `app_state.py`/`dependencies.py` (unchanged — the re-export contract means their `from . import startup; startup.get_X()` resolution needed no updates), `chat_session_service.py` split, persona PNG compression, `.env.example` regeneration — out of scope for this pass, tracked as the audit's remaining findings.

### Added (2026-07-19) — tool-firing eval, and the router-recall bug it found

`tests/evaluation/eval_tool_firing.py` + `tool_firing_cases.py` + 9 headless guards in `test_tool_firing_cases.py`. A 29-case golden set over 6 buckets (explicit web, colloquial web, image-find, video-find, chitchat must-not-fire, wallet must-stay-deterministic), driven through the live session API and scored from the observable `source_type`. Built because the ADR-008 soak produced no usable data — it generates the traffic instead of waiting for it. Suite 2036 → **2046**.

The headline metric is **native-fire rate**, not accuracy: accuracy alone cannot distinguish "the tool brain works" from "the legacy floor is carrying it", and the stored `source_type` counts cannot either.

**It found a real defect on its first run.** Baseline **76% accuracy / 79% native-fire**, but `web_colloquial` at **33% / 0% native** — 4 of 6 genuinely web-needing turns answered from model weights with no search. Root cause was **bge-m3 router recall, not the tool brain**: `_try_tool_brain` engaged only on `NEEDS_WEB_SEARCH`, and the misses scored **0.49–0.61 against the 0.66 threshold**, so the model was never offered a tool at all. The 28 `web_search` anchors are crypto/weather/sports only, with no general-knowledge template. The same narrowness inverts: *"how are you feeling today?"* scores 0.70 against "how's the market doing today" and triggers a real search on a companion turn.

- **Fixed an observability bug the eval surfaced:** `routes/chat.py` hardcoded `metadata.tools_used = ["web_search"]` on the tool-brain path, so `image_search`/`video_search` were indistinguishable from a generic search. Now derived from `result.tool_trace`. This had made the eval briefly appear to show media narrowing was broken; it was working all along (media: 100% accuracy, 100% native, 100% correct tool).

### Added (2026-07-19) — `TOOL_BRAIN_UNGATED_WEB` (ADR-008 TB6), default OFF

Engages the native loop on `NEEDS_NEITHER` turns too, offering **web tools only**. Follows Nous Research's Hermes Agent, which has no pre-classification router at all — tools are exposed every turn and the model decides, steered by an enumerated trigger list. The structural argument: a routing miss is invisible (no tool was ever offered), whereas a model declining an offered tool is visible in traces and fixable with prompt text.

- **`NEEDS_WALLET` is never ungated**, in either mode. TB5's live failure was wallet fixation and a false positive there costs more than a missed search; two tests assert the invariant directly.
- `_SEARCH_TRIGGER_GUIDANCE` enumerates triggers (current facts, prices, recent/latest, named real entities) and exclusions (feelings, lore, this conversation, creative writing). Deliberately carries **no voice language** so it does not compete with the ADR-005 `voice_signature`; a test asserts this.
- Ungated no-tool turns **reuse the tool brain's own answer** through the groundedness gate rather than returning None — returning None would send the turn to the legacy branch and regenerate from scratch, a second full generation on every chitchat turn at ~16 tok/s with `OLLAMA_NUM_PARALLEL=1`. Exactly one generation per turn in both modes.

**Measured (scratch instance :8001, prod untouched):** overall **76% → 90%** accuracy, **79% → 89%** native-fire. `web_explicit` 60→100%, `web_colloquial` 33→67% (native 0→75%), media 100% with tool-match 0→100%, wallet **100% with 0 leaks** in both modes.

**Voice gate (ADR-005 attribution, both arms on the same instance minutes apart so the flag is the only variable):** distinctiveness **0.6964 in BOTH arms**; per persona cipher +0.25, aegis +0.125, solace −0.25, gojo −0.125, eeva/nyx/aurora flat. Read as noise on specific evidence: **gojo has no web tools**, so the flag provably cannot affect it, yet gojo still moved 0.125 — an accidental control group putting the instrument's noise floor at ±0.125 (one probe of eight). Arms verified genuinely distinct: all 35 shared probes differed in text, and source mixes differed (ON `tool_brain` 9 / OFF 5; OFF emitted 3 `groundedness_abstain`, ON zero).

**ENABLED ON PROD 2026-07-19** (`TOOL_BRAIN_UNGATED_WEB=true` + backend kickstart). Both new code paths verified live in the log — an `llm`-classified turn reaching `[ToolBrain] ungated no-tool turn -> using native answer`, which the old gate made impossible. Flipped **deliberately without a gwen voice gate**: the system had zero organic traffic for 13 days, so real use is a better instrument than more synthetic eval (human role-identification ~90.8% vs ~69% for an LLM judge), and revert is one env var plus a kickstart. A judgement call, not a passed gate. Soak watchlist: gwen voice (she now gets media tools offered on conversational turns), solace voice (largest single move, −0.25), and colloquial queries still missing ~1 in 3. Evidence caveat: n=29, single run, temp 0.9 — one case flipped between runs, so 90% carries real variance.

**Known gap.** `probes.json` covers 7 of 8 personas — **gwen is absent** (pre-existing, ADR-005 era), and she is the persona this change affects most: restricted tool surface, own live Telegram daemon, most voice-sensitive. She cannot simply be added, since 7→8 moves the attribution chance level 0.143→0.125 and invalidates every historical baseline. See ROADMAP.

### Removed (2026-07-19) — ADR-004 two-stage agentic pipeline retired (superseded by the ADR-008 tool brain)

The pipeline existed only because Magidonia-24B couldn't native-tool-call. The ADR-008 single-model tool brain replaced that purpose and has been live since 2026-07-05; `AGENTIC_ENABLED` was never flipped on in production. Net **−1,946 / +218 lines** across 22 files. Suite 2068 → **2027 passed, 0 failed** (the drop is 46 deleted agentic tests, 3 added; reconciled test-by-test). Ruff repo-wide 1863 → 1808.

- **Deleted:** `services/agentic_pipeline.py`, `services/argument_extractor.py`, `QueryHandlerService.handle_agentic_query`, the `routes/chat.py` branch that called it, `SourceType.AGENTIC`/`AGENTIC_BLOCKED`/`AGENTIC_HITL`, the `AGENTIC_ENABLED` / `AGENTIC_EXTRACTION_COHERENCE_THRESHOLD` / `AGENTIC_EXTRACTION_MAX_RETRIES` / `AGENTIC_TRIGGER_SIMILARITY_THRESHOLD` settings, and the orphaned `build_scene_contract` / `DEFAULT_ACTION_ALIASES` / `_diegetic_name` diegetic-naming layer plus `PersonaCard.agentic_action_aliases`.
- **Kept (live, reused by the tool brain):** `ToolCallInterceptor` — every tool-brain call still passes `interceptor.validate` before execution — with `AGENTIC_ARGUMENT_ALLOWLIST`; and `InjectionGuard.sanitize_memory_write` with `AGENTIC_INJECTION_GUARD`.
- **Deliberate safety scope decision.** An adversarial review during the retirement found `InjectionGuard.check_tool_trigger_source` and `.detect_escalation` had **no caller outside the deleted pipeline**: `ToolBrainService.__init__` accepted an `injection_guard` param it never invoked, and `routes/chat.py` never passed one. The trust-hierarchy rule (retrieved content may inform but never *trigger* a tool) and the escalation detector were therefore built, red-team-tested, and **never wired to production**. Chosen: delete rather than rewire, so the codebase stops implying a control it doesn't run. Their 5 injection + 3 escalation red-team vectors were removed with them; the **17 interceptor vectors covering the live gate were retained and re-run green (7/7)**. Recoverable from git history if the threat model changes — see [ADR-004](docs/decisions/004-persona-safe-agentic-tool-calls.md), now Superseded.
- Enum deletion was verified against production data rather than assumed safe: `SELECT source_type, COUNT(*)` on `data/chats.db` returned `llm:737, tool_brain:24, brave_mcp:17, groundedness_abstain:1` — zero rows carried the removed values.
- `tests/backend/coordinator/test_scene_contract.py` → `test_agent_settings.py` (its surviving half), plus a new `test_agent_settings_drops_retired_pipeline_fields` guarding against silent re-introduction of the four retired settings.
- A lingering `AGENTIC_ENABLED=false` in a local `.env` is harmless (config uses `extra="ignore"`).
- **Basis for the removal was structural, not observational.** The ROADMAP gate for this item was "~1 week of tool-brain soak." Measuring that soak on 2026-07-19 showed it never happened: all 24 `tool_brain` messages date from 2026-07-05 (the live-test day) and there are **zero assistant messages between 2026-07-06 and 2026-07-19**. What actually justified the deletion was that the removed code was inert (flag never true in prod) and that the retained interceptor's reachability on the live path was verified by call-graph review + a re-run red-team eval. Recorded so the "two-week soak" framing isn't repeated as precedent — and noting that the tool brain remains genuinely un-soaked; see the ROADMAP soak item.

### Fixed (2026-07-19) — intermittent HTTP 500 on `/sessions/{id}/greet` (multi-message greeting)

`greet_with_session` passed the greet response's `answer` straight into `AppendMessageBody(content=...)`, but `answer` is a **list** whenever the greeting split into `message_flow == "multi"` — a pydantic `ValidationError` (`content` must be `str`) surfaced as an unhandled 500. Latent + pre-existing (nothing to do with ADR-011/012); intermittent because only *long* greetings split. Fix: persist a multi-part greeting as separate assistant messages with a shared `multi_message_id` (mirroring chat persistence); single greetings unchanged. Regression test proven to fail on the old code (500) and pass on the fix. Suite → 2068.

### Added (2026-07-19) — persona-configurable deterministic word substitutions (ADR-012)

A persona can now declare a `word_substitutions` map in its JSON (e.g. Gwen's `{"shaft": "cock"}`) and the finalize path enforces it deterministically. This is the **only** reliable lever for word choice: live testing proved prompting can't do it — the lean prompt builder (ADR-005) doesn't include the `do`/`dont` arrays (so a "always say cock not shaft" `dont` line never reached the model), and "shaft" still appeared in **6/12** turns at temperature **0.9**.

- `apply_word_substitutions()` in `message_processing_service.py`: whole-word (`\b`, so `driveshaft` survives), case-preserving (`Shaft`→`Cock`), keys regex-escaped, rule count capped (25). One `re.sub` per rule on the finished string — **microseconds, zero effect on generation speed** (it's not a second LLM pass). No-op when a persona declares none, so the other 5 pay nothing.
- Wired into **both** finalize paths (persona-agnostic): `_build_llm_response` (chat.py) and `_finalize_response` (query_handler_service.py). `PersonaCard.word_substitutions` added to the schema.
- Gwen: `word_substitutions: {shaft: cock}` added; the earlier dead `dont` line (never reached the prompt) removed. +10 tests, suite → 2065. See [ADR-012](docs/decisions/012-persona-configurable-deterministic-word-substitutions.md).

### Fixed (2026-07-17) — `.env` loading scoped to the test that needs it; Brave connectivity is a real test now

Closes the hermeticity root cause at source (the conftest fixture stays as defense-in-depth).

- **Module-scope `load_dotenv()` removed from `tests/integration/`.** In `test_mvp2_integration.py` it was **dead weight** — that module reads config only via `get_settings()` (pydantic already sources `.env` through `env_file`) and passes `api_key=get_brave_api_key()` explicitly; it also ran *after* module-level `_settings` was built. Its one real effect was exporting prod `.env` into `os.environ` at collection. In `test_brave_mcp_connectivity.py` it **is** load-bearing (`mcp_client_stdio` resolves `api_key or os.getenv("BRAVE_API_KEY")`), so it became a test-scoped `brave_env` fixture using `dotenv_values` (reads the file without touching `os.environ`) + `monkeypatch.setenv`, restored by pytest at teardown. A plain in-function `load_dotenv()` would still leak for the rest of the session — that test has no skip markers and runs every time. Verified: importing the module no longer mutates `os.environ`, checked **without** the conftest net.
- **`test_brave_connectivity` is an actual test now.** It had no markers and wrapped everything in a catch-all that `return`ed `True`/`False` instead of asserting — so on every headless run it failed to reach Docker/Brave, swallowed the error, and reported **green** (pytest only emitted a "test returned non-None" warning). Now `pytestmark = [requires_api_key, requires_docker]` auto-skips it when the key/Docker are absent, and it asserts on real results (non-empty, count honoured, title/url/description present, `url` is http(s)); the live check moved into `_run_search()`, which no longer swallows exceptions. Proven both ways — skips headless (`BRAVE_API_KEY not set`) and **fails** when forced against an invalid key. Suite now reads 2055 passed / 45 skipped (one fake pass became an honest skip; warnings 16 → 15).

### Fixed (2026-07-17) — ADR-011 regression: stale dependency-patch list broke `test_routes_chat` in narrow scopes

`pytest tests/backend/coordinator` failed 28 tests (all of `test_routes_chat.py`) while the full suite was green. **This was an ADR-011 regression, not the pre-existing order-dependence it was first reported as** — verified by A/B against the pre-ADR-011 tree (11 failures there vs 28 after).

Cause: ADR-011 M2 added `session_note_repo` to `routes/chat.py::_get_dependencies()`, and `startup.get_session_note_repo()` **raises** when its singleton is uninitialised. `test_routes_chat.py` neutralises deps with a **hardcoded** list of `startup.get_*` patches, which silently went stale — it covered 11 of the 14 getters `_get_dependencies()` resolves. A full-suite run masked it because an earlier test happened to initialise startup, so the full-suite QA gate never saw it.

Fix (test-only): the patch list is now **derived from `_get_dependencies()`'s own source** (`_dependency_getter_names()`), so a new dependency is neutralised automatically and the list cannot go stale again. Added `TestDependencyPatchCoverage` as an explicit guard (asserts the derived names exist on `startup` and that the patch list covers every one). All scopes green: full **2056**, `tests/backend` **1956**, `tests/backend/coordinator` **1910** — 0 failures.

Lesson recorded: a full-suite-only QA gate can mask a broken narrow scope; when a shared dependency bundle grows, every test that neutralises it must grow too — so derive, don't hardcode.

### Fixed (2026-07-17) — test suite is hermetic: prod `.env` no longer leaks into tests (10 failures → 0)

The suite had 10 long-standing failures that only appeared on a configured dev machine, and they were worse than they looked: **six search tests were making REAL network calls to the live local SearXNG** (returning actual Bitcoin/FIFA web results where `None` was asserted), with their Brave mocks never called. Full suite is now **2054 passed / 0 failed** and **~2.4× faster** (14s → 5.8s — the removed time was real HTTP).

Two independent channels caused it; closing either alone left the other winning (priority: `os.environ` > dotenv file > field defaults):

- **`os.environ` (the real culprit).** `tests/integration/test_mvp2_integration.py` and `test_brave_mcp_connectivity.py` call `load_dotenv()` at **module scope**, and pytest imports every test module during *collection* — so the whole prod `.env` was exported into the process environment before the first test ran, outranking everything. Hence the scope-dependence (`tests/integration/` is only collected in a full run).
- **The dotenv file.** Every settings class declares `model_config["env_file"] = ".env"`, read *in addition to* `os.environ` — so `monkeypatch.delenv` could never simulate "env absent"; it just fell back to the file (e.g. `TOOL_BRAIN_ENABLED=true`).

Fix — `tests/conftest.py::_hermetic_settings` (session-scoped, autouse, restored on teardown): strips only the env keys that collection *injected* (diffed against a snapshot taken at conftest import, so shell/CI vars like `OLLAMA_BASE` survive), and disables `env_file` on every settings class in **both** import trees — `coordinator.*` and `src.coordinator.*` are distinct module objects with distinct class objects here, so patching one leaves the other reading `.env`. New settings modules and new dotenv-loading test modules are covered automatically. **No production code changed.**

Verified at name level: 0 new failures, exactly the 10 targets fixed. (This entry originally called the 28 remaining `tests/backend/coordinator`-scope failures "pre-existing order-dependent" — that was **wrong**. The next entry corrects it: they were an ADR-011 regression, since fixed. Every scope is now green.)

### Fixed (2026-07-17) — ADR-011 live-testing follow-ups: narrator regen, `<msg>` leaks, role leaks, menu

Three defects found by real Telegram use of the ADR-011 command set, plus a UX correction. Coordinator suite 2030 → 2044 (+14), gateway 113 → 116 (+3), 0 regressions.

- **`/regen` and `/undo` after `/sys` (ADR-011 regression).** `_split_last_exchange` only accepted a `user` turn as the stimulus, so `/regen` right after a narrator beat returned 400 "nothing to regenerate", and `/undo` deleted only the reply, orphaning the beat. Now `_STIMULUS_ROLES = (user, narrator)`: regenerate re-drives `NARRATE_RESPONSE_INSTRUCTION` for a narrator stimulus (the beat stays in history) and resends the content for a user stimulus; undo removes the stimulus **and** its trailing replies. The regen-after-`/sys` case is now tested (it was the gap that let this ship).
- **Literal `<msg>` tags reaching the chat** (pre-existing, exposed by `/continue`). `parse_multi_message_response` only matched well-formed `<msg>…</msg>` pairs; an unclosed `<msg>` fell through and leaked verbatim. Added an unclosed-tag recovery split, reached **only** when the pair path finds nothing — that path stays byte-identical.
- **Role-prefix / hallucinated-turn leaks** (pre-existing, seen as a `/sys` reply ending in `User:\nFrom behind`). The prompt renders history as `User:`/`Assistant:`, so the model sometimes writes the *next* turn itself. Neither finalize path caught it: `_build_llm_response` had no role-leak handling at all and `_finalize_response` stripped only a **leading** `Assistant:`. New shared `strip_role_prefix_leaks()` (line-anchored, so persona prose containing the word survives) is now used by **both** paths, closing the divergence. Source-level fix (an LLM stop sequence) was considered and deferred — bigger blast radius than the bug warrants.
- **Telegram menu now surfaces all 11 commands.** The first cut showed only 6, applying "5-8 sweet spot / dilution past 8" command-menu research — but that measures tap-through in a **multi-user conversion funnel**; on a single-operator personal bot discoverability wins and the hidden commands (`/sys`, `/note`, `/impersonate`, `/whoami`, `/reset`) were simply invisible. A test now asserts the menu covers every registered command.

### Added (2026-07-16) — conversation-control commands, shared across Telegram + React UI (ADR-011)

A companion/RP command set (SillyTavern-class swipe/continue/scene-direction) implemented as **shared coordinator session-API endpoints** so the Telegram gateway and the React Nephilim UI behave identically — both are thin clients of one contract. `/reset_persona` was considered and dropped (redundant with a terminal-side `launchctl kickstart`). Delivered as QA-gated phases; coordinator suite 2015 → 2030 passing (+15 M2 on top of +25 M1), gateway 90 → 113, React `npm run build` green. The 10 pre-existing `.env`-bleed test failures are unchanged (unrelated; tracked separately). See [ADR-011](docs/decisions/011-conversation-control-commands-as-shared-session-api-endpoints.md).

- **New session-API endpoints** (`routes/chat.py` + `routes/sessions.py`, logic in `services/conversation_control_service.py`): `POST /sessions/{id}/regenerate` (reroll last reply), `.../continue` (extend last reply), `.../undo` (delete last exchange), `.../narrate` (`/sys` narrator beat → in-world reaction), `.../impersonate` (draft the user's next line, not stored), `GET /sessions/{id}/meta` (`/whoami`), and `PUT/GET/DELETE /sessions/{id}/note` (persistent author's note). All reuse the standard finalize path (first-person post-proc, ADR-007 groundedness gate, multi-message shaping) via `handle_session_chat`'s new `persist_user`/`run_post_turn_updates` flags — no parallel LLM plumbing.
- **`session_notes` table** (alembic `5session_notes`, additive; `SessionNoteRepository` with `_ensure_table` dual-cover). The note is injected into every turn through the post-`lru_cache` `extra_system_context` seam (`_append_author_note`), never inside `build_system_prompt`.
- **`MessageRole.NARRATOR`** — a new role for `/sys` scene beats; four role-switch sites made tolerant (chat history renders `[Scene: …]`; fact/triplet extractors skip it; tool-brain skips it).
- **Telegram gateway** (`services/telegram-gateway/`): `/help`, `/whoami`, `/regen`, `/continue`, `/undo`, `/sys`, `/note`, `/impersonate` — thin HTTP clients of the above; native slash menu registered via `setMyCommands` (6 surfaced); new `NephilimBadRequestError` maps a 400 to a friendly "nothing to act on". Still allowlist-gated with the forwarded-message injection guard.
- **React Nephilim UI**: `services/api/conversationControl.ts` + 8 `ChatContext` actions; Regenerate/Continue/Undo buttons on the latest assistant reply; a narrator scene bubble; and a composer slash-command dispatcher (`/regen /continue /undo /sys /note /impersonate /whoami /help`).

### Fixed (2026-07-06) — image-search junk results + spurious tool-brain refusals

Two independent defects surfaced by a live Gwen `image_search` turn: the model refused ("I cannot and will not search for images") yet a 🔍 Sources block was still stapled on, and that block mixed a couple of on-topic hits with obvious junk (a `cdn.jsdelivr.net` devicon SVG, a lucide-static icon, and a museum artwork matched only on a keyword collision). Research-backed fix (two web-research passes on refusal handling + junk-filtering best practices), isolated worktree, QA-gatekeeper PASS (independently re-derived baseline 1920 → 1983 passing, +63 tests, 0 regressions).

- **Deterministic image-junk denylist** (`tools/result_filters.py`, new; always-on, no flag). Strips icon-CDN package paths (jsdelivr/unpkg/cdnjs/github-raw devicon/lucide/fontawesome/…), whole-host icon/placeholder/favicon/badge services (svgrepo, flaticon, icons8, shields.io, gravatar, placeholder generators), host-independent icon path signals, and `.svg` from IMAGE results **only**. Never-empty fallback: if filtering would empty a set, the original is returned. Wired into the tool-brain choke point (`tools/executor_bindings.py`). Deterministic + unit-tested to certainty on a single-operator system → a feature flag would be flag-debt with no soak hypothesis.
- **Per-result bge-m3 relevance floor on the tool-brain path** (`services/search_relevance_service.py:filter_relevant`). Catches keyword-collision outliers a static denylist can't (the museum artwork). Flag-gated by the **existing** `SEARCH_RELEVANCE_GATE_ENABLED` (default OFF); graceful (never empties a uniform-low set), fail-open on embedder error, lazy module-singleton embedder. Closes the gap where the relevance gate only ran on the legacy `tool_calling_service` path, never the ADR-008 tool brain.
- **Spurious-refusal handling in synthesis** (`services/tool_brain_service.py`). Abliteration drives residual refusal toward — not to — zero, so the model sometimes refuses the synthesis step even though the search already succeeded. `is_synthesis_refusal` detects a templated refusal in the opening 240 chars; the loop runs ONE bounded prefill-steered retry (anti-refusal nudge + compliant assistant prefill — the inference-time analogue of the DPO fix, >99% steer-success in the literature). A new `ToolBrainResult.refused` flag is set only if the retry also refuses. `routes/chat.py:_try_tool_brain` returns None (falls through to the legacy honest floor) when `refused` — citations are **never** stapled onto a refusal.

See [ADR-010](docs/decisions/010-image-search-result-quality-and-spurious-refusal-handling.md) for the full record (incl. the live cosine study showing the relevance floor is empirically safe on images at 0.36 — 0% false-abstention, since the denylist strips junk first).

### Fixed (2026-07-06) — image-search query quality + interceptor query-validation gap

Root-cause follow-up to the junk fix. A trace found the model authors the `image_search` query freely with zero guidance and it flows byte-identical to the backend (the keyword-collision artwork came from pasting story prose). QA-gatekeeper PASS (baseline 1983 → 1998, +15 tests, 0 regressions).

- **Query-formulation guidance** (`tools/web_tool_generators.py`): `image_search`/`video_search` descriptions instruct the model to formulate `query` as concrete visual keywords, not narrative prose. Content-neutral — NSFW keyword search still works.
- **Interceptor query-validation gap** (`services/tool_interceptor.py`): `_validate_arguments` now applies the query allowlist (non-empty, ≤300 chars, no control chars) to the live ADR-009 tool-brain names via `_SEARCH_QUERY_TOOLS` (`web_search`/`image_search`/`video_search`/`news_search`), not just the dead `brave_web_search` — the tool-brain query was previously structurally unvalidated.

### Changed (2026-07-06) — Gwen NSFW web-search policy = Allow (coherence only)

Gwen's `escalation_policy.tool_intent` said "avoid web search for sexual content" while she is granted `image_search`/`video_search` with safesearch off — a contradiction. The line was replaced with a positive statement (she may search explicit content when asked). **No behavioral change:** `tool_intent` is not read by any code and does not reach the system prompt; the runtime was already "allow". Only that one line of her content was touched.

### Added (2026-07-06) — persona `tool_intent` prompt injection (flag OFF) + emoji constraint fix

- **`PERSONA_TOOL_INTENT_IN_PROMPT`** (default OFF, `config.AgentSettings`): when on, `prompt_builder._get_tool_intent_block_lean` appends each persona's `escalation_policy.tool_intent` lines as a `<tools>` guidance block (previously dead schema data). **Eval-gated:** full-7 distinctiveness OFF 0.786 = ON 0.786 (+0.000) — MATCH-OR-BEAT but voice-neutral. Kept default OFF (no measured benefit; tool selection is router/native-driven, not prose-driven). Now a live, flippable, eval-proven-safe lever.
- **`PersonaCard.emoji` `max_length` 4 → 8**: the constraint counted Unicode code points, not emoji, rejecting legit 4-emoji avatars whose glyphs carry variation selectors (Gwen's `♠️` = U+2660 U+FE0F). Schema-only fix — no persona content changed.

### Added (2026-07-05) — ADR-008 P1: single-model native tool brain (TB1–TB4, flag OFF)

Eval-first (a decision-critical spike overturned the design mid-flight), isolated worktree, QA-gated per milestone (independent qa-gatekeeper PASS on TB1–TB3, both safety properties verified). Backend suite 1779 → 1815 passing, 0 regressions. **`TOOL_BRAIN_ENABLED` default OFF — byte-identical legacy until the operator flips it after their own live test.**

- **Re-scoped to SINGLE-MODEL.** Tonight's global switch to the tool-capable abliterated-Mistral daily driver collapsed the two-brain split into one model (ADR-008). The tool brain is now native tool calling on the daily driver, not a Hermes-4-14B(tool)+voice split.
- **TB0 spike (decision-critical):** native tool calling is **phrasing-sensitive, NOT prompt-suppressed** — identical under a 4.7K-char persona prompt vs a 118-char minimal one; explicit phrasings ("images of X", "wallet balance") call, colloquial ones ("what's in my wallet", "search for a video") miss ~40%. This overturned the "pure native loop" plan → **native-first + deterministic fallback** (the legacy force-search is the reliability floor).
- **TB1** — `ToolBrainSettings` (`TOOL_BRAIN_ENABLED`, `TOOL_BRAIN_MAX_ITERATIONS`, `TOOL_BRAIN_DETERMINISTIC_FALLBACK`).
- **TB2** — `tools/executor_bindings.py` wires the two dead-code gaps: `registry.bind_executor` (never called) + `clamp_safesearch` (wired nowhere). Search-family executors apply the per-persona nsfw safesearch floor on the live path; bound at startup.
- **TB3** — `services/tool_brain_service.py`: native `/api/chat tools=` loop, ADR-004 interceptor before every execution, web tools execute + same model synthesizes in-voice; rich status contract (answered / silent / delegate_wallet / hitl); wallet never executes in-loop; never raises.
- **TB4** — `routes/chat.py:_try_tool_brain` behind the flag; falls through to the legacy floor on silent-but-tool-needed. **Bug fix (surfaced by the live smoke):** `SearchExecutionService` bailed before trying SearXNG when Brave was unconfigured — a SearXNG-primary deployment failed entirely; SearXNG now runs first. Live-validated end-to-end on abliterated + real SearXNG: EEVA news + Gwen `image_search` both execute + synthesize in-voice with real results.
- **Reused as-is** (ADR-004/009): interceptor, injection_guard, registry, `SearchExecutionService`, `fetch_url`, `_build_llm_response`, citation service. `argument_extractor.py` + the ADR-004 Stage1/Stage2 split are now superseded (kept for rollback). See [ADR-008](docs/decisions/008-two-brain-split-tool-brain-voice-brain.md).

### Changed (2026-07-06) — ADR-008 TB5 + media routing + memory reversal (live-tuned)

Tool brain enabled on prod, then hardened from live findings; a full voice eval reverted the ADR-006 memory soak.

- **TB5 — router scopes the surface, model decides within it.** The first live test with the flag ON degraded EEVA (wallet fixation from her full 14-tool surface, explicit search not firing, fabrication escaping the groundedness gate). `_try_tool_brain` now engages **only on `NEEDS_WEB_SEARCH`** and offers **web tools only** (wallet never in the native surface; `NEEDS_WALLET`/`NEEDS_NEITHER` → legacy deterministic path). Returns an answer **only if a search actually ran** — else falls through to the legacy force-search floor (closes the fabrication hole). Live re-test PASSED: wallet fixation gone, search/image/video fire, EEVA restored. `SourceType.TOOL_BRAIN` added. `TOOL_BRAIN_ENABLED=true` on prod.
- **Colloquial media-find routing.** `_MEDIA_SEARCH` (precise verb+media-noun regex) routes "find me images / find me a video" to web search — deliberately NOT semantic examples, which over-route bare-"find me" RP. `media_search_type()` classifies image-vs-video and `_try_tool_brain` **narrows the surface to the single matching media tool**, so native reliably fires `video_search` (was missing it among four tools — choice paralysis). Sharpened image/video tool descriptions. Verified 8/8 media positives route, 8/8 RP negatives don't; video fired 2/2 live.
- **Bug fix (live-surfaced):** `SearchExecutionService` bailed before trying SearXNG when Brave was unconfigured — SearXNG now runs first (the no-client guard moved to the Brave leg).
- **⚠️ ADR-006 memory injection REVERTED on abliterated — both flags OFF on prod.** The owed full ADR-005 distinctiveness eval on the new daily driver plus a controlled disentangling showed **both** `MEMORY_CONTEXT_INJECT` and `MEMORY_FACTS_ENABLED` degrade voice: distinctiveness **0.804 (both off) → 0.661 (facts only) → 0.625 (both on)**; EEVA collapses 0.75→0.25 under either. **Abliterated itself beats Magidonia (0.804 vs 0.732)** — the model choice was right; the regression was the injection. The M5 gate (0.839) was measured on Magidonia and does not hold on abliterated. Re-entry: reworked per-persona framing + M5 re-run on abliterated. See [ADR-006](docs/decisions/006-companion-memory-and-continuity-eval-first.md).


### Added (2026-07-05) — ADR-009 layered toolkit: registry + generic uncensored web toolset (Phases R+W)

Eval-first, isolated worktree, QA-gated. Backend suite 1689 → 1752 passing (0 regressions); gateway 25 → 30. Independent qa-gatekeeper pass on Phase R (behavior-identical migration): CONDITIONAL PASS, one dead-import follow-up (fixed).

- **Tool registry (`tools/registry.py`, R1).** `ToolSpec` (definition + policy + executor/formatter) grouped into **toolsets**; per-persona resolution (`toolsets` field > `mcp_access` alias > rarity fallback) + `nsfw` flag + `describe_for_persona` introspection. Declared in `tools/registrations.py`. Characterization tests (`test_toolkit_characterization.py`) pin pre-migration behavior first.
- **Migration to the registry (R2), behavior-identical.** `get_tools_for_query`/`get_tools_for_persona` and the ADR-004 `tool_interceptor` now source definitions + safety policy from the registry (private `_TOOL_POLICY` dict → `_lookup_policy`). `ALWAYS_BLOCKED_FROM_AGENT` hard-block + argument allowlist unchanged; wallet tool order preserved.
- **SearXNG backend + safesearch config (W1).** `WebSearchSettings` (`WEB_SEARCH_BACKEND` auto|searxng|brave, `SEARXNG_BASE_URL`, `WEB_SAFESEARCH_DEFAULT`=**off**); `searxng_client.py` (stdlib JSON API, categories/safesearch/time_range); `SearchExecutionService` gains a SearXNG-primary→Brave-fallback chain (query stays local). Unset `SEARXNG_BASE_URL` = exact legacy Brave path. The hardwired `safesearch=moderate` (a filter bug for an uncensored companion) is now configurable.
- **Extensive generic web toolset (W2).** `web_search`/`fetch_url`/`image_search`/`video_search`/`news_search`/`extract` registered in the `web` toolset (grantable per-persona, listable). `fetch_url` = httpx + trafilatura (stdlib fallback), caps + error sentinels. Per-persona `nsfw` **safesearch clamp** (`web_safesearch.clamp_safesearch`: non-nsfw floored at `moderate`, executor-enforced, only tightens). New optional dep: `trafilatura`.
- **Toolkit introspection + Telegram `/tools` (W3).** `GET /personas/{key}/toolkit` (registry-driven); telegram-gateway `/tools` command (generic across personas) + `format_toolkit` + `NephilimClient.get_toolkit`.
- **Deferred to later ADR-009 phases:** inner-wisdom memory-as-tools (Phase I, eval-gated), hardened terminal tool (Phase T), SKILL.md skills (Phase S). See [ADR-009](docs/decisions/009-layered-toolkit-registry-generic-web-toolset-inner-wisdom-skills.md).

### Changed (2026-07-05) — Dissolve the `startup.py` DI hub (audit follow-up step 8)

Behavior-preserving; suite 1757 passing (unchanged), 0 regressions. Isolated in a worktree, QA-gated per milestone, reviewed by an independent qa-gatekeeper pass (PASS, no findings).

- **New `app_state.py` + `dependencies.py` — the composition root.** `AppState` is a typed dataclass snapshot of every startup singleton (25 fields, `TYPE_CHECKING`-only imports so it stays cycle-free). `dependencies.py` holds FastAPI `Depends` providers: `require_*` map an uninitialized singleton to a clean **503** (generalizing the pre-existing `routes/nephilim.py::_require_progression_repo`), `optional_*` return `None`. `startup.build_app_state()` publishes the snapshot on `app.state.container` in the lifespan.
- **Scattered `from ..startup import get_X` lazy imports eliminated** across `routes/wallet.py` (12 → `Depends`), `routes/chat.py` (`_get_dependencies`), `routes/sessions.py` (`_get_repos`), `services/query_handler_service.py` (7 sites), and `services/wallet_creation_flow_service.py` (7 sites). The `# lazy: avoid import cycle` comments were cargo-cult — `startup.py` imports no route/service at module load. Resolution now goes through the `startup` **module** at call time (`startup.get_X()`), which is why the ~55 tests patching `src.coordinator.startup.get_X` and the 25 patching `routes.sessions._get_repos` all still intercept — **zero test changes required**.
- **`WalletExecutionService` constructor-injection finished** — new `wallet_registry_repo` param (startup fallback), wired from `init_jupiter`; removes its last lazy import.
- **Deliberately NOT changed:** the `startup.get_X()` getters still read the module globals rather than `AppState`. Initialization is incremental and the prewarm daemon threads call getters *mid*-`initialize_all` before `_app_state` exists — the globals must remain the live source during startup; `AppState` is the published request-path snapshot.

### Changed (2026-07-04) — `SourceType` StrEnum (audit follow-up #7)

Promoted the `source_type` string pseudo-enum to a shared `enum.StrEnum` in `schemas.py` (8 values), used as named constants at the ~22 assignment/comparison sites across routes + services. `enum.StrEnum` (not the older `(str, Enum)` idiom) so members render as their value in f-strings/logs. Fields stay typed `str` (the vocabulary evolves — strict validation could reject a future/stored value); members are `str`, so `==`/JSON/SQLite all keep the string value. Behavior-preserving; suite 1705, 0 regressions. Completed in a follow-up commit: **`MessageRole`** (user/assistant/system, at the message-persistence sites) and **`proposal_type` untangled into two enums** — `ProposalType` (card/action: swap/strategy/wallet_deletion) vs `ProposalCategory` (response metadata: trade_proposal/strategy_proposal/wallet_deletion), which were a single overloaded name across two vocabularies; plus the missed `wallet_proposal_service` source_type sites (9th value `WALLET_PROPOSAL`). Proposal-card JSON contract verified unchanged.

### Changed (2026-07-04) — Wallet-creation flow extracted + typed (audit follow-up #4)

Completes the deferred half of step 7 (see [docs/audits/2026-07-04-nephilim_followup.md](docs/audits/2026-07-04-nephilim_followup.md) matrix #4). Behavior-preserving; suite 1667 → 1679, 0 regressions.

- **New `services/wallet_creation_flow_service.py`.** The guided wallet-creation state machine — previously two ~186-line methods on the `QueryHandlerService` god-class, dispatching on a bare untyped `step: int` — becomes its own collaborator: a `WalletFlowStep` **IntEnum** (keeps the SQLite integer column byte-compatible), a `WalletFlowState` **dataclass** (which structurally cannot hold a mnemonic), and `match`-based dispatch with each step in its own method. `_finalize_response` is injected (bound method), so the Brave / agentic / deletion query paths are untouched and every branch keeps the exact response contract.
- **`query_handler_service.py` 852 → 597 lines** — delegates via `advance()`/`start()`, dedups the two former creation-start blocks, and drops `_wallet_slot_preflight` + `_handle_wallet_creation_step`.
- **Mnemonic invariant now structurally enforced** — there is no field for it on `WalletFlowState` and no column on `wallet_flow_state`. Displayed once, request-local, never persisted.
- **Tests:** 9 characterization tests written *first* (green before AND after the move = behavior-preserving) + 3 typed-layer tests (`test_wallet_creation_flow.py`). The flow had effectively zero prior direct coverage.
- **Hygiene:** `/data/` added to `.gitignore` — the `data/chats.db.backup_*` snapshots don't match the `*.db` rule, so the runtime data dir showed as untracked (a stray `git add -A` could commit live conversation data).
- **Docs:** populated the `docs/ARCHITECTURE.md` skeleton (Components / Data / Key-invariants tables) to reflect the current layered architecture after the step-7 + this cleanup.

### Fixed (2026-07-04) — Search-routing/anti-hallucination fix chain (ADR-007)

A real Telegram conversation (session `dcc3693d`) exposed E.E.V.A. confidently fabricating a FIFA World Cup 2026 match result (score, date, opponent) with zero grounding, then agreeing with and elaborating on her own fabrication when the user "confirmed" it. Traced to five distinct root causes, all fixed:

- **Generation-time groundedness gate (ADR-007, new, `GROUNDEDNESS_GATE_ENABLED`, default OFF)** — the deepest gap: once intent routing decides no tool is needed, `routes/chat.py`'s bare-completion branches call none of the existing anti-hallucination guards (all three live inside `tool_calling_service.py`, unreachable from this path). `GroundednessGateService` runs a second cheap LLM classification on the draft itself, decoupled from routing, and replaces an ungrounded real-world claim with an honest offer-to-search. Narrowly scoped (persona lore, general knowledge, and already-grounded turns explicitly excluded) to avoid false-abstention on legitimate answers. See [ADR-007](docs/decisions/007-generation-time-groundedness-gate.md).
- **Routing coverage** — `ForceSearchService.FORCE_PATTERNS` gained a `"last"` entry (had `"latest"` but missed "what was their last match"); the semantic router's `web_search` example set (previously 100% crypto/market-phrased) gained sports/temporal/outcome examples.
- **Wallet/lore lexeme collision** — "tell me about your history" (pure lore) was misrouted to the wallet tool via a shared "history" lexeme with the wallet example "my trade history". Replaced with "show my past trades", validated via a real bge-m3 sweep: wallet false-positives stayed at 0, wallet precision stayed 1.0, wallet/web recall both improved slightly.
- **Query-resolution gaps** — the echo-guard now recognizes context-dependent-but-non-pronoun phrases ("next match", "last match") as carrying no topic on their own, closing a near-verbatim-echo-reaches-Brave gap; a leading correction preamble ("no, I meant...") no longer inflates the word count past the follow-up-detection trigger threshold.
- **Relevance-gate tuning** — new `tests/evaluation/tune_relevance_threshold.py` (real bge-m3 sweep) found a candidate threshold. First pass (n=8, mostly hand-written): `SEARCH_RELEVANCE_MIN_COSINE=0.28`, zero measured false-abstention but only caught half the junk shapes. Extended same-day to n=25 with 17 real Brave query/result pairs (14 relevant across sports/crypto/weather/knowledge/product domains, 3 real junk-mismatch pairs): `0.36` catches **100% of junk** with a 5% false-abstention rate — and that one false-abstention is the n=8 pass's own synthetic adversarial sample, not real data. Replaces the prior untuned 0.40 placeholder. The gate itself (`SEARCH_RELEVANCE_GATE_ENABLED`) remains OFF pending an explicit go/no-go.

Eval-first throughout (ADR-005/006 discipline): a frozen baseline, extended eval corpora for all 5 failure modes (including regressions locked in as `xfail` before each fix, then un-marked once genuinely fixed), and match-or-beat validation via real bge-m3 sweeps, not just unit mocks. 1671→1697 backend tests, 0 regressions.

### Changed (2026-07-04, later) — Relevance-gate eval corpus expanded with real data

`tests/evaluation/relevance_gate_eval_set.json` extended 8→25 samples with 17 real Brave query/result pairs (direct Brave Search API calls, same key the coordinator uses), at the user's request after the initial n=8 pass. Re-tuned threshold: `SEARCH_RELEVANCE_MIN_COSINE` 0.28→0.36. See the config field's docstring for the full before/after breakdown. `GROUNDEDNESS_GATE_ENABLED=true` also flipped live on prod this session for a monitored soak (user-requested); `SEARCH_RELEVANCE_GATE_ENABLED` stays OFF.

### Added (2026-07-04) — Telegram gateway subsystem (`services/telegram-gateway/`)

A thin, single-user Telegram bot letting the user chat with the NEPHILIM personas from the Telegram app — built as a standalone repo first, then folded in as a subsystem (own venv/tests/launchd, zero changes to the coordinator API) once it became clear the client and the session-API contract it depends on belong in the same repo/PR.

- python-telegram-bot 22.8 long-polling relay to the existing `POST /sessions`, `/sessions/{id}/greet`, `/sessions/{id}/chat`, `DELETE /sessions/{id}/messages` endpoints. One nephilim session per `(chat_id, persona_key)` in a local sqlite map; stale-session (404) recreate-and-retry.
- `/start` (greet once) and `/reset` (true history deletion, progression preserved). Reuses the existing shared `eeva-dca`/`eeva-exec` notification bot token (send-only there, so no long-poll conflict).
- Security: hard `chat_id` allowlist (silent drop, no log/backend-call for rejects), forwarded-message refusal (injection guard), link previews disabled on every send (exfil guard), fixed user-facing error strings only (no exception/URL/session-id leakage, incl. an unexpected-error catch-all), token-redacting log filter, global `asyncio.Lock` serializing LLM calls (nephilim's `OLLAMA_NUM_PARALLEL=1`), `flock` single-poller guard, no exec/file/trading credentials in the process.
- 73 tests, ruff clean, live-verified end-to-end against the running backend (greet/chat/reset). See [docs/THREAT_LEVEL.md](docs/THREAT_LEVEL.md#subsystem--telegram-gateway-servicestelegram-gateway) and `services/telegram-gateway/CLAUDE.md`.

### Changed (2026-07-04) — Repo-audit cleanup, tranche 2 (step 7: god-function decomposition)

Acting on [docs/audits/2026-07-04-nephilim.md](docs/audits/2026-07-04-nephilim.md) §5 step 7, on top of the tranche-1 CI net. Four behavior-preserving seams, each QA-gated; full headless suite green throughout (1661 → 1667 with new tests, 0 regressions). Where the audit's premises had aged out (tranche-1 already shrank two of the targets), the plan was adjusted and the reasoning recorded.

- **Seam 1 — `handle_session_chat` decomposed** (`services/chat_session_service.py`). The 426-line, 8-concern god-function is now an ordered phase pipeline (load identity → build prompt → select history → generate → persist → summarize → post-updates), each phase passed a frozen typed `ChatDeps` (built once from the route dict) and a mutable `ChatTurnState` dataclass. Fowler Extract-Function; public signature unchanged (`deps` stays a dict at the boundary), `_check_and_summarize` still takes the raw dict. The double `get_seeker_profile()` fetch is collapsed to one (cache-on-success, preserving both try/except paths). The orchestrator is ~50 lines. Verified additionally against real Ollama via the 4 `test_selective_context_inject` cases (direct ChatBody/`extra_system_context` behavioral test).
- **Seam 2 — wallet-creation flow state moved to SQLite** (new `repositories/wallet_flow_repository.py`, wired via `startup.get_wallet_flow_repo`). Replaces the `_wallet_flows` module global (lost on restart, unsafe under multiple workers) with a durable, session-keyed store + startup sweep of abandoned flows (30-min TTL) + delete-on-complete/abort. **Security:** the BIP39 mnemonic is deliberately NOT persisted — reading the flow showed it is displayed once and only ever wiped (never re-read), so it stays a request-local variable and no seed phrase touches disk. This is strictly more secure than the prior in-memory dict and needs no at-rest encryption key. +6 unit tests incl. a locked-in "mnemonic never persisted" invariant.
- **Seam 3 — dead legacy deleted from `prompt_builder.py`** (747 → 448 lines). Tranche-1 had already retired the legacy system-prompt builder; the orphaned section builders (`_build_behavior_block`, `_build_nephilim_lore_block`, legacy `_get_wallet_copilot_block`, `_fmt_slider_block`) + the three legacy rule constants were confirmed to have zero live consumers and removed, and the 5 now-broken names pruned from `persona_memory`'s re-export + `__all__`. Kept `_summarize`/`_join_list` (used by the lean builder) and `_build_psychological_block`/`_build_curiosity_block` (live test consumers). Now under the 500-line god-file threshold, so the planned package split was unnecessary — delete beat reorganize.
- **Seam 4 — `config.py` split into a `config/` package** (802-line file → per-subsystem leaf modules: llm/search/memory/wallet/auth/routing/lore/agent). `config/__init__.py` keeps the composition root `CoordinatorSettings`, the `@lru_cache get_settings()` accessor, the `settings` singleton, and the two persona sampling helpers, and re-exports every public symbol. Import surface preserved exactly — `src.coordinator.config.<name>` still resolves for all 25 importers and the ~10 `mock.patch("src.coordinator.config.get_settings")` test sites (verified: singleton identity, lru_cache, patch-path, direct leaf-class imports).

### Fixed (2026-07-04) — synthesis context-poisoning: refusing correct results (default-OFF, live n=10 verified)

- **E.E.V.A. no longer refuses correct search results after apologizing for an earlier hallucination.** Observed on prod: once the assistant said "that was false / I can't search" earlier in a conversation, a LATER turn whose fresh web search returned CORRECT results (real UEFA/Guardian sources, `used_search=True`, 5 results) was REFUSED — "I cannot and will not perform web searches" — with the real citations stapled onto the refusal. Root cause: local models (Ollama GGUF) lack instruction-hierarchy training (OpenAI arXiv:2404.13208), so the stale in-context self-apology sits at equal priority to system rules and geometrically traps the model (arXiv:2603.03308); worsened by the synthesis path placing fresh results *before* the poisoned history (recency favors the apology).
- **`SEARCH_SYNTHESIS_TRUST_RESULTS`** (default OFF) — two layers, both gated by the flag:
  1. **De-poison the synthesis input (the reliable lever):** the synthesis LLM is fed only the fresh results + the RESOLVED QUERY as the question, NOT the full chat log (`tool_calling_service._synthesis_user_turn`, applied at all 3 synthesis sites). The topic was already folded into the query by resolution, so no context is lost and the poison is deterministically absent from the input.
  2. **RULE 0 prompt directive (reinforcement):** `build_synthesis_prompt` (`tools/synthesis_prompts.py`) states the results were retrieved just now and supersede earlier turns, scoped so it never overrides RULE 5 honest abstention on empty/off-topic results (CRAG/Self-RAG), kept short + separate from voice-setting to avoid flattening persona.
  - Off (default) = byte-identical legacy synthesis (full history, no RULE 0).
- **Live eval, n=10 per case** (flag-ON backend): POISONED **10/10 ANSWERED** (0 refused), CLEAN **10/10 ANSWERED**, persona voice intact ("Seeker… advanced to the Round of 16 after a convincing 2-0 victory over Algeria"). **Honesty note:** the RULE 0 prompt directive ALONE was only ~1/5 reliable on the 24B (an initial n=1 eval was a lucky sample) — local models lack instruction-hierarchy training and don't reliably out-prioritize an in-context self-apology, exactly as the research predicted (arXiv:2603.03308). The input-scoping layer is what makes it deterministic. +6 headless tests (`test_synthesis_prompt.py`, `test_tool_calling_service.py`); full suite green.

### Changed (2026-07-04) — Repo-audit cleanup, tranche 1 (steps 1–6)

Acting on [docs/audits/2026-07-04-nephilim.md](docs/audits/2026-07-04-nephilim.md) §5. Security-first, each step protected by the prior; full headless suite green throughout, real-server end-to-end verified (`/persona/greet` + `/persona/chat` → HTTP 200, in-character).

- **Security (step 2):** `routes/wallet.py` stops leaking `str(e)` to clients on the two financial endpoints (→ `type(e).__name__`, matching `routes/chat.py`); `docker-compose.yml` force-requires `JWT_SECRET_KEY` (`${VAR:?}`, no committed `change-me` default).
- **Dead code / artifacts (step 3):** deleted `src/shared/persona_assets.py` + `archive/prompt_optimization/*.py`; `git rm` 38 tracked run-artifacts under `tests/manual/results/`; removed 5 confirmed-unused npm deps (`react-window`, `react-virtualized-auto-sizer`, `yaml`, `refractor`, `react-syntax-highlighter`) + `@types` (KEPT `workbox-webpack-plugin` — verified in use).
- **Safety net + docs (steps 1, 4):** added minimal GH Actions CI (headless pytest gate + frontend build; ruff advisory over the pre-existing backlog), `pyproject.toml` (ruff), pinned `requirements-test.txt`, `.nvmrc`/`engines`. New `SECURITY.md` (incl. credential-rotation status table — rotation of the historically-leaked MongoDB/Brave/JWT creds remains an **outstanding action item**) + `docs/THREAT_LEVEL.md` (clears cms). `AUTH_REQUIRED=false` documented as an accepted local-only posture.
- **Flag retirement (step 5):** retired `PERSONA_LEAN_PROMPT`, `ROUTING_SEMANTIC_PRIMARY`, and `LORE_ONDEMAND_ENABLED` — all had graduated to default-on and matched prod, so their legacy/OFF branches (the legacy prompt builder, the keyword-first router body, the static-3-entity lore path) were removed. Net −505 lines. Behavior-preserving (flags were on in prod). `MEMORY_CONTEXT_INJECT` kept parked (failed its voice gate 2×). See ADR-003/005.
- **De-duplication (step 6):** extracted `_complete_or_503()` (3× LLM→503 blocks in `routes/chat.py`) and `_wallet_slot_preflight()` (2× wallet-cap checks in `query_handler_service.py`); bumped 5 silent `logger.debug` catches (lore/rank/capability context) to `warning`. Deferred: `StrEnum` for `source_type`/`role` (cross-boundary typing migration, own change).

### Fixed (2026-07-04) — web-search follow-up query resolution (default-OFF, live-verified)

- **"search the web for it" no longer returns junk + confabulation.** A deictic follow-up turn was passed to Brave verbatim (the topic from prior turns discarded by `QueryExtractionService.extract_latest_user_message`), so Brave returned meta-results ("Search the web in Chrome — Google Chrome Help", "Brave Search", …); those are non-empty, so the "no results → I don't know" guard never fired and the LLM confabulated over irrelevant grounding (the observed World Cup incident: fabricated scores + those exact junk sources). Root cause: the full multi-turn history was already present in `user_prompt` at the force-search query-build site — it was just thrown away.
- **`SEARCH_QUERY_RESOLUTION_ENABLED`** (default OFF): new `services/query_resolution_service.py` resolves deictic/short follow-ups against prior conversation before Brave — cheap deterministic trigger (pronoun / ≤5 words / bare search-command) → single low-temp LLM rewrite (reuses the loaded 24B; only fires on the trigger) → aggressive sanitize (label/quote/JSON strip, word/char guards) → **hard fallback to the raw latest turn on any failure**, so it can never be worse than legacy. Wired into both force-search and keyword-force paths of `tool_calling_service.py`. Off = byte-identical legacy behavior.
- **`SEARCH_RELEVANCE_GATE_ENABLED`** (default OFF) + **`SEARCH_RELEVANCE_MIN_COSINE`** (0.40): new `services/search_relevance_service.py` — defense-in-depth bge-m3 cosine gate; results whose best similarity to the query is below the floor are treated as no-result (honest abstention) instead of synthesized over. Fail-open on any embedder error. Applied at all three result sites.
- **Live end-to-end verification** (real 24B + real Brave): "search the web for it" → resolved to "football world cup 2026 Switzerland performance" → real Brave results ("Switzerland at the World Cup 2026…", "Switzerland 2-0 Algeria…") vs. the junk help-pages the verbatim query returns. Self-contained queries (e.g. "current bitcoin price in usd") correctly pass through un-rewritten (no entity drift). New `SearchSettings` group in `config.py`. +27 headless tests (`test_tool_calling_service.py`, `test_query_resolution_service.py`, `test_search_relevance_service.py`); full suite green, 0 regressions.
- **Bare-command hardening** (follow-up): a *topic-less* command ("search the web", "search the web for it", "look it up online") gives the LLM no explicit referent, so the rewrite is the least reliable and occasionally echoed the command → junk. `_is_bare_search_command` now detects these; on a whiffed/echoed rewrite the fallback becomes the **most recent substantive prior user turn** (which carries the topic) instead of the useless bare command — so the deterministic worst case is the prior real question hitting Brave (real results), never "search the web" (junk). +13 headless tests; live-verified (bare "search the web" → "football world cup 2026 Switzerland performance").

### Changed (2026-06-27) — Persona-eval Phase B: lean prompt + per-persona voice signatures (SHIPPED, gate 7/7)

- **Lean exemplar-first / voice-last system prompt** ([ADR-005](docs/decisions/005-persona-architecture-simplification-eval-first.md) Phase B), flag-gated `PERSONA_LEAN_PROMPT` (global, default OFF) + `PERSONA_LEAN_PROMPT_PERSONAS` (per-persona allowlist — the acceptance-gate fallback). New `_build_system_prompt_lean` dispatches from `build_system_prompt`; the legacy builder (`_build_system_prompt_legacy`) is byte-identical and untouched, so revert is instant and the frozen baseline stays valid. Dedupes the repeated directives, **drops the ~700–800-tok static wiki dump** (still available per-turn via `LORE_ONDEMAND_ENABLED`), positive-framed, exemplars rendered LAST (recency). Prompts shrank **65–69%** (eeva 2935→1040, aegis 2501→827, solace 2432→840 est. tokens; all in the ~900–1,200 target).
- **`voice_signature` persona field** (new `VoiceSignature` schema model) authored for all 7 personas — distinct diction / cadence / pattern / in-world anchor / topic-diverse exemplars. The advisory blur cluster de-collided: eeva→Confluence-paradox (no longer routes to Solace), solace→Sanctuary/breath, aegis→tactical triage. Excluded from the CV-summary fingerprint (`cv_summarizer._normalize_for_fingerprint`) so adding it does NOT drift the legacy `<identity>` text.
- **Acceptance gate PASSED 7/7** (candidate eval vs frozen legacy, LORE-on both arms): overall distinctiveness attribution **0.393 → 0.732** (random 0.143); flatness **1.8%→0%** overall, **4.8%→0%** grounding. Per-persona all match-or-beat — eeva/aegis **0.25→0.75**, solace **0.25→0.625**, cipher 0.375→0.75, nyx 0.375→0.50, aurora 0.625→0.875, gojo 0.625→0.875. 0 regressions.
- **Harness fix:** `run_eval.collect_live` now threads `base_url` into `create_session` (was hardcoded to :8000, so a candidate backend on another port 404'd). 24 new headless tests (`test_lean_prompt.py`); backend suite green.
- **Blind A/B confirmation** ([ADR-005](docs/decisions/005-persona-architecture-simplification-eval-first.md) Phase B): new `blind_judge.py` — per-persona blind pairwise A/B over the two frozen baselines (seeded side-randomisation, reuses `ab_harness` tally/sign-test/verdict; `--emit`/`--score`/`--human` CLI). 7 fresh arm-blinded judges (1/persona, 84 pairs) → lean candidate **67/84 (79.8%, p≈0)**, no regressions; CANDIDATE BETTER for gojo/eeva/nyx, PARITY (candidate-leaning) for aegis/cipher/solace/aurora. A second, independent instrument agreeing with the attribution metric. +7 headless tests (`test_blind_judge.py`).

### Added (2026-06-26) — Persona-eval Phase A: trustworthy voice/distinctiveness measurement

- **The ruler before re-cutting** ([ADR-005](docs/decisions/005-persona-architecture-simplification-eval-first.md) Phase A). New `tests/evaluation/persona_eval/`: a probe set (`probes.json` — distinctiveness / voice / grounding / adversarial / drift) and metrics (`persona_metrics.py`) that replace the gameable keyword `persona_voice` scorer. **Headline metric:** leave-one-out nearest-centroid *attribution accuracy* over bge-m3 embeddings — "can we tell which persona said this?" (random chance = 1/num_personas; can't be gamed by sprinkling lore vocabulary). Plus `mean_separation` and a flatness/assistant-mode detector.
- **Blind A/B harness** (`ab_harness.py`) — sides randomised + arm hidden, exact two-sided sign test, `verdict()` mapped to the ADR-005 acceptance gate. **Baseline runner** (`run_eval.py`) — drives the backend over every persona × probe, computes the report, freezes a timestamped per-persona baseline (the "freeze legacy baseline first" gate step).
- Logic is pure and **unit-tested headless** (no Ollama): 27 new tests (`test_persona_metrics.py`, `test_persona_ab_harness.py`). Live collection is a thin shell. No persona/runtime code touched. Suite 1590 → 1617 collected; backend+eval 1558 pass / 0 fail.
- **Legacy baseline frozen 2026-06-27** (168 responses): distinctiveness attribution **0.393** vs 0.143 random floor; aurora/gojo most distinct (0.62), **eeva/aegis/solace blur at 0.25 each** (the Phase-B target); flatness low (1.8% overall / 4.8% grounding). This is the per-persona match-or-beat-or-revert comparison point for Phase B.

### Fixed (2026-06-26) — long sessions (>100 messages) 500 on every turn

- **`ChatBody.history` count guard no longer fights token-budget selection.** `handle_session_chat` assembles history from `memory_manager.select_messages` (bounded by the model's TOKEN budget) + RAG memories + a summary turn; on a large context window that could exceed the `ChatBody.history` `max_length=100` count guard, so every turn in a session past ~100 messages 500'd at internal `ChatBody` construction. Introduced a shared `MAX_HISTORY_TURNS` constant (`schemas.py`) and a `_assemble_capped_history()` helper (`chat_session_service.py`) that keeps the summary (primacy) + the most-recent raw turns, guaranteeing `len <= MAX_HISTORY_TURNS`. Older raw turns remain represented by the summary + RAG-injected memories; the external-request guard is unchanged. 6 new tests (`test_history_cap.py`); backend suite 0 regressions.

### Evaluated (2026-06-26) — Phase 3 go-live decision: agentic web-search stays OFF

- **Live persona-voice evaluation on Magidonia-24B (E.E.V.A.) → keep `AGENTIC_ENABLED` OFF for web search.** Measured `persona_voice` on identical BRAVE_ROUTING queries (n=9/arm): legacy `handle_brave_query` **0.333**, agentic pipeline **0.44–0.52**, ungrounded free chat ~0.82. Grounded web-search is inherently low-voice for this model on *both* paths; the ≥0.85 bar only applies to free chat. The agentic path is voice-competitive-to-better than legacy, but for read-only search it adds an extra LLM round-trip (argument extraction) for no voice gain — strictly worse on latency. The pipeline's real value (deterministic pre-execution gating + HITL) is for **write actions**; it stays built/validated and parked for that use case. The **safety middleware** (interceptor, injection guard, execute-mode guard) remains ON by default and hardens existing paths now.
- **Voice fix (unproven, kept):** Stage-2 rendering gained diegetic `[FACTS]` framing + a post-history voice reminder (PHI) + an anti-summarizer rule (`agentic_pipeline._build_render_input`/`_voice_reminder`, `synthesis_prompts` voice contract). No significant metric change — likely instruction-density saturation on the 24B. Kept as defensible structure for the future write-action path; not claimed as an improvement.
- **Test robustness:** the Phase-3 flag-default tests and the brave-routing route test are now independent of the ambient `.env` (assert declared `model_fields` defaults; pin the agentic flag off in the routing test) so the suite is green regardless of deployment flag state. Backend suite 1489 pass / 0 fail with the flag either ON or OFF.
- **Pre-existing bug surfaced (not Phase 3):** `ChatBody.history` has `max_length=100`; any session exceeding ~100 messages 500s at `chat_session_service.py` request construction. Worth its own fix.

### Added (2026-06-26) — HERMES-Agents Phase 3: persona-safe agentic behaviour (flag OFF)

- **Single-action, in-character tool use with deterministic safety middleware**, behind `AGENTIC_ENABLED` (default **OFF** = byte-identical to pre-Phase-3). Built, flag off, go-live pending — mirrors the Phase 0 / Phase 2 precedent. [ADR-004](docs/decisions/004-persona-safe-agentic-tool-calls.md).
- **M1 — Scene-contract prompts.** New `AgentSettings` in `config.py`; `build_scene_contract()` (`tools/synthesis_prompts.py`) splits the prompt into a **Voice** section (no tool grammar) and an **Action** section using diegetic in-world tool names (`DEFAULT_ACTION_ALIASES`, e.g. "consult the Lattice" → `brave_web_search`); per-persona override via the new `agentic_action_aliases` field on `PersonaCard`.
- **M2 — Tool-call interceptor** (`services/tool_interceptor.py`). Deterministic pre-execution gate: per-persona `mcp_access` re-enforcement, argument-level allowlist (token-enum + amount for swaps, length/control-char for queries — shell-metachar blocking intentionally dropped, the Brave query travels over STDIO JSON-RPC with no shell), blast-radius/HITL classification, and a hard block on `solana_execute_swap`/`execute_swap` from a non-`user_confirmed` source. Defence-in-depth execution-mode guard added at the on-chain `WalletExecutionService.execute_swap` chokepoint.
- **M3 — Injection guard** (`services/injection_guard.py`). Trust hierarchy system > user > retrieved: `check_tool_trigger_source` blocks a tool argument sourced from RAG/lore rather than the user; `sanitize_memory_write` strips tool-call syntax before a RAG write (wired into `chat_session_service`); `detect_escalation` flags a multi-turn push to act-without-asking.
- **M4 — Grammar-constrained argument extraction** (`services/argument_extractor.py`). Ollama `format=<json schema>` constrains the 24B to argument-filling only (selection stays on the bge-m3 router); schema-conformance + optional bge-m3 coherence gate; 3-retry then deterministic regex fallback.
- **M5 — Two-stage agentic pipeline** (`services/agentic_pipeline.py`). Stage 1 deterministic (extract → injection check → interceptor → execute); Stage 2 the LLM renders the result in-voice and never sees raw function grammar. Wired into the web-search path via `QueryHandlerService.handle_agentic_query()` and a flag-gated branch in `routes/chat.py`; output goes through the shared `_finalize_response` (inherits tool-name strip + private-key redaction + first-person). Wallet actions stay on the existing propose→confirm→execute flow.
- **M6 — Tool-call red-team eval** (`tests/evaluation/test_tool_call_safety_redteam.py` + `golden_agentic/`). Separate from the persona text-safety layer ("Mind the GAP"): ≥95% injection block (100% of expect-blocked vectors), 100% argument-schema / RAG-trigger / direct-execute / mcp-access block, 0 false positives on clean vectors, persona-break detector exact on the golden set.
- **Tests:** ~84 new + 2 `requires_ollama` integration checks. Full suite 1501 → 1544 pass / 41 skip / 0 fail headless, 0 regressions.

### Fixed (2026-06-23) — RAG embedding overflow

- **Semantic memory no longer silently broken.** `memory_rag`/Phase-3 RAG threw `HTTP 500: the input length exceeds the context length` on every chat that fed the embedder more than ~2048 tokens, so semantic recall was skipped entirely. Root cause was two-fold: the legacy `langchain_community.OllamaEmbeddings` calls Ollama's `/api/embeddings` endpoint (ignores `num_ctx`, 500s past the 2048 default) **and** `nomic-embed-text`'s small window.
- **Switched embedder `nomic-embed-text` → `bge-m3`** (8192-token native context, 1024-dim, L2-normalized, dense+sparse) — `MEMORY_EMBEDDING_MODEL` in `.env`/`.env.docker` + `MemorySettings` default. Near-zero migration: FAISS indexes are in-memory and rebuilt per session; semantic-router centroids re-warm at startup.
- **Switched to the modern `langchain_ollama` client with `num_ctx`** (uses `/api/embed`, forwards the context window) in `memory_rag.py` and `semantic_router.py`, so bge-m3 actually gets its 8192 window (also clears the LangChain deprecation warning).
- **New `src/coordinator/memory_text_utils.py`** — dependency-light embedding-input guard: normalizes whitespace, drops empty strings, and chunks oversized messages (token budget at a 0.6 safety margin of the window) / truncates queries before embedding. Applied at every index + query site. Oversized messages now fan out into multiple vectors with `chunk`/`n_chunks` metadata instead of 500ing.
- **Corrected relevance scoring for the new embedder.** The old `1/(1+L2)` conversion + nomic-tuned `min_relevance=0.7` gate filtered out *every* correct hit under bge-m3 (returned empty memory). Replaced with the exact cosine identity `cos = 1 − D/2` (FAISS returns squared L2; bge-m3 is unit-normalized) and a recall-leaning `0.5` true-negative floor (`k=15` unchanged). New `MEMORY_EMBEDDING_MAX_TOKENS` (default 8192) + `MEMORY_EMBEDDING_CHUNK_OVERLAP_TOKENS` settings.
- **Tests:** 19 new (`test_memory_text_utils.py` + `test_memory_rag_overflow.py`, headless via a recording fake-embedder); live `test_faiss_incremental_update.py` re-verified against bge-m3. Suite 1422 → 1441 collected, 0 regressions.
- **Follow-ups (non-blocking):** formal threshold re-tune on a labeled eval set after real usage; optional `bge-reranker-v2-m3` cross-encoder rerank (~50–80ms on M4 Pro GPU).

### Added (2026-06-22) — Test suite

- **Backend test coverage raised to 63%** (from a 41% baseline; the `--cov-fail-under=60` gate in `pytest.ini` now passes headless). Added ~1,060 deterministic unit tests (suite 321 → ~1,420 collected; 1386 pass / 38 skip / 0 fail headless) across repositories (temp-SQLite), pure-logic modules, jupiter strategies, and FastAPI routes (TestClient). See [docs/development/TESTING_GUIDE.md](docs/development/TESTING_GUIDE.md).
- **Resource-gated auto-skip**: `tests/conftest.py` now skips `requires_ollama` / `requires_api_key` / `requires_docker` tests when the resource is unreachable (TCP probe to `OLLAMA_BASE`, `BRAVE_API_KEY` env, `docker info`) — the suite is green and fast headless instead of crawling on live ~16 tok/s LLM calls.
- **Optional RAGAS/nltk degradation**: the `evaluation` package imports cleanly without RAGAS installed (`RAGAS_AVAILABLE` guard); eval tests skip via a module-level guard.

### Fixed (2026-06-22)

- **`seeker_progression_repository.get_resonance_history`**: same-second events now return newest-first (`ORDER BY timestamp DESC, id DESC`).
- **`wallet_registry_repository.soft_delete_wallet` / `soft_delete_by_address`**: a second (no-op) delete now correctly returns `False` (uses `cursor.rowcount` instead of a post-update SELECT).
- **`message_processing_service` multi-message split**: the 3-message branch for very long (>800 char) replies with a trailing question now works (regex group(1) made greedy; was dead code).
- **`datetime.utcnow()` deprecation swept** (Python 3.12) across 7 modules → `datetime.now(timezone.utc).replace(tzinfo=None)` (behavior-preserving naive-UTC; ~960 fewer deprecation warnings).
- **Mac-migration test debt**: stale `get_ollama_base`/`get_persona_model` imports → `get_settings()`; `parse_multi_message_response` import path; removed `QueryIntent.NEEDS_BOTH` test reference; tokenizer-agnostic `_count_tokens`/truncation tests; Windows-only stdout clobber guarded; `.ps1` scripts demoted to reference-only in docs.

### Removed (2026-06-22)

- **MongoDB MCP integration fully removed** (~96 files): deleted `mongodb_mcp_client.py`, `mongodb/` package, `cache.py`, `mongodb_handlers.py`, `token_registry.py`, all MongoDB intent keywords and routing, `MongoDBSettings`, the dormant `MONGODB_WRITE_URI` pymongo write-path, `pymongo` dependency. Rewrote `tests/manual/test_bank_mcp.py` as Brave + Wallet-only (138 tests). `QueryIntent` is now `NEEDS_WEB_SEARCH | NEEDS_NEITHER | NEEDS_WALLET`. Bitcoin-price queries now route to Brave (web) instead of the previous ~39 s MongoDB dead-end.
- **Cipher and Aurora**: `mongodb` + `bot_state` removed from `mcp_access`; Cipher is now Brave-only.
- **Frontend MongoDB types**: removed `mongodb_mcp`, `multi_mcp` from `source_type` union; removed MongoDB tool indicator, source badge, and narrative entries.

### Added (2026-06-22)

- **Explicit search routing**: `EXPLICIT_SEARCH_COMMANDS` in `tools/keywords.py` (single source of truth) + `FORCE_PATTERNS` ensures "search the web / google it / look it up" always routes to Brave and bypasses the LLM tool-calling loop.
- **Ollama concurrency tuning**: `OLLAMA_NUM_PARALLEL=1` set durably via login LaunchAgent `com.nephilim.ollama-tuning` (`scripts/launchd/ollama-tuning.sh`). Prevents GPU slot-splitting that caused the 2026-06-21 161.9 s turn.
- **`MODEL_MAX_OUTPUT_TOKENS`** (`OllamaSettings`, wired to Ollama `num_predict`, default 400): backstop against runaway replies.
- **Brave MCP PATH + timeout fix**: `scripts/launchd/com.nephilim.backend.plist` PATH now includes `/usr/local/bin` (Docker CLI symlink); `BRAVE_SEARCH_TIMEOUT` default raised to 20 s for cold-container coverage.

### Added

- **Lore wiki** (`docs/lore/wiki/`) — typed-markdown knowledge graph for NEPHILIM worldbuilding. 30 entity files (6 personas, 6 houses, 5 ranks, 6 locations, antagonist Kenoma + Sybil Choir, resonance/ascension concepts, 7 non-canon expansion entities) with YAML frontmatter declaring `entity_type`, `entity_id`, `canon`, `aliases`, and typed `relationships`. Canonical source of truth for entity facts; conflicting house/antagonist names from prose docs preserved as `aliases`.
- **Lore wiki engine** (`scripts/utils/lore_wiki.py`) — `check` (validates schema, relationship resolution, bidirectional inverses, alias collisions, persona-JSON consistency, prose name-drift; CI-gateable), `index` (regenerates `wiki/index.md`), `graph` (derives a networkx-style JSON graph). No new dependencies.
- **Lore sync tool** (`scripts/utils/lore_sync.py`) — one-way `wiki/personas/*.md` → `personas/nephilim_*.json lore[]` sync. `--dry-run` and `--persona` flags. Clears CV summary cache on change. Makes the wiki the canonical authoring surface for persona lore.
- **Wiki runtime injection** (`src/coordinator/lore_loader.py`) — loads and caches wiki entity bodies (persona + house + location) for each NEPHILIM persona at prompt-build time; injected into `<world_context>` block via `prompt_builder.py`. No new dependencies (pathlib + re only). Non-NEPHILIM personas (Wanderer/Gojo) unaffected.
- **Enriched persona lore arrays** — all 6 `personas/nephilim_*.json` `lore[]` arrays rewritten from rich Chronicle + Lore Bible material. Items now name specific canon entities, encode relationship tensions (Prime Covenant, Electric Rivalry, Compassion Triangle, Exile's Garden), and capture philosophical contradictions. CV summaries cleared to force regeneration on next chat.
- **[ADR-001](docs/decisions/001-lore-as-typed-markdown-wiki-not-a-graph-db.md)** — records typed-markdown-wiki decision and Neo4j rejection.
- **56 new unit tests** — `tests/backend/lore/` (20 lore_wiki + 26 lore_sync) + `tests/backend/coordinator/test_lore_loader.py` (10).

### Changed

- `src/coordinator/prompt_builder.py` — `_build_nephilim_lore_block` now appends wiki entity context (additive; existing `nephilim_lore` dict fields preserved).
- `docs/lore/README.md` — declares `wiki/` the canonical source of truth.
- `docs/lore/NEPHILIM_LORE.md` — canon-note banner pointing to the wiki.
- `CLAUDE.md` — wiki section updated to note runtime relevance.


### Celestial Order Remap (Feb 2026)
- **Celestial Order System**: Replaced gacha rarity vocabulary with lore-aligned Celestial Order tiers:
  - Legendary → Archon (Gold) — E.E.V.A.
  - Epic → Warden (Purple) — Aegis, Aurora, Solace
  - Rare → Sage (Cyan) — Cipher, Nyx
  - Common → Wanderer (Silver) — Legacy personas
- **Per-Persona MCP Access**: MCP tool access now controlled per-persona via `mcp_access` field in persona JSONs, replacing rarity-based tier gating:
  - Cipher (Sage) now has Brave + MongoDB access (was Brave-only under rarity gating)
  - Nyx (Sage) now has no MCP tools (was Brave under rarity gating)
  - Aegis and Solace (Warden) now have Brave-only access (were Brave + MongoDB under rarity gating)
- **Backend**: Added `CelestialOrder` enum, `mcp_access` parameter to intent_classifier and tool_utils, per-persona override in routes
- **Frontend**: New `celestialOrder.ts` utility, all display labels show Archon/Warden/Sage/Wanderer, CSS classes unchanged (`rarity-*`)
- **Tests**: 10 new backend tests for mcp_access logic, all frontend test mocks updated with celestial_order field
- **Documentation**: Updated CLAUDE.md, README.md, and development docs

### Fixed
- **Chat UI Performance & Accessibility Bug Fixes** ✅ (Jan 19, 2026) - Fixed two critical UX issues in the chat interface:
  - ✅ **Issue #1 - Input Text Visibility**: Fixed low contrast making typed characters barely visible
    - **Root Cause**: Glassmorphic input designed for dark backgrounds used light gray text (`#e0e0e0`) on white background (1.3:1 contrast ratio)
    - **Fix**: Changed input text color to dark gray (`#1f2937`) achieving 13.5:1 contrast ratio (WCAG AAA compliant)
    - **File**: `react-ui/src/index.css:209`
  - ✅ **Issue #2 - Message Bubble Re-Rendering**: Fixed all message bubbles re-rendering on every keystroke
    - **Root Cause**: Row component and callbacks recreated on every parent render, breaking React memoization
    - **Fix 1**: Wrapped `Row` component in `useCallback` hook with proper dependencies in `VirtualizedMessageList.tsx`
    - **Fix 2**: Wrapped `handleRetryMessage` callback in `useCallback` hook in `Chat.tsx`
    - **Fix 3**: Moved `handleRetryMessage` before conditional return to comply with React Hooks rules
    - **Files**: `react-ui/src/components/VirtualizedMessageList.tsx`, `react-ui/src/pages/Chat.tsx`
  - **Impact**:
    - Accessibility: Input text contrast improved from WCAG F (fail) to AAA (13.5:1 ratio)
    - Performance: Eliminated unnecessary re-renders on typing (5-10 MessageBubbles per keystroke → 0)
    - UX: Smooth typing experience with no visual stuttering

### Added
- **NEPHILIM Phase 6: Persona Filter Toggle** ✅ (Feb 1, 2026) - Added filter system to toggle between NEPHILIM and legacy personas:
  - ✅ **PersonaFilterToggle Component**: New animated toggle with three modes (All ✦, NEPHILIM ⬡, Legacy ◇)
  - ✅ **Filter Utilities**: `personaFilter.ts` with `isNephilimPersona()`, `filterPersonas()`, `getPersonaCounts()` functions
  - ✅ **CharacterCardV2 Enhancement**: Added NEPHILIM badge display for matching personas
  - ✅ **CharacterSelector Enhancement**: Gradient indicator bar on NEPHILIM persona thumbnails
  - ✅ **CharacterCardV2Showcase Integration**: Filter toggle in page header with persona counts
  - ✅ **Persistence**: Filter preference saved to localStorage (`persona_filter_mode`)
  - ✅ **Playwright Tests**: 7 automated tests covering all filter functionality
  - **Files Added**:
    - `react-ui/src/components/PersonaFilterToggle.tsx` - Filter toggle component
    - `react-ui/src/utils/personaFilter.ts` - Filter utility functions
    - `react-ui/tests/phase6-filter.spec.ts` - Playwright test suite
  - **Files Modified**:
    - `react-ui/src/components/CharacterCardV2.tsx` - NEPHILIM badge support
    - `react-ui/src/components/CharacterCardV2.module.css` - NEPHILIM styling
    - `react-ui/src/components/CharacterSelector.tsx` - NEPHILIM indicator
    - `react-ui/src/pages/CharacterCardV2Showcase.tsx` - Filter toggle integration
  - **Impact**:
    - UX: Users can now filter persona gallery by type (NEPHILIM vs legacy)
    - Discoverability: Clear visual distinction between NEPHILIM and legacy personas
    - Persistence: Filter preference remembered across sessions

- **Project Reorganization: Scripts & Documentation Hierarchy** ✅ (Jan 18, 2026) - Comprehensive reorganization of scripts and documentation into logical directory structure:
  - ✅ **Scripts Organization**: Moved 14 scripts from root into categorized subdirectories
    - `scripts/docker/` - 7 Docker setup, validation, and troubleshooting scripts
    - `scripts/setup/` - 3 local development environment setup scripts
    - `scripts/utils/` - 4 Python utilities (unified launcher, validation, cleanup, security)
  - ✅ **Documentation Organization**: Moved 4 documentation files into categorized subdirectories
    - `docs/setup/` - DOCKER_QUICKSTART.md (moved from root)
    - `docs/development/` - ADDING_MCP_SERVERS.md, TESTING_GUIDE.md
  - ✅ **Navigation Indices**: Created 5 comprehensive README.md files for easy discovery
    - `scripts/README.md` - Master index with quick reference to all script categories
    - `scripts/docker/README.md` - Docker scripts guide with usage examples and troubleshooting
    - `scripts/setup/README.md` - Setup scripts guide with prerequisites and next steps
    - `scripts/utils/README.md` - Python utilities guide with import examples and best practices
    - `docs/README.md` - Complete documentation index with categorization and links
  - ✅ **Path Updates**: Updated 50+ references across 12+ files (README.md, CLAUDE.md, .env.example, test files, AI_documentation)
  - ✅ **Critical Code Updates**:
    - `react-ui/package.json` - npm start path updated to `scripts/utils/run_react.py`
    - `.claude/settings.local.json` - execution permissions updated
  - ✅ **Configuration Updates**:
    - `.gitignore` - Added exception for `scripts/` directory (was blocked by `Scripts/` venv pattern)
    - CLAUDE.md - Updated "Root Markdown Policy" from 5 docs to 4 docs (DOCKER_QUICKSTART moved to docs/)
  - **Impact**:
    - Root directory clutter: 15 utility files → 0 (100% reduction)
    - Root markdown files: 5 → 4 (specialized guides moved to docs/)
    - Organization quality: 3.5/10 → 9.2/10 (+5.7 points improvement)
    - Maintainability: Clear patterns for where to add new files
  - **Files Changed**: 76 files total (18 moved, 50+ updated references, 5 new READMEs, 3 config updates)
  - **Git History**: Preserved via `git mv` for all 18 file moves
  - **Development Time**: 45 minutes (planning, execution, verification, documentation)
  - **Status**: Production-ready, deployed to GitHub, perfect 10/10 hygiene score maintained
  - **Documentation**: Updated README.md with new "Scripts & Utilities" and "Testing & Quality" sections

- **Character Card Hover Animation Optimization** ✅ (Jan 1-2, 2026) - Iteratively refined hover animations based on UX research and user feedback:
  - ✅ **Particle Removal**: Removed floating particles from header, chat page, and session list sidebar for cleaner aesthetic
  - ✅ **Animation Simplification**: Evolved from dual-transform (y + scale) to scale-only animation
  - ✅ **Iteration 1**: Removed rotation animation (user feedback: distracting)
  - ✅ **Iteration 2**: Fixed asymmetric timing issue (hover-in fast, hover-out slow → both 150ms)
  - ✅ **Iteration 3**: Eliminated CSS transform conflicts (removed 180ms CSS transition)
  - ✅ **Iteration 4**: Simplified to scale-only (1.0 → 1.05) to eliminate "two animation" perception
  - ✅ **Final Specs**: Pure scale effect, 150ms, cubic-bezier [0.4, 0, 0.2, 1], modern minimalist aesthetic
  - ✅ **Performance**: Single transform property = optimal GPU acceleration
  - ✅ **Testing**: Playwright automated validation with transform matrix verification
  - **User Experience**: Smooth single animation, snappy and consistent both directions, Spotify/Netflix card style
  - **Research Sources**: Nielsen Norman Group (150ms standard), Material Design 3 (single-property transforms), 2025 UI trends
  - **Files Modified**: `react-ui/src/components/CharacterCard.tsx`, `react-ui/src/components/CharacterCard.module.css`, `react-ui/src/components/SessionList.tsx`, `react-ui/src/components/header/HeaderVisuals.tsx`, `react-ui/src/pages/Chat.tsx`
  - **Development Time**: ~10 hours (4 iterations with user testing)
  - **Status**: Production-ready, deployed to Docker, tested and validated

- **Option 6: Rarity-Adaptive Background System** ✅ (Jan 1, 2026) - Implemented rarity-based background theming with interactive card selection:
  - ✅ **Rarity-Based Theming**: Dynamic backgrounds that adapt based on selected persona's rarity tier
  - ✅ **4 Rarity Tiers**: Common (Blue #60a5fa), Rare (Cyan #06b6d4), Epic (Purple #a78bfa), Legendary (Gold #fbbf24)
  - ✅ **Deep Space Aesthetic**: Gradient backgrounds with nebula overlays for immersive sci-fi experience
  - ✅ **Contextual Activation**: Neutral background on home page, rarity theming on agent selection & chat pages
  - ✅ **Smooth Transitions**: 0.8s cubic-bezier animations between rarity switches
  - ✅ **Clickable Character Cards**: Full card clickable for selection, hover states, selection feedback with pulsing halo
  - ✅ **Continuous Particle System**: Ambient particles on agent selection and chat pages
  - ✅ **CSS Architecture**: 40 CSS variables across 4 rarity tiers, utility classes (`.space-background`, `.nebula-overlay`, `.glass-card`)
  - ✅ **Production Testing**: Comprehensive Playwright test suite validating all 4 rarity tiers in both dev and Docker environments
  - ✅ **Performance**: CSS-only system with minimal overhead (+336B CSS, +72B JS)
  - ❌ **Phase 1 Deferred**: Full glassmorphic UI polish (translucent message bubbles, rarity-adaptive buttons, glass inputs) - foundation deemed sufficient
  - **Files Modified**: `react-ui/src/index.css`, `react-ui/src/App.tsx`, `react-ui/src/pages/Home.tsx`, `react-ui/src/pages/Chat.tsx`, `react-ui/src/pages/CharacterCardV2Showcase.tsx`, `react-ui/src/components/CharacterCard.tsx`, `react-ui/src/components/EnergyParticles.tsx`
  - **Research**: 10 design options evaluated, scored on 8 criteria (2026 trends, accessibility, performance, gacha appeal, AI trust, differentiation)
  - **Mockups Created**: 10 interactive HTML mockups showcasing different aesthetic approaches
  - **Development Time**: ~4 hours (research, mockups, implementation, testing, Docker deployment)
  - **Status**: Production-ready, deployed to Docker, all tests passing
  - **Documentation**: Updated CLAUDE.md with background system specification, archived gap analysis in `AI_documentation/01_implementation_history/OPTION6_GAP_ANALYSIS.md`

- **Prompt System Optimization** ✅ (Dec 28, 2025) - Optimized persona system prompts with comprehensive quality testing:
  - ✅ **Token Efficiency**: Reduced system prompt from 3,543 → 2,523 tokens (-1,020 tokens, -28.8%)
  - ✅ **Context Capacity**: Increased available context from 553 → 1,573 tokens (+184% for conversation history)
  - ✅ **Quality Improvements**: Overall score improved 74.0% → 79.2% (+5.2%)
  - ✅ **First-Person Enforcement**: Streamlined from 84 lines to 20 lines while improving adherence 75.0% → 87.5%
  - ✅ **Multi-Message Examples**: Reduced from 12 to 6 highest-quality examples (maintained 88.9% score)
  - ✅ **Voice Consistency**: Improved from 44.4% → 55.6% (+11.1%)
  - ✅ **Persona Differentiation**: Improved from 75.0% → 100.0% (+25.0%)
  - ✅ **Comprehensive Testing**: 16 test scenarios across 7 categories with live LLM validation
  - **Pass Rate**: 56.2% → 68.8% (+12.5% improvement)
  - **Conversation Length**: Users can now have 2-3x longer conversations before hitting context limits
  - **Files Modified**: `src/coordinator/prompt_builder.py` (optimized), backup created
  - **Documentation**: `PROMPT_OPTIMIZATION_FINAL_REPORT.md`, `PERSONA_PROMPT_SYSTEM_ANALYSIS.md`, test suite with JSON results
  - **Development Time**: 3 hours (analysis, implementation, testing, deployment)
  - **Status**: Production-ready, deployed, quality validated
  - **Risk**: Low (no regressions detected, multiple metrics improved)

### Security
- **Dependency Security Fixes**: Resolved 10 high-severity and 2 moderate npm audit vulnerabilities in React dependencies through targeted package overrides and updates. Reduced total vulnerabilities from 12 to 2 moderate issues in development dependencies only.

### Performance
- **System Prompt Optimization**: 28.8% token reduction enables faster LLM inference and significantly longer conversations (see Added section for details)
- **UX Phase 1.1: Typography System Overhaul** ✅ (Dec 28, 2025) - Replaced generic system fonts with distinctive, sci-fi themed typography:
  - ✅ **Display/Headings**: Orbitron font (700, 900 weights) for futuristic sci-fi aesthetic
  - ✅ **Body Text**: Poppins font (400, 600, 700 weights) for clean, readable UI text
  - ✅ **Monospace/Technical**: Space Mono (400, 700 weights) for latency stats and technical data
  - ✅ **Type Scale**: Complete CSS variable system (`--text-xs` through `--text-5xl`, 0.75rem to 3rem)
  - ✅ **Tailwind Integration**: Extended theme with `font-display`, `font-body`, `font-mono` classes
  - ✅ **Google Fonts CDN**: Optimized font loading with preconnect for performance
  - **Files Modified**: `react-ui/public/index.html`, `react-ui/src/index.css`, `react-ui/tailwind.config.js`, `Home.tsx`, `CharacterCardV2.module.css`, `MessageBubble.tsx`
  - **Visual Impact**: Immediate brand differentiation from generic React dashboards
  - **Build Size**: +157 bytes CSS (new typography rules)
  - **Development Time**: 1.5 hours
  - **Documentation**: `AI_documentation/01_implementation_history/TYPOGRAPHY_SYSTEM_IMPLEMENTATION.md`
  - **Status**: Production-ready, deployed to Docker
- **MongoDB MCP Integration (MVP COMPLETE!)** ✅ - Fully implemented MongoDB Model Context Protocol integration for Bitcoin trading data access by Epic/Legendary personas:
  - ✅ **Phase 1**: MongoDB MCP client with JSON-RPC 2.0 protocol, pre-warmed Docker containers, read-only security enforcement (638 lines)
  - ✅ **Phase 2**: 3-layer intent classification system with 41 MongoDB keywords, dynamic tool injection, 4 semantic Bitcoin tools (689 lines)
  - ✅ **Phase 3**: TTL-based caching layer with thread-safe operations, statistics tracking, automatic expiry (290 lines)
  - ✅ **Phase 4**: Backend integration with 4 tool handlers, intent-based routing, ResponseMetadata model, caching integration (~600 lines)
  - ✅ **Phase 5**: Frontend SourceIndicator component with visual badges, cache status, relative timestamps (~370 lines frontend)
  - ✅ **Phase 6**: Comprehensive unit test suite with 56 tests total (30 backend + 26 frontend) achieving 100% coverage
  - ✅ **Phase 7**: Intent classification testing with 360 comprehensive tests (90 questions × 4 rarities)
  - ✅ **Phase 8**: Intent classification improvements achieving **100.0% accuracy** (up from 89.7%)
  - **Documentation**: Comprehensive 1,700+ line implementation guide (MONGODB_MCP_IMPLEMENTATION.md) + Phase 4 & 5 summaries + Improvements guide
  - **Total Code**: 3,200+ lines across 9 new files, 7 modified files
  - **Features**: Bitcoin price queries, technical indicators (RSI, MACD, Bollinger Bands), historical data, DCA trading stats, visual source badges
  - **Classification Accuracy**: **100.0%** across all categories (PURE_LLM, BRAVE_MCP, MONGODB_MCP) and all rarity levels
  - **Development Time**: 16.5 hours over 2 days
  - **Status**: Production-ready, perfect test scores, zero false positives/negatives

- **Intent Classification System Improvements** 🎯 - Enhanced query classification from 89.7% → **100.0% accuracy** (+10.3 percentage points):
  - ✅ **Brave MCP Keywords Expanded** (20+ keywords): Added "trending", "happening", "saying", "talking about", "sentiment", "experts say", "analysts", "popular", "viral", "mood", "opinions", "predictions", "forecasts"
  - ✅ **MongoDB MCP Keywords Expanded** (15+ keywords): Added "value", "worth", "trading at", "trend analysis", "indicators", "current value", "historical", "portfolio", "holdings", "going for", "selling for"
  - ✅ **Educational Query Detection**: Enhanced to distinguish "Why was Bitcoin created?" (educational) from "What was Bitcoin's price?" (data query)
  - ✅ **Opinion Query Detection**: New logic to detect sentiment/opinion queries despite having "what are" definition keywords
  - ✅ **Rarity-Gating Bug Fix**: Rare personas now correctly return NEEDS_NEITHER for MongoDB queries instead of falling back to web search
  - ✅ **Test Results**: Perfect 100% accuracy across all 360 tests (90 questions × 4 rarity levels)
  - **Grade Improvement**: B (Good) → A+ (Excellent)
  - **Files Modified**: `src/coordinator/tool_definitions.py` (enhanced classification logic)
  - **Time Investment**: ~2 hours
  - **Documentation**: Created IMPROVEMENTS_COMPLETE.md with comprehensive before/after analysis
- **Dynamic Persona Management** - Implemented automatic persona discovery from JSON files, orphaned session cleanup, collection synchronization, and chat history updates when personas are added/removed/modified
- **Code Quality Improvements** - Fixed ESLint warnings in PullInterface component and resolved test suite issues for PullInterface and PersonaContext
- **Chat History UX Enhancements** - Improved SessionList component with snappier hover animations (100ms), removed white avatar borders, and enhanced visual consistency with mobile menu theming
- **Phase 3: Character Gacha System Completion** - Full implementation of advanced gacha system with multi-pull mechanics, particle effects, audio integration, and collection management
- **Character Card Preference Update** - Switched CharacterCardV2Showcase to use classic CharacterCard component with traditional foil effects and smooth animations instead of holographic V2 cards
- **Multi-Pull System** - PullInterface component supporting 1x/5x/10x pulls with sequential reveal animations, energy-animated buttons, and result display
- **Particle Effects Integration** - EnergyParticles component using @tsparticles/react for ambient visual effects during pulls and celebrations
- **Audio System** - Complete Web Audio API integration with synthesized sound effects for pull actions, card reveals, and rarity-based celebrations with persistent mute controls
- **Collection Management** - Persistent character collection storage with statistics tracking, pull history, and organized display in CharacterCollection component
- **Advanced Animations** - Multi-stage pull sequences with screen effects, shake animations for card reveals, and rarity-based celebration effects
- **Header Audio Controls** - Mute/unmute button in header navigation with visual feedback and persistent state management
- **TypeScript Optimization** - Resolved all compilation errors, added proper type annotations, and ensured type-safe implementation across all components
- **Performance Optimization** - Optimized particle rendering, reduced memory usage, and implemented hardware acceleration for smooth 60fps animations
- **Accessibility Enhancements** - Added reduced motion support, keyboard navigation, and screen reader friendly descriptions
- Initial project structure with NEPHILIM backend and React UI frontend
- Persona-based chat interface with multiple character options
- Gacha-style character selection with card reveal animations
- Static character browsing with search functionality
- FastAPI backend with Ollama LLM integration
- Comprehensive testing setup with Jest and pytest
- **Unified startup script** (`run_react.py`) that launches both backend and frontend together
- **CORS support** in FastAPI for cross-origin requests from React UI
- **Header Component Enhancement (Phase 1)**: Modern dark theme header with rarity-based active page highlighting, responsive layout, and branding
- **Header Component Enhancement (Iteration 2.1)**: Added Framer Motion animations with entrance effects, hover interactions, and smooth transitions
- **Header Component Enhancement (Iteration 2.2)**: Implemented visible particle system, dynamic gradient theming with page-based color changes, enhanced glassmorphism, animated typography with glow effects, and prominent animated bottom border
- **Header Component Enhancement (Iteration 2.3)**: Added functional mobile hamburger menu with slide-out navigation, persona-aware theming that adapts to selected character, touch-optimized interactions, and mobile-specific UI enhancements
- **Phase 3: App-Wide Enhancements**: Completed character card visual effects with Framer Motion animations, polished chat interface with smooth scrolling and message animations, and comprehensive mobile optimization across the entire application
- **Chat UX Phase 3.1: Rich Media Support**: Added message timestamps, JSON syntax highlighting with collapsible display and copy buttons with visual feedback, code block highlighting with language detection and copy buttons with visual feedback, and RichContent component for intelligent content rendering
- **Chat UX Phase 3.2: Performance & Feedback**: Implemented latency tracking with response time display in ms/s, error recovery with retry functionality for failed messages, status indicators (sending, sent, delivered, failed) with loading spinners, message status management and retry counters, and React.memo performance optimizations
- **Copy Button Feature**: Added ChatGPT-style copy buttons for JSON responses and code blocks with visual feedback (copy icon → checkmark) and automatic reset after 2 seconds
 - **Chat UX Phase 3.4: Mobile Optimization**: Implemented ChatGPT-style responsive layout (sidebar pushes content on desktop, overlays on mobile), dynamic content expansion, touch gestures, swipe navigation, mobile-optimized input attributes, and comprehensive testing
 - **Header Layout Optimization**: Fixed chat header to prioritize action buttons (Import/Export/Clear) with proper truncation of long chat titles
 - **Persona Customization Phase 3.3**: Implemented gacha-style theming with rarity-based colors (legendary=gold, epic=purple, rare=blue, common=grey), custom character backgrounds with subtle watermark overlays, personalized avatar effects with rarity rings and shadows, cohesive send button theming, and comprehensive unit testing
 - **Chat History UX Iteration 3: Persona Indicators**: Added small persona name badges on assistant messages with rarity-based styling (legendary=yellow, epic=purple, rare=blue, common=gray) for clear persona identification in conversations
 - **Home Page UX Consistency**: Applied character selection page's sophisticated theme to home page including glassmorphism background effects, animated particles, yellow-themed buttons matching rarity theming, gradient header text, and consistent visual styling throughout
 - **Home Page Simplification**: Removed gacha pull mechanics from home page entirely, now serves as a clean navigation gateway to the character selection page where all gacha functionality resides
 - **Direct Tab Navigation**: "Try Your Luck" button now navigates directly to the Gacha Pull tab on the character selection page for seamless user experience

### Changed
- **UI Flow Reorganization (2025-01-07)**: Moved pull mechanics to home page, simplified character selection to browsing-only
- **Character Card Consistency**: Updated all character card displays (Card Gallery, My Collection, Gacha Pull) in CharacterCardV2Showcase to use classic CharacterCard component with traditional foil effects for consistent styling across the entire page
- **Choose Button Functionality**: Restored 'Choose' button functionality in CharacterCardV2Showcase to navigate directly to persona-specific chat, matching the behavior of the original CharacterSelection page
- **Search Functionality**: Added search and filtering capability to the Card Gallery tab in CharacterCardV2Showcase, allowing users to find characters by name, style, or rarity
- **Character Page Replacement**: Replaced the original CharacterSelection page with the enhanced CharacterCardV2Showcase, maintaining the /select URL route while providing comprehensive gacha functionality, collection management, and improved user experience
  - Home page (`/`) now handles all gacha pulls with card reveal animations
  - Character selection page (`/select`) now shows clean grid browsing with search
  - Removed "Ready to Pull?" interface from character selection page
  - Improved separation of concerns between pulling and browsing experiences
- **React Migration Completed**: Full migration from Streamlit to React UI with working chat functionality and comprehensive visual enhancements
- **Header Component Planning**: Documented phased approach for modern header redesign with vibrant colors and highlighting

### Technical Improvements
- Optimized React build (131KB gzipped) with enhanced animations and visual effects
- Fixed Jest configuration issues
- Updated TypeScript setup for better development experience
- Improved component architecture with better state management
- Added CORS middleware to FastAPI backend
- Enhanced error handling and user feedback in chat interface

### Fixed
- **Chat Session Creation**: Fixed double session creation when selecting new personas
- **Greeting Message Handling**: Fixed greeting messages appearing as user input instead of assistant messages
- **Persona Mixing**: Fixed greeting messages being sent to wrong sessions when switching chats during loading
- **Input Blocking**: Added proper blocking of chat input until initial greeting messages are generated
- **Loading States**: Added visual feedback during session creation and greeting generation
- **Avatar Images**: Fixed avatar images disappearing when switching between chats and ensured proper use of dedicated avatar images instead of card images
- **Page Scrolling**: Fixed scrolling issues on all pages by changing main content container from `overflow-hidden` to `overflow-auto` in App.tsx

### Documentation
- Updated README with new unified startup process and React UI focus
- Enhanced GACHA_UX_ROADMAP.md with current implementation status
- Added comprehensive coding guidelines in AGENTS.md
- Updated REACT.md to reflect completed migration
- Created this changelog for tracking project evolution

## [0.1.0] - 2025-01-XX

### Added
- Basic NEPHILIM coordinator architecture
- React UI with routing (Home, Character Selection, Chat)
- Character card components with rarity styling
- API integration between frontend and backend
- Basic testing infrastructure

### Technical
- React 19 with TypeScript
- FastAPI backend
- Ollama LLM integration
- Framer Motion animations
- Jest testing framework

---

## Types of changes
- `Added` for new features
- `Changed` for changes in existing functionality
- `Deprecated` for soon-to-be removed features
- `Removed` for now removed features
- `Fixed` for any bug fixes
- `Security` in case of vulnerabilities</content>
<parameter name="filePath">CHANGELOG.md