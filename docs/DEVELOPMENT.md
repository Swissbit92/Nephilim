---
title: Development Commands (nephilim)
status: active
created: 2026-04-19
last_reviewed_on: 2026-04-19
review_in: 6 months
applies_to: nephilim
---

# Development Commands

> Moved verbatim from CLAUDE.md 2026-04-19 as part of the /cms migration. Agents load this lazily when they need command details rather than on every session.

## Docker (full stack + MCP containers)

> On **macOS, local dev is the recommended path** (native Ollama → Metal GPU). Docker-on-Mac runs Ollama CPU-only. Use Docker for the full containerized stack or on Linux/NVIDIA hosts.

```bash
# One-command setup
./scripts/docker/setup-docker.sh     # macOS/Linux
.\scripts\docker\setup-docker.ps1    # Windows PowerShell

# Manual start
docker-compose --env-file .env.docker up -d

# Pull models (required on first run)
docker exec -it ai-companion-brain ollama pull gemma2:9b-instruct-q5_K_M
docker exec -it ai-companion-brain ollama pull nomic-embed-text:latest

# Common operations
docker-compose logs -f backend       # View logs
docker-compose restart backend       # Restart
docker-compose down                  # Stop all

# Rebuild backend (ALWAYS verify after rebuild)
docker-compose --env-file .env.docker build --no-cache backend
docker-compose --env-file .env.docker up -d backend
python scripts/docker/verify_startup.py          # Mandatory post-rebuild check
python scripts/docker/verify_startup.py --skip-queries  # Quick mode (subsystems only)
```

**Access:** Frontend `http://localhost:3000` | Backend `http://localhost:8000` | API Docs `http://localhost:8000/docs`

> **⚠️ Note:** Docker serves legacy UI unless rebuilt. For Phase 7 NEPHILIM UI, use local development.

## Local Development (NEPHILIM UI)

```bash
# Setup
pip install -r requirements.txt
cd react-ui && npm install

# Active dev — Terminal 1 (backend)
.venv/bin/python -m uvicorn src.coordinator.server:app --reload --port 8000

# Active dev — Terminal 2 (frontend dev server; --openssl-legacy-provider baked into script for Node 25)
cd react-ui && PORT=3001 npm run start:dev

# Access at http://localhost:3001

# Static production build (required before launchd reinstall)
cd react-ui && npm run build
```

**CORS Configuration:** `src/coordinator/server.py:41` allows `localhost:3000` and `localhost:3001`

## Always-on (launchd) — production path on Mac Mini

Nephilim runs persistently as two launchd services (RunAtLoad + KeepAlive):

| Service | Command | Port |
|---------|---------|------|
| `com.nephilim.backend` | uvicorn `src.coordinator.server:app` | `:8000` |
| `com.nephilim.frontend` | `scripts/serve_frontend.py` (static build + API reverse-proxy to `:8000`) | `:3001` |

Reinstall after any code change or plist update:

```bash
# 1. Rebuild the React static build first
cd react-ui && npm run build

# 2. Install / reinstall both launchd plists
bash scripts/launchd/install.sh
```

Plists and the installer live in `scripts/launchd/`. The frontend service (`serve_frontend.py`) serves the static build **and** reverse-proxies all API paths (`/auth`, `/sessions`, `/persona`, `/nephilim`, …) to the backend on `:8000` — the React dev server (`:3001`) is for active UI development only.

## Remote access (SSH tunnel)

Reachable from another machine over SSH without physical access. **Both ports must be forwarded** — the chat UI calls the backend on the absolute `:8000` URL, while auth refresh proxies through `:3001`.

**One-off:**

```bash
ssh -L 3001:localhost:3001 -L 8000:localhost:8000 swissbit.@192.168.1.246
# Then open http://localhost:3001
```

**Recommended — `ssh nephilim` alias.** Add to the *client* machine's `~/.ssh/config` (Windows: `C:\Users\<you>\.ssh\config`):

```
Host nephilim
    HostName 192.168.1.246
    User swissbit.
    LocalForward 3001 localhost:3001
    LocalForward 8000 localhost:8000
    ServerAliveInterval 60
```

Then the whole tunnel is just `ssh nephilim` → open `http://localhost:3001`. (`ServerAliveInterval 60` keeps the idle tunnel from dropping.)

**One-click (Windows)** — save `nephilim.cmd`:

```bat
@echo off
start http://localhost:3001
ssh nephilim
```

> Hands-off, always-available alternative (no tunnel at all): **Tailscale** — see the "always-on desktop-station hardening" item in the ecosystem roadmap.

## Testing

```bash
# React tests
cd react-ui && npm test
cd react-ui && npm test -- --testNamePattern="MessageBubble" --watchAll=false

# Playwright E2E tests
cd react-ui && npx playwright test                    # Run all E2E tests
cd react-ui && npx playwright test --headed           # Run with browser visible

# Python tests (run from project root)
pytest tests/backend/                    # Backend unit tests
pytest tests/integration/                # Integration tests
pytest tests/evaluation/ -v              # RAGAS persona quality
```

## Comprehensive Persona Test Suite (binary checks only — NOT a quality gate)

> **Its `persona_voice` score is repudiated.** [ADR-005](decisions/005-persona-architecture-simplification-eval-first.md)
> found it to be a keyword heuristic that is Goodhart-able and *penalises* genuinely
> distinctive voice: "Every voice number to date … is suspect." The deterministic binary
> checks (no-leak, safety, first-person) were deliberately kept and remain valid. For what
> replaces it, and for which published numbers must not be reused, see
> [PERSONA_EVAL.md](PERSONA_EVAL.md).

**Do NOT create new persona tests** — the suite covers all 8 personas across all MCPs and behavioral dimensions.

```bash
# Full run — all 8 personas, ~1045 tests (~60 min, requires backend on port 8000)
python tests/manual/comprehensive_persona_test.py

# Single persona (fast, ~10-15 min)
python tests/manual/comprehensive_persona_test.py --persona nephilim_eeva

# Quick sanity check — 30 tests per persona, no MCP bank (~8 min)
python tests/manual/comprehensive_persona_test.py --quick
```

Results saved to `tests/manual/results/`. Pass threshold: composite >= 0.60. Suite pass: >= 70%.

> **Scoring dimensions, test bank structure, and baseline results:** See [NEPHILIM_REFERENCE.md](NEPHILIM_REFERENCE.md).

## Ollama Setup

Run Ollama **natively** on macOS — Metal GPU acceleration is automatic on Apple Silicon. Docker-on-Mac runs Ollama CPU-only.

```bash
ollama serve                                                                  # Start service (Metal GPU automatic)
ollama pull hf.co/TheDrummer/Magidonia-24B-v4.3-GGUF:Q4_K_M                 # Daily-driver model (~17 tok/s on M4 Pro Metal)
ollama pull gemma2:9b-instruct-q5_K_M                                         # Fallback / smoke-test model (config.py default)
ollama pull nomic-embed-text:latest                                            # Embeddings (RAG memory)
```

Set `PERSONA_MODEL=hf.co/TheDrummer/Magidonia-24B-v4.3-GGUF:Q4_K_M` in `.env` to use the daily driver. `MODEL_CONTEXT_WINDOW=16384` is wired to Ollama `num_ctx` (model max is 131K, dial up as needed). `MODEL_MAX_OUTPUT_TOKENS=400` caps per-turn generation (Ollama `num_predict`) — a backstop against runaway replies (turn latency is ~linear in output tokens at ~16 tok/s).

### Concurrency tuning (single-user — important)

`OLLAMA_NUM_PARALLEL=1` is **required** on this box. Unset, Ollama opens parallel slots that split the single GPU **and** split `num_ctx` across them, and a 2nd concurrent slot is cold (no cached prefix) — two overlapping requests then crawl at ~1 tok/s instead of one running alone at ~16 tok/s (this caused a one-off 161.9s turn on 2026-06-21). It's set durably by the login LaunchAgent `com.nephilim.ollama-tuning` (`scripts/launchd/ollama-tuning.sh`), since Ollama runs as the menu-bar `Ollama.app` and `launchctl setenv` alone doesn't survive reboot. **Do NOT set `OLLAMA_MAX_LOADED_MODELS=1`** — nephilim keeps Magidonia + nomic-embed both resident; `=1` makes them evict each other (17 GB reload per RAG embedding).

> **Latency note:** prefill is *not* the bottleneck here (~0.4 s for the ~3.5 K-token persona prompt); generation throughput (~16 tok/s × output length) is. See memory `project_nephilim_latency_ops`.

### Brave web search depends on Docker

The Brave MCP runs as an ephemeral `docker run` per request. The launchd backend's `PATH` **must** include `/usr/local/bin` (where the `docker` CLI symlink lives) or Brave silently disables at startup — already set in `scripts/launchd/com.nephilim.backend.plist`. `BRAVE_SEARCH_TIMEOUT=20` (s) covers the container cold-start; pre-pull once with `docker pull docker.io/mcp/brave-search` so first-search isn't a slow image download.
