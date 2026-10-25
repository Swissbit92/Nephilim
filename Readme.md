# NEPHILIM — AI Companion Platform

> **Local AI Companions with Personality, Memory, Live Data & Wallet Integration**
> _Private · Local-First · Docker-Ready · React 19 + FastAPI_

<div align="center">

[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg?logo=docker)](https://docker.com)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![React](https://img.shields.io/badge/React-19-61dafb.svg)](https://reactjs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg)](https://fastapi.tiangolo.com)
[![Ollama](https://img.shields.io/badge/Ollama-Latest-orange.svg)](https://ollama.ai)
[![SQLite](https://img.shields.io/badge/SQLite-3-003B57.svg?logo=sqlite)](https://sqlite.org)

**[What Can This Do?](#what-can-this-do) · [Docker Setup](#quick-start-docker) · [Local Setup](#local-development-setup) · [Architecture](#architecture)**

</div>

---

## Table of Contents

- [What Can This Do?](#what-can-this-do)
- [How Does It Compare?](#how-does-it-compare)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [System Requirements](#system-requirements)
- [Quick Start (Docker)](#quick-start-docker)
- [Local Development Setup](#local-development-setup)
- [Architecture](#architecture)
- [Available AI Companions](#available-ai-companions)
- [NEPHILIM Worldbuilding System](#nephilim-worldbuilding-system)
- [Usage](#usage)
- [Testing & Quality](#testing--quality)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

---

## What Can This Do?

**5-Second Pitch:** Local AI companions with personality-driven conversations, web search, trading data, Solana wallet integration, and advanced memory — all running on your machine.

### Key Capabilities

- **7 AI Companions** (1 Legacy + 6 NEPHILIM) with psychological depth and emotional tracking
- **Natural Multi-Message Conversations** (2-4 messages per response, like texting a real person)
- **Advanced Memory System** — remembers you across sessions and extracts facts
  automatically into a Neo4j knowledge graph, with bi-temporal validity so a fact
  that stops being true is superseded rather than deleted. Extraction fires both on
  a turn interval **and when a conversation ends**, which matters more than it
  sounds: measured on real usage, 98.3% of sessions are shorter than the interval,
  so without the end-of-session trigger almost nothing was ever saved.
- **Web Search with Citations** — personas autonomously search Brave API with mandatory sources
- **Jupiter Wallet Integration** — Solana DEX trading, autonomous strategies, trade proposals (Phase 8)
- **Google OAuth Authentication** — secure login with JWT access/refresh tokens
- **Summoning Ritual System** — five-phase animated card pulls with audio feedback
- **Seeker Progression** — ranks, resonance, faction affinity, unlockable lore fragments
- **100% Local & Private** — all conversations stay on your device (no data transmission)
- **One-Command Docker Setup** — Nginx reverse proxy, full NEPHILIM UI out of the box

### Who Is This For?

- **Developers** wanting a local ChatGPT alternative with customizable AI companions
- **Crypto Enthusiasts** needing a research assistant with live Bitcoin data and Solana wallet
- **Privacy-Conscious Users** who want full control over their AI conversations
- **AI Experimenters** interested in companion-driven conversational AI with advanced memory

---

## How Does It Compare?

| Feature | NEPHILIM | Open WebUI | LM Studio | Jan | LibreChat | ChatGPT |
|---------|----------|------------|-----------|-----|-----------|---------|
| **100% Local & Private** | Yes | Yes | Yes | Yes | Yes | No (Cloud) |
| **Multi-Message Conversations** | Yes (2-4 msgs) | No | No | No | No | No |
| **AI Companion System** | Yes (7 companions) | No | No | No | No | No |
| **Advanced Memory (RAG)** | Yes (FAISS + profiles) | No | No | No | No | No |
| **Emotional Tracking** | Yes (trust/rapport) | No | No | No | No | No |
| **Web Search Integration** | Yes (Brave API) | Yes (Plugins) | No | No | Yes (Plugins) | Yes (Bing) |
| **Live Trading Data** | No (removed) | No | No | No | No | No |
| **Wallet / DEX Trading** | Yes (Jupiter/Solana) | No | No | No | No | No |
| **OAuth Authentication** | Yes (Google) | Yes | No | No | Yes | Yes |
| **Gamification / Progression** | Yes (Seeker ranks) | No | No | No | No | No |
| **One-Command Docker Setup** | Yes | Partial | Yes (GUI) | Yes (GUI) | Partial | N/A |
| **Open Source** | Yes | Yes | No | No | Yes | No |
| **Cost** | Free | Free | Free | Free | Free | $20/mo |

---

## Features

### AI & Conversational Intelligence

| Feature | Description |
|---------|-------------|
| **Dynamic AI Companions** | 7 companions with unique personality, expertise, and voice |
| **Advanced Memory System** | Importance scoring, auto-summarization, RAG semantic search, cross-session user profiles, and a Neo4j fact graph with bi-temporal validity (`valid_from`/`valid_to` for world time, `created_at`/`expired_at` for belief time) — facts are invalidated, never deleted, so "what did you used to think?" stays answerable |
| **Multi-Message Conversations** | 2-4 messages per response with staggered rendering (1.2s delays) |
| **Psychological Depth** | core_wound, defense_style, growth_edge, contradiction_pairs per persona |
| **Emotional State Tracking** | trust_level, rapport, and current_mood across conversations |
| **Advanced Sampling** | Per-persona temperature, top_k, top_p, repeat_penalty with presets |

### External Integrations

| Feature | Description |
|---------|-------------|
| **Web / Image / Video Search** | SearXNG-primary (Brave fallback) web, image, video & news search with mandatory citation validation, per-persona NSFW safesearch clamp, and deterministic junk-result filtering for image search |
| **Jupiter / Solana Wallet** | DEX swap proposals, autonomous DCA/RSI strategies, AES-encrypted key storage |
| **Email Notifications** | Optional SMTP alerts for executed trades |

### Authentication & Security

| Feature | Description |
|---------|-------------|
| **Google OAuth** | Sign in with Google via `@react-oauth/google` SDK |
| **JWT Tokens** | Short-lived access tokens (1h) + HttpOnly refresh cookies (30d) |
| **Auth Middleware** | FastAPI dependency injection guards all protected routes |
| **Local Bypass** | Set `AUTH_REQUIRED=false` for dev/offline use without Google |
| **Protected Routes** | Frontend `ProtectedRoute` wrapper redirects unauthenticated users to `/login` |

### User Experience

| Feature | Description |
|---------|-------------|
| **Summoning Ritual System** | Five-phase animated card pulls with 1x/5x/10x multi-pull |
| **Seeker Progression** | Ranks (Initiate → Nephilim), resonance, faction affiliation, unlockable lore |
| **Persistent Chat History** | SQLite-backed sessions with automatic orphan cleanup |
| **Session Switching** | Sidebar navigation between persona chats |
| **Glassmorphic UI** | Dark void theme (`#0B0B0D`), `backdrop-blur-xl`, cyan/magenta accents |
| **Mobile Optimization** | Responsive layout, touch gestures, bottom tab navigation |
| **WCAG AA Compliance** | 4.5:1 contrast ratios, keyboard navigation, `aria-label` on interactive elements |

---

## Tech Stack

**Current as of February 2026**

### Frontend
- **React 19** with TypeScript 4.9.5
- **Framer Motion** for 60fps animations
- **Tailwind CSS** for utility-first styling
- **@react-oauth/google** for Google Sign-In
- **Lucide React** for icons
- **Playwright** for E2E testing

### Backend
- **FastAPI 0.100+** with Uvicorn ASGI server
- **Python 3.11+** with type hints
- **Pydantic 2.x** + `pydantic-settings` for validated configuration
- **SQLite 3** for persistent storage
- **Alembic** for database migrations
- **google-auth** + **PyJWT** for OAuth/JWT authentication
- **APScheduler** for autonomous strategy execution

### AI/ML
- **Ollama** local LLM server (native macOS — Metal GPU acceleration automatic via Apple Silicon)
- **gemma4:26b** (daily-driver persona model — MoE Gemma 4, 128 experts / 8 active (~4B of 25.8B), 50.7 tok/s on Metal measured 2026-10-25. **Needs `OLLAMA_REASONING=false`** — it is a reasoning model and without that its thinking stream consumes the whole output budget, returning empty replies). Magidonia-24B-v4.3 was the daily driver until 2026-07-05 and is **deleted from disk**.
- ~~**gemma2:9b-instruct-q5_K_M**~~ — **NOT a fallback and NOT installed.** The fallback
  model name was removed deliberately (`require_model_configured` refuses to start rather
  than silently run a different model), and this tag is no longer pulled. It is named here
  only because a worktree has no `.env`, so this default once made a test called
  `test_the_deployed_model_returns_text` exercise a 9B model at 4096 context for months
  while passing — see docs/LESSONS_LEARNED.md.
- **bge-m3:latest** for embeddings (8192 native context, 1024-dim, L2-normalised). `nomic-embed-text` was
  replaced 2026-06-23: its small window 500s past 2048 tokens on the legacy `/api/embeddings` endpoint.
- **FAISS CPU** for vector search and semantic retrieval
- **LangChain** for LLM orchestration

### Integrations
- **Brave Search API** for web search (per-persona `mcp_access`, ephemeral Docker)
- **Jupiter DEX** for Solana token swaps (via Docker MCP)
- **Docker + Docker Compose** with Nginx reverse proxy

---

## System Requirements

### Docker Setup (Recommended)

| Component | Requirement | Notes |
|-----------|-------------|-------|
| **OS** | Windows 10/11, macOS 13+, or Linux | — |
| **Docker Desktop** | v24.0+ with Compose v2.0+ | [docker.com](https://docker.com) |
| **Disk Space** | 15GB free | Images + models |
| **RAM** | 8GB min, 16GB recommended | Ollama LLM inference |
| **GPU** | Apple Silicon (Metal, built-in) | Metal GPU acceleration is automatic with native Ollama on macOS |

### Local Development (Mac Mini M4 Pro — primary deployment)

| Component | Requirement | Notes |
|-----------|-------------|-------|
| **Python** | 3.12+ | [python.org](https://python.org) |
| **Node.js** | v22+ with npm — CI and `.nvmrc` pin **24 LTS**. Any Node 17+ needs `NODE_OPTIONS=--openssl-legacy-provider` for `react-scripts@5`; already baked into the `package.json` scripts | [nodejs.org](https://nodejs.org) |
| **Ollama** | Latest (native, not Docker) | [ollama.ai](https://ollama.ai) — Docker-on-Mac runs Ollama CPU-only |
| **GPU** | Apple Silicon M4 Pro (Metal) | Metal GPU acceleration is automatic with native Ollama |

---

## Quick Start (Docker)

> Docker provides the easiest setup with all dependencies containerized, including Nginx reverse proxy for API routing and cookie-based authentication.

**Prerequisites**: [Docker Desktop](https://docker.com) installed and running.

### One-Command Setup

```bash
# 1. Clone the repository
git clone https://github.com/Swissbit92/nephilim.git
cd nephilim

# 2. Run the setup script
# macOS/Linux (primary):
chmod +x scripts/docker/setup-docker.sh
./scripts/docker/setup-docker.sh

# Windows (PowerShell, reference):
.\scripts\docker\setup-docker.ps1
```

The script will start all containers, download the 9GB AI model, and open your browser.

**App ready at:** `http://localhost:3000`

### Manual Setup

```bash
# 1. Start all services
docker compose --env-file .env.docker up -d

# 2. Pull LLM model (~9GB, takes 5-10 minutes)
docker exec -it ai-companion-brain ollama pull gemma2:9b-instruct-q5_K_M

# 3. Pull embedding model (for memory features)
docker exec -it ai-companion-brain ollama pull nomic-embed-text:latest

# 4. Verify all subsystems are healthy
python scripts/docker/verify_startup.py

# 5. Open browser
open http://localhost:3000     # Mac/Linux
```

### Docker Architecture

The Docker deployment runs three containers behind an Nginx reverse proxy:

```
Browser (localhost:3000)
    │
    ▼
┌──────────────────────────────────┐
│  Nginx (ai-companion-web)        │
│  - Serves React static build     │
│  - Proxies /auth/* → backend     │
│  - Proxies /chat, /sessions, etc │
│  - React Router catch-all        │
└──────────┬───────────────────────┘
           │ proxy_pass
           ▼
┌──────────────────────────────────┐
│  FastAPI (ai-companion-api)      │
│  - OAuth + JWT auth              │
│  - Chat, personas, wallet routes │
│  - Spawns ephemeral MCP containers│
└──────────┬───────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│  Ollama (ai-companion-brain)     │
│  - Local LLM inference (GPU)     │
│  - Embedding model               │
└──────────────────────────────────┘
```

### Docker Commands

```bash
# View logs
docker compose logs -f backend

# Stop all services
docker compose down

# Restart a service
docker compose restart backend

# Rebuild after code changes
docker compose --env-file .env.docker build --no-cache
docker compose --env-file .env.docker up -d
python scripts/docker/verify_startup.py    # Always verify after rebuild

# Backup database
cp data/chats.db data/chats.db.backup
```

### Docker Environment Variables

Key variables in `.env.docker`:

| Variable | Default | Description |
|----------|---------|-------------|
| `PERSONA_MODEL` | `gemma2:9b-instruct-q5_K_M` | Ollama LLM model |
| `AUTH_REQUIRED` | `true` | Require Google OAuth login |
| `AUTH_ENV` | `development` | `development` for HTTP, `production` for HTTPS |
| `GOOGLE_CLIENT_ID` | — | Google OAuth Client ID |
| `JWT_SECRET_KEY` | (dev default) | JWT signing secret (change in production) |
| `BRAVE_API_KEY` | — | Brave Search API key (optional, per-persona) |
| `JUPITER_ENABLED` | `false` | Enable Jupiter wallet features |
| `SOLANA_RPC_URL` | `https://api.devnet.solana.com` | Solana RPC endpoint |
| `EMAIL_ENABLED` | `false` | Enable trade email notifications |

See [`.env.docker`](.env.docker) for the full list with documentation.

---

## Local Development Setup

> Use this if you need to modify code or prefer running services directly.

### Step 1: Install Dependencies

```bash
git clone https://github.com/Swissbit92/nephilim.git
cd nephilim

# Python backend
pip install -r requirements.txt

# React frontend
cd react-ui && npm install && cd ..
```

### Step 2: Setup Ollama

```bash
ollama serve                                                                              # Start service (native macOS — Metal GPU automatic)
ollama pull gemma4:26b                               # Daily-driver model (~16.4 tok/s on Metal)
ollama pull gemma2:9b-instruct-q5_K_M                                                    # Fallback / smoke-test model
ollama pull nomic-embed-text:latest                                                       # Embeddings (RAG memory)
```

### Step 3: Configure Environment

Create a `.env` file in the project root:

```bash
# Required
OLLAMA_BASE=http://127.0.0.1:11434
PERSONA_MODEL=gemma4:26b   # Daily driver. NO fallback model is configured, deliberately — see CLAUDE.md
PERSONA_TEMPERATURE=0.9
COORD_PORT=8000
PERSONA_DIR=personas
MODEL_CONTEXT_WINDOW=16384            # Wired to Ollama num_ctx; model max is 131K

# Authentication
AUTH_REQUIRED=false                   # false = auto-login via /auth/refresh bypass (local single-user)
GOOGLE_CLIENT_ID=your-client-id      # From Google Cloud Console (Phase 8 / future)

# Optional: Brave Search
BRAVE_API_KEY=

# Optional: Jupiter Wallet
JUPITER_ENABLED=false
SOLANA_RPC_URL=https://api.devnet.solana.com

# Optional: Memory & RAG
MEMORY_EMBEDDING_MODEL=nomic-embed-text:latest
```

### Step 4: Run

#### Active development (two terminals)

```bash
# Terminal 1: Backend
.venv/bin/python -m uvicorn src.coordinator.server:app --reload --port 8000

# Terminal 2: Frontend dev server
cd react-ui && PORT=3001 npm run start:dev   # --openssl-legacy-provider baked into script
```

**Access at:** `http://localhost:3001`

#### Always-on (launchd) — production path on Mac Mini

Nephilim runs as two persistent launchd services:

```bash
# Rebuild frontend first
cd react-ui && npm run build

# Install / reinstall launchd plists
bash scripts/launchd/install.sh
```

- `com.nephilim.backend` — uvicorn on `:8000` (RunAtLoad + KeepAlive)
- `com.nephilim.frontend` — `scripts/serve_frontend.py` on `:3001` (serves the static build + reverse-proxies API paths to `:8000`)

**Access:** Frontend `http://localhost:3001` · Backend `http://localhost:8000` · API docs `http://localhost:8000/docs`

#### Remote access (SSH tunnel)

Reachable from another machine without physical access:

```bash
ssh -L 3001:localhost:3001 -L 8000:localhost:8000 <user>@<mac-mini>
# Then open http://localhost:3001 on the remote machine
# Both ports are required (chat uses absolute :8000 URL; auth proxies via :3001)
```

---

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │         React 19 Frontend                │
                    │  (OAuth • Glassmorphic UI • Framer Motion)│
                    └───────────────────┬─────────────────────┘
                                        │ HTTP / CORS
                                        ▼
                    ┌─────────────────────────────────────────┐
                    │     FastAPI Coordinator (0.100+)         │
                    │  (Auth Middleware • Persona Router • LLM) │
                    └──┬──────┬──────┬──────┬──────────┘
                       │      │      │      │
         ┌─────────────┤      │      │      └──────────────┐
         ▼             ▼      ▼      ▼                    ▼
   ┌───────────┐ ┌──────────┐ ┌──────────┐  ┌──────────────┐
   │  Brave    │ │  SQLite  │ │  FAISS   │  │   Jupiter    │
   │  Search   │ │ Database │ │ Vectors  │  │  DEX / MCP   │
   │(web search)│ │ (Chats)  │ │ (Memory) │  │(Solana swaps)│
   └───────────┘ └──────────┘ └──────────┘  └──────────────┘
                                    │
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │   Ollama LLM (Local Inference)           │
                    │  • gemma4:26b  (MoE 128/8)               │
                    │    (daily driver, Metal GPU, 50.7 tok/s) │
                    │  • NO fallback model, deliberately       │
                    │  • nomic-embed-text (embeddings)          │
                    └─────────────────────────────────────────┘
```

### Component Breakdown

- **React Frontend**: TypeScript, Framer Motion animations, Google OAuth, mobile-optimized
- **FastAPI Backend**: Auth middleware, persona routing, MCP orchestration, wallet routes
- **Brave Search**: Web search with citation validation (per-persona `mcp_access`, ephemeral Docker)
- **Jupiter MCP**: Solana DEX swaps via Docker container, autonomous strategies (DCA, RSI)
- **SQLite**: Chat sessions, messages, summaries, user accounts, wallets, trade proposals
- **FAISS**: Vector database for semantic memory search
- **Ollama**: Local LLM server (native macOS = Metal GPU; Docker-on-Mac = CPU-only)

### Authentication Flow

```
Browser                    Nginx (Docker)              FastAPI Backend
  │                            │                            │
  │  GET /login                │                            │
  │  ← LoginPage (React)      │                            │
  │                            │                            │
  │  Google Sign-In popup      │                            │
  │  ← credential (ID token)  │                            │
  │                            │                            │
  │  POST /auth/google ───────►│ proxy_pass ───────────────►│
  │                            │                            │ verify Google token
  │                            │                            │ upsert user in SQLite
  │                            │                            │ issue access_token (JWT)
  │  ◄─ { access_token, user } │ ◄──────────────────────── │ set refresh_token cookie
  │                            │                            │
  │  GET /personas (Bearer) ──►│ ─────────────────────────►│ validate JWT
  │  ◄─ persona list           │ ◄──────────────────────── │
```

### Route Map

| Route | Component | Auth Required | Description |
|-------|-----------|:---:|-------------|
| `/` | NephilimHome | No | Cinematic landing portal |
| `/login` | LoginPage | No | Google OAuth / local bypass |
| `/select` | CharacterCardV2Showcase | Yes | Companion selection & summoning |
| `/chat` | Chat | Yes | Chat interface |
| `/chat/:sessionId` | Chat | Yes | Chat with specific session |
| `/dashboard` | Dashboard | Yes | Seeker's Sanctum progression hub |

### MCP Integration Patterns

- **Ephemeral STDIO (Brave Search)**: `docker run -i --rm` per request, 2-3 s lifecycle
- **Long-Running STDIO (Jupiter/Solana)**: Container stays alive for wallet operations, managed by wallet routes

All MCP containers have resource limits (256-512MB RAM, 0.5-1.0 CPU, 100 PIDs max).

See [docs/development/ADDING_MCP_SERVERS.md](docs/development/ADDING_MCP_SERVERS.md) for integration guide.

---

## Available AI Companions

**Current Roster (February 2026) — 7 Companions**

### Legacy Companions ("Wanderers")

| Companion | Style | Order | Special Access |
|-----------|-------|-------|----------------|
| **Gojo** | Confident, powerful, playful | Wanderer | None |

### NEPHILIM Companions

| Companion | Title | Domain | Order | Special Access |
|-----------|-------|--------|-------|----------------|
| **E.E.V.A.** | The Primarch | Guidance, wisdom, life planning | Archon | Brave + Solana wallet |
| **Aegis** | The Sentinel | Productivity and discipline | Warden | Brave |
| **Solace** | The Empath | Emotional support and wellbeing | Warden | Brave |
| **Nyx** | The Muse | Creativity and chaos | Sage | None |
| **Cipher** | The Maven | Knowledge and research | Sage | Brave |
| **Aurora** | The Oracle | Future planning and strategy | Warden | Brave |

### Companion Features

- **Psychological Profiles**: core_wound, defense_style, growth_edge, contradiction_pairs
- **Emotional Tracking**: trust_level, rapport, current_mood per session
- **Example Dialogues**: 50 training examples across all personas
- **Custom Sampling**: Per-persona temperature with presets (creative, balanced, precise, chaotic)
- **Per-Persona MCP Access**: Configured via `mcp_access` field in persona JSON

### Managing Companions

**Add**: Copy `personas/template.jsonc` → `personas/[name].json`, add images to `react-ui/public/images/personas/[name]/`. Auto-discovered on load.

**Remove**: Delete the JSON file. Sessions and collections auto-cleanup on next load.

**Modify**: Edit the JSON. Summary regenerates on next access.

---

## NEPHILIM Worldbuilding System

Six interconnected AI companions exist within a shared lore — a realm of fallen celestial beings, six Houses, and a Seeker progression system.

### Entering the Realm

- **Portal** (`/`): Cinematic landing page with particle and aurora effects
- **Login** (`/login`): Google OAuth or local bypass authentication
- **Onboarding**: Name entry, faction quiz, persona introductions, first companion selection
- **Progression**: Earn 5 resonance per conversation; unlock lore fragments as affinity grows

### Rank System

| Rank | Resonance Required |
|------|-------------------|
| Initiate | 0 |
| Acolyte | 100 |
| Adept | 500 |
| Ascendant | 2,000 |
| Nephilim | 10,000 |

### Narrative MCP Integration

External data sources are framed as Nephilim powers:

| MCP Source | NEPHILIM Name | Patron |
|------------|---------------|--------|
| Brave Search | Cipher's Archives | Cipher |
| Solana Wallet | The Ledger of Becoming | E.E.V.A. |

### Lore Documents

- [`docs/lore/NEPHILIM_LORE.md`](docs/lore/NEPHILIM_LORE.md) — World bible: creation myth, the Fall, realm geography
- [`docs/lore/NEPHILIM_FACTIONS.md`](docs/lore/NEPHILIM_FACTIONS.md) — Six Houses aligned with Nephilim patrons
- [`docs/lore/NEPHILIM_RANKS.md`](docs/lore/NEPHILIM_RANKS.md) — Seeker progression and rank thresholds

---

## Usage

### First Launch

1. Navigate to `http://localhost:3000` (Docker) or `http://localhost:3001` (local dev)
2. **Login**: Sign in with Google OAuth, or use local bypass if `AUTH_REQUIRED=false`
3. **Onboarding**: Complete the faction quiz and select your first companion
4. **Chat**: Start conversations from the companion selection page

### Key Pages

| Page | What You Can Do |
|------|-----------------|
| **Landing** (`/`) | Enter the Realm portal with cinematic effects |
| **Login** (`/login`) | Google OAuth or local bypass authentication |
| **Companion Selection** (`/select`) | Browse cards, run Summoning Ritual pulls, manage collection |
| **Chat** (`/chat`) | Converse with AI companions, switch sessions via sidebar |
| **Dashboard** (`/dashboard`) | Track rank, resonance, faction, affinities, unlocked lore |

### API Documentation

Backend API docs available at `http://localhost:8000/docs` (Swagger UI).

Key endpoint groups:
- `/auth/*` — Google OAuth login, token refresh, logout
- `/chat`, `/greet` — Conversation endpoints
- `/sessions/*` — Chat session management
- `/personas/*` — Persona listing and details
- `/nephilim/*` — Seeker progression, ranks, factions, lore
- `/wallet/*` — Jupiter wallet management, trade proposals, strategies

---

## Testing & Quality

### Automated Testing

```bash
# Python backend — full suite (live Ollama tests auto-skip if Ollama is down)
pytest tests/

# Fast deterministic gate (no live services; matches headless CI). Tests that need
# Ollama/Brave/Docker auto-skip when the resource is unreachable.
OLLAMA_BASE=http://127.0.0.1:1 pytest tests/      # forces the live tests to skip

# React unit tests
cd react-ui && npm test

# Playwright E2E tests
cd react-ui && npx playwright test
cd react-ui && npx playwright test --headed    # With browser visible
```

> Conventions for writing backend tests (resource markers, the temp-SQLite repository pattern, TestClient-without-lifespan, the `asyncio.run` rule) live in [docs/development/TESTING_GUIDE.md](docs/development/TESTING_GUIDE.md).

### Test Coverage

- **Backend**: ~2,050 tests; **63%** line coverage, enforced by `--cov-fail-under=60` in `pytest.ini` (headless, live tests excluded).
- **Evals** (`tests/evaluation/`): persona voice/distinctiveness (ADR-005), lore retrieval, tool-call red team, and tool-firing (`eval_tool_firing.py` — does a tool actually fire when a turn needs one). Files named `eval_*.py` are **not** auto-collected by pytest; they cost real LLM turns and are run deliberately.
- **Frontend**: 40+ Jest tests with React Testing Library
- **Playwright E2E**: OAuth flow, chat interactions, Jupiter wallet, visual regression
- **Live-LLM tests** (`requires_ollama`): run only when Ollama is reachable; the comprehensive persona suite (`tests/manual/`) remains the primary quality gate.
- **Type Safety**: TypeScript strict mode + Pydantic validation throughout

### Code Quality

- Zero unused imports, zero dead code
- npm audit passing (zero production vulnerabilities)
- ESLint enforced, PEP 8 compliant
- 60fps animations, <500ms API responses

---

## Documentation

### Setup & Deployment

| Document | Description |
|----------|-------------|
| [docs/setup/DOCKER_QUICKSTART.md](docs/setup/DOCKER_QUICKSTART.md) | Complete Docker setup guide |
| [.env.docker](.env.docker) | Docker environment configuration |
| [scripts/docker/](scripts/docker/) | Docker setup, validation, troubleshooting scripts |

### Development

| Document | Description |
|----------|-------------|
| [CLAUDE.md](CLAUDE.md) | Developer guide, project structure, commands |
| [docs/development/ADDING_MCP_SERVERS.md](docs/development/ADDING_MCP_SERVERS.md) | MCP integration guide |
| [docs/development/TESTING_GUIDE.md](docs/development/TESTING_GUIDE.md) | Testing setup and best practices |
| [docs/archive/2026-05/OAUTH_IMPLEMENTATION_PLAN.md](docs/archive/2026-05/OAUTH_IMPLEMENTATION_PLAN.md) | OAuth architecture and flow |
| [docs/development/JUPITER_WALLET_IMPLEMENTATION.md](docs/development/JUPITER_WALLET_IMPLEMENTATION.md) | Wallet integration details |
| [CHANGELOG.md](CHANGELOG.md) | Version history |

---

## Contributing

See `CLAUDE.md` for project structure, coding style, and conventions.

### Development Roadmap

**Completed (Dec 2025 – Feb 2026):**
- Phase 1-2: Persona quality (psychological depth, emotional tracking)
- Phase 1-3: Memory system (RAG, user profiles, fact extraction)
- Phase 2: Multi-message conversational AI architecture
- Brave MCP integration with per-persona access; Jupiter/Solana wallet MCP
- Docker deployment with automated setup scripts
- NEPHILIM Phases 0-6: Worldbuilding, progression, onboarding, narrative MCP, filter toggle
- NEPHILIM Phase 7: Full UI transition — unified dark theme, glassmorphic chat, summoning ritual, dashboard, WCAG AA
- Phase 8: Google OAuth authentication with JWT tokens
- Phase 8: Jupiter wallet integration (Solana DEX, autonomous strategies, trade proposals)
- Phase 8: Nginx reverse proxy in Docker for cookie-based auth
- Phase 8: Playwright E2E test suite (OAuth, wallet, chat, visual regression)

**Future Enhancements:**
- Phase 9: Cross-persona storylines and advanced progression
- Phase 3-5 Conversational AI (response timing, reflection loops, multi-turn planning)
- PostgreSQL migration (optional, for scaling beyond local use)
- Kubernetes deployment (optional, for production at scale)

---

## Security & Privacy

### Authentication
- **Google OAuth 2.0**: Server-side token verification via `google-auth` library
- **JWT Tokens**: HS256-signed, short-lived access (1h) + HttpOnly refresh cookies (30d)
- **Cookie Security**: `SameSite=strict`, `Secure` flag in production (HTTPS)
- **Wallet Encryption**: AES-GCM encryption for Solana private keys at rest

### Docker Deployment
- **Container Isolation**: Services on private Docker network
- **Nginx Reverse Proxy**: Single entry point, no direct backend exposure
- **MCP Resource Limits**: Memory (256-512MB), CPU (0.5-1.0), PID (100 max) per container
- **Non-Root User**: Backend container uses dedicated `coordinator` user

### General
- **Local-First**: All AI processing and storage happens on your device
- **No Data Transmission**: Conversations never leave your machine (except optional Brave web search)
- **Dependency Audits**: Regular npm audit with zero production vulnerabilities

---

## Important Notes

- **First Launch**: Docker image download + model pull takes 10-20 minutes
- **Resource Usage**: Ollama LLM requires ~4-8GB RAM; models are 4-10GB on disk
- **Data Persistence**: All data in `./data/` (Docker) or `chats.db` (local)
- **Backups**: Copy `data/chats.db` to back up all conversations and user data
- **GPU**: Metal acceleration is automatic via native Ollama on macOS Apple Silicon. Do NOT run Ollama inside Docker on Mac — Docker-on-Mac runs Ollama CPU-only.
- **Auth Bypass**: Set `AUTH_REQUIRED=false` in `.env` for offline/dev use without Google

---

## License

(c) 2025-2026 — All rights reserved.

This project is provided "as is", without warranty. Use responsibly and at your own discretion.
