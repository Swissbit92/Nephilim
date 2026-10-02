"""SQLite-backed mapping of (telegram chat_id, persona_key) -> nephilim session_id.

Stdlib sqlite3 only — no new dependency. The table is keyed on
(chat_id, persona_key) from day one so a future per-chat persona switch is
purely additive; today each chat has exactly one persona so it is effectively
one row per chat.

The DB is a tiny local operational file (data/sessions.sqlite3), not user data
in the privacy sense — it holds only integer chat ids, persona keys, and opaque
session UUIDs, never message content.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    chat_id     INTEGER NOT NULL,
    persona_key TEXT    NOT NULL,
    session_id  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    PRIMARY KEY (chat_id, persona_key)
);

-- The REVERSE direction: session_id -> chat. A generated image arrives from
-- the coordinator carrying only the session it belongs to, so delivery needs
-- to find the chat. Deliberately NOT unique: the schema has never guaranteed
-- one chat per session, and a UNIQUE index would turn a duplicate into a
-- failed insert on the chat path, which matters far more than this lookup.
-- _SCHEMA runs through executescript on every open, so this appears on the
-- next boot with no migration.
CREATE INDEX IF NOT EXISTS idx_chat_sessions_session ON chat_sessions (session_id);

-- Documents we sent, so /reset can try to remove them from the chat.
-- sent_at is what decides deletability: Telegram only lets a bot delete a
-- message under 48h old, and the check is against the message date.
CREATE TABLE IF NOT EXISTS sent_media (
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    sent_at    TEXT    NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_sent_media_chat ON sent_media (chat_id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SessionStore:
    """Persistent chat_id+persona -> session_id map.

    A module-level lock serialises writes; sqlite is opened with
    check_same_thread=False so the single connection is safe across PTB's
    handler threads.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def get(self, chat_id: int, persona_key: str) -> str | None:
        """Return the stored session_id for this chat+persona, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id FROM chat_sessions WHERE chat_id = ? AND persona_key = ?",
                (chat_id, persona_key),
            ).fetchone()
        return row["session_id"] if row else None

    def chat_for_session(self, session_id: str) -> tuple[int, str] | None:
        """Find the chat a session belongs to. (chat_id, persona_key) or None.

        The direction notifications need: a finished image knows its session
        and nothing else, and the delivery has to reach a person.

        Returns the FIRST match if a session were somehow bound to two chats.
        The schema does not forbid that — `set()` is only ever called with a
        freshly minted id, so it is 1:1 in practice — but silently picking one
        is the right failure here: sending the image to one of two chats beats
        sending it to neither, and the alternative would be an exception on a
        background poll loop.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT chat_id, persona_key FROM chat_sessions "
                "WHERE session_id = ? ORDER BY updated_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return (row["chat_id"], row["persona_key"]) if row else None

    def set(self, chat_id: int, persona_key: str, session_id: str) -> None:
        """Insert or replace the mapping for this chat+persona."""
        now = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO chat_sessions (chat_id, persona_key, session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, persona_key) DO UPDATE SET
                    session_id = excluded.session_id,
                    updated_at = excluded.updated_at
                """,
                (chat_id, persona_key, session_id, now, now),
            )
            self._conn.commit()

    def delete(self, chat_id: int, persona_key: str) -> None:
        """Remove the mapping (used when a stored session is gone from the backend)."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM chat_sessions WHERE chat_id = ? AND persona_key = ?",
                (chat_id, persona_key),
            )
            self._conn.commit()

    # ---------- sent media (phase 3) ----------

    def record_media(self, chat_id: int, message_id: int) -> None:
        """Remember a document we sent, so /reset can try to remove it."""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO sent_media (chat_id, message_id, sent_at) "
                "VALUES (?, ?, ?)",
                (chat_id, message_id, _now()),
            )
            self._conn.commit()

    def media_for_chat(self, chat_id: int) -> list[tuple[int, str]]:
        """Return [(message_id, sent_at_iso)] for a chat, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT message_id, sent_at FROM sent_media WHERE chat_id = ? "
                "ORDER BY sent_at",
                (chat_id,),
            ).fetchall()
        return [(int(r[0]), str(r[1])) for r in rows]

    def forget_media(self, chat_id: int) -> int:
        """Drop our record of a chat's media. Returns rows removed.

        Called after a reset regardless of whether Telegram accepted the
        deletes: a message we could not remove is one we will never be able to
        remove (the 48h window only closes further), so keeping the row would
        make every later reset retry a guaranteed failure.
        """
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM sent_media WHERE chat_id = ?", (chat_id,)
            )
            self._conn.commit()
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
