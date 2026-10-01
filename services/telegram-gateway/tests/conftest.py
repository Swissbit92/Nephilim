"""Shared test fixtures for eeva-telegram."""

from __future__ import annotations

from pathlib import Path

import pytest

from eeva_telegram.config import TelegramConfig
from eeva_telegram.session_store import SessionStore


@pytest.fixture
def cfg() -> TelegramConfig:
    """A fully-populated TelegramConfig with dummy values, safe for unit tests."""
    return TelegramConfig(
        bot_token="test-bot-token",
        allowed_chat_ids=frozenset({111, 222}),
        nephilim_base_url="http://127.0.0.1:8000",
        default_persona_key="nephilim_eeva",
        chat_personas={222: "nephilim_nyx"},
        request_timeout_seconds=5.0,
        typing_interval_seconds=0.01,
        message_char_limit=4000,
        media_enabled=False,
        media_root=None,
        media_max_bytes=20_000_000,
        log_content=False,
    )


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    """A temp-file-backed SessionStore, isolated per test."""
    s = SessionStore(tmp_path / "sessions.sqlite3")
    yield s
    s.close()
