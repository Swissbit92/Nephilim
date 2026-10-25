---
title: Docker Quick Start Guide - SQLite Edition
status: active
created: 2026-04-03
last_reviewed_on: 2026-06-22
review_in: 6 months
applies_to: nephilim
---

# Docker Quick Start Guide - SQLite Edition

Get your MCP Coordinator running in Docker in under 10 minutes with this optimized SQLite setup.

**Perfect for**: Local personal use, testing, development
**Data storage**: SQLite database in `./data/chats.db` (persists on your machine)
**Production deployment**: For production, consider PostgreSQL migration

---

## Why SQLite Edition?

This setup is **optimized for single-user local use** and provides:

- ✅ **Zero migration work** - uses existing SQLite database
- ✅ **Faster performance** - no network overhead, direct file access
- ✅ **Simpler setup** - 3 services instead of 5
- ✅ **Easy backups** - copy single database file
- ✅ **Less resource usage** - ~50-100MB RAM saved (no PostgreSQL/Redis)
- ✅ **Perfect for you** - if running locally with 1 user

---

## Prerequisites

- **Docker Desktop** installed (v24.0+)
  - Windows: https://docs.docker.com/desktop/install/windows-install/
  - Mac: https://docs.docker.com/desktop/install/mac-install/
  - Linux: https://docs.docker.com/engine/install/
- **Docker Compose** v2.0+ (included with Docker Desktop)
- **15GB free disk space** (for Docker images + Ollama models)

---

## Quick Start (3 Steps)

### Step 1: Configure Environment (Optional)

```bash
# The .env.docker file has sensible defaults
# Optional: Edit it to customize settings

# Windows (PowerShell)
notepad .env.docker

# Mac/Linux
nano .env.docker
```

**Optional customizations**:
- `BRAVE_API_KEY` - Add API key to enable web search (get free key at https://brave.com/search/api/)
- `PERSONA_MODEL` - Change LLM model (default: `gemma2:9b-instruct-q5_K_M`)
- `PERSONA_TEMPERATURE` - Adjust creativity (default: `0.9`)

---

### Step 2: Start the Stack

```bash
# Create data directory for SQLite database
mkdir data

# Start all services (first run takes 5-10 minutes to download images)
docker-compose --env-file .env.docker up -d

# Check status (wait for all to be "healthy")
docker-compose ps
```

Expected output:
```
NAME                IMAGE                       STATUS
ai-companion-brain  ollama/ollama:latest       Up (healthy)
ai-companion-api    nephilim-backend        Up (healthy)
ai-companion-web    nephilim-frontend       Up (healthy)
```

**What's running:**
- `ai-companion-brain` - Local LLM server (Ollama with GPU support)
- `ai-companion-api` - FastAPI coordinator (Python)
- `ai-companion-web` - React UI (served by Nginx)

**Data locations:**
- SQLite database: `./data/chats.db` (persists on your machine)
- Persona summaries: `./personas/_summaries/`
- Ollama models: Docker volume (managed by Docker)

---

### Step 3: Pull LLM Model

```bash
# Pull the default model (9GB download - takes 10-15 minutes)
docker exec -it ai-companion-brain ollama pull gemma2:9b-instruct-q5_K_M

# Optional: Pull embedding model for Phase 3 memory features
docker exec -it ai-companion-brain ollama pull nomic-embed-text:latest

# Verify models are ready
docker exec -it ai-companion-brain ollama list
```

Expected output:
```
NAME                                         SIZE
gemma2:9b-instruct-q5_K_M                   9.0 GB
nomic-embed-text:latest                      274 MB
```

**Alternative models** (if you prefer):
```bash
# Daily-driver persona model — uncensored, higher quality (larger; needs more RAM)
docker exec -it ai-companion-brain ollama pull huihui_ai/mistral-small-abliterated:24b

# General purpose (censored, more formal)
docker exec -it ai-companion-brain ollama pull llama3.1:latest
```

---

## Access Your App

Once all services are running:

- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/health
- **Readiness Check**: http://localhost:8000/ready (subsystem-level status)

---

## First Time Setup

1. **Open the app**: Navigate to http://localhost:3000
2. **Summon a companion**: Click the summoning button to receive your first persona
3. **Start chatting**: Select a persona and send a message
4. **Test web search** (if configured):
   - Use a persona with MCP access (see the persona's `mcp_access` field)
   - Ask a current events question (e.g., "What's the weather like today?")

---

## Useful Commands

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f backend
docker-compose logs -f frontend
docker-compose logs -f ollama
```

### Restart Services

```bash
# Restart backend (e.g., after code changes)
docker-compose restart backend

# Restart all services
docker-compose restart
```

### Stop Everything

```bash
# Stop all services (keeps data)
docker-compose down

# Stop and remove volumes (WARNING: deletes database)
docker-compose down -v
```

### Rebuild After Code Changes

```bash
# Rebuild backend
docker-compose build backend
docker-compose up -d backend
python scripts/docker/verify_startup.py    # Mandatory: verify MCP subsystems

# Rebuild frontend
docker-compose build frontend
docker-compose up -d frontend
```

> **Important:** Always run `verify_startup.py` after rebuilding the backend. This checks that the database, Ollama, and Brave MCP all initialized correctly. Without this step, broken MCP subsystems can silently return 500 errors.

---

## Data Persistence & Backups

### Where Your Data Lives

All data persists on your host machine - it's NOT lost when containers stop!

| Data | Location | Size | Persists? |
|------|----------|------|-----------|
| **Chat Database (SQLite)** | `./data/chats.db` | 1-10MB | ✅ Yes (local directory) |
| **Persona Summaries** | `./personas/_summaries/` | <1MB | ✅ Yes (local directory) |
| **Ollama Models** | Docker volume `ollama_models` | 4-10GB per model | ✅ Yes (Docker volume) |
| **Application Logs** | `./logs/` | <10MB | ✅ Yes (local directory) |

**Your data survives:**
- ✅ `docker-compose down` (stops containers)
- ✅ `docker-compose restart` (restarts containers)
- ✅ System reboots
- ✅ Docker updates

**Your data is DELETED if:**
- ❌ You run `docker-compose down -v` (removes volumes)
- ❌ You manually delete `./data/` directory
- ❌ You manually delete Ollama volume: `docker volume rm nephilim_ollama_models`

### Backup Your Data

**Quick backup (database only):**
```bash
# Backup SQLite database with timestamp
cp data/chats.db data/chats.db.backup-$(date +%Y%m%d)

# Verify backup
ls -lh data/*.backup*
```

**Full backup (everything):**
```bash
# Create complete backup
tar -czf mcp_backup_$(date +%Y%m%d).tar.gz data/ personas/_summaries/ logs/

# Verify backup
tar -tzf mcp_backup_*.tar.gz | head
```

**Backup Ollama models** (optional - saves re-downloading 4-10GB):
```bash
# List Ollama volume
docker volume inspect ollama_models

# Backup Ollama models to host
docker run --rm -v ollama_models:/source -v $(pwd):/backup alpine tar -czf /backup/ollama_models_backup.tar.gz -C /source .
```

### Restore Your Data

**Restore database:**
```bash
# Stop backend to avoid conflicts
docker-compose stop backend

# Restore from backup
cp data/chats.db.backup-20250101 data/chats.db

# Restart backend
docker-compose start backend
```

**Full restore:**
```bash
# Stop all services
docker-compose down

# Restore everything
tar -xzf mcp_backup_20250101.tar.gz

# Restart
docker-compose up -d
```

### Migration from Local Setup

**If you have existing `chats.db` from running locally:**

```bash
# 1. Stop local server (if running)
# 2. Copy existing database
cp chats.db data/chats.db

# 3. Start Docker stack
docker-compose --env-file .env.docker up -d

# Your existing chat history will be available!
```

---

## Troubleshooting

### Issue: "Port 3000 already in use"

**Cause**: Another application is using port 3000 (common with React dev servers)

**Solution**:
```bash
# Option 1: Stop the other application
# On Mac/Linux
lsof -ti:3000 | xargs kill -9

# On Windows
netstat -ano | findstr :3000
taskkill /PID <PID> /F

# Option 2: Change the port in docker-compose.yml
# Edit docker-compose.yml line with "3000:80" to "3001:80"
```

---

### Issue: "Ollama model not found"

**Symptom**: Backend returns "model not found" error when sending messages

**Solution**:
```bash
# Pull the model
docker exec -it ai-companion-brain ollama pull llama3.1:latest

# Verify
docker exec -it ai-companion-brain ollama list
```

---

### Issue: Backend shows "unhealthy" status

**Symptom**: `docker-compose ps` shows backend as unhealthy

**Solution**:
```bash
# Run the readiness check for detailed subsystem status
python scripts/docker/verify_startup.py --skip-queries

# Check backend logs
docker-compose logs backend

# Common causes:
# 1. Ollama not ready - wait 1-2 minutes
# 2. Model not pulled - run: docker exec -it ai-companion-brain ollama pull llama3.1:latest
# 3. Database error - check ./data/ directory exists

# Restart backend
docker-compose restart backend
```

---

### Issue: "Cannot connect to backend" in frontend

**Symptom**: Frontend loads but shows "Failed to fetch" errors

**Solution**:
```bash
# Check if backend is running
curl http://localhost:8000/health

# If that fails, check backend logs
docker-compose logs backend

# Restart backend
docker-compose restart backend
```

---

### Issue: Very slow responses

**Symptom**: Messages take 30+ seconds to respond

**Possible Causes**:
1. **No GPU acceleration**: Ollama running on CPU (slow but works)
2. **Large model**: Using a model that's too big for your RAM
3. **First request**: First message after restart is always slower

**Solutions**:
```bash
# Option 1: Use a smaller model
docker exec -it ai-companion-brain ollama pull llama3.1:8b  # Smaller 8B version
# Then update .env.docker: PERSONA_MODEL=llama3.1:8b

# Option 2: Enable GPU (NVIDIA only)
# Uncomment the GPU section in docker-compose.yml under ollama service

# Option 3: Wait - subsequent messages will be faster
```

---

### Issue: Database locked error

**Symptom**: "Database is locked" errors in backend logs

**Cause**: Multiple backend instances or file permissions issue

**Solution**:
```bash
# Stop all services
docker-compose down

# Check for orphaned processes
docker ps -a

# Delete database lock file
rm data/chats.db-shm data/chats.db-wal

# Restart
docker-compose up -d
```

---

## Advanced: GPU Support

**Apple Silicon (Mac Mini M4 Pro / M1/M2/M3/M4):** Run Ollama natively for automatic Metal GPU acceleration — Docker-on-Mac always runs Ollama CPU-only regardless of the GPU section in `docker-compose.yml`.

```bash
# macOS — native Ollama for Metal GPU (recommended)
ollama serve
ollama pull gemma2:9b-instruct-q5_K_M
```

Then run only the backend + frontend containers via Docker, and point `OLLAMA_BASE` at the host:
```bash
OLLAMA_BASE=http://host.docker.internal:11434 docker-compose --env-file .env.docker up -d backend frontend nginx
```

**NVIDIA GPU (Windows/Linux only):** Install the NVIDIA Docker runtime and uncomment the GPU section in `docker-compose.yml` under the `ollama` service:
```yaml
ollama:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
```
See https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html for the runtime setup.

---

## Updating Models

### Pull a Different Model

```bash
# See available models: https://ollama.com/library

# Example: Pull the daily-driver persona model
docker exec -it ai-companion-brain ollama pull huihui_ai/mistral-small-abliterated:24b

# Update .env.docker
PERSONA_MODEL=huihui_ai/mistral-small-abliterated:24b

# Restart backend
docker-compose restart backend
```

### List All Downloaded Models

```bash
docker exec -it ai-companion-brain ollama list
```

### Remove Old Models

```bash
# Remove a model to free space
docker exec -it ai-companion-brain ollama rm llama3.1:latest
```

---

## Development Workflow

If you're making code changes:

### Backend Changes

```bash
# Make your changes to Python files in src/

# Rebuild and restart (with hot reload)
docker-compose restart backend

# Verify subsystems are healthy after restart
python scripts/docker/verify_startup.py --skip-queries

# Watch logs
docker-compose logs -f backend
```

**Note**: The backend runs with `--reload` flag, so most changes are picked up automatically. For dependency changes, rebuild:

```bash
docker-compose build backend
docker-compose up -d backend
python scripts/docker/verify_startup.py    # Full verification after rebuild
```

---

### Frontend Changes

```bash
# Make changes to React files in react-ui/

# Rebuild frontend
docker-compose build frontend
docker-compose up -d frontend

# Clear browser cache and refresh
```

**Note**: Frontend builds are static, so any change requires a rebuild.

---

## Cleaning Up

### Remove Everything

```bash
# Stop and remove containers (keeps data in ./data/)
docker-compose down

# Remove Docker images to free space
docker rmi nephilim-backend nephilim-frontend

# Remove Ollama models volume (WARNING: deletes all models)
docker volume rm nephilim_ollama_models
```

### Fresh Start

```bash
# Complete reset (WARNING: deletes all data)
docker-compose down -v
rm -rf data/ logs/ personas/_summaries/

# Rebuild from scratch
docker-compose up -d --build

# Verify everything works
python scripts/docker/verify_startup.py
```

---

## What's Next?

### For Personal Use (Current Setup)
- ✅ You're all set! Enjoy chatting with your AI personas
- Add more personas by creating JSON files in `personas/`
- Enable web search by adding `BRAVE_API_KEY` to `.env.docker`

### For Production Deployment
If you want to deploy this to the cloud for others to use:
- For production, consider PostgreSQL migration
- Migrate to PostgreSQL (required for multi-user)
- Follow Phase 1-3 implementation plan

---

## Support

**Common Questions**:
- **How much RAM do I need?** Minimum 8GB, recommended 16GB+
- **Does this work on ARM (M1/M2/M3/M4 Mac)?** Yes — Docker Desktop runs ARM64 natively. Note: Ollama-in-Docker is CPU-only on Mac; use native Ollama (`ollama serve`) for Metal GPU acceleration.
- **Can I use this without internet?** Yes, after initial model download
- **How do I add personas?** Copy `personas/template.jsonc` and customize

**Issues?**
- Check logs: `docker-compose logs -f`
- Try restarting: `docker-compose restart`
- Fresh start: `docker-compose down && docker-compose up -d`
- Open a GitHub issue if problems persist

---

## Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                       Your Computer (Host)                        │
│                                                                   │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────┐ │
│  │   Frontend         │  │   Backend          │  │  Ollama    │ │
│  │   (React + Nginx)  │  │   (FastAPI)        │  │  (LLM)     │ │
│  │   Port 3000        │  │   Port 8000        │  │  Port      │ │
│  │                    │  │                    │  │  11434     │ │
│  │ ┌────────────────┐ │  │ ┌────────────────┐ │  │            │ │
│  │ │ Summoning      │ │  │ │ Persona Engine │ │  │ ┌────────┐ │ │
│  │ │ Chat UI        │ │  │ │ MCP Clients    │ │  │ │ Models │ │ │
│  │ │ CharacterCards │ │  │ │ Memory Manager │ │  │ │ 4-10GB │ │ │
│  │ └────────────────┘ │  │ └────────────────┘ │  │ └────────┘ │ │
│  └────────────────────┘  └────────────────────┘  └────────────┘ │
│           │                       │                      │       │
│           │                       │                      │       │
│           └───────────────────────┴──────────────────────┘       │
│                                   │                              │
│                    ┌──────────────▼───────────────┐              │
│                    │  Persistent Storage (Host)   │              │
│                    │                              │              │
│                    │  • SQLite DB (./data/)       │              │
│                    │    └─ chats.db (1-10MB)      │              │
│                    │                              │              │
│                    │  • Persona Files (./personas/)│             │
│                    │    └─ _summaries/ (<1MB)     │              │
│                    │                              │              │
│                    │  • Logs (./logs/)            │              │
│                    │    └─ *.log (<10MB)          │              │
│                    └──────────────────────────────┘              │
│                                                                   │
│  Docker Network: mcp-network (bridge)                            │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Container-to-container communication via service names   │   │
│  │ backend → ollama:11434                                    │   │
│  │ frontend → backend:8000                                   │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

### Request Flow

**1. User loads app:**
```
Browser → http://localhost:3000 → Nginx (frontend) → Serves React app
```

**2. User sends chat message:**
```
React UI → http://localhost:8000/sessions/{id}/chat
    ↓
FastAPI Backend → Loads persona from ./personas/eeva.json
    ↓
Backend → Calls Ollama at http://ollama:11434/api/generate
    ↓
Ollama → Runs LLM (gemma2:9b-instruct-q5_K_M) → Returns response
    ↓
Backend → Saves message to SQLite ./data/chats.db
    ↓
Response → Returns to React UI → Displays in chat
```

**3. Web search (if enabled):**
```
Backend → Detects query needs web search
    ↓
Backend → Calls Brave MCP client → Brave Search API
    ↓
Backend → Synthesizes LLM response with citations
    ↓
Response → Includes 🔍 Sources section
```

### Resource Usage (Typical)

| Component | CPU | RAM | Disk |
|-----------|-----|-----|------|
| **Frontend (Nginx)** | <5% | ~20MB | ~50MB (image) |
| **Backend (Python)** | 5-10% | ~200MB | ~200MB (image) |
| **Ollama (LLM)** | 20-100% | ~4-8GB | ~5GB per model |
| **SQLite Database** | <1% | ~5MB | 1-10MB (data) |
| **Total** | 25-115% | ~4.2-8.2GB | ~10-15GB |

**Performance:**
- First message: 5-15 seconds (model loading)
- Subsequent messages: 2-5 seconds
- Web search: 3-8 seconds
- Database queries: <10ms

---

Enjoy your AI companion app! 🤖✨
