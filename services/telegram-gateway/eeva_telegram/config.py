"""Configuration loader for eeva-telegram.

Loads all runtime settings from .env (via python-dotenv). The .env file is the
single source of truth; no Keychain, no secret manager. See docs/SECURITY.md.

This process is deliberately isolated from the rest of the ecosystem: it must
NEVER load KuCoin / MongoDB / trading credentials. Its .env holds only the
Telegram token, the chat allowlist, and the nephilim base URL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class TelegramConfig:
    """Frozen runtime config for the Telegram gateway.

    Required fields fail fast if missing. Tuning fields have safe defaults.
    """

    # Telegram
    bot_token: str
    allowed_chat_ids: frozenset[int]

    # NEPHILIM backend (must be loopback)
    nephilim_base_url: str

    # Persona selection
    default_persona_key: str
    chat_personas: dict[int, str]  # chat_id -> persona_key override

    # Tuning
    request_timeout_seconds: float
    typing_interval_seconds: float
    message_char_limit: int

    # Media (image transport, phase 1). Disabled by default.
    #: The gateway enforces its OWN allowlist root rather than trusting the
    #: path the coordinator names. "A server names a path and the client opens
    #: it" is an arbitrary-file-read primitive the moment the server is
    #: confused; this is the belt to the coordinator's braces.
    media_enabled: bool
    media_root: Path | None
    media_max_bytes: int

    # Ops
    log_content: bool

    def persona_for_chat(self, chat_id: int) -> str:
        """Resolve the persona key for a chat: per-chat override, else default."""
        return self.chat_personas.get(chat_id, self.default_persona_key)

    def is_allowed(self, chat_id: int) -> bool:
        """True iff this chat_id is on the allowlist."""
        return chat_id in self.allowed_chat_ids


def _require(key: str) -> str:
    value = os.getenv(key)
    if value is None or value == "" or value.startswith("YOUR_"):
        raise RuntimeError(f"Missing or placeholder env var: {key}. Copy .env.example to .env and fill in real values.")
    return value


def _parse_chat_ids(raw: str) -> frozenset[int]:
    """Parse a comma-separated list of numeric chat IDs.

    Raises RuntimeError if any entry is non-numeric or the list is empty — an
    empty/garbage allowlist would either lock everyone out or (worse) is a sign
    of misconfiguration, so fail loud rather than silently.
    """
    ids: set[int] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            ids.add(int(token))
        except ValueError as exc:
            raise RuntimeError(f"TG_ALLOWED_CHAT_IDS contains a non-numeric entry: {token!r}") from exc
    if not ids:
        raise RuntimeError("TG_ALLOWED_CHAT_IDS is empty — at least one numeric chat id is required.")
    return frozenset(ids)


def _parse_chat_personas(raw: str) -> dict[int, str]:
    """Parse "chat_id:persona_key,chat_id:persona_key" overrides.

    Empty string -> no overrides. Malformed entries fail loud.
    """
    mapping: dict[int, str] = {}
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        if ":" not in token:
            raise RuntimeError(f"TG_CHAT_PERSONAS entry missing ':' — expected chat_id:persona_key, got {token!r}")
        chat_raw, persona = token.split(":", 1)
        persona = persona.strip()
        try:
            chat_id = int(chat_raw.strip())
        except ValueError as exc:
            raise RuntimeError(f"TG_CHAT_PERSONAS has a non-numeric chat id: {chat_raw!r}") from exc
        if not persona:
            raise RuntimeError(f"TG_CHAT_PERSONAS entry has an empty persona key for chat {chat_id}")
        mapping[chat_id] = persona
    return mapping


def load_config(env_path: Path | None = None) -> TelegramConfig:
    """Load config from .env.

    Args:
        env_path: Override .env location. Defaults to <project_root>/.env.

    Raises:
        RuntimeError: if any required variable is missing or still placeholder.
    """
    if env_path is None:
        env_path = _PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=env_path, override=False)

    # Fail at config load, not at the first send: a media-enabled gateway with
    # no root would reject every image with a message that looks like the
    # coordinator's fault.
    _media_enabled = os.getenv("TG_MEDIA_ENABLED", "false").lower() == "true"
    _raw_root = os.getenv("TG_MEDIA_ROOT", "").strip()
    if _media_enabled and not _raw_root:
        raise RuntimeError("TG_MEDIA_ENABLED=true requires TG_MEDIA_ROOT to be set.")
    _media_root = Path(_raw_root).resolve() if _raw_root else None

    return TelegramConfig(
        bot_token=_require("TG_BOT_TOKEN"),
        allowed_chat_ids=_parse_chat_ids(_require("TG_ALLOWED_CHAT_IDS")),
        nephilim_base_url=os.getenv("NEPHILIM_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
        default_persona_key=os.getenv("EEVA_PERSONA_KEY", "nephilim_eeva"),
        chat_personas=_parse_chat_personas(os.getenv("TG_CHAT_PERSONAS", "")),
        request_timeout_seconds=float(os.getenv("NEPHILIM_TIMEOUT_SECONDS", "180")),
        typing_interval_seconds=float(os.getenv("TG_TYPING_INTERVAL_SECONDS", "4.5")),
        message_char_limit=int(os.getenv("TG_MESSAGE_CHAR_LIMIT", "4000")),
        media_enabled=_media_enabled,
        media_root=_media_root,
        media_max_bytes=int(os.getenv("TG_MEDIA_MAX_BYTES", "20000000")),
        log_content=os.getenv("TG_LOG_CONTENT", "false").lower() == "true",
    )
