# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MCP Coordinator is a **local-first persona-driven chat interface** combining a FastAPI backend with a React frontend. It enables conversations with AI personas powered by Ollama LLM models, featuring a summoning-style character collection system with persistent chat history.

**Key Architecture:**
- **Backend**: FastAPI coordinator (`src/coordinator/`) bridging persona definitions with Ollama LLM
- **Frontend**: React 19 + TypeScript with Framer Motion animations and Tailwind CSS
- **Persistence**: SQLite database (`chats.db`) for sessions, messages, and collections
- **Personas**: JSON-defined characters in `personas/` with lore, voice, behavior, and expertise
- **MCP Integration**: Brave Search (web) via ephemeral Docker STDIO containers; Solana/Jupiter wallet via long-running Docker container

## Development Commands

Full command reference: [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) — Docker, local dev, testing, persona test suite, Ollama setup.

**Access (local dev, macOS):** Frontend `http://localhost:3001` | Backend `http://localhost:8000` | API Docs `http://localhost:8000/docs`

Quick hits:

```bash
# Local dev — PRIMARY on macOS (native Ollama + Metal GPU). Backend, then frontend:
.venv/bin/python -m uvicorn src.coordinator.server:app --port 8000
cd react-ui && PORT=3001 npm run start:dev   # --openssl-legacy-provider baked into the script (needed on any Node 17+)

# Backend tests (~2,050; gate --cov-fail-under=60). Live tests
# auto-skip when Ollama/Brave/Docker are unreachable (tests/conftest.py).
pytest tests/
OLLAMA_BASE=http://127.0.0.1:1 pytest tests/   # force headless: live tests skip

# Full persona test suite (primary quality gate, ~60 min)
.venv/bin/python tests/manual/comprehensive_persona_test.py

# Tool-firing eval — does a tool actually fire when a turn needs one, and is it
# the native brain or the legacy floor doing the work? Needs the backend live.
# NOTE: `eval_*.py` is not auto-collected (pytest.ini: python_files = test_*.py);
# its headless guards live in tests/evaluation/test_tool_firing_cases.py.
.venv/bin/python tests/evaluation/eval_tool_firing.py
NEPHILIM_BASE_URL=http://127.0.0.1:8001 .venv/bin/python tests/evaluation/eval_tool_firing.py  # scratch instance

# Docker (full stack + MCP containers; for Linux/NVIDIA GPU — runs Ollama CPU-only on Mac)
docker-compose --env-file .env.docker up -d
```

> **Writing backend tests:** mark anything that hits Ollama/Brave/Docker with `@pytest.mark.requires_ollama`/`requires_api_key`/`requires_docker` (else it fails headless). Use `asyncio.run()` not `get_event_loop()`, `TestClient(app)` without the `with` (skips lifespan), and don't add `__init__.py` to the test tree. Full conventions: [`docs/development/TESTING_GUIDE.md`](docs/development/TESTING_GUIDE.md).

> **Tests are hermetic — never let prod `.env` reach them.** `tests/conftest.py::_hermetic_settings` (session, autouse) closes BOTH channels: it strips whatever a module-scope `load_dotenv()` exported into `os.environ` during collection (diffed against a pre-collection snapshot, so shell/CI vars survive), and disables `model_config["env_file"]` on every settings class in BOTH import trees (`coordinator.*` AND `src.coordinator.*` are distinct module objects here). Settings then resolve from `os.environ` + field defaults only. **Do not add a module-scope `load_dotenv()` to a test** — it leaks prod config into the whole session at collection time. Symptoms it prevents: search tests bypassing their mocks to hit the real SearXNG; `monkeypatch.delenv` failing to reach defaults.

> **⚠️ macOS:** run Ollama natively for Metal GPU acceleration — Docker-on-Mac runs Ollama CPU-only. Docker also serves the legacy UI unless rebuilt, so use local dev for the Phase 7 NEPHILIM UI (`docs/DEVELOPMENT.md`).

**Always-on (launchd):** `com.nephilim.backend` (uvicorn :8000) + `com.nephilim.frontend` (static `scripts/serve_frontend.py` :3001) run under launchd with RunAtLoad+KeepAlive. Reinstall after changes: `scripts/launchd/install.sh` (rebuild frontend first: `cd react-ui && npm run build`). Migration cleanup punch list: `docs/MAC_MIGRATION_CLEANUP.md`.

**Subsystem — Telegram gateway** (`services/telegram-gateway/`, own venv/tests/launchd daemon `com.eeva.telegram`): allowlisted Telegram bot client of the session API above (zero gateway-side logic; shares the coordinator's conversation-control endpoints with the React UI per [ADR-011](docs/decisions/011-conversation-control-commands-as-shared-session-api-endpoints.md)). See its own [CLAUDE.md](services/telegram-gateway/CLAUDE.md).

## Project Structure

### Backend (`src/coordinator/`)

```
server.py, startup.py          # App entry, lifecycle (startup.py = thin orchestrator: initialize_all() ordering + build_app_state(); publishes AppState on app.state.container)
di/                            # 2026-08-22: startup.py's 25 get_*/8 init_* functions + globals, split by cluster — repositories.py (12 SQLite repos + init_db/Alembic), services.py (Brave/memory/RAG/facts/tool-interceptor), jupiter.py (Jupiter MCP/wallet exec/strategy/scheduler). startup.py re-exports every name (`from .di.X import name`) so `startup.get_X`/`init_X` keep resolving unchanged — do not import di/* directly elsewhere, go through startup.get_X() as before
app_state.py, dependencies.py  # DI composition root: AppState (typed snapshot of every startup singleton) + FastAPI Depends providers (require_*→503 when uninit, optional_*→None). Providers resolve startup.get_X() at CALL TIME (module-attr, not `from ..startup import`), so tests patching src.coordinator.startup.get_X still intercept
config/, schemas.py            # Settings package (per-subsystem: llm/search/memory/wallet/auth/routing/lore/agent/groundedness; __init__ = composition root + get_settings), API schemas
routes/                        # chat.py, sessions.py, personas.py, nephilim.py, auth.py
services/                      # Business logic (llm_completion, tool_calling, citation, chat_session, query_handler, wallet_*, strategy, etc.)
repositories/                  # SQLite data access — ALL extend BaseRepository via db_adapter (connection pooling)
                               #   session, message, summary, emotional_state, seeker_progression,
                               #   user_profile, user (OAuth), trade_proposal, wallet, wallet_flow (guided-creation state)
models/                        # persona_schema.py, sampling_presets.py, mcp_models.py
tools/                         # intent_classifier.py, synthesis_prompts.py, keywords.py, tool_generators.py, tool_utils.py, result_filters.py (image junk denylist)
```

**Key files:**
- `llm_client.py` - LLM orchestration facade (passes per-persona sampling overrides)
- `prompt_builder.py` - System prompt construction from persona JSON (XML-tagged sections with bookend pattern)
- `mcp_client_stdio.py` - Brave Search MCP client
- `persona_memory.py` - CV summary generation and caching
- `memory_manager.py`, `memory_rag.py` - RAG semantic search (bge-m3 embeddings via modern `langchain_ollama` + `num_ctx`; cosine `1 − D/2` scoring, `min_relevance=0.5` recall floor)
- `context_framing.py` - ADR-006 M1 per-persona `<remembered>` framing of injected memory (prose narratives + non-imitation preamble; the Gate-0/0.1 fix). Narrative variants live on `UserProfile.get_narrative_context` / `EmotionalState.to_narrative_context`
- `memory_fact_repository.py`, `triplet_extractor.py`, `fact_write_policy.py`, `fact_extraction_worker.py`, `memory_fact_retrieval.py` - ADR-006 M2–M4 ontology-lite fact store: two-table temporal store + async triplet extraction (abstention few-shot + quote-span guard, off the interactive path) + recency-wins write + inject-all/top-k retrieval through the M1 frame. All gated `MEMORY_FACTS_ENABLED` (default OFF)
- `memory_text_utils.py` - embedding-input guard (normalize, drop-empty, chunk oversized / truncate query before embedding — prevents Ollama HTTP 500 overflow)
- `tools/intent_classifier.py` - Query intent classification (wallet, brave, llm) with follow-up detection
- `tools/keywords.py` - Keyword dictionaries for intent classification routing

### Frontend (`react-ui/src/`)

```
pages/                         # Chat.tsx, NephilimHome.tsx, NephilimOnboarding.tsx, CharacterCardV2Showcase.tsx, Dashboard.tsx
components/                    # UI components (Header, MessageBubble, CharacterCard, SessionList, etc.)
components/nephilim/           # NEPHILIM progression components (SeekerRankBadge, LoreCodex, etc.)
context/                       # PersonaContext.tsx (composition wrapper), ChatContext.tsx, CollectionContext.tsx, AudioContext.tsx
services/api/                  # Domain-split API client: base.ts, auth.ts, sessions.ts, chat.ts, nephilim.ts, wallet.ts, personas.ts
services/api.ts                # Barrel re-export (backward compat: import from here as before)
types/                         # personas.ts (canonical Persona type), index.ts barrel
utils/                         # animations.ts, helpers, celestialOrder.ts
```

### Database Schema

**Core Tables:**
- `chat_sessions`: session_id, persona_key, title, timestamps
- `messages`: id, session_id, role, content, timestamp, latency_ms
- `conversation_summaries`: session_id, message_range, summary_text, emotional_developments

**NEPHILIM Progression Tables:**
- `seeker_profiles`: user_id, rank_name, total_resonance, faction_primary, faction_secondary
- `persona_affinity`: user_id, persona_key, messages_count, affinity_level
- `resonance_log`: user_id, amount, reason, persona_key, session_id
- `unlocked_lore`: user_id, persona_key, fragment_id, unlocked_at

**Companion Memory Tables (ADR-006 Phase 1, alembic `4memory_facts`; gated `MEMORY_FACTS_ENABLED`, default OFF):**
- `memory_entities`: entity_id, user_id, name, entity_type, properties (the user + people/things they mention)
- `memory_facts`: id, user_id, subject_id→entity, predicate (controlled vocab), object, object_type, `valid_from`/`valid_to` (bi-temporal — supersede by invalidation, never delete), confidence, source_session_id/message_id, superseded_by

> **Architecture details:** See `docs/architecture/SQLITE_ARCHITECTURE.md` for thread-safety pattern, migration guide, and backup procedures.

## Environment Variables

Required in `.env`:
```bash
OLLAMA_BASE=http://127.0.0.1:11434
PERSONA_MODEL=huihui_ai/mistral-small-abliterated:24b   # daily driver since 2026-07-05 (single-model, ADR-008): 0.96 tool calls + no refusal floor + voice≈Magidonia. Prior: hf.co/TheDrummer/Magidonia-24B-v4.3-GGUF:Q4_K_M (revert-ready, still pulled). gemma2:9b = smoke-test
PERSONA_TEMPERATURE=0.9
COORD_PORT=8000
PERSONA_DIR=personas
```

Optional (see `.env.docker` for full list):
- `BRAVE_API_KEY` - Web search (access controlled per-persona via `mcp_access`/`toolsets` in persona JSON)
- **Web toolset (ADR-009 W1):** `WEB_SEARCH_BACKEND` (`auto`|`searxng`|`brave`, default `auto` = SearXNG-if-configured-else-Brave), `SEARXNG_BASE_URL` (empty = Brave-only, byte-identical legacy), `SEARXNG_TIMEOUT` (10), `WEB_SAFESEARCH_DEFAULT` (`off`|`moderate`|`strict`, default **`off`** — uncensored companion; per-persona `nsfw:false` clamps UP to `moderate`, the model may tighten per-call). Self-host SearXNG: `docker run -d -p 8888:8080 searxng/searxng` with `search.formats: [html, json]` enabled, then `SEARXNG_BASE_URL=http://127.0.0.1:8888`
- **Tool brain (ADR-008 P1):** `TOOL_BRAIN_ENABLED` (default **OFF** = byte-identical legacy force-search), `TOOL_BRAIN_MAX_ITERATIONS` (3), `TOOL_BRAIN_DETERMINISTIC_FALLBACK` (default ON). Single-model native tool-calling loop — the daily driver decides+fills tool calls itself, and when it stays silent on a tool-needing query (native calling is phrasing-sensitive per the TB0 spike) the loop falls through to the legacy intent-router/force-search floor. Enables Gwen's image/video subset + the safesearch clamp to actually execute. **`TOOL_BRAIN_UNGATED_WEB`** (default **OFF**, ADR-008 TB6) additionally engages the loop on `NEEDS_NEITHER` turns with **web tools only** — the bge-m3 router silently blocked real web queries below its 0.66 threshold (measured 0.49–0.61), so the model was never offered a tool. `NEEDS_WALLET` is never ungated in either mode. Measured 76%→90% on the tool-firing eval (n=29, single run at temp 0.9 — real variance), voice-neutral on the full probe set. **LIVE on prod since 2026-07-19**, flipped deliberately without a Gwen voice gate (she is absent from the probe set) — Gwen and solace are on the soak watchlist; see [ROADMAP](docs/ROADMAP.md). Revert: `TOOL_BRAIN_UNGATED_WEB=false` + `launchctl kickstart -k gui/$(id -u)/com.nephilim.backend`.
- `MEMORY_EMBEDDING_MODEL` - RAG embeddings (default `bge-m3:latest`, 8192-token ctx; `ollama pull bge-m3`). `MEMORY_EMBEDDING_MAX_TOKENS` (8192) caps input before chunking
- **Sampling (2026-08-23):** persona `model_preferences` now forwards `top_k`, `top_p` and `repeat_last_n` in addition to `temperature`/`min_p`/`repeat_penalty` — the first three were declared on `SamplingPreset` and silently dropped. Both spellings of the penalty (`repeat_penalty`, `repetition_penalty`) are accepted; only the former was read while the schema documented the latter. `repeat_last_n` matters most: Ollama's default is **64 tokens (~50 words)** — enough to stop a sentence repeating inside one reply, far too short to notice a repeated paragraph; `-1` scales it to the full context. Out-of-range values are omitted, never clamped, so the caller's default applies.
- **`min_p` needs `OLLAMA_COMPLETION_BACKEND=http`** (default `langchain`). `langchain_ollama.OllamaLLM` coerces options through `ollama.Options`, which has **no `min_p` field** — verified two ways against the installed 1.0.1 (upstream langchain-ai/langchain#32744). No config change reaches it; `services/ollama_http.py` posts to `/api/generate` with the options dict verbatim. Defaults to `langchain` for one soak cycle because the HTTP path is new wire code. On the langchain path a requested `min_p` now logs a warning rather than vanishing.
- **`/reset` (`DELETE /sessions/{id}/messages`) clears derived state too** (2026-08-23): `conversation_summaries`, the FAISS index and `session_notes`, not just messages + emotional state. The response body reports what was cleared. The declared `ON DELETE CASCADE`s do not cover this — `/reset` deletes children directly and never the parent row, and cascades only fire top-down.
- `MEMORY_CONTEXT_INJECT` (default OFF) - inject the M1-framed profile+emotional narrative. `MEMORY_FACTS_ENABLED` (default OFF) - the ADR-006 M2–M4 ontology-lite fact store (async extraction + framed retrieval); `MEMORY_FACTS_RETRIEVAL_K` (5), `MEMORY_FACTS_INJECT_ALL_THRESHOLD` (15). **Both OFF on prod** — the 2026-07-06 abliterated-model eval showed both injections degrade voice distinctiveness (EEVA 0.75→0.25; overall 0.804 off → 0.625 on); the M5 gate that greenlit them was measured on Magidonia and does NOT hold on the current model. **Memory-injection line CLOSED 2026-08-11:** the per-persona framing rework was done and FAILED on abliterated (C1/C2/C3 variants all regressed vs the OFF ruler) — prompt-framing cannot fix it; re-entry would need a model-level change, not more framing ([ADR-006](docs/decisions/006-companion-memory-and-continuity-eval-first.md))

## Branch model

**Two branches only** (`PROD` and five stale feature branches were retired 2026-07-19 — see [ROADMAP](docs/ROADMAP.md)):

- **`dev`** integrates. Feature work goes on its own branch/worktree → PR → `dev`.
- **`main`** releases. Promoted from `dev` locally with a `Release: promote dev to main — …` merge commit (not via PR).

The live backend runs from the **`dev` checkout** under launchd, so `dev` is what is actually serving. There is no separate deploy branch.

## Code Style

### Python
- PEP 8, 4-space indent, type hints (`from __future__ import annotations`)
- `snake_case` functions/modules, `PascalCase` classes
- Async/await preferred, `HTTPException` for errors

### React/TypeScript
- `PascalCase` components, explicit types, strict mode
- No semicolons, 2-space indent (ESLint enforced)
- Tailwind for utilities, Framer Motion for animations
- Canonical `Persona` type lives in `react-ui/src/types/personas.ts` — import from there, never redefine locally
- New code should use `useChat()` / `useCollection()` hooks; `usePersona()` is kept for backward compat
- API functions: import from `'../services/api'` (barrel) or the specific domain module in `services/api/`

### Design System
- **Typography:** Outfit (display), Manrope (body), Space Mono (mono)
- **Celestial Order Colors:** Wanderer (silver `#C0C0C0`), Sage (cyan `#00BFFF`), Warden (purple `#DA70D6`), Archon (gold `#FFD700`)
- **Accessibility:** WCAG AA, 4.5:1 contrast, keyboard nav, `aria-label` on interactive elements

## Key Workflows

### Adding a Persona
1. Copy `personas/template.jsonc` to `personas/[name].json`
2. Fill in: key, display_name, rarity, celestial_order, mcp_access, lore, voice, behavior, expertise
3. Add images to `react-ui/public/images/personas/[name]/` (card.png, avatar.png, logo.png)
4. Persona auto-discovered on next load - no restart needed

> **Full field reference:** See `docs/development/PERSONA_SCHEMA.md` for all fields, valid values, and NEPHILIM-only extended schema.

### Chat Flow
1. Frontend POST `/greet` creates session
2. User message → POST `/sessions/{session_id}/chat` with persona, message
3. Backend builds system prompt from persona JSON + cached CV summary (XML-tagged sections)
4. For wallet-capable personas: ground-truth wallet state injected into system prompt (anti-hallucination)
5. Intent classifier routes query → wallet / brave / pure LLM
6. Ollama generates response with per-persona sampling overrides (min_p, repeat_penalty), stored in SQLite
7. Post-processor strips leaked tool names via regex, enforces first-person
8. Frontend renders with Celestial Order theming

### MCP Integration Patterns
- **Ephemeral (Brave):** `docker run -i --rm` per request, dies after 2-3s
- **Long-Running (Jupiter/Solana):** Container stays alive for wallet operations
- Feature access controlled per-persona via `mcp_access` field in persona JSON (fallback: rarity-based `.env` vars)

### Tool registry & generic web toolset (ADR-009)
- **Registry** (`tools/registry.py`): the single source of truth for tools. A `ToolSpec` bundles definition (OpenAI-function dict) + safety policy (toolset, `blast_radius`, `requires_hitl`) + optional executor/formatter. Declared at import in `tools/registrations.py`; the ADR-004 interceptor reads its policy from here (no more private `_TOOL_POLICY`). Tools group into **toolsets** (`web`, `wallet`, later `memory`/`terminal`).
- **Per-persona toolset grants**: persona JSON `toolsets` field (`mcp_access` kept as a back-compat alias → `brave_search`=web, `solana_wallet`=wallet; rarity fallback preserved). Per-persona `nsfw:true|false` flag modulates the web safesearch floor + (later) the intimate memory partition. `registry.describe_for_persona(card)` powers the introspection API.
- **Generic web toolset** (`tools/web_tool_generators.py`, all read-only): `web_search` (category+safesearch+time_range), `fetch_url` (`services/web_fetch_service.py`: httpx + trafilatura extraction w/ stdlib fallback), `image_search`/`video_search`/`news_search`, `extract`. These are catalog-only until the ADR-008 tool brain can execute them — the **legacy** `get_tools_for_query`/`get_tools_for_persona` still offer only `brave_web_search`+wallet (byte-identical; force-search path). Backend chain (`SearchExecutionService`): SearXNG-primary (query stays local) → Brave fallback; `WEB_SEARCH_BACKEND`/`SEARXNG_BASE_URL` config.
- **Toolkit introspection**: `GET /personas/{key}/toolkit` (registry-driven) + the Telegram **`/tools`** command (`services/telegram-gateway/`, generic across personas).

### MCP Query Routing Pipeline
Queries flow through a two-layer classification system:

1. **Intent Classifier** (`tools/intent_classifier.py`): Keyword-based routing determines which tool to use (web/wallet/llm). Uses keyword dictionaries from `tools/keywords.py`. A bge-m3 embedding **semantic router** (`tools/semantic_router.py`) runs as a fallback for queries that miss all keywords.
   - **Semantic-primary routing** (HERMES-Agents Phase 0): the bge-m3 semantic router is the **only** intent classifier — order is follow-up → high-precision keyword fast-path (`WALLET_FASTPATH`/`EXPLICIT_SEARCH_COMMANDS`/`_MEDIA_SEARCH`) → semantic router → NEEDS_NEITHER. `_MEDIA_SEARCH` (2026-07-05) routes colloquial media-find ("find me images", "show me a video") to web search via a precise **verb+media-noun** regex — deliberately NOT semantic examples, which over-route bare-"find me" RP ("come find me"); the noun requirement makes RP structurally unmatchable. `media_search_type()` classifies image-vs-video, and `_try_tool_brain` **narrows the offered tool surface to that single media tool** — native calling reliably invokes the one tool it's given but often misses `video_search` among four (choice paralysis), so the deterministic layer picks the tool, the model fills the query. (The `ROUTING_SEMANTIC_PRIMARY` flag and the legacy keyword-first order were **retired 2026-07-04** after the semantic path graduated to prod default.) The router uses **max-over-examples** (nearest-example) scoring, not mean centroids (centroids smear and over-route chitchat). Threshold/margin tuned via `tests/evaluation/tune_routing_threshold.py` on a held-out set (`ROUTING_SEMANTIC_THRESHOLD=0.66`, wallet precision 1.0 / recall 0.96 / acc 0.91). `ROUTING_*` settings live in `config.RoutingSettings`.
2. **Tool Calling Service** (`services/tool_calling_service.py`): When Brave search is needed, force-executes the search directly via Docker instead of relying on the local LLM to generate JSON tool calls (small models are unreliable at structured tool calling). This "keyword force search" pattern bypasses the LLM tool-calling loop entirely.
   - **Follow-up query resolution** (flag-gated `SEARCH_QUERY_RESOLUTION_ENABLED`, default OFF): before hitting Brave, a deictic/short follow-up turn ("search the web for it", "and Geneva?", "look it up") is resolved against prior conversation into a standalone query via `services/query_resolution_service.py` (cheap heuristic trigger → single LLM rewrite → sanitize, with a hard fallback to the raw latest turn on any failure — never worse than legacy). Fixes the bug where "search the web for it" was sent to Brave verbatim and returned junk "how to search the web" help pages. Off (default) = byte-identical legacy behavior. `SEARCH_*` settings live in `config.SearchSettings`.

**Anti-hallucination guards:**
- If keyword filter says search is needed but search returns no results → honest "I don't know" response
- If LLM somehow bypasses force-search and doesn't call the tool → honest "I don't know" response
- If the **relevance gate** (flag-gated `SEARCH_RELEVANCE_GATE_ENABLED`, default OFF) is on and the best result's bge-m3 cosine to the query falls below `SEARCH_RELEVANCE_MIN_COSINE` (default 0.40) → treated as no-result (honest abstention) instead of synthesizing over off-topic junk (`services/search_relevance_service.py`; fail-open on embedder error). Catches non-empty-but-irrelevant results that the "no results" guard misses.
- **Context-poisoning guard** (flag-gated `SEARCH_SYNTHESIS_TRUST_RESULTS`, default OFF): local models lack instruction-hierarchy training, so an earlier in-conversation self-apology ("I hallucinated / I can't search") sits at equal priority to system rules and can make the model REFUSE even when fresh correct results are present. When on: (1) the synthesis LLM is fed only the fresh results + the resolved query, NOT the full chat log (`tool_calling_service._synthesis_user_turn`) — deterministically removes the poison (the **reliable** lever, n=10 → 10/10 answered); (2) `build_synthesis_prompt` adds a **RULE 0** supersede directive as reinforcement (RULE 0 alone was only ~1/5 on the 24B). Both scoped so RULE 5 honest abstention on empty/off-topic results still holds. Off (default) = byte-identical legacy synthesis.
- LLM-generated citations are stripped and replaced with verified citations from actual search results

## Important Implementation Details

### Celestial Order & Per-Persona MCP Access
MCP access is controlled per-persona via the `mcp_access` field in persona JSONs:
- **E.E.V.A.** (Archon): Brave + Solana wallet
- **Aegis** (Warden): Brave only (productivity needs web)
- **Aurora** (Warden): Brave only (Oracle insight via web)
- **Solace** (Warden): Brave only (empathy needs resources)
- **Cipher** (Sage): Brave only (Maven's research is web-based)
- **Nyx** (Sage): None (creativity flows from imagination)
- **Wanderer personas** (Gojo, etc.): None (pure LLM)

### SQLite Concurrency
- Thread-safe locking via `_lock` in `repositories/base_repository.py`
- Connection uses `check_same_thread=False`
- Foreign key cascade deletes for cleanup

### Backend Configuration
- All config access via `get_settings()` (in the `config/` package) returning a typed `CoordinatorSettings` Pydantic model
- Legacy `get_*()` getter functions have been removed — use `get_settings().subsystem.field` instead. Per-subsystem settings classes live in `config/<subsystem>.py`; import them (and `get_settings`) from `..config` — never from the submodules directly (patch-path + re-export contract)
- All repositories extend `BaseRepository` in `db_adapter.py` — never open raw `sqlite3.connect()` calls

### React Performance
- `React.memo` for expensive components (MessageBubble, CharacterCard)
- Hardware-accelerated Framer Motion animations
- `useCallback` on all context CRUD functions (ChatContext) and event handlers (Chat.tsx, CharacterCardV2Showcase)
- `useMemo` for derived state (search filtering in CharacterCardV2Showcase)
- Authenticated API calls use `fetchWithAuth()` (auto-injects Bearer token, handles 401 refresh)

## Troubleshooting

See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) for: backend startup, MCP issues, database reset, Docker networking, post-rebuild verification.

## NEPHILIM System

6 interconnected personas (E.E.V.A., Aegis, Solace, Nyx, Cipher, Aurora) with worldbuilding, progression (5 ranks), and gamification. Prompt construction via `prompt_builder.py` (XML-tagged bookend pattern). Anti-hallucination for wallet personas via ground-truth injection.

**On-demand lore + capabilities (HERMES-Agents Phase 2).** The `LORE_ONDEMAND_ENABLED` flag and the static-3-entity-only legacy path were **retired 2026-07-04** — per-turn retrieval is now unconditional. Beyond the static 3-entity-per-persona prefill, the coordinator retrieves query-relevant lore per turn and appends it after the cached `build_system_prompt` (never inside it — `lru_cache` would poison). Hybrid: deterministic alias/keyword match + bge-m3 semantic search over the full 34-entity wiki (`memory_rag.search_lore`, reuses the RAG embedder; `canon_only` for the semantic tier), deduped vs the static core, trimmed to `LORE_MAX_BUDGET_TOKENS`. This lights up the 16 previously-dormant wiki entities (NPCs/ranks/concepts/faction). "Skills" are **internal** `entity_type: capability` wiki entries (`docs/lore/wiki/capabilities/`) gated by persona + rank + affinity (`lore_retrieval.py`) — never user-invokable; a brief diegetic unlock toast (`CapabilityUnlockToast`, persona voice) fires via `response.metadata.capability_unlocks`. Progression fixes shipped alongside: `affinity_level` now increments (was dead → affinity-gated lore never fired), and an optional seeker-rank prompt block (`LORE_RANK_CONTEXT_ENABLED`). Tuning/eval: `tests/evaluation/eval_lore_retrieval.py`. See [ADR-003](docs/decisions/003-on-demand-hybrid-lore-retrieval.md).

**Single-model native tool brain (ADR-008 P1, flag-gated `TOOL_BRAIN_ENABLED`, default OFF).** The daily driver (abliterated Mistral-Small-24B) natively decides + fills tool calls via Ollama `/api/chat` `tools=`; `services/tool_brain_service.py` runs the loop — each call passes the reused ADR-004 `ToolCallInterceptor` (mcp gate + arg allowlist + hard-block `execute_swap` + HITL) before executing via ADR-009 registry-bound executors (`tools/executor_bindings.py`, with the per-persona safesearch clamp), then the SAME model synthesizes in-voice (no restyle handoff — single model). **TB5 (post-live-test) — the router scopes the surface, the model decides within it.** The live test showed EEVA's full 14-tool surface caused wallet fixation + mis-selection + fabrication, so `routes/chat.py:_try_tool_brain` engages **only on `NEEDS_WEB_SEARCH`** and offers **only the persona's WEB tools** (never wallet; respects Gwen's image/video subset). `NEEDS_WALLET`/`NEEDS_NEITHER` return None → the legacy deterministic wallet flow + the ADR-007 groundedness gate stay in charge. On a web-intent turn it returns an answer **only if a search actually ran** (`used_search=True`); silent-or-answered-without-search falls through to the legacy force-search floor — so every web-intent answer is grounded, none from training data. Wallet stays on the existing propose→confirm→execute / `handle_wallet_query` flow. Superseded the ADR-004 two-stage `AGENTIC` pipeline, which was deleted 2026-07-19. See [ADR-008](docs/decisions/008-two-brain-split-tool-brain-voice-brain.md).

**Search-result quality on the tool-brain path (2026-07-06 fix).** Two filters run in the bound search executor (`tools/executor_bindings.py`), the single choke point feeding both synthesis and citations: (1) a **deterministic image-junk denylist** (`tools/result_filters.py`, always-on, images-only, never-empty fallback) strips icon-CDN/placeholder/favicon/badge/`.svg` junk that aggregated engines surface on keyword collisions; (2) a **per-result bge-m3 relevance floor** (`SearchRelevanceService.filter_relevant`, flag-gated `SEARCH_RELEVANCE_GATE_ENABLED` default OFF, graceful/fail-open) drops keyword-collision outliers a denylist can't catch. **Spurious-refusal handling:** abliteration leaves residual refusal, so the synthesis step can refuse even when the search succeeded; `tool_brain_service.is_synthesis_refusal` detects it and runs ONE bounded prefill-steered retry, exposing `ToolBrainResult.refused`. `routes/chat.py:_try_tool_brain` falls through to the legacy honest floor when `refused` — citations are never stapled onto a refusal. **Query-side (2026-07-06):** the `image_search`/`video_search` tool descriptions instruct the model to formulate `query` as concrete visual keywords, not narrative prose (long prose caused a keyword-collision artwork); and `tool_interceptor._validate_arguments` now applies the query allowlist (non-empty, ≤300 chars, no control chars) to the live ADR-009 search names (`_SEARCH_QUERY_TOOLS`), not just the legacy `brave_web_search` — closing a gap where the tool-brain query was structurally unvalidated.

**Per-persona tool guidance (flag-gated `PERSONA_TOOL_INTENT_IN_PROMPT`, default OFF).** `escalation_policy.tool_intent` was a dead schema field (populated in every persona JSON, read nowhere). When the flag is on, `prompt_builder._get_tool_intent_block_lean` appends those lines as a `<tools>` guidance block in the lean system prompt (static per-persona data → safe inside the `lru_cache`; merged with the wallet copilot block so there's one `<tools>` section). **Eval-gated (2026-07-06): full-7 distinctiveness OFF 0.786 = ON 0.786 (+0.000, flatness 0.006→0.0) — MATCH-OR-BEAT ✅ but voice-NEUTRAL, no measured benefit.** Kept default OFF (flip is authorized/voice-safe, but adds prompt tokens to every persona for no measured gain; tool *selection* is router/native-driven, not prose-driven). Flip only for a concrete tool-behavior need. Setting lives in `config.AgentSettings.tool_intent_in_prompt`. Separately, `PersonaCard.emoji` `max_length` was raised 4→8 (a naive code-point count rejected legit 4-emoji avatars whose glyphs carry variation selectors, e.g. Gwen's `♠️`).

**Persona behavioural constraints in the prompt (flag-gated `PERSONA_CONSTRAINTS_IN_PROMPT`, default OFF).** `do`, `dont`, `boundaries`, `user_relationship` (incl. `exclusivity`) and `escalation_policy.when_to_decline` had **zero readers anywhere in `src/`** — `persona_schema.py` even documents the workaround ("the only reliable lever for word choice, since the lean prompt omits do/dont"). Consequence observed 2026-08-23: a persona declared exclusivity, the model was never told, and it proposed the opposite when probed. Not drift — an unsent rule. When on, `prompt_builder._lean_constraints_block` adds a `<constraints>` section (static per-persona → safe inside the `lru_cache`) and `build_constraint_reminder` emits a one-line restatement injected in `routes/chat.py` **immediately before the latest user turn** (recall is worst mid-context, arXiv:2307.03172 — and this cannot live inside the selector-keyed `lru_cache`). **Negations are re-anchored, not listed**: open models violate negated instructions 77-100% of the time vs affirmative framing ("When Prohibitions Become Permissions"). **`boundaries.content` is deliberately NOT rendered** — unlike `ethics` it is a capability declaration with mixed polarity, and at least one shipped persona has an entry plainly meant as a prohibition carrying no negation marker; emitting it as instructions would assert the opposite of intent. Budgets: 150 tokens cached, 100 per-turn (context rot means more input degrades recall even when the fact is present). Measured on gwen: +182 tokens on, byte-identical off. **Eval-gated before any flip** — this repo has twice measured a prompt-content addition as neutral-or-negative.

**Un-pinning voice exemplars + the session opener (flag-gated `PERSONA_UNPIN_ON_DEPTH`, default OFF; `PERSONA_UNPIN_DEPTH_TURNS`=6).** Three cached `example_dialogues` and `memory_manager`'s first-3 pin reached the model on **every turn for the life of a session**, in dialogue format — the condition few-shot copying feeds on, worsening with the number of similar examples (arXiv:2402.09954); SillyTavern's own guidance is to evict example dialogues once real history establishes the voice. `build_system_prompt(selector, include_examples=)` takes a **second `lru_cache` key** rather than relocating the block out of the cache, so the deliberate voice-last ordering is preserved. `routes/chat.py` decides from `len(body.history)`.

**Per-persona `<format>` block (flag-gated `PERSONA_FORMAT_OVERRIDE_ENABLED`, default OFF) — built, tested, and SHIPPED UNUSED.** `LEAN_FORMAT` ("Reply like texting, not essays… 1-2 sentences" per chunk) goes into every persona's prompt. A persona may now select `dialogue_prefs.format_style` (`texting` | `analytical`) to **replace** it — a constrained enum, not free text, so a persona file cannot inject arbitrary system instructions; unknown values degrade to `LEAN_FORMAT` rather than raising. A swap, never an addition: small models do not arbitrate conflicting instructions (they fall back on the pattern stronger in training, and position does not decide it), which is why adding "finish the analysis" to a persona card while `LEAN_FORMAT` still said "texting, not essays" barely moved the output. A test asserts the two blocks can never both reach the prompt. **Pre-registered experiment (2026-08-15, rule fixed before any data): KILL.** Primary endpoint causal density 0.267→0.343, 6/12 items, exact permutation p=0.596, d=0.155; sensitivity analysis excluding gated answers p=0.492 — still flat. **The root-cause claim was refuted**: removing `LEAN_FORMAT` made answers *shorter* (73.4→51.5 words, shorter on all 12 probes, p=0.0005, d=-1.69), because `LEAN_FORMAT`'s "then a follow-up or a question" exemplar *was* much of the length — the deflection was the volume. So the ~7× gap between persona output and the same model unprompted is **not** explained by the format block, and remains unexplained. No shipped persona declares a `format_style` (a test enforces this). Verdict + pre-registration: `tests/evaluation/persona_eval/analytical_format_prereg.json`, `analyse_format_experiment.py`.

**Deterministic tool-call safety middleware (retained from the retired ADR-004 pipeline).** `ToolCallInterceptor` (`services/tool_interceptor.py`) gates EVERY tool the ADR-008 tool brain executes: per-persona `mcp_access` + an argument-level allowlist (token-enum/amount for swaps; length/control-char for queries, applied to the live ADR-009 search names) + a hard block on `execute_swap` from a non-`user_confirmed` source + HITL short-circuit. Flag `AGENTIC_ARGUMENT_ALLOWLIST` (default ON). `InjectionGuard.sanitize_memory_write` (`services/injection_guard.py`, flag `AGENTIC_INJECTION_GUARD`, default ON) strips tool-call syntax and instruction-override payloads before a RAG memory write, so a stored payload cannot fire on a later turn. Tool-call red-team eval: `tests/evaluation/test_tool_call_safety_redteam.py` + `golden_agentic/` (17 interceptor vectors) — text-safety ≠ tool-call safety.

> The ADR-004 two-stage pipeline itself (`agentic_pipeline.py`, `argument_extractor.py`, `build_scene_contract`, `AGENTIC_ENABLED`) was **deleted 2026-07-19** — it existed only because Magidonia couldn't native-tool-call, and the ADR-008 tool brain superseded it. The injection guard's `check_tool_trigger_source` / `detect_escalation` went with it: that pipeline was their only caller, so they never protected production. See [ADR-004](docs/decisions/004-persona-safe-agentic-tool-calls.md) (Superseded).

**Conversation-control commands (ADR-011).** Companion/RP verbs implemented as **shared session-API endpoints** so the Telegram gateway and React UI behave identically (both thin clients; logic in `services/conversation_control_service.py`). Endpoints: `POST /sessions/{id}/regenerate|continue|undo|narrate|impersonate`, `GET /sessions/{id}/meta`, `PUT/GET/DELETE /sessions/{id}/note`. All reuse `handle_session_chat` (new `persist_user`/`run_post_turn_updates` flags) → same finalize path (first-person, ADR-007 gate, multi-message). The per-session author's note (`session_notes` table, alembic `5session_notes`, `SessionNoteRepository`) injects every turn via the post-`lru_cache` `extra_system_context` seam (`_append_author_note`) — never inside `build_system_prompt`. `MessageRole.NARRATOR` (`/sys` scene beats) is tolerated at 4 role-switch sites (history renders `[Scene: …]`; fact/triplet/tool-brain skip it). **`MessageRole.RECALLED` (2026-08-23) follows the same pattern but is render-time only and never persisted** — RAG-retrieved turns were built with their original role and rendered `Assistant: …`, byte-identical to the model's own last line, so it continued them verbatim; they now render `[Recalled from earlier — background only…]` and `tool_brain_service` skips them (it maps unknown roles to `"user"`, which would present weeks-old text to the router as a fresh request). **Live enablement needs `alembic upgrade head` + backend restart.** See [ADR-011](docs/decisions/011-conversation-control-commands-as-shared-session-api-endpoints.md).

**Lean persona prompt (ADR-005 Phase B).** `build_system_prompt` is `_build_system_prompt_lean` (exemplar-first / voice-last, deduped, drops the static wiki dump — still available per-turn via on-demand lore retrieval). The `PERSONA_LEAN_PROMPT` flag, the per-persona allowlist, and the legacy builder (`_build_system_prompt_legacy`) were **retired 2026-07-04** after the lean builder graduated to prod default for all 7 personas. Distinctiveness comes from a per-persona `voice_signature` (diction/cadence/pattern/anchor/exemplars), excluded from the CV-summary fingerprint so it never drifts the `<identity>`. **Acceptance gate (`tests/evaluation/persona_eval/`) PASSED 7/7**: distinctiveness attribution 0.393→0.732. See [ADR-005](docs/decisions/005-persona-architecture-simplification-eval-first.md).

**Generation-time groundedness gate (ADR-007, flag-gated `GROUNDEDNESS_GATE_ENABLED`, default OFF).** Closes a gap the SearchSettings guards can't reach: those guards (`query_resolution_enabled`, `relevance_gate_enabled`) live inside `tool_calling_service.py` and only run when the intent router already offered a tool — when routing decides `NEEDS_NEITHER`, `routes/chat.py`'s `if not tools:`/fallback branches are bare completions with none of those guards reachable. `GroundednessGateService` (`services/groundedness_gate_service.py`) runs a second, cheap LLM classification on the draft itself — does it assert a specific, falsifiable, temporally-scoped real-world claim (score/date/outcome) with no grounding this turn? — and replaces it with an honest offer-to-search if so. Narrowly scoped (persona lore, general knowledge, and already-grounded turns are explicitly excluded from the classifier prompt) to avoid the named top risk: false-abstention on legitimate answers. **Narrowed again 2026-08-23:** the lore exclusion covered a persona's *backstory* but not the scene it narrates now, so first-person in-scene narration — specific, present-tense, falsifiable-*sounding* — tripped the gate mid-roleplay and replaced the reply with an offer to search. Adds an in-scene-narration exclusion and scopes the live-state clause to real accounts/money; the 2026-08-12 catch case is regression-pinned. **⚠️ `test_routes_chat.py` pins `gate_enabled=False` in its shared fixture, so every gate path in that suite is dark** — write gate tests with it explicitly on (`test_groundedness_roleplay.py`). See [ADR-007](docs/decisions/007-generation-time-groundedness-gate.md).

> **⚠️ MEASURED 2026-08-15 — the gate had a blind spot, and `eval_groundedness_gate.py` did not exist.** The eval set had named that harness in its own description since it was written, so no change to the gate had ever been measurable. Built it (catch-rate and false-abstain-rate reported **separately** — one blended "accuracy" hides exactly this class of bug — plus a flip-rate across repeats; one adversarial miss vetoes a ship regardless of the aggregate). First run, 19 cases × 5 repeats: the gate passed *"your position is currently unhedged after the rebalance, so you're carrying directional risk"* **0/5** — an unverified claim about live position state, wrapped in valid reasoning, on the wallet-capable persona. It fell outside the trigger (not a score/date/statistic about a real-world *event*) and contains no digits; numeral-presence was separately measured **not** to predict firing. Two fixes, both tightenings: **`GROUNDEDNESS_LIVE_STATE_CLAIMS`** (default ON) extends the trigger to the user's own live state and tells the classifier to judge a *premise* on its own rather than the argument built on it; **`GROUNDEDNESS_CLASSIFIER_TEMPERATURE`** (default **0.0**) is now passed explicitly, because `create_llm_client` prefers the persona card's own `model_preferences.temperature` — so the classifier had been inheriting the *speaking* persona's creative setting (cipher 0.65, eeva 0.7, nyx 0.95), giving one safety control six sensitivities. `PERSONA_TEMPERATURE` cannot reach it. Abstained drafts are now logged **in full** (was 80 chars, which is why the evidence had to be reconstructed from a separate run). Result: in-sample catch 0.90→1.00, false-abstain 0.10→0.00, flip 0.10→0.00. **False-abstain on real probes was NOT improved: 1/12 before and after** — in-sample numbers partly measure a corpus written by the same author, so treat the holdout as the honest read. An intermediate variant scored the best holdout (0/12) and was auto-REJECTED for letting a fabricated funding rate through 5/5.

**Companion memory & continuity (ADR-006 Phase 1, M1–M4 BUILT 2026-07-04; gated `MEMORY_CONTEXT_INJECT` + `MEMORY_FACTS_ENABLED`, both default OFF).** The frontier beyond voice (ADR-005): giving personas cross-session memory without flattening voice. Gate 0/0.1 proved that identically-formatted injected blocks homogenize personas — so **M1** injects memory as a per-persona non-echoable `<remembered>` frame over *prose* narratives (`context_framing.py`), never the old `**Header**\n- field: value` skeleton. **M2–M4** add the ontology-lite fact store: a two-table temporal store (`memory_entities`/`memory_facts`, bi-temporal validity + confidence + provenance + ~30 controlled predicates), fully-async triplet extraction off the interactive path (`fact_extraction_worker.py` — abstention few-shot + verbatim quote-span guard + recency-wins writes), and inject-all/top-k retrieval rendered through the same M1 frame. Dev-iterate on the **eeva+nyx canary** (`run_eval.py --personas eeva,nyx`); the gate is always full-7. **⚠️ REVERTED on abliterated (2026-07-06): both flags OFF on prod.** M5 passed on Magidonia and was briefly flipped ON, but the daily-driver switch to abliterated invalidated it — a full eval on the new model showed both injections homogenize the warm personas (EEVA/solace collapse to 0.25; overall 0.804 off → 0.625 on). Abliterated itself is a voice *win* (0.804 > Magidonia 0.732) — the regression was the injection, not the model. **Memory-injection line CLOSED 2026-08-11:** the per-persona framing rework was done and FAILED on abliterated (three deterministic variants C1 minimal 0.708 / C2 reposition 0.542 / C3 in-voice-exemplar 0.500, all < the 0.792 OFF ruler; eeva degraded monotonically 0.625→0.5→0.375) — prompt-framing cannot recover the voice cost; re-entry needs a model-level change, not more framing. Knowledge-graph/Neo4j was evaluated and **rejected** (not the right regime at single-user scale). See [ADR-006](docs/decisions/006-companion-memory-and-continuity-eval-first.md).

> **Full reference:** [`docs/NEPHILIM_REFERENCE.md`](docs/NEPHILIM_REFERENCE.md) — personas, schema, progression tables, visual theme, onboarding, Phase 7 details.
> **Lore documents:** [`docs/lore/README.md`](docs/lore/README.md) — worldbuilding, factions, ranks.
> **Lore wiki (canonical + runtime):** [`docs/lore/wiki/`](docs/lore/wiki/) — typed entity graph (personas/houses/ranks/locations/factions/concepts). Now injected into system prompts at chat time via `src/coordinator/lore_loader.py`. Sync wiki→persona JSON with `python scripts/utils/lore_sync.py`. Validate with `python scripts/utils/lore_wiki.py check`; regenerate index with `... index`. See [ADR-001](docs/decisions/001-lore-as-typed-markdown-wiki-not-a-graph-db.md).

## Documentation

- `README.md` - User setup guide, features
- `docs/setup/DOCKER_QUICKSTART.md` - Docker deployment
- `docs/development/ADDING_MCP_SERVERS.md` - MCP integration guide
- `docs/development/TESTING_GUIDE.md` - Testing guide
- `docs/lore/` - All NEPHILIM worldbuilding and lore documents (see `docs/lore/README.md`)
- **Semantic platform (concept architecture)** — lives at the ecosystem root, not here, because it spans projects: [../docs/architecture/SEMANTIC_PLATFORM.md](../docs/architecture/SEMANTIC_PLATFORM.md). Implementation starts in this repo; the document explains why.

### Protected Reference Files (do NOT delete)

These files have **no build dependency** and are never imported, so automated cleanup passes may flag them as dead code. **Do not delete them.** Open directly in a browser for visual reference during CSS iteration.

| File | Purpose |
|------|---------|
| `react-ui/rarity-effects-showcase.html` | **Canonical VFX reference** — gacha-quality card effect showcase (12 variants). Use this when iterating on card effects without needing the dev server. Uses legacy `.rarity-*` class names — see the file header for mapping to current `.order-*` equivalents. Live implementations: `CharacterCard.module.css` (V1) and `CharacterCardV2.module.css` (V2). |
