# src/coordinator/repositories/media_repository.py
"""Per-session media directory allocation (Telegram image transport, phase 1).

One row per session, mapping it to the opaque directory its generated images
live under. The directory name is server-generated lowercase hex and is NEVER
derived from the session id — see the ``6media_dirs`` migration for why that is
a live concern rather than a precaution.

``_ensure_table`` self-creates (dual-covered with the alembic ``6media_dirs``
revision, matching SessionNoteRepository) so it works in Docker envs that skip
alembic and in unit tests. The FK to ``chat_sessions`` is declared only in the
migration, not here: the self-create path runs against test DBs where
``chat_sessions`` may not exist. The UNIQUE on ``media_dir`` IS kept in both —
a collision must be a hard error, never a silent merge of two sessions' images
into one directory.
"""

from __future__ import annotations

import logging
import uuid

from .base_repository import BaseRepository, utc_now_iso

logger = logging.getLogger(__name__)


class MediaRepository(BaseRepository):
    """Allocates and looks up the media directory for a session."""

    def __init__(self, db_path: str | None = None):
        super().__init__(db_path)
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create the session_media_dirs table if absent (dual-covered with alembic)."""
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS session_media_dirs (
                session_id TEXT PRIMARY KEY,
                media_dir  TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )

    def get_media_dir(self, session_id: str) -> str | None:
        """Return the session's media directory name, or None if never allocated."""
        row = self._fetchone_dict(
            "SELECT media_dir FROM session_media_dirs WHERE session_id = ?",
            (session_id,),
        )
        return row["media_dir"] if row else None

    def get_or_create_media_dir(self, session_id: str) -> str:
        """Return the session's media directory, allocating one on first call.

        Idempotent: repeated calls return the same name. Each step is its own
        top-level helper call — ``BaseRepository._lock`` is a plain
        ``threading.Lock`` and is NOT reentrant, so nesting would deadlock.

        The insert is ``ON CONFLICT DO NOTHING`` followed by a re-read rather
        than a check-then-insert, so a concurrent caller cannot produce two
        directories for one session: the loser's insert is discarded and it
        reads the winner's name.
        """
        existing = self.get_media_dir(session_id)
        if existing is not None:
            return existing

        candidate = uuid.uuid4().hex  # lowercase hex by construction
        self._execute(
            """
            INSERT INTO session_media_dirs (session_id, media_dir, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO NOTHING
            """,
            (session_id, candidate, utc_now_iso()),
        )

        stored = self.get_media_dir(session_id)
        if stored is None:
            # Unreachable in practice: the insert either landed or lost a race to
            # a row that is now readable. Fail loudly rather than returning a
            # directory nothing recorded — an unrecorded directory is unreachable
            # by a later reset.
            raise RuntimeError(
                f"media_dir allocation failed for session {session_id[:8]}"
            )
        if stored != candidate:
            logger.debug(
                "[Media] lost media_dir allocation race for session %s — using stored",
                session_id[:8],
            )
        return stored

    def delete(self, session_id: str) -> bool:
        """Forget the session's media directory. Returns True if a row was removed.

        Removes only the mapping. Deleting the files on disk is the caller's job
        (phase 3), and the order matters: read the directory name, delete the
        files, THEN delete this row — doing it the other way round orphans a
        directory nothing can name.
        """
        cur = self._execute(
            "DELETE FROM session_media_dirs WHERE session_id = ?", (session_id,)
        )
        return cur.rowcount > 0
