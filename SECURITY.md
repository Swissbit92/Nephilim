# Security Policy

How to report a vulnerability in nephilim, and the current security posture of the
project. nephilim is a **local-first, single-user** AI-companion platform (FastAPI +
Ollama + SQLite) run always-on via launchd on a Mac Mini, with all listeners bound to
localhost. See [docs/THREAT_LEVEL.md](docs/THREAT_LEVEL.md) for the threat model.

## Supported versions

| Version | Supported |
|---------|-----------|
| latest (`main`/`dev`) | ✅ |
| older | ⚠️ best-effort |

## Reporting a vulnerability

**Do not open a public issue for security problems.** Report privately via GitHub
**Private Vulnerability Reporting** (repo → Security → Report a vulnerability).

Include: affected version/commit, reproduction steps, impact, and any suggested fix.

## Response targets

| Stage | Target |
|-------|--------|
| Acknowledge report | within 48 hours |
| Initial triage / severity | within 5 business days |
| Fix or mitigation plan | Critical: days · High: ~2 weeks · Medium/Low: next release |

## Security posture

### Authentication — `AUTH_REQUIRED=false` (accepted)

The deployment runs with `AUTH_REQUIRED=false`. This is a **deliberate, accepted posture**
for the current single-user, localhost-only deployment (launchd on a Mac Mini; remote
access only through an SSH tunnel — no public ingress). A Google-OAuth + JWT path exists
and must be enabled (`AUTH_REQUIRED=true`, with a real `JWT_SECRET_KEY`) **before any
network exposure**. There is no committed default JWT secret — `docker-compose.yml`
force-requires `JWT_SECRET_KEY` (a `docker-compose up` without it fails loudly rather than
booting with a known secret).

### Credential handling

- `.env` and `.env.docker` are **untracked and gitignored**; only `.env.example` (with
  placeholders) is committed. `tracked_artifacts = 0` — no secrets in the tracked tree.
- Solana/Jupiter wallet private keys are AES-GCM encrypted at rest.
- HTTP error responses return `type(e).__name__`, never the raw exception text, so internal
  detail does not leak to clients (financial endpoints included).

### Credential rotation — OUTSTANDING action item

A historical exposure is on record ([ADR-002](docs/decisions/002-remove-mongodb-mcp.md)):
the Atlas `Eeva_Admin` MongoDB URI (and, by the same exposure, the Brave API key and any
JWT secret) was once committed in a tracked `.env` and pushed to GitHub. The MongoDB MCP
has since been fully removed from nephilim, but **a pushed credential must be treated as
compromised regardless.**

| Credential | Status (2026-07-04) | Action |
|-----------|---------------------|--------|
| MongoDB Atlas `Eeva_Admin` URI | Rotation **unconfirmed** — no longer used by nephilim, but shared across the E.E.V.A. ecosystem | Rotate on the Atlas side; record the date here |
| Brave API key | Rotation **unconfirmed** — still in active use, and **exposed a SECOND time by a different vector** (see below) | Rotate in the Brave dashboard; the live `.env` then holds the new value |
| JWT secret | N/A locally — no real `JWT_SECRET_KEY` was set (auth is off); the only committed default has been removed | Generate a fresh secret when enabling auth |

> This table is the source of truth for rotation status. Update the **Status** column (and
> add the rotation date) once each provider-side rotation is completed.

#### Second exposure of the Brave key — the code is fixed, the key is not

Found 2026-09-24 while auditing the legacy search path, and **independent of the
2026-07-04 committed-`.env` exposure above**: the live `BRAVE_API_KEY` was written into
`logs/launchd-backend.err.log`. `mcp_client_stdio` passed it as `-e BRAVE_API_KEY=<value>`
on the `docker run` command line, and on `subprocess.TimeoutExpired` Python puts the entire
command into the exception message, which the traceback then logged.

- **Blast radius.** The log is gitignored, so the value was never committed or pushed — but
  it is **world-readable on disk** and the key is current (hash-verified against `.env`).
  One occurrence.
- **The code path is FIXED (v0.2.3).** A bare `-e BRAVE_API_KEY` with the value supplied in
  the child process's environment, so it never enters `argv`. Four tests pin it.
- ⚠️ **STILL OUTSTANDING: rotate the key that was already written.** Fixing the writer does
  not unwrite the log line. This is an operator action — Brave dashboard → API Keys →
  generate new, revoke old — and it is the one security item on this repo's roadmap that
  code cannot close.

**The general lesson, which generalises past this key:** a secret passed on a command line
is a secret in every crash report, every process listing and every subprocess exception
message. Pass credentials through the environment, never through `argv`.

## Scope

- **In scope:** code in this repository.
- **Out of scope:** third-party dependencies (report upstream), social engineering, and
  issues requiring privileged local access to the single-user host.
