---
title: Threat Level
status: active
created: 2026-07-04
last_reviewed_on: 2026-10-23
review_in: 6 months
applies_to: nephilim
threat_level: Low
---

# Threat Level — nephilim

A lightweight, living threat model. The `threat_level` frontmatter field above is the
machine-readable rating (Low · Medium · High · Critical, CVSS-aligned) — keep it current.

## Current assessment

| Field | Value |
|-------|-------|
| Threat level | Low (see frontmatter) |
| Owner | Repo maintainer (single-user deployment) |
| Deployment | Local-first: launchd on a Mac Mini, backend `:8000` + static frontend `:3001`, **bound to localhost** (remote access only via SSH tunnel) |
| Last reviewed | 2026-07-19 |

Rated **Low** because the only running deployment is a single-user, always-on Mac Mini
with all listeners on localhost and no public ingress. The rating would rise to **Medium**
the moment the service is exposed to a network (see [Escalation](#escalation)) — at which
point `AUTH_REQUIRED=false` (below) stops being acceptable.

## Trust boundaries

```
[ user chat input ]              [ Brave web results / RAG memory ]
        │                                    │  (untrusted content — reaches the model
        ▼                                    ▼   ungated at tool-SELECTION; see Residual risks)
   [ FastAPI coordinator ] ── system prompt ──> [ Ollama (local LLM) ]
        │                                    │
        ├──> [ SQLite (chats.db) ]           └──> [ Brave MCP (Docker, ephemeral) ]
        └──> [ Solana / Jupiter wallet MCP ]  (encrypted private keys; propose→confirm→execute)
                          ▲
                   trust boundary
```

Untrusted data crossing in: (1) the user's chat messages, (2) web-search results and
retrieved RAG memory that get summarized into responses. Trusted: the persona JSON, system
prompt, and local config.

**Corrected 2026-07-19 — a mitigation this document previously claimed does not exist.**
It stated that "the agentic pipeline (ADR-004) enforces a strict trust hierarchy —
retrieved content can inform an answer but can never *trigger* a tool call." That control
(`InjectionGuard.check_tool_trigger_source`) was built and red-team-tested but **never
wired into a production code path**: its only caller was the ADR-004 pipeline, which was
gated behind `AGENTIC_ENABLED` and never enabled in prod. `ToolBrainService` accepted an
`injection_guard` parameter it never invoked. The pipeline and both unreachable guard
methods were deleted 2026-07-19 ([ADR-004](decisions/004-persona-safe-agentic-tool-calls.md),
Superseded). **There is currently NO control preventing retrieved RAG/lore content from
influencing a tool call** — see Accepted risks.

## STRIDE

| Threat | Applies? | Control / mitigation | Status |
|--------|----------|----------------------|--------|
| **S**poofing | Low (local, single-user) | `AUTH_REQUIRED=false` is an *accepted* posture for localhost-only; Google-OAuth+JWT path exists for when auth is turned on | Accepted |
| **T**ampering | Low | `.env` untracked + gitignored; SQLite is local-only; no external writers | OK |
| **R**epudiation | Low | Not a multi-user/audit context; message history persisted with timestamps | N/A |
| **I**nformation disclosure | Medium→controlled | HTTP error bodies return `type(e).__name__`, never `str(e)` (fixed 2026-07-04); wallet private keys AES-GCM encrypted; tool-name + private-key redaction in `_finalize_response` | Mitigated |
| **D**enial of service | Low | Localhost only; no untrusted network ingress | N/A |
| **E**levation of privilege | Low | Per-persona `mcp_access` allowlist enforced by `ToolCallInterceptor` on every tool-brain call; wallet swaps require explicit `user_confirmed` (propose→confirm→execute); wallet is never placed in the model-decided tool surface | Mitigated |

## OWASP Top 10 checklist

- [x] A01 Broken access control — per-persona `mcp_access`; wallet confirm gate. Auth is off by policy (localhost single-user); revisit before any network exposure.
- [x] A02 Cryptographic failures — wallet private keys AES-GCM encrypted; JWT secret must be supplied (no committed default — fixed 2026-07-04).
- [ ] A03 Injection — LLM prompt-injection is the live surface. **Partially mitigated:** `ToolCallInterceptor` gates every executed tool call (per-persona `mcp_access` + argument allowlist + hard-block on `execute_swap` from a non-`user_confirmed` source), and `InjectionGuard.sanitize_memory_write` strips tool-call syntax before a RAG write. **NOT mitigated:** nothing stops retrieved RAG/lore content from influencing which tool the model chooses — the control that claimed to (ADR-004 trust hierarchy) was never wired to prod and was deleted 2026-07-19. No SQL string-building (parameterized repos).
- [x] A04 Insecure design — propose→confirm→execute for financial actions; deterministic middleware, not LLM self-policing.
- [ ] A05 Security misconfiguration — `.env.example`/`.env` flag drift tracked; `AUTH_REQUIRED=false` is deliberate for local use. Recheck on exposure.
- [ ] A06 Vulnerable / outdated components — no automated dependency scanning yet (follow-up; CI added 2026-07-04 is a starting point).
- [x] A07 Identification & authentication failures — OAuth+JWT available; off by policy locally.
- [x] A08 Software & data integrity failures — no committed secrets in the tracked tree (`tracked_artifacts = 0`).
- [ ] A09 Security logging & monitoring failures — app logs errors server-side; no central monitoring (acceptable for single-user).
- [x] A10 SSRF — outbound web calls go only to the Brave MCP; no user-controlled URL fetching.

## Residual risks

Risks accepted for now, with rationale:
- **`AUTH_REQUIRED=false`** — accepted for the localhost-only, single-user deployment. Must flip to `true` (with real JWT-secret handling) before any network exposure. See [SECURITY.md](../SECURITY.md).
- **Leaked-credential rotation unconfirmed** — the historical Atlas `Eeva_Admin` URI (and Brave/JWT) were once committed and pushed ([ADR-002](decisions/002-remove-mongodb-mcp.md)); rotation is an **outstanding action item** tracked in [SECURITY.md](../SECURITY.md). MongoDB is no longer used by nephilim, but the pushed credential must still be treated as compromised.
- **The live `BRAVE_API_KEY` was written to a world-readable log, by a second and unrelated vector** — `mcp_client_stdio` passed it as `-e BRAVE_API_KEY=<value>` in `argv`, and a `subprocess.TimeoutExpired` traceback logged the whole command. Gitignored, so never committed or pushed, and one occurrence — but the key is current (hash-verified against `.env`) and the file is readable by any local process. **The code path is fixed (v0.2.3, value passed via the child environment, 4 tests pin it); the key itself is NOT rotated and only the operator can close that.** Full write-up in [SECURITY.md](../SECURITY.md). ⚠️ The generalisable control: a secret in a command line is a secret in every crash report, process listing and subprocess exception message — pass credentials through the environment, never through `argv`.
- **LLM prompt injection via retrieved content** — inherent to any LLM app, and currently **unbounded at the tool-selection layer**. The ADR-004 trust-hierarchy check that this document used to cite was never reachable in production and is now deleted; the surviving controls gate *what a tool may do* (interceptor) and *what enters memory* (`sanitize_memory_write`), not *what the model is influenced to call*. Accepted for a localhost single-user deployment where the only untrusted input is web-search results the user asked for. Revisit before any network exposure, before adding write-capable tools, or if the ADR-009 inner-wisdom (memory-as-tools) work proceeds.

## Subsystem — Telegram gateway (`services/telegram-gateway/`)

A second client of the coordinator (see [ARCHITECTURE.md](ARCHITECTURE.md)): a single-user, allowlisted Telegram bot relaying to the existing session API. It introduces one new secret (a Telegram bot token) and one new untrusted-input channel (Telegram messages); it does not change nephilim's own trust boundary (still localhost-only, `AUTH_REQUIRED=false`).

| Threat | Applies? | Control / mitigation | Status |
|--------|----------|----------------------|--------|
| **S**poofing | Yes | Hard numeric `chat_id` allowlist; non-allowlisted updates silently dropped, no reply. Bot token is the only impersonation vector — chmod-600 `.env`, never in the launchd plist or logs. | Mitigated |
| **T**ampering | Low | Outbound long-poll only (no inbound listener); local sqlite holds only ids + opaque session UUIDs. | Mitigated |
| **R**epudiation | Low | Local logs record chat_id + outcome, not message content by default. | Accepted |
| **I**nformation disclosure | Yes | Fixed user-facing error strings only (no exception/URL/session-id leakage); Telegram link previews disabled (server-side fetch is an exfil channel); token-redaction log filter. | Mitigated |
| **D**enial of service | Low | Single/dual user; a global `asyncio.Lock` + single-poller `flock` bound concurrency to nephilim's own `OLLAMA_NUM_PARALLEL=1` limit. No rate limiter (accepted at this scale). | Accepted |
| **E**levation of privilege | Yes | The gateway process has no exec/file/trading access and no trading credentials in its environment — a compromised chat can reach nothing beyond nephilim's existing chat API. | Mitigated |

Residual risks specific to this subsystem: typed (non-forwarded) prompt injection is accepted the same way as nephilim's own LLM surface (bounded by the read-oriented backend; the ADR-004 agentic path was deleted 2026-07-19); the bot token is shared with `eeva-dca`/`eeva-exec`'s notification bot (send-only, no polling conflict) — rotating it now touches three `.env` files.

## Escalation

If the threat level would rise to **Critical** (e.g. the service is exposed to the public
internet, or a wallet key compromise is suspected): stop the launchd services, rotate all
credentials, enable `AUTH_REQUIRED=true`, and record a post-incident note in
[LESSONS_LEARNED.md](LESSONS_LEARNED.md).
