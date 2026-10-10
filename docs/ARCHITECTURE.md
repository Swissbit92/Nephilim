---
title: Architecture
status: active
created: 2026-04-19
last_reviewed_on: 2026-10-10
review_in: 6 months
applies_to: nephilim
published_url: https://claude.ai/code/artifact/335a787e-3444-4eb1-8b1b-b217319da38f
---

# Architecture

Reference-style: tables and diagrams, not prose narratives. A local-first,
persona-driven chat platform — FastAPI backend + React 19 frontend + local Ollama,
SQLite for persistence, FAISS for semantic memory.

```archstat
[
  {
    "label": "Trading data",
    "value": "Read",
    "note": "never writes",
    "state": "ok"
  },
  {
    "label": "Inference",
    "value": "Local",
    "note": "on-device"
  },
  {
    "label": "Clients",
    "value": "Two",
    "note": "UI + Telegram"
  },
  {
    "label": "Wallet path",
    "value": "Manual",
    "note": "never model-decided",
    "state": "ok"
  },
  {
    "label": "Off-machine",
    "value": "Two",
    "note": "MCP servers",
    "state": "warn"
  }
]
```

## System context

Verified against the call graph, not sketched from memory — every edge below is
an import or call that exists in `src/coordinator/`.

```archview
{
  "caption": "routes/chat.py is the orchestrator; the turn pipeline, the tool brain and the legacy wallet path are its three siblings.",
  "nodes": [
    {
      "id": "ui",
      "label": "React UI",
      "sub": "chat \u00b7 personas \u00b7 wallet",
      "tech": "React 19 \u00b7 TS",
      "kind": "external",
      "note": "The React client. Hits the same session API the Telegram gateway does."
    },
    {
      "id": "tg",
      "label": "telegram-gateway",
      "sub": "allowlisted single user",
      "tech": "own venv \u00b7 launchd",
      "kind": "external",
      "note": "An allowlisted single-user bot. Deliberately thin \u2014 it holds no logic the UI does not also use."
    },
    {
      "id": "routes",
      "label": "routes/chat.py",
      "sub": "orchestrator \u2014 picks the path",
      "tech": "FastAPI",
      "kind": "service",
      "note": "The orchestrator. Decides which of three paths a turn takes, and is where the tool brain is gated."
    },
    {
      "id": "chat",
      "label": "handle_session_chat",
      "sub": "per-turn phase pipeline",
      "tech": "ChatDeps / ChatTurnState",
      "kind": "module",
      "note": "The per-turn pipeline: load identity, build the prompt, select history, generate, persist."
    },
    {
      "id": "brain",
      "label": "tool_brain_service",
      "sub": "web intents only, never wallet",
      "tech": "Ollama native tool-calling",
      "kind": "module",
      "note": "Native tool-calling on web intents only. It is never offered wallet tools \u2014 the full surface caused fixation and fabrication in a live test."
    },
    {
      "id": "qh",
      "label": "query_handler_service",
      "sub": "legacy wallet + force-search",
      "tech": "bge-m3 semantic router",
      "kind": "module",
      "note": "The legacy deterministic path. Still in charge of wallet, and the floor the tool brain falls through to."
    },
    {
      "id": "persona",
      "label": "persona_memory",
      "sub": "-> prompt_builder, lean prompt",
      "tech": "lru_cache",
      "kind": "module",
      "note": "Loads the persona and its cached CV summary. The cache is why per-turn context must be appended after it, never inside."
    },
    {
      "id": "facts",
      "label": "memory_fact_retrieval",
      "sub": "through the M1 frame",
      "tech": "bi-temporal store",
      "kind": "module",
      "note": "Retrieves stored facts through the per-persona frame. Off on prod \u2014 the injection homogenised the warm personas."
    },
    {
      "id": "llm",
      "label": "llm_client",
      "sub": "per-persona sampling",
      "tech": "Ollama",
      "kind": "module",
      "note": "Ollama, on-device, with per-persona sampling. Local-first is a constraint: the alternative leaks financial context."
    },
    {
      "id": "repos",
      "label": "repositories/",
      "sub": "all extend BaseRepository",
      "tech": "SQLite \u00b7 pooled",
      "kind": "module",
      "note": "Every repository extends BaseRepository, so nothing opens a raw SQLite connection."
    },
    {
      "id": "guard",
      "label": "tool_interceptor",
      "sub": "validate() only \u2014 gates, never executes",
      "tech": "deterministic middleware",
      "kind": "secret",
      "note": "Validates every tool call before it runs \u2014 access, arguments, and a hard block on swaps that did not come from a human."
    },
    {
      "id": "exec",
      "label": "executor_bindings",
      "sub": "runs the tool the guard permitted",
      "tech": "registry-bound",
      "kind": "module",
      "note": "Runs the tool the guard permitted. Separate from the guard on purpose: gating and executing are different jobs."
    },
    {
      "id": "relevance",
      "label": "search_relevance_service",
      "sub": "per-result cosine floor",
      "tech": "reuses the RAG embedder",
      "kind": "module",
      "note": "Drops search results that are non-empty but off-topic, which the no-results guard cannot catch."
    },
    {
      "id": "rag",
      "label": "memory_rag",
      "sub": "EpisodicMemoryRAG \u00b7 startup singleton",
      "tech": "bge-m3",
      "kind": "module",
      "note": "Semantic recall over past sessions and the lore wiki, sharing one embedder."
    },
    {
      "id": "sqlite",
      "label": "SQLite",
      "sub": "chats \u00b7 progression \u00b7 facts \u00b7 wallets",
      "tech": "local file",
      "kind": "store",
      "note": "Sessions, progression, facts and wallets. Local file, pooled access."
    },
    {
      "id": "faiss",
      "label": "FAISS",
      "sub": "session + lore vectors",
      "tech": "local index",
      "kind": "store",
      "note": "The vector index for session and lore search."
    },
    {
      "id": "ollama",
      "label": "Ollama",
      "sub": "LLM + bge-m3 embeddings",
      "tech": "on-device, Metal GPU",
      "kind": "external",
      "note": "On-device inference and embeddings, on the Metal GPU."
    },
    {
      "id": "web",
      "label": "SearXNG / Brave",
      "sub": "ephemeral Docker",
      "tech": "MCP",
      "kind": "external",
      "note": "Search, via SearXNG first so the query stays local, falling back to Brave."
    },
    {
      "id": "jupiter",
      "label": "Jupiter / Solana",
      "sub": "long-running Docker",
      "tech": "MCP",
      "kind": "external",
      "note": "The wallet path. Always propose then confirm then execute \u2014 never model-decided."
    }
  ],
  "edges": [
    {
      "from": "ui",
      "to": "routes",
      "label": "session API"
    },
    {
      "from": "tg",
      "to": "routes",
      "label": "the SAME API"
    },
    {
      "from": "routes",
      "to": "chat",
      "label": "handle_session_chat"
    },
    {
      "from": "routes",
      "to": "brain",
      "label": "_try_tool_brain"
    },
    {
      "from": "routes",
      "to": "qh",
      "label": "legacy fallthrough"
    },
    {
      "from": "chat",
      "to": "persona"
    },
    {
      "from": "chat",
      "to": "facts"
    },
    {
      "from": "chat",
      "to": "llm"
    },
    {
      "from": "chat",
      "to": "repos"
    },
    {
      "from": "brain",
      "to": "guard",
      "label": "every tool call"
    },
    {
      "from": "guard",
      "to": "exec",
      "label": "only if permitted"
    },
    {
      "from": "exec",
      "to": "relevance"
    },
    {
      "from": "relevance",
      "to": "rag",
      "label": "reuses the embedder"
    },
    {
      "from": "exec",
      "to": "web"
    },
    {
      "from": "qh",
      "to": "jupiter",
      "label": "propose -> confirm -> execute"
    },
    {
      "from": "llm",
      "to": "ollama"
    },
    {
      "from": "repos",
      "to": "sqlite"
    },
    {
      "from": "facts",
      "to": "sqlite"
    },
    {
      "from": "rag",
      "to": "faiss"
    }
  ]
}
```

**Wallet is never model-decided.** `_try_tool_brain` returns `None` on
`NEEDS_WALLET` unconditionally, so the wallet path stays on the deterministic
propose→confirm→execute flow. The tool brain is offered **web tools only** — a
live test showed the full 14-tool surface caused wallet fixation and
fabrication.

**The guard gates; it does not execute.** `ToolCallInterceptor` exposes
`validate()` and nothing else. Execution happens in `executor_bindings` *after*
the check passes, which is why the two are separate nodes above rather than one.

**Local-first is a constraint, not a preference.** Inference runs on-device
because the alternative leaks financial context to a third party. The two Docker
MCP servers are the only paths off the machine.

The Telegram gateway consumes the **same** session API as the React UI
([ADR-011](decisions/011-conversation-control-commands-as-shared-session-api-endpoints.md)),
which is why regenerate/continue/undo exist once rather than twice.

## The turn pipeline

The diagram above shows which modules exist. It does not show what happens when
someone actually sends a message, which is the thing most questions are really
about.

```archview
{
  "id": "turn",
  "caption": "One chat turn, from message to persisted reply.",
  "nodes": [
    {
      "id": "arrive",
      "label": "Message arrives",
      "sub": "React UI or Telegram",
      "kind": "external",
      "note": "A message lands. Both clients use the same endpoint, which is the whole point of ADR-011."
    },
    {
      "id": "identity",
      "label": "Load identity",
      "sub": "persona + cached CV",
      "kind": "module",
      "note": "Persona and cached CV summary. The voice signature stays out of the cache key so edits take effect."
    },
    {
      "id": "route",
      "label": "Route the intent",
      "sub": "semantic, bge-m3",
      "kind": "module",
      "note": "Follow-up check, then a narrow keyword fast-path, then the semantic router. Wallet is never model-decided."
    },
    {
      "id": "prompt",
      "label": "Build the prompt",
      "sub": "lean, exemplar-first",
      "kind": "module",
      "note": "The lean prompt: exemplars first, voice last. It is cached, so anything per-turn is appended after it."
    },
    {
      "id": "history",
      "label": "Select history",
      "sub": "token budget + RAG",
      "kind": "module",
      "note": "Messages chosen against a token budget, with semantic recall over older sessions."
    },
    {
      "id": "generate",
      "label": "Generate",
      "sub": "per-persona sampling",
      "kind": "external",
      "note": "On-device generation with per-persona sampling overrides."
    },
    {
      "id": "guard",
      "label": "Post-process",
      "sub": "strip tool leaks, first person",
      "kind": "secret",
      "note": "Validates every tool call before it runs \u2014 access, arguments, and a hard block on swaps that did not come from a human."
    },
    {
      "id": "persist",
      "label": "Persist",
      "sub": "SQLite",
      "kind": "store",
      "note": "The exchange is written through the pooled repository layer."
    },
    {
      "id": "after",
      "label": "Post-turn updates",
      "sub": "progression, facts, summaries",
      "kind": "module",
      "note": "Progression, fact extraction and summaries run after the reply is out, so none of them can slow it."
    }
  ],
  "edges": [
    {
      "from": "arrive",
      "to": "identity"
    },
    {
      "from": "identity",
      "to": "route"
    },
    {
      "from": "route",
      "to": "prompt"
    },
    {
      "from": "prompt",
      "to": "history"
    },
    {
      "from": "history",
      "to": "generate"
    },
    {
      "from": "generate",
      "to": "guard"
    },
    {
      "from": "guard",
      "to": "persist"
    },
    {
      "from": "persist",
      "to": "after"
    }
  ]
}
```

```archflow
{
  "view": "turn",
  "flows": [
    {
      "id": "chat-turn",
      "label": "A single chat turn",
      "steps": [
        {"node": "arrive", "note": "Both clients hit the same session API. That is the whole point of ADR-011 — regenerate, continue and undo exist once, not twice."},
        {"node": "identity", "note": "The persona JSON and its cached CV summary are loaded. The cache is why the voice signature must stay OUT of the fingerprint, or edits would not take."},
        {"node": "route", "note": "Intent routing is semantic-primary: follow-up detection, then a narrow keyword fast-path, then the bge-m3 router. Wallet is never model-decided."},
        {"node": "prompt", "note": "The lean prompt is built exemplar-first and voice-last. It is lru_cached, so anything per-turn (lore, author's note) must be appended AFTER it rather than baked in."},
        {"node": "history", "note": "Messages are selected against a token budget, with semantic recall over past sessions."},
        {"node": "generate", "note": "Ollama generates on-device with per-persona sampling overrides. Local-first is a constraint, not a preference — the alternative leaks financial context to a third party."},
        {"node": "guard", "note": "Leaked tool names are stripped, first person is enforced, and any tool call that ran had to pass the interceptor before execution."},
        {"node": "persist", "note": "The exchange is written to SQLite through the pooled BaseRepository — never a raw connection."},
        {"node": "after", "note": "Progression, resonance, fact extraction and summarisation run after the reply is already out, so none of them can slow the response."}
      ]
    }
  ]
}
```

## Components

Layered: **routes → services → repositories → models**, mirrored on the frontend.

| Component | Responsibility | Module |
|-----------|----------------|--------|
| API routes | HTTP endpoints: chat, sessions, personas, nephilim, auth, wallet | `src/coordinator/routes/` |
| Chat-turn orchestration | Per-turn phase pipeline (load identity → build prompt → select history → generate → persist → post-updates), typed `ChatDeps`/`ChatTurnState`; `persist_user`/`run_post_turn_updates` flags let regenerate/continue reuse it | `services/chat_session_service.py` |
| Conversation-control (ADR-011) | Shared session-API verbs consumed by BOTH the React UI and Telegram gateway: regenerate/continue/undo/narrate(`/sys`)/impersonate + session metadata + author's note. Reuses the standard finalize path (no parallel LLM plumbing) | `services/conversation_control_service.py` (routes in `routes/chat.py` + `routes/sessions.py`) |
| Query routing | Intent → wallet / brave / agentic / pure-LLM; wallet ground-truth injection | `services/query_handler_service.py` |
| Wallet-creation flow | Guided multi-turn onboarding, typed `WalletFlowStep`(IntEnum)/`WalletFlowState` + `match` dispatch | `services/wallet_creation_flow_service.py` |
| LLM orchestration | Ollama completion, per-persona sampling, forced tool-calling | `llm_client.py`, `services/llm_completion_service.py`, `services/tool_calling_service.py` |
| Prompt construction | Lean exemplar-first / voice-last system prompt (XML-tagged bookend); `lru_cache`d | `prompt_builder.py`, `persona_memory.py` |
| Memory / RAG | Token-budget message selection, bge-m3 semantic search, summaries, fact extraction | `memory_manager.py`, `memory_rag.py`, `cv_summarizer.py`, `fact_extractor.py` |
| Companion memory — framing (ADR-006 M1) | Wrap injected memory in a per-persona non-echoable `<remembered>` frame over prose narratives (anti-homogenization, the Gate-0/0.1 fix) | `context_framing.py` |
| Companion memory — fact store (ADR-006 M2–M4) | Two-table temporal ontology-lite store + async triplet extraction (abstention + quote-span guard, off the interactive path) + recency-wins write + inject-all/top-k retrieval through the M1 frame | `repositories/memory_fact_repository.py`, `triplet_extractor.py`, `fact_write_policy.py`, `fact_extraction_worker.py`, `memory_fact_retrieval.py` |
| Lore | On-demand hybrid lore retrieval over the typed wiki; rank/affinity-gated capabilities | `lore_loader.py`, `lore_retrieval.py` |
| In-voice status lines | Every user-facing image line is GENERATED in the persona's voice, never a status string. Variant floor + shuffled permutation queue + near-duplicate rejection; a lifetime cache only where the model is evicted (`_MODEL_UNREACHABLE`), recycle-on-drain elsewhere with exponential backoff on failure | `services/persona_lines.py` |
| Tool-call safety middleware | Deterministic pre-execution gating on every tool-brain call: `mcp_access` + argument allowlist + HITL; plus RAG memory-write sanitization | `services/tool_interceptor.py`, `services/injection_guard.py` |
| Persistence | SQLite repositories — ALL extend `BaseRepository` via `db_adapter` (connection pooling, thread-safe) | `repositories/` |
| Configuration | Per-subsystem settings package (llm/search/memory/wallet/auth/routing/lore/agent) + `get_settings()` | `config/` |
| Composition root | `startup.py` = thin orchestrator (`initialize_all()` ordering + `build_app_state()`), re-exporting `get_*`/`init_*` singletons split by cluster into `di/{repositories,services,jupiter}.py` (2026-08-22); `dependencies.py` = FastAPI `Depends` providers (`require_*`→503-on-uninit, `optional_*`→None) resolving `startup.get_X()` at call time | `app_state.py`, `dependencies.py`, `startup.py`, `di/`, `server.py` |

## Data

| Source | Format | Writer | Readers |
|--------|--------|--------|---------|
| `chat_sessions`, `messages`, `conversation_summaries` | SQLite (`messages.role` ∈ user/assistant/**narrator**, ADR-011) | session / message / summary repos | `chat_session_service`, `memory_manager` |
| `session_notes` (ADR-011) | SQLite one-row-per-session author's note (FK-cascade to `chat_sessions`); alembic `5session_notes` + `_ensure_table` dual-cover | `session_note_repository` | `chat_session_service._append_author_note` (injected every turn via `extra_system_context`) |
| `emotional_state` | SQLite | `emotional_state_repository` | `chat_session_service` (trust/rapport/mood) |
| `seeker_profiles`, `persona_affinity`, `resonance_log`, `unlocked_lore` | SQLite | `seeker_progression_repository` | `chat_session_service`, nephilim routes |
| `user_profiles` | SQLite | `user_profile_repository` | cross-session memory injection |
| `memory_entities`, `memory_facts` (ADR-006) | SQLite S-P-O triples, bi-temporal `valid_from`/`valid_to`, confidence, provenance | fact worker (M3) | fact retrieval (M4) |
| wallet registry / summary / **flow** | SQLite | `wallet_registry` / `wallet_summary` / **`wallet_flow`** repos | `query_handler_service`, `wallet_creation_flow_service` |
| Session + lore vector indexes | FAISS | `memory_rag` | `chat_session_service` (semantic recall, on-demand lore) |

## Key invariants

| Invariant | What it guarantees | Where it is enforced |
|---|---|---|
| **Read-only on trading data** | A companion bug can never reach capital | No write path to `btc_data` exists |
| **The mnemonic is never persisted** | Shown once at the password step, then wiped. There is nowhere for it to leak from | Structural — `WalletFlowState` has no field and the table has no column |
| **Settings import from the package root** | Test patch-paths keep working, because `..config` re-exports are the contract | `config/__init__.py` |
| **Every repository extends `BaseRepository`** | One pooled, thread-safe connection rather than raw handles | `db_adapter.py` |
| **Per-turn content never enters the cached prompt** | Lore and memory are appended *after* `build_system_prompt`, or the `lru_cache` would serve one user's context to another | `routes/chat.py`, via `extra_system_context` |
| **Companion memory is default OFF** | The M5 gate passed on the previous model and does not hold on the current one — both injections homogenise the warm personas | `MEMORY_CONTEXT_INJECT`, `MEMORY_FACTS_ENABLED` |
| **Facts are invalidated, never deleted** | History stays reconstructable; a correction does not erase what was believed before | `valid_to` on `memory_facts` |
| **Search results pass one choke point** | Junk filtering and the relevance floor apply to synthesis and citations alike, so the two can never disagree | `tools/executor_bindings.py` |
| **Citations are never stapled to a refusal** | A refused answer stays a refusal instead of looking sourced | `routes/chat.py`, on `ToolBrainResult.refused` |
| **A lifetime cache is only for a line said while the model is gone** | A status line cannot silently become the same line forever. Measured: 9 of 9 flagged repetition turns were byte-identical replays of one cached generation; genuine model cross-turn repetition was 0/127 | `scripts/checks/lifetime_cache_only_when_model_is_gone.py` ([INV](INVARIANTS.md)) |

## Cross-repo contracts

See ecosystem-level contracts (nephilim is read-only; these apply to CRA / btc_price_tracker):
- [../../docs/shared/indicator_api.md](../../docs/shared/indicator_api.md)
- [../../docs/shared/launchd_schedule.md](../../docs/shared/launchd_schedule.md)

The Telegram gateway (`services/telegram-gateway/`) couples to nephilim only through the localhost HTTP session API — never SQLite/FAISS directly.

## Decisions

A pointer to a directory is not an index — it makes the reader open twelve files
to find out which one governs the thing they are looking at. ADR-008 in
particular decides the branch at the centre of the System context diagram above,
and this page previously never named it.

| ADR | Decides | Status |
|---|---|---|
| [001](decisions/001-lore-as-typed-markdown-wiki-not-a-graph-db.md) | Lore is a typed markdown wiki, not a graph DB | Accepted |
| [002](decisions/002-remove-mongodb-mcp.md) | MongoDB MCP removed from the persona path | Accepted |
| [003](decisions/003-on-demand-hybrid-lore-retrieval.md) | Lore is retrieved on demand, hybrid dense + lexical | Accepted |
| [004](decisions/004-persona-safe-agentic-tool-calls.md) | Persona-safe agentic tool calls | **Superseded** — by 008/009 |
| [005](decisions/005-persona-architecture-simplification-eval-first.md) | Persona simplification, gated eval-first | Accepted |
| [006](decisions/006-companion-memory-and-continuity-eval-first.md) | Companion memory and continuity | Accepted |
| [007](decisions/007-generation-time-groundedness-gate.md) | Groundedness gate at generation time | Proposed |
| [008](decisions/008-two-brain-split-tool-brain-voice-brain.md) | **Two-brain split: tool brain vs voice brain** — the branch the diagram above turns on | Proposed |
| [009](decisions/009-layered-toolkit-registry-generic-web-toolset-inner-wisdom-skills.md) | Layered toolkit registry | Accepted |
| [010](decisions/010-image-search-result-quality-and-spurious-refusal-handling.md) | Image-search quality and spurious refusals | Accepted |
| [011](decisions/011-conversation-control-commands-as-shared-session-api-endpoints.md) | Conversation control as shared session API — consumed by both the React UI and the Telegram gateway | Proposed |
| [012](decisions/012-persona-configurable-deterministic-word-substitutions.md) | Persona-configurable deterministic word substitutions | Accepted |

"Proposed" here does not mean unbuilt. Several of these are implemented behind
flags that are off, or on in production while the ADR is still open — the ADR
status tracks the *decision*, not the deployment. Which flags are actually live
is recorded in `CLAUDE.md`, deliberately not here: this page describes structure,
and runtime state on a structural page is the thing that goes stale first.

## Glossary

| Term | What it means here |
|---|---|
| **Persona** | A JSON-defined character — lore, voice, behaviour, and which tools it may use |
| **Voice signature** | The per-persona diction and cadence block. Deliberately excluded from the CV-summary cache key so edits take effect |
| **Tool brain** | The model deciding and filling its own tool calls natively, rather than a deterministic router doing it |
| **Force-search** | The legacy path: the router decides a search is needed and runs it directly, bypassing the model's tool-calling |
| **Semantic router** | Intent classification by embedding similarity rather than keywords. Scored nearest-example, not by centroid |
| **RAG** | Retrieval-augmented generation — pulling relevant past messages or lore into the prompt |
| **FAISS** | The local vector index behind that retrieval |
| **MCP** | Model Context Protocol — how external tools (search, wallet) are exposed |
| **HITL** | Human in the loop. Required before anything that spends |
| **Groundedness gate** | A second cheap classification of the draft, catching a specific factual claim made with nothing to back it |
| **Interceptor** | Deterministic middleware that validates every tool call before it runs. It gates; it never executes |
| **Abliterated** | A model with its refusal behaviour removed. Leaves residue, which is why a spurious-refusal retry exists |
| **Seeker / resonance / affinity** | The progression system — the user's rank, the points earned, and per-persona familiarity |
