"""
Unit tests for src/coordinator/routes/sessions.py

Mocks:
- src.coordinator.routes.sessions._get_repos  (returns (session_repo, message_repo, emotional_state_repo) mocks)
- src.coordinator.routes.sessions.get_persona_card
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from fastapi.testclient import TestClient
from src.coordinator.server import app

client = TestClient(app)


# ─── Fixture helpers ──────────────────────────────────────────────────────────

_SENTINEL = object()


def _make_repos(
    *,
    sessions=None,
    session_obj=_SENTINEL,
    session_exists=True,
    messages=None,
    message_id="msg-1",
    emotional_state=None,
    persona_key="eeva",
):
    """Return (session_repo, message_repo, emotional_state_repo) mock triple."""
    _default_session = {
        "id": "sess-1",
        "persona_key": persona_key,
        "title": "Test Session",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    resolved_session = _default_session if session_obj is _SENTINEL else session_obj
    session_repo = MagicMock()
    session_repo.get_all_sessions.return_value = sessions or []
    session_repo.get_session.return_value = resolved_session
    session_repo.session_exists.return_value = session_exists
    session_repo.create_session.return_value = "sess-1"
    session_repo.get_persona_key.return_value = persona_key if session_exists else None

    message_repo = MagicMock()
    message_repo.get_messages_by_session.return_value = messages or []
    message_repo.create_message.return_value = message_id

    emo_repo = MagicMock()
    if emotional_state is None:
        emo_obj = MagicMock()
        emo_obj.trust_level = 5
        emo_obj.rapport = 3
        emo_obj.current_mood = "neutral"
        emo_obj.mood_intensity = 1
        emo_obj.last_emotional_event = None
        emo_obj.updated_at = "2026-01-01T00:00:00"
        emo_repo.get_or_create.return_value = emo_obj
    else:
        emo_repo.get_or_create.return_value = emotional_state

    return session_repo, message_repo, emo_repo


def _patch_repos(**kwargs):
    """Return a context manager that patches _get_repos to return mocks."""
    repos = _make_repos(**kwargs)
    return patch("src.coordinator.routes.sessions._get_repos", return_value=repos), repos


# ─── GET /sessions ────────────────────────────────────────────────────────────

class TestListSessions:
    def test_empty_returns_empty_list(self):
        with patch("src.coordinator.routes.sessions._get_repos", return_value=_make_repos(sessions=[])):
            resp = client.get("/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_sessions_returned(self):
        sess = [{"id": "s1", "persona_key": "eeva", "title": "Chat 1", "created_at": "2026-01-01", "updated_at": "2026-01-01"}]
        with patch("src.coordinator.routes.sessions._get_repos", return_value=_make_repos(sessions=sess)):
            resp = client.get("/sessions")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["id"] == "s1"


# ─── POST /sessions ───────────────────────────────────────────────────────────

class TestCreateSession:
    def test_creates_and_returns_session(self):
        with patch("src.coordinator.routes.sessions._get_repos", return_value=_make_repos()):
            resp = client.post("/sessions", json={"persona_key": "eeva", "title": "New Chat"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "sess-1"
        assert body["message_count"] == 0

    def test_blank_title_defaults_to_new_chat(self):
        repos = _make_repos()
        session_repo, *_ = repos
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos):
            client.post("/sessions", json={"persona_key": "eeva", "title": "   "})
        session_repo.create_session.assert_called_once_with("eeva", "New Chat")

    def test_missing_persona_key_returns_422(self):
        resp = client.post("/sessions", json={"title": "Chat"})
        assert resp.status_code == 422


# ─── GET /sessions/{session_id} ───────────────────────────────────────────────

class TestGetSession:
    def test_returns_session_and_messages(self):
        msgs = [{"id": "m1", "role": "user", "content": "hello", "timestamp": "2026-01-01"}]
        with patch("src.coordinator.routes.sessions._get_repos", return_value=_make_repos(messages=msgs)):
            resp = client.get("/sessions/sess-1")
        assert resp.status_code == 200
        body = resp.json()
        assert "session" in body
        assert "messages" in body
        assert body["session"]["message_count"] == 1

    def test_session_not_found_returns_404(self):
        with patch("src.coordinator.routes.sessions._get_repos", return_value=_make_repos(session_obj=None)):
            resp = client.get("/sessions/nonexistent")
        assert resp.status_code == 404

    def test_message_count_matches_messages(self):
        msgs = [
            {"id": "m1", "role": "user", "content": "a"},
            {"id": "m2", "role": "assistant", "content": "b"},
        ]
        with patch("src.coordinator.routes.sessions._get_repos", return_value=_make_repos(messages=msgs)):
            resp = client.get("/sessions/sess-1")
        assert resp.json()["session"]["message_count"] == 2


# ─── PUT /sessions/{session_id} ───────────────────────────────────────────────

class TestUpdateSession:
    def test_updates_title(self):
        updated_session = {"id": "sess-1", "persona_key": "eeva", "title": "Renamed", "created_at": "2026-01-01", "updated_at": "2026-01-02"}
        repos = _make_repos(session_obj=updated_session)
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos):
            resp = client.put("/sessions/sess-1", json={"title": "Renamed"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["id"] == "sess-1"

    def test_not_found_returns_404(self):
        repos = _make_repos(session_exists=False)
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos):
            resp = client.put("/sessions/bad-id", json={"title": "X"})
        assert resp.status_code == 404

    def test_blank_title_defaults_to_untitled(self):
        repos = _make_repos()
        session_repo, *_ = repos
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos):
            client.put("/sessions/sess-1", json={"title": "   "})
        session_repo.update_session_title.assert_called_once_with("sess-1", "Untitled")


# ─── DELETE /sessions/{session_id} ───────────────────────────────────────────

class TestDeleteSession:
    def test_deletes_session(self):
        repos = _make_repos()
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos):
            resp = client.delete("/sessions/sess-1")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_not_found_returns_404(self):
        repos = _make_repos(session_exists=False)
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos):
            resp = client.delete("/sessions/bad-id")
        assert resp.status_code == 404


# ─── POST /sessions/{session_id}/messages ────────────────────────────────────

class TestAddMessage:
    def test_adds_message_returns_message_id(self):
        with patch("src.coordinator.routes.sessions._get_repos", return_value=_make_repos(message_id="msg-42")):
            resp = client.post("/sessions/sess-1/messages", json={
                "role": "user",
                "content": "Hello there",
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["message_id"] == "msg-42"

    def test_session_not_found_returns_404(self):
        repos = _make_repos(session_exists=False)
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos):
            resp = client.post("/sessions/bad/messages", json={"role": "user", "content": "hi"})
        assert resp.status_code == 404

    def test_missing_content_returns_422(self):
        resp = client.post("/sessions/sess-1/messages", json={"role": "user"})
        assert resp.status_code == 422

    def test_optional_fields_accepted(self):
        with patch("src.coordinator.routes.sessions._get_repos", return_value=_make_repos()):
            resp = client.post("/sessions/sess-1/messages", json={
                "role": "assistant",
                "content": "Hi",
                "latency_ms": 500,
                "source_type": "llm",
                "multi_message_id": "mm-1",
                "multi_message_index": 0,
            })
        assert resp.status_code == 200


# ─── DELETE /sessions/{session_id}/messages ──────────────────────────────────

@contextmanager
def _derived_stores(*, summary_repo=_SENTINEL, note_repo=_SENTINEL, rag=_SENTINEL):
    """Patch the three message-derived stores a reset must also clear."""
    summary_repo = MagicMock() if summary_repo is _SENTINEL else summary_repo
    note_repo = MagicMock() if note_repo is _SENTINEL else note_repo
    rag = MagicMock() if rag is _SENTINEL else rag

    def _getter(value):
        def _get():
            if isinstance(value, Exception):
                raise value
            return value
        return _get

    with patch("src.coordinator.startup.get_summary_repo", _getter(summary_repo)), \
         patch("src.coordinator.startup.get_session_note_repo", _getter(note_repo)), \
         patch("src.coordinator.startup.get_episodic_memory_rag", _getter(rag)):
        yield summary_repo, note_repo, rag


class TestClearSessionMessages:
    def test_clears_messages_and_emotional_state(self):
        repos = _make_repos()
        session_repo, message_repo, emo_repo = repos
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos), \
             _derived_stores():
            resp = client.delete("/sessions/sess-1/messages")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        message_repo.delete_messages_by_session.assert_called_once_with("sess-1")
        emo_repo.delete.assert_called_once_with("sess-1")

    def test_clears_summaries_note_and_vector_index(self):
        """The stores derived FROM the messages must go too, or a reset leaves
        the model reading a conversation the user believes was wiped."""
        repos = _make_repos()
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos), \
             _derived_stores() as (summary_repo, note_repo, rag):
            resp = client.delete("/sessions/sess-1/messages")
        assert resp.status_code == 200
        summary_repo.delete_summaries_by_session.assert_called_once_with("sess-1")
        note_repo.clear_note.assert_called_once_with("sess-1")
        rag.clear_session.assert_called_once_with("sess-1")
        assert resp.json()["cleared"] == {
            "summaries": True,
            "note": True,
            "vector_index": True,
        }

    def test_missing_rag_does_not_fail_the_reset(self):
        repos = _make_repos()
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos), \
             _derived_stores(rag=None):
            resp = client.delete("/sessions/sess-1/messages")
        assert resp.status_code == 200
        assert resp.json()["cleared"]["vector_index"] is False

    def test_uninitialized_repo_does_not_fail_the_reset(self):
        repos = _make_repos()
        boom = RuntimeError("SummaryRepository not initialized")
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos), \
             _derived_stores(summary_repo=boom) as (_, note_repo, rag):
            resp = client.delete("/sessions/sess-1/messages")
        assert resp.status_code == 200
        assert resp.json()["cleared"]["summaries"] is False
        # the other stores are still cleared — one dead subsystem must not
        # abort the whole reset
        note_repo.clear_note.assert_called_once_with("sess-1")
        rag.clear_session.assert_called_once_with("sess-1")

    def test_reset_is_idempotent(self):
        repos = _make_repos()
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos), \
             _derived_stores() as (summary_repo, note_repo, rag):
            first = client.delete("/sessions/sess-1/messages")
            second = client.delete("/sessions/sess-1/messages")
        assert first.status_code == second.status_code == 200
        assert first.json()["cleared"] == second.json()["cleared"]
        assert summary_repo.delete_summaries_by_session.call_count == 2
        assert rag.clear_session.call_count == 2

    def test_session_not_found_returns_404(self):
        repos = _make_repos(session_exists=False)
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos):
            resp = client.delete("/sessions/bad/messages")
        assert resp.status_code == 404


# ─── GET /sessions/{session_id}/export ───────────────────────────────────────

class TestExportSession:
    def test_exports_session_with_persona_info(self):
        fake_card = {"key": "eeva", "display_name": "E.E.V.A.", "style": "analytical"}
        with patch("src.coordinator.routes.sessions._get_repos", return_value=_make_repos()), \
             patch("src.coordinator.routes.sessions.get_persona_card", return_value=fake_card):
            resp = client.get("/sessions/sess-1/export")
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == "1.0"
        assert body["persona"]["key"] == "eeva"

    def test_persona_not_found_returns_400(self):
        with patch("src.coordinator.routes.sessions._get_repos", return_value=_make_repos()), \
             patch("src.coordinator.routes.sessions.get_persona_card", return_value=None):
            resp = client.get("/sessions/sess-1/export")
        assert resp.status_code == 400

    def test_session_not_found_returns_404(self):
        with patch("src.coordinator.routes.sessions._get_repos", return_value=_make_repos(session_obj=None)):
            resp = client.get("/sessions/nonexistent/export")
        assert resp.status_code == 404


# ─── POST /sessions/import ───────────────────────────────────────────────────

class TestImportSession:
    _VALID_IMPORT = {
        "data": {
            "version": "1.0",
            "exported_at": "2026-01-01T00:00:00",
            "app_version": "1.0.0",
            "persona": {"key": "eeva", "display_name": "E.E.V.A.", "style": "analytical"},
            "session": {"id": "old-id", "title": "Old Chat", "created_at": "2026-01-01T00:00:00"},
            "messages": [{"role": "user", "content": "hi", "timestamp": "2026-01-01T00:00:00"}],
        },
        "create_new_session": True,
    }

    def test_import_success(self):
        fake_card = {"key": "eeva", "display_name": "E.E.V.A."}
        with patch("src.coordinator.routes.sessions._get_repos", return_value=_make_repos()), \
             patch("src.coordinator.routes.sessions.get_persona_card", return_value=fake_card):
            resp = client.post("/sessions/import", json=self._VALID_IMPORT)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "session_id" in body

    def test_unknown_persona_returns_400(self):
        with patch("src.coordinator.routes.sessions._get_repos", return_value=_make_repos()), \
             patch("src.coordinator.routes.sessions.get_persona_card", return_value=None):
            resp = client.post("/sessions/import", json=self._VALID_IMPORT)
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower()

    def test_missing_required_fields_returns_422(self):
        resp = client.post("/sessions/import", json={"data": {}, "create_new_session": True})
        assert resp.status_code == 422


# ─── GET /sessions/{session_id}/emotional-state ──────────────────────────────

class TestGetEmotionalState:
    def test_returns_emotional_state(self):
        with patch("src.coordinator.routes.sessions._get_repos", return_value=_make_repos()):
            resp = client.get("/sessions/sess-1/emotional-state")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "sess-1"
        assert "trust_level" in body
        assert "rapport" in body
        assert "current_mood" in body

    def test_session_not_found_returns_404(self):
        repos = _make_repos(session_exists=False)
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos):
            resp = client.get("/sessions/bad/emotional-state")
        assert resp.status_code == 404


class TestGreetWithSessionPersistence:
    """POST /sessions/{id}/greet must persist a multi-message greeting without 500.

    Regression: a greeting that split into message_flow=='multi' (answer is a LIST)
    was passed straight to AppendMessageBody(content=list) -> ValidationError -> 500.
    """

    def _greet(self, answer, flow):
        repos = _make_repos(persona_key="gwen")
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos), \
             patch("src.coordinator.routes.chat.greet",
                   return_value={"answer": answer, "message_flow": flow, "message_count": 1}), \
             patch("src.coordinator.routes.sessions.add_message") as add_msg:
            resp = client.post("/sessions/sess-1/greet", json={})
        return resp, add_msg

    def test_multi_message_greeting_persists_without_500(self):
        resp, add_msg = self._greet(["Hey Daddy", "I missed you"], "multi")
        assert resp.status_code == 200
        # one add_message per part, each with a STRING content (never a list)
        assert add_msg.call_count == 2
        for call in add_msg.call_args_list:
            body = call.args[1]
            assert isinstance(body.content, str)
        # parts share a multi_message_id
        ids = {c.args[1].multi_message_id for c in add_msg.call_args_list}
        assert len(ids) == 1 and next(iter(ids)) is not None

    def test_single_message_greeting_persists(self):
        resp, add_msg = self._greet("Hey Daddy", "single")
        assert resp.status_code == 200
        assert add_msg.call_count == 1
        assert isinstance(add_msg.call_args_list[0].args[1].content, str)

    def test_greet_unknown_session_404(self):
        repos = _make_repos(session_exists=False)
        with patch("src.coordinator.routes.sessions._get_repos", return_value=repos):
            resp = client.post("/sessions/bad/greet", json={})
        assert resp.status_code == 404
