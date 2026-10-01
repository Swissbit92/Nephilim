# tests/backend/coordinator/test_media_fixture_route.py
"""The fixture media endpoint and the MediaItem response contract (phase 1 / M2).

TestClient WITHOUT the context manager, so lifespan never runs — the house
convention, and the reason no background startup work fires here.

The contract test at the bottom is the important one: the body this endpoint
returns must be parseable by the SAME extractor the gateway will use on the
real chat path. If the two shapes drift, the transport proof proves nothing.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.coordinator.config import get_settings
from src.coordinator.repositories.media_repository import MediaRepository
from src.coordinator.schemas import ResponseMetadata
from src.coordinator.server import app

client = TestClient(app)  # no context manager → lifespan skipped

SESSION = "sess-fixture-1"


# ---------- Fixtures ----------


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Media + fixture endpoint enabled, repos patched at the startup seam."""
    monkeypatch.setenv("MEDIA_ENABLED", "true")
    monkeypatch.setenv("MEDIA_FIXTURE_ENABLED", "true")
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    get_settings.cache_clear()

    session_repo = MagicMock()
    session_repo.session_exists.return_value = True
    monkeypatch.setattr(
        "src.coordinator.routes.sessions._get_repos",
        lambda: (session_repo, MagicMock(), MagicMock()),
    )
    media_repo = MediaRepository(db_path=str(tmp_path / "media.db"))
    monkeypatch.setattr("src.coordinator.startup.get_media_repo", lambda: media_repo)
    return {"root": tmp_path / "media", "session_repo": session_repo}


# ---------- Tests — the flag gate ----------


def test_404_when_the_fixture_flag_is_off(tmp_path, monkeypatch):
    """404 rather than 403: a dev surface should not advertise itself."""
    monkeypatch.setenv("MEDIA_FIXTURE_ENABLED", "false")
    get_settings.cache_clear()
    assert client.post(f"/sessions/{SESSION}/media/fixture").status_code == 404


def test_404_for_an_unknown_session(wired):
    wired["session_repo"].session_exists.return_value = False
    assert client.post(f"/sessions/{SESSION}/media/fixture").status_code == 404


def test_409_when_storage_itself_is_disabled(wired, monkeypatch):
    """A refusal must surface, not return an empty media list — an ambiguous
    success is the failure mode this whole feature guards against."""
    monkeypatch.setenv("MEDIA_ENABLED", "false")
    get_settings.cache_clear()
    resp = client.post(f"/sessions/{SESSION}/media/fixture")
    assert resp.status_code == 409
    assert "disabled" in resp.json()["detail"]


# ---------- Tests — the happy path ----------


def test_returns_one_media_item(wired):
    body = client.post(f"/sessions/{SESSION}/media/fixture").json()
    meta = ResponseMetadata(**body["metadata"])
    assert len(meta.media) == 1


def test_the_described_file_exists_and_matches_its_digest(wired):
    """sha256 on the response is what lets a consumer prove losslessness end to
    end; it is worthless if it does not describe the bytes on disk."""
    body = client.post(f"/sessions/{SESSION}/media/fixture").json()
    item = ResponseMetadata(**body["metadata"]).media[0]
    on_disk = Path(item.path)

    assert on_disk.is_file()
    assert on_disk.stat().st_size == item.bytes
    assert hashlib.sha256(on_disk.read_bytes()).hexdigest() == item.sha256


def test_path_is_absolute_and_inside_the_media_root(wired):
    body = client.post(f"/sessions/{SESSION}/media/fixture").json()
    item = ResponseMetadata(**body["metadata"]).media[0]
    path = Path(item.path)
    assert path.is_absolute()
    assert path.is_relative_to(wired["root"].resolve())


def test_protect_content_defaults_on(wired):
    """The privacy lever that actually works on Telegram: it blocks forwarding
    and saving at send time."""
    body = client.post(f"/sessions/{SESSION}/media/fixture").json()
    assert ResponseMetadata(**body["metadata"]).media[0].protect_content is True


def test_dimensions_are_reported(wired):
    body = client.post(f"/sessions/{SESSION}/media/fixture").json()
    item = ResponseMetadata(**body["metadata"]).media[0]
    assert (item.width, item.height) == (256, 256)


def test_caption_is_plain_text(wired):
    """messaging.py forbids parse_mode; a caption is the same surface, so it
    must not contain anything that would need one."""
    body = client.post(f"/sessions/{SESSION}/media/fixture").json()
    caption = ResponseMetadata(**body["metadata"]).media[0].caption
    assert caption
    assert not set(caption) & set("*_`[]()~>#+=|{}")


def test_two_calls_share_a_directory_but_not_an_id(wired):
    first = ResponseMetadata(
        **client.post(f"/sessions/{SESSION}/media/fixture").json()["metadata"]
    ).media[0]
    second = ResponseMetadata(
        **client.post(f"/sessions/{SESSION}/media/fixture").json()["metadata"]
    ).media[0]
    assert first.media_id != second.media_id
    assert Path(first.path).parent == Path(second.path).parent


# ---------- Tests — the contract ----------


def test_body_is_chat_shaped(wired):
    """The gateway must parse this with the same extractor it uses on /chat.
    Pinning the keys here is what stops the two shapes drifting apart."""
    body = client.post(f"/sessions/{SESSION}/media/fixture").json()
    assert set(body) == {
        "answer",
        "message_flow",
        "message_count",
        "used_search",
        "metadata",
        "rewritten",
    }
    assert isinstance(body["answer"], str)
    assert body["message_flow"] == "single"


def test_media_is_empty_by_default_on_the_model():
    """Every other response path must be unaffected until a backend is wired."""
    assert ResponseMetadata().media == []


# ---------- Tests — the FK that unit tests cannot see ----------


def test_fk_rejects_a_session_that_does_not_exist(tmp_path):
    """QA flagged this as a latent green-test/failing-prod shape.

    ``_ensure_table`` deliberately omits the FK, so in every unit test a
    media_dir can be allocated for a session id absent from ``chat_sessions``.
    Production runs the alembic schema, which has the FK and rejects it. This
    builds the real schema so the divergence is pinned rather than discovered
    in production.
    """
    db = tmp_path / "fk.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE chat_sessions (id TEXT PRIMARY KEY);
        CREATE TABLE session_media_dirs (
            session_id TEXT PRIMARY KEY,
            media_dir  TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()
    conn.close()

    repo = MediaRepository(db_path=str(db))
    with pytest.raises(sqlite3.IntegrityError):
        repo.get_or_create_media_dir("session-that-does-not-exist")


def test_fk_cascade_removes_the_mapping_with_its_session(tmp_path):
    """Deleting a session must not leave a dangling media_dir row."""
    db = tmp_path / "cascade.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE chat_sessions (id TEXT PRIMARY KEY);
        CREATE TABLE session_media_dirs (
            session_id TEXT PRIMARY KEY,
            media_dir  TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
        );
        INSERT INTO chat_sessions VALUES ('live-session');
        """
    )
    conn.commit()
    conn.close()

    repo = MediaRepository(db_path=str(db))
    repo.get_or_create_media_dir("live-session")
    assert repo.get_media_dir("live-session") is not None

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM chat_sessions WHERE id = 'live-session'")
    conn.commit()
    conn.close()

    assert repo.get_media_dir("live-session") is None
