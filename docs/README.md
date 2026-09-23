---
title: Documentation Index
status: active
created: 2026-04-03
last_reviewed_on: 2026-04-19
review_in: 6 months
applies_to: nephilim
---

# Documentation Index

Organized documentation for NEPHILIM development and deployment.

## Directory Structure

```
docs/
├── setup/          Setup and deployment guides
├── development/    Development guides, MCP integration, and QA reports
├── architecture/   Architecture decision records and system design
└── lore/           NEPHILIM worldbuilding, lore, and strategy documents
```

## Setup & Deployment

### [setup/DOCKER_QUICKSTART.md](setup/DOCKER_QUICKSTART.md)

**Complete Docker deployment guide** (Recommended setup method)

**Contents:**
- One-command setup for Windows/Linux/Mac
- Manual deployment steps
- Environment configuration
- Troubleshooting guide
- Network debugging
- Container health checks

**Quick start:**
```bash
# macOS/Linux
./scripts/docker/setup-docker.sh

# Windows PowerShell
.\scripts\docker\setup-docker.ps1
```

## Development

### [development/ADDING_MCP_SERVERS.md](development/ADDING_MCP_SERVERS.md)

**Guide for integrating new MCP (Model Context Protocol) servers**

**Topics covered:**
- MCP server architecture (ephemeral vs long-running)
- Docker container configuration
- Tool definition and schema
- Per-persona MCP access (Celestial Order)
- Intent classification integration
- Citation and response synthesis

**Current MCP servers:**
- Brave Search (ephemeral, 2-3 s lifecycle)
- Jupiter/Solana Wallet (long-running, stateful)

### [development/TESTING_GUIDE.md](development/TESTING_GUIDE.md)

**Testing setup and best practices**

**Test organization:**
- `tests/backend/` - Backend unit tests
- `tests/integration/` - End-to-end tests
- `tests/manual/` - Comprehensive persona test suite (binary checks only; its `persona_voice`
  score was repudiated by ADR-005 — see [PERSONA_EVAL.md](PERSONA_EVAL.md))

**Running tests:**
```bash
# React tests
cd react-ui && npm test

# Python tests
pytest tests/backend/ -v
pytest tests/integration/ -v

# Comprehensive persona test suite
python tests/manual/comprehensive_persona_test.py
python tests/manual/comprehensive_persona_test.py --persona nephilim_eeva --quick
```

### [development/PERSONA_SCHEMA.md](development/PERSONA_SCHEMA.md)

**Complete field reference for persona JSON definitions**

**Contents:**
- Core fields (key, display_name, rarity, celestial_order, mcp_access)
- NEPHILIM extension fields (nephilim_lore, unlockable_lore, title, archetype)
- Voice and behavior configuration
- Validation rules and common mistakes

### [development/DESIGN_SYSTEM.md](development/DESIGN_SYSTEM.md)

**Visual design language and UI component guidelines**

**Contents:**
- Typography: Outfit (display), Manrope (body), Space Mono (mono)
- Celestial Order colors: Wanderer, Sage, Warden, Archon palettes
- CSS variables and Tailwind configuration
- Card effects: holographic shimmer, order glows
- Glassmorphism recipe and usage rules
- WCAG AA contrast requirements and accessibility rules

### [development/API_REFERENCE.md](development/API_REFERENCE.md)

**Backend REST API documentation**

**Contents:**
- All backend endpoints grouped by route file with request/response schemas
- Route files: chat.py, sessions.py, personas.py, nephilim.py
- Authentication and error handling patterns

### [development/JUPITER_WALLET_IMPLEMENTATION.md](development/JUPITER_WALLET_IMPLEMENTATION.md)

**Solana wallet integration and Jupiter DEX implementation**

### [archive/2026-05/OAUTH_IMPLEMENTATION_PLAN.md](archive/2026-05/OAUTH_IMPLEMENTATION_PLAN.md)

**Google OAuth integration design and implementation plan**

### [development/SCORER_PROMPT_IMPROVEMENTS.md](development/SCORER_PROMPT_IMPROVEMENTS.md)

**Persona test scorer calibration notes and prompt hardening analysis**

### QA Reports

| File | Description |
|------|-------------|
| [archive/2026-05/QA_WAVE1_REVIEW.md](archive/2026-05/QA_WAVE1_REVIEW.md) | Wave 1 QA gatekeeper review — Phase 7 component audit |
| [archive/2026-05/UX_WAVE1_REVIEW.md](archive/2026-05/UX_WAVE1_REVIEW.md) | UX review findings for Phase 7 NEPHILIM UI |
| [development/E2E_TEST_RUN.md](development/E2E_TEST_RUN.md) | Playwright E2E test results |
| [development/EDGE_CASE_TEST_RESULTS.md](development/EDGE_CASE_TEST_RESULTS.md) | Edge case and adversarial test findings |
| [development/UI_TESTING_BASELINE.md](development/UI_TESTING_BASELINE.md) | UI testing baseline snapshots and notes |

## Architecture

### [architecture/SQLITE_ARCHITECTURE.md](architecture/SQLITE_ARCHITECTURE.md)

**SQLite persistence layer design and implementation**

**Contents:**
- Thread-safety pattern via `_lock` in `BaseRepository`
- Schema: core tables, NEPHILIM progression tables
- Alembic migration approach and adding new tables
- Backup and recovery procedures

### [architecture/CELESTIAL_ORDER.md](architecture/CELESTIAL_ORDER.md)

**Four-tier Celestial Order system design**

**Contents:**
- Tier definitions: Wanderer (silver), Sage (cyan), Warden (purple), Archon (gold)
- Per-persona `mcp_access` field and legacy env var fallback
- Frontend theming and color palette per tier
- Adding new tiers

### [architecture/WALLET_METADATA.md](architecture/WALLET_METADATA.md)

**Wallet metadata & AI context layer**

**Contents:**
- What data the AI companion can see per-message (wallet inventory, balances, trade history)
- Hard guardrails: 3-wallet limit, secret key ceremony, mnemonic wipe
- SQLite tables for wallet persistence
- Trade pattern: SQLite-backed

## Decisions (ADRs)

| ADR | Title | Status |
|-----|-------|--------|
| [decisions/001-lore-as-typed-markdown-wiki-not-a-graph-db.md](decisions/001-lore-as-typed-markdown-wiki-not-a-graph-db.md) | Lore as typed-markdown-wiki not a graph DB | Accepted |
| [decisions/002-remove-mongodb-mcp.md](decisions/002-remove-mongodb-mcp.md) | Remove MongoDB MCP from nephilim | Accepted |

## Lore

> Full index: **[lore/README.md](lore/README.md)**

NEPHILIM worldbuilding, brand strategy, and persona development reference materials.

| File | Type | Description |
|------|------|-------------|
| [lore/BUSINESS_PLAN.md](lore/BUSINESS_PLAN.md) | Primary source | Brand philosophy, visual identity, persona design, monetization strategy |
| [lore/THE_CHRONICLE.md](lore/THE_CHRONICLE.md) | AI synthesis | Mythic prose — creation narrative, character profiles, philosophical arc |
| [lore/LORE_BIBLE_DRAFT.md](lore/LORE_BIBLE_DRAFT.md) | AI synthesis | Structured lore bible — Houses, antagonist, world rules, artifacts |
| [lore/NEPHILIM_LORE.md](lore/NEPHILIM_LORE.md) | Quick-ref | World bible — creation myth, the Fall, realm geography |
| [lore/NEPHILIM_FACTIONS.md](lore/NEPHILIM_FACTIONS.md) | Quick-ref | Six Houses aligned with Nephilim patrons |
| [lore/NEPHILIM_RANKS.md](lore/NEPHILIM_RANKS.md) | Quick-ref | Seeker progression system (Initiate → Nephilim) |
| [lore/_pdf/](lore/_pdf/) | Archive | Original PDF sources (Business Plan, Lore Bible, Chronicle) |

## Root Documentation

Essential documentation files in project root:

| Document | Description |
|----------|-------------|
| **[../README.md](../README.md)** | Main project overview and quick start |
| **[../CLAUDE.md](../CLAUDE.md)** | AI coding guidelines and development commands |
| **[../CHANGELOG.md](../CHANGELOG.md)** | Version history and feature additions |

## Scripts

Development and deployment scripts:

```
../scripts/
├── docker/     Docker setup and troubleshooting (7 scripts)
├── setup/      Local development setup (3 scripts)
└── utils/      Python utilities (4 scripts)
```

See [../scripts/README.md](../scripts/README.md) for full reference.

## Documentation Standards

**Format:** GitHub-flavored Markdown with CommonMark spec

**Structure:**
- Clear section headers (## H2 for main sections)
- Code blocks with language hints (```bash, ```python)
- Usage examples before detailed explanations
- Related docs linked at bottom

**Maintenance:**
- Update on major feature changes
- Archive obsolete docs to `archive/` subdirectories
- Keep guides under 500 lines (split if larger)
- Use relative links for portability

## Need Help?

- **Setup issues**: See [setup/DOCKER_QUICKSTART.md](setup/DOCKER_QUICKSTART.md) troubleshooting section
- **MCP integration**: See [development/ADDING_MCP_SERVERS.md](development/ADDING_MCP_SERVERS.md)
- **Testing**: See [development/TESTING_GUIDE.md](development/TESTING_GUIDE.md)
- **Persona development**: See [lore/README.md](lore/README.md) for lore doc hierarchy
- **Codebase questions**: See [../CLAUDE.md](../CLAUDE.md) project overview
