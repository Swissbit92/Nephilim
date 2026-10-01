# tests/backend/coordinator/test_media_repository.py
"""Unit tests for MediaRepository (Telegram image transport, phase 1).

Hermetic: a throwaway SQLite file per test, self-created via _ensure_table, no
alembic. Covers allocation idempotence, the lowercase-hex shape the filesystem
layer depends on, and that two sessions never share a directory.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from src.coordinator.repositories.media_repository import MediaRepository

SESSION = "sess-alpha"
OTHER = "sess-beta"

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


# ---------- Fixtures ----------


@pytest.fixture
def repo(tmp_path):
    return MediaRepository(db_path=str(tmp_path / "media.db"))


# ---------- Tests — allocation ----------


def test_allocates_lowercase_hex_32(repo):
    """The filesystem layer's allowlist is exactly this shape — if allocation
    ever drifts from it, every write starts failing the path guard."""
    assert _HEX32.match(repo.get_or_create_media_dir(SESSION))


def test_allocation_is_idempotent(repo):
    first = repo.get_or_create_media_dir(SESSION)
    second = repo.get_or_create_media_dir(SESSION)
    assert first == second


def test_distinct_sessions_get_distinct_dirs(repo):
    assert repo.get_or_create_media_dir(SESSION) != repo.get_or_create_media_dir(OTHER)


def test_media_dir_is_never_the_session_id(repo):
    """The whole point of the side table: POST /sessions/import accepts a
    client-supplied id, so the id must never reach a filesystem path."""
    traversal = "../../etc"
    allocated = repo.get_or_create_media_dir(traversal)
    assert allocated != traversal
    assert _HEX32.match(allocated)


# ---------- Tests — lookup ----------


def test_get_media_dir_is_none_before_allocation(repo):
    assert repo.get_media_dir(SESSION) is None


def test_get_media_dir_returns_allocated(repo):
    allocated = repo.get_or_create_media_dir(SESSION)
    assert repo.get_media_dir(SESSION) == allocated


# ---------- Tests — delete ----------


def test_delete_returns_true_when_a_row_went(repo):
    repo.get_or_create_media_dir(SESSION)
    assert repo.delete(SESSION) is True
    assert repo.get_media_dir(SESSION) is None


def test_delete_returns_false_when_absent(repo):
    assert repo.delete(SESSION) is False


def test_delete_then_reallocate_yields_a_new_dir(repo):
    first = repo.get_or_create_media_dir(SESSION)
    repo.delete(SESSION)
    assert repo.get_or_create_media_dir(SESSION) != first


# ---------- Tests — schema ----------


def test_media_dir_is_unique_across_sessions(repo):
    """A UNIQUE collision must be a hard error, never a silent merge of two
    sessions' images into one directory."""
    allocated = repo.get_or_create_media_dir(SESSION)
    with pytest.raises(sqlite3.IntegrityError):
        repo._execute(
            "INSERT INTO session_media_dirs (session_id, media_dir, created_at) "
            "VALUES (?, ?, ?)",
            (OTHER, allocated, "2026-10-01T00:00:00Z"),
        )
