# services/telegram-gateway — Agent Context

Thin Telegram gateway to the NEPHILIM personas: relays Telegram messages to the coordinator's own session API (`../../src/coordinator/`, `http://127.0.0.1:8000`) and relays persona replies back. Single/dual user (allowlisted), text-only, no agent framework. Own venv, own tests, own launchd daemon — a separate **process**, not a separate repo (see [../../docs/LESSONS_LEARNED.md](../../docs/LESSONS_LEARNED.md#2026-07-04--telegram-gateway-built-standalone-folded-in-same-session) for why).

**Ecosystem/repo context: don't re-read the coordinator's own CLAUDE.md on every turn — fetch it on demand.**
Repo root: [../../CLAUDE.md](../../CLAUDE.md) · Ecosystem: [../../../CLAUDE.md](../../../CLAUDE.md)

## Critical invariants (read first, every session)

- **Thin gateway, HTTP-only client of the session API.** The gateway adds ZERO business logic — all coupling is HTTP calls to the coordinator session API. The API surface may be *extended by the coordinator* for features consumed by ALL clients (the React UI + this gateway), e.g. the ADR-011 conversation-control endpoints — that is a coordinator feature, not gateway logic. Existing calls: `POST /sessions`, `/sessions/{id}/greet`, `/sessions/{id}/chat`, `DELETE /sessions/{id}/messages`, read-only `GET /personas/{key}/toolkit` (for `/tools`). ADR-011 adds (thin calls, no logic): `GET /sessions/{id}` (whoami), `POST .../regenerate` `.../continue` `.../undo` `.../narrate` `.../impersonate`, `PUT/GET/DELETE .../note`. Do not add tool-use, exec, or trading access — a compromised chat must have nothing to reach. **File access is a scoped exception as of 2026-10-01 (phase-1 image transport):** the gateway may READ one path the coordinator names, inside one gateway-configured allowlist root (`TG_MEDIA_ROOT`), and upload it to an allowlisted chat. It gains no path construction, no directory traversal, no write access and no new inbound surface. `media.resolve_media_path` enforces the root **independently of the coordinator** — "the server names a path and the client opens it" is an arbitrary-file-read primitive the moment the server is confused, so the gateway does not trust it. Disabled by default (`TG_MEDIA_ENABLED=false`); enabling it without `TG_MEDIA_ROOT` fails at config load, not at the first send. Adds `POST /sessions/{id}/media/fixture` (dev only) to the consumed API surface and `/testimage`, which is registered ONLY when the flag is on and is deliberately absent from the slash menu and `/help`. Bot commands: `/start`, `/reset`, `/tools`, plus ADR-011: `/help`, `/whoami`, `/regen`, `/continue`, `/undo`, `/sys`, `/note`, `/impersonate`. Forwarded content is still refused before any of these (injection guard).
- **The security boundary is the Telegram allowlist + localhost-only backend.** The coordinator's chat routes have no auth (`AUTH_REQUIRED=false` is a deliberate, separate posture — see root [docs/THREAT_LEVEL.md](../../docs/THREAT_LEVEL.md)). Never expose `:8000`; never point `NEPHILIM_BASE_URL` at a non-loopback address.
- **Secrets only from this subfolder's own `.env` (chmod 600).** The launchd plist carries NO secrets (world-readable). This process must never load KuCoin/MongoDB/trading credentials — its `.env` holds only Telegram vars + the nephilim base URL. The bot token is intentionally shared with `eeva-dca`/`eeva-exec`'s notification bot (send-only there — no long-poll conflict); rotating it touches three `.env` files.
- **Never leak internals into a reply.** Exception text, URLs, session ids, and the bot token must NEVER be interpolated into an outbound Telegram message — only the fixed `MSG_*` strings in `eeva_telegram/handlers.py`. Full detail is logged locally (token-redacted).
- **Forwarded messages never reach the LLM** (injection guard, `handlers.is_forwarded`). Link previews are disabled on every outbound send (exfil guard, `eeva_telegram/messaging.py`).
- **One LLM call at a time.** The coordinator runs `OLLAMA_NUM_PARALLEL=1`; a global `asyncio.Lock` (`Gateway.llm_lock`) serialises chat/greet calls across this whole process.
- **One poller per bot token.** The `flock` singleton guard in `bin/run_telegram_bot.py` prevents a launchd `KeepAlive` restart from colliding with a shutting-down poller (Telegram 409).
- **Multi-instance (one process per persona/bot-token).** `EEVA_TG_INSTANCE=<name>` selects `.env.<name>` + a per-instance lock `data/bot.<name>.lock`; unset = the original single-bot path (`.env`, `data/bot.lock`), byte-identical. Each instance needs its OWN bot token in its own `.env.<name>` (chmod 600, gitignored via `.env.*`) and its own launchd plist (`com.eeva.telegram-<name>.plist`) carrying only `EEVA_TG_INSTANCE` (not the token). Live: `com.eeva.telegram` (EEVA) + `com.eeva.telegram-gwen` (Gwen NSFW, persona=gwen). The `bin/` entrypoint is version-controlled (do not let the root `.gitignore`'s venv patterns re-swallow it).
- **Independent dependency set.** This subfolder has its own `venv`/`pyproject.toml` (python-telegram-bot, httpx, python-dotenv) — do not add these to the coordinator's own dependencies, and do not import coordinator Python modules directly (HTTP only).

## Where things live

| What | Where |
|------|-------|
| Architecture + module map | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Dev / deploy / ops / testing reference | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| Threat model (subsystem section) | [../../docs/THREAT_LEVEL.md](../../docs/THREAT_LEVEL.md#subsystem--telegram-gateway-servicestelegram-gateway) |
| Roadmap items | [../../docs/ROADMAP.md](../../docs/ROADMAP.md) |
| Changelog entry | [../../CHANGELOG.md](../../CHANGELOG.md) |

## Quick commands

```bash
# Install (first time) — dedicated venv, Python 3.12 (never system 3.14)
python3.12 -m venv venv && ./venv/bin/pip install -e ".[dev]"

# Dev loop (run from this directory)
./venv/bin/pytest tests/ -q
./venv/bin/ruff check . && ./venv/bin/ruff format --check .

# Live smoke test (foreground run + manual checklist; no money at risk)
scripts/smoke_test.sh

# launchd control (long-polling daemon)
launchctl load   ~/Library/LaunchAgents/com.eeva.telegram.plist
launchctl unload ~/Library/LaunchAgents/com.eeva.telegram.plist
launchctl list | grep com.eeva.telegram
```

Full reference: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)
