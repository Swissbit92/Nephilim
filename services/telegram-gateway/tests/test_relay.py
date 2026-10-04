"""Tests for relay orchestration: session lifecycle, 404 recreate, extraction."""

from __future__ import annotations

import pytest

from eeva_telegram import relay
from eeva_telegram.nephilim_client import NephilimSessionNotFoundError
from eeva_telegram.session_store import SessionStore


class FakeClient:
    """Minimal async stand-in for NephilimClient with scripted behaviour."""

    def __init__(self):
        self.created = []
        self.chat_calls = []
        self.cleared = []
        self.greeted = []
        self._next_session = 0
        # If set, chat() raises SessionNotFound for these session_ids (once each).
        self.not_found_sessions: set[str] = set()
        self.chat_response = {"answer": "hi there", "message_flow": "single"}
        self.greet_response = {"answer": "Welcome."}

    async def create_session(self, persona_key: str, title: str = "Telegram") -> str:
        self._next_session += 1
        sid = f"sess-{self._next_session}"
        self.created.append((persona_key, sid))
        return sid

    async def chat(self, session_id: str, message: str):
        if session_id in self.not_found_sessions:
            self.not_found_sessions.discard(session_id)
            raise NephilimSessionNotFoundError(session_id)
        self.chat_calls.append((session_id, message))
        return self.chat_response

    async def greet(self, session_id: str):
        self.greeted.append(session_id)
        return self.greet_response

    async def clear_messages(self, session_id: str) -> None:
        if session_id in self.not_found_sessions:
            self.not_found_sessions.discard(session_id)
            raise NephilimSessionNotFoundError(session_id)
        self.cleared.append(session_id)


# ─── extract_messages ────────────────────────────────────────────────────────


def test_extract_single_string():
    assert relay.extract_messages({"answer": "hello", "message_flow": "single"}) == ["hello"]


def test_extract_multi_list():
    resp = {"answer": ["one", "two", "three"], "message_flow": "multi"}
    assert relay.extract_messages(resp) == ["one", "two", "three"]


def test_extract_drops_empty_pieces():
    resp = {"answer": ["one", "  ", "", "two"], "message_flow": "multi"}
    assert relay.extract_messages(resp) == ["one", "two"]


def test_extract_none_answer_returns_empty():
    assert relay.extract_messages({"answer": None}) == []
    assert relay.extract_messages({}) == []


# ─── ensure_session ──────────────────────────────────────────────────────────


async def test_ensure_session_creates_when_absent(store: SessionStore):
    client = FakeClient()
    sid, created = await relay.ensure_session(client, store, 111, "nephilim_eeva")
    assert created is True
    assert sid == "sess-1"
    assert store.get(111, "nephilim_eeva") == "sess-1"


async def test_ensure_session_reuses_when_present(store: SessionStore):
    client = FakeClient()
    store.set(111, "nephilim_eeva", "sess-existing")
    sid, created = await relay.ensure_session(client, store, 111, "nephilim_eeva")
    assert created is False
    assert sid == "sess-existing"
    assert client.created == []  # no new session minted


# ─── handle_user_message ─────────────────────────────────────────────────────


async def test_handle_user_message_happy(store: SessionStore):
    client = FakeClient()
    msgs = await relay.handle_user_message(client, store, 111, "nephilim_eeva", "hi")
    assert msgs.messages == ["hi there"]
    assert msgs.media == []
    assert client.chat_calls == [("sess-1", "hi")]


async def test_handle_user_message_recreates_on_stale_404(store: SessionStore):
    client = FakeClient()
    # Pre-seed a stale mapping; the backend will 404 on it once.
    store.set(111, "nephilim_eeva", "sess-stale")
    client.not_found_sessions.add("sess-stale")

    msgs = await relay.handle_user_message(client, store, 111, "nephilim_eeva", "hi")

    assert msgs.messages == ["hi there"]
    assert msgs.media == []
    # A fresh session was created and stored, replacing the stale one.
    assert store.get(111, "nephilim_eeva") == "sess-1"
    assert client.chat_calls == [("sess-1", "hi")]


async def test_handle_user_message_second_404_propagates(store: SessionStore):
    client = FakeClient()
    store.set(111, "nephilim_eeva", "sess-stale")
    # Make BOTH the stale session and the recreated one 404.
    client.not_found_sessions.add("sess-stale")

    async def always_404(session_id, message):
        raise NephilimSessionNotFoundError(session_id)

    client.chat = always_404  # type: ignore[assignment]
    with pytest.raises(NephilimSessionNotFoundError):
        await relay.handle_user_message(client, store, 111, "nephilim_eeva", "hi")


# ─── start_session ───────────────────────────────────────────────────────────


async def test_start_session_greets_when_new(store: SessionStore):
    client = FakeClient()
    msgs, greeted = await relay.start_session(client, store, 111, "nephilim_eeva")
    assert greeted is True
    assert msgs == ["Welcome."]
    assert client.greeted == ["sess-1"]


async def test_start_session_no_regreet_when_existing(store: SessionStore):
    client = FakeClient()
    store.set(111, "nephilim_eeva", "sess-existing")
    msgs, greeted = await relay.start_session(client, store, 111, "nephilim_eeva")
    assert greeted is False
    assert msgs == []
    assert client.greeted == []  # did not re-greet


# ─── reset_session ───────────────────────────────────────────────────────────


async def test_reset_clears_existing_session(store: SessionStore):
    client = FakeClient()
    store.set(111, "nephilim_eeva", "sess-existing")
    await relay.reset_session(client, store, 111, "nephilim_eeva")
    assert client.cleared == ["sess-existing"]


async def test_reset_creates_then_clears_when_absent(store: SessionStore):
    client = FakeClient()
    await relay.reset_session(client, store, 111, "nephilim_eeva")
    # ensure_session created sess-1, then it was cleared.
    assert client.cleared == ["sess-1"]


async def test_reset_recreates_on_stale_404(store: SessionStore):
    client = FakeClient()
    store.set(111, "nephilim_eeva", "sess-stale")
    client.not_found_sessions.add("sess-stale")
    await relay.reset_session(client, store, 111, "nephilim_eeva")
    # Stale cleared attempt 404'd -> recreated sess-1 -> cleared it.
    assert client.cleared == ["sess-1"]
    assert store.get(111, "nephilim_eeva") == "sess-1"


# ---------- extract_media (M4) ----------
# Defensive at every level: the text reply is the important part of a turn and
# a malformed media item must never be able to swallow it.


def _body(media):
    """A chat-shaped response, as the coordinator actually returns it."""
    return {
        "answer": "here",
        "message_flow": "single",
        "message_count": 1,
        "used_search": False,
        "metadata": {"source_type": "llm", "media": media},
        "rewritten": False,
    }


def test_extract_media_reads_a_well_formed_item():
    item = {
        "media_id": "abc",
        "path": "/abs/img/a.png",
        "filename": "a.png",
        "caption": "look",
        "protect_content": True,
    }
    (ref,) = relay.extract_media(_body([item]))
    assert (ref.path, ref.filename, ref.caption, ref.protect_content) == (
        "/abs/img/a.png",
        "a.png",
        "look",
        True,
    )


def test_extract_media_preserves_order():
    body = _body([{"path": "/a.png"}, {"path": "/b.png"}])
    assert [m.path for m in relay.extract_media(body)] == ["/a.png", "/b.png"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"answer": "x"},
        {"metadata": None},
        {"metadata": "nope"},
        {"metadata": {}},
        {"metadata": {"media": None}},
        {"metadata": {"media": "nope"}},
        {"metadata": {"media": {}}},
    ],
)
def test_extract_media_tolerates_a_missing_or_malformed_envelope(body):
    assert relay.extract_media(body) == []


@pytest.mark.parametrize(
    "item",
    ["not-a-dict", None, 42, {}, {"path": ""}, {"path": "   "}, {"path": 99}],
)
def test_extract_media_skips_an_unusable_item(item):
    assert relay.extract_media(_body([item])) == []


def test_extract_media_skips_only_the_bad_item():
    """One malformed entry must not cost the good one."""
    body = _body([{"nope": 1}, {"path": "/good.png"}])
    assert [m.path for m in relay.extract_media(body)] == ["/good.png"]


def test_extract_media_defaults_a_missing_filename():
    (ref,) = relay.extract_media(_body([{"path": "/a.png"}]))
    assert ref.filename == "image.png"


def test_extract_media_defaults_protect_content_on():
    """If the coordinator omits the flag, the privacy-preserving choice is the
    one that must happen by accident."""
    (ref,) = relay.extract_media(_body([{"path": "/a.png"}]))
    assert ref.protect_content is True


def test_extract_media_honours_an_explicit_protect_content_false():
    (ref,) = relay.extract_media(_body([{"path": "/a.png", "protect_content": False}]))
    assert ref.protect_content is False


def test_extract_media_treats_an_empty_caption_as_none():
    (ref,) = relay.extract_media(_body([{"path": "/a.png", "caption": ""}]))
    assert ref.caption is None
