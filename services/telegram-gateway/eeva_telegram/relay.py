"""Core relay orchestration: Telegram chat <-> nephilim session.

This module is deliberately free of any python-telegram-bot types so it can be
unit-tested with a mocked NephilimClient and a real (temp-file) SessionStore.
The PTB handler layer (handlers.py) is a thin adapter over these functions.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .nephilim_client import NephilimClient, NephilimSessionNotFoundError
from .session_store import SessionStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MediaRef:
    """One media item the coordinator offered, as the gateway sees it.

    Deliberately NOT the coordinator's MediaItem: the gateway keeps only what
    it needs to deliver, so a new coordinator field cannot silently become a
    gateway behaviour.
    """

    path: str
    filename: str
    caption: str | None = None
    protect_content: bool = True


@dataclass(frozen=True)
class RelayReply:
    """A persona turn: text to send, plus anything to attach."""

    messages: list[str] = field(default_factory=list)
    media: list[MediaRef] = field(default_factory=list)


def extract_messages(response: dict[str, Any]) -> list[str]:
    """Turn a nephilim chat/greet response into an ordered list of message strings.

    - message_flow == "multi" with a list answer -> each element is its own message
    - a list answer (any flow) -> non-empty elements, in order
    - a string answer -> single message
    Empty/whitespace-only pieces are dropped. Returns [] if nothing usable.
    """
    answer = response.get("answer")
    pieces: list[Any]
    if isinstance(answer, list):
        pieces = answer
    elif answer is None:
        pieces = []
    else:
        pieces = [answer]

    out: list[str] = []
    for piece in pieces:
        text = str(piece).strip()
        if text:
            out.append(text)
    return out


def extract_media(response: dict[str, Any]) -> list[MediaRef]:
    """Pull deliverable media out of a chat-shaped response.

    Defensive at every level and never raises: ``metadata`` may be absent or not
    a dict, ``media`` may be absent or not a list, and an item may be missing
    the one field that matters. A malformed item is skipped with a log line
    rather than losing the whole turn — the text reply is the important part and
    an image must never be able to swallow it.
    """
    metadata = response.get("metadata")
    if not isinstance(metadata, dict):
        return []
    raw_items = metadata.get("media")
    if not isinstance(raw_items, list):
        return []

    out: list[MediaRef] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            logger.warning("[Media] skipping media[%d]: not an object", index)
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            logger.warning("[Media] skipping media[%d]: no usable path", index)
            continue
        filename = item.get("filename")
        if not isinstance(filename, str) or not filename.strip():
            filename = "image.png"
        caption = item.get("caption")
        out.append(
            MediaRef(
                path=path,
                filename=filename,
                caption=caption if isinstance(caption, str) and caption else None,
                # Default ON: if the coordinator omits the flag, the privacy-
                # preserving choice is the one that must happen by accident.
                protect_content=item.get("protect_content") is not False,
            )
        )
    return out


async def ensure_session(
    client: NephilimClient,
    store: SessionStore,
    chat_id: int,
    persona_key: str,
) -> tuple[str, bool]:
    """Return (session_id, created). Reuses a stored session or creates a new one.

    'created' is True only when a brand-new session was minted this call.
    """
    existing = store.get(chat_id, persona_key)
    if existing:
        return existing, False
    session_id = await client.create_session(persona_key)
    store.set(chat_id, persona_key, session_id)
    return session_id, True


async def _with_session_recreate(
    client: NephilimClient,
    store: SessionStore,
    chat_id: int,
    persona_key: str,
    action: Callable[[str], Awaitable[Any]],
) -> Any:
    """Run action(session_id); on 404 (stale session), recreate once and retry.

    Keeps the gateway resilient when a session row is deleted out-of-band on the
    backend (e.g. a DB reset). One retry only — a second 404 propagates.
    """
    session_id, _ = await ensure_session(client, store, chat_id, persona_key)
    try:
        return await action(session_id)
    except NephilimSessionNotFoundError:
        store.delete(chat_id, persona_key)
        new_id = await client.create_session(persona_key)
        store.set(chat_id, persona_key, new_id)
        return await action(new_id)


async def handle_user_message(
    client: NephilimClient,
    store: SessionStore,
    chat_id: int,
    persona_key: str,
    text: str,
) -> RelayReply:
    """Relay one user message; return the persona's reply text and any media."""
    response = await _with_session_recreate(client, store, chat_id, persona_key, lambda sid: client.chat(sid, text))
    return RelayReply(messages=extract_messages(response), media=extract_media(response))


async def start_session(
    client: NephilimClient,
    store: SessionStore,
    chat_id: int,
    persona_key: str,
) -> tuple[list[str], bool]:
    """Handle /start.

    If no session exists yet, create one and greet in-character -> (greeting, True).
    If a session already exists, don't re-greet (avoids duplicate greetings on
    every /start or restart) -> ([], False); caller sends a short ack.
    """
    session_id, created = await ensure_session(client, store, chat_id, persona_key)
    if not created:
        return [], False
    greeting = await client.greet(session_id)
    return extract_messages(greeting), True


async def reset_session(
    client: NephilimClient,
    store: SessionStore,
    chat_id: int,
    persona_key: str,
) -> None:
    """Handle /reset: true history deletion on the existing session.

    Clears all messages + emotional state via the backend, preserving the
    session (and thus relationship progression). If the session is gone
    server-side, recreate a clean one.
    """
    await _with_session_recreate(client, store, chat_id, persona_key, lambda sid: client.clear_messages(sid))


# ── ADR-011 conversation-control verbs (thin relay over the session API) ─────


async def regenerate_reply(client, store, chat_id, persona_key) -> list[str]:
    """/regen — reroll the last reply; returns the new persona message(s)."""
    resp = await _with_session_recreate(client, store, chat_id, persona_key, lambda sid: client.regenerate(sid))
    return extract_messages(resp)


async def continue_reply(client, store, chat_id, persona_key) -> list[str]:
    """/continue — extend the last reply; returns the continuation message(s)."""
    resp = await _with_session_recreate(client, store, chat_id, persona_key, lambda sid: client.continue_reply(sid))
    return extract_messages(resp)


async def undo_last(client, store, chat_id, persona_key) -> None:
    """/undo — delete the last exchange."""
    await _with_session_recreate(client, store, chat_id, persona_key, lambda sid: client.undo(sid))


async def narrate(client, store, chat_id, persona_key, text: str) -> list[str]:
    """/sys — inject a scene beat; returns the persona's in-world reaction."""
    resp = await _with_session_recreate(client, store, chat_id, persona_key, lambda sid: client.narrate(sid, text))
    return extract_messages(resp)


async def impersonate(client, store, chat_id, persona_key, hint: str | None) -> str:
    """/impersonate — draft the user's next line; returns the draft text."""
    resp = await _with_session_recreate(client, store, chat_id, persona_key, lambda sid: client.impersonate(sid, hint))
    return str(resp.get("draft", "")).strip()


async def whoami(client, store, chat_id, persona_key) -> dict[str, Any]:
    """/whoami — lean session/persona metadata."""
    return await _with_session_recreate(client, store, chat_id, persona_key, lambda sid: client.get_session_meta(sid))


async def set_note(client, store, chat_id, persona_key, note: str) -> None:
    """/note <text> — set the author's note."""
    await _with_session_recreate(client, store, chat_id, persona_key, lambda sid: client.set_note(sid, note))


async def get_note(client, store, chat_id, persona_key) -> str | None:
    """/note — show the author's note."""
    resp = await _with_session_recreate(client, store, chat_id, persona_key, lambda sid: client.get_note(sid))
    return resp.get("note")


async def clear_note(client, store, chat_id, persona_key) -> None:
    """/note clear — remove the author's note."""
    await _with_session_recreate(client, store, chat_id, persona_key, lambda sid: client.clear_note(sid))


async def request_fixture_media(
    client: NephilimClient,
    store: SessionStore,
    chat_id: int,
    persona_key: str,
) -> RelayReply:
    """Ask the coordinator for a probe image (dev only).

    Goes through the SAME extractors as a real turn. If this parsed the fixture
    response with bespoke code, the transport proof would be a proof about
    different code than production runs.
    """
    response = await _with_session_recreate(
        client, store, chat_id, persona_key, lambda sid: client.request_fixture_image(sid)
    )
    return RelayReply(messages=extract_messages(response), media=extract_media(response))
