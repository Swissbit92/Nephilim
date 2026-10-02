# src/coordinator/repositories/image_job_repository.py
"""The durable record of an image generation.

A generation runs ~331 s as a detached subprocess that deliberately outlives
the coordinator. This table is the only thing connecting "a process is running"
to "someone asked for this, in this session, and is waiting". The supervisor
knows the former; nothing else knows the latter.

Three rules this module exists to enforce, each from a measured or sourced
failure rather than a preference:

1. **Claim atomically, in one statement.** ``claim_next`` is a single
   conditional ``UPDATE ... WHERE id = (SELECT ... LIMIT 1) AND status =
   'queued'``. SQLite allows exactly one writer, so two workers cannot both
   match the same row — the loser updates zero rows and gets None. A
   read-then-write would let both claim it.

2. **Never hold a transaction across the subprocess wait.** Claim and commit;
   run the 331 s job with no transaction open; open a new one to write the
   result. `busy_timeout` only helps the waiter — it cannot shrink the
   lock-holder's window, so a transaction held across the wait would block
   every other writer for five and a half minutes.

3. **Notification is claimed, not just marked.** ``claim_for_notify`` is also
   a conditional UPDATE (``WHERE notified_at IS NULL``), because the completion
   path and the reconciliation sweeper can both decide a job finished. Without
   the conditional, a restart mid-delivery sends the image twice.

``_ensure_table`` self-creates, dual-covered with the ``7image_jobs`` alembic
revision. That is mandatory here, not belt-and-braces: ``init_db()`` swallows
migration failures and loads ``alembic.ini`` by RELATIVE path, so wherever cwd
is not the repo root the migration silently does not run. The FK to
``chat_sessions`` is declared only in the migration, matching MediaRepository —
the self-create path runs against test DBs where that table may not exist.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .base_repository import BaseRepository, utc_now_iso

logger = logging.getLogger(__name__)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: Terminal states. A job in one of these is never picked up again.
TERMINAL = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})

_COLUMNS = (
    "id, session_id, persona_key, prompt, status, job_dir, media_path, "
    "pid, pgid, proc_start, error, created_at, started_at, finished_at, "
    "heartbeat_at, notified_at"
)


@dataclass(frozen=True)
class ImageJob:
    id: str
    session_id: str
    persona_key: str
    prompt: str
    status: str
    job_dir: str | None = None
    media_path: str | None = None
    pid: int | None = None
    pgid: int | None = None
    proc_start: float | None = None
    error: str | None = None
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    heartbeat_at: str | None = None
    notified_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ImageJob:
        return cls(**{k: row.get(k) for k in cls.__dataclass_fields__})

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL


class ImageJobRepository(BaseRepository):
    """Create, claim, finish and reconcile generation jobs."""

    def __init__(self, db_path: str | None = None):
        super().__init__(db_path)
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create image_jobs if absent (dual-covered with alembic 7image_jobs)."""
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS image_jobs (
                id           TEXT PRIMARY KEY,
                session_id   TEXT NOT NULL,
                persona_key  TEXT NOT NULL,
                prompt       TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'queued'
                             CHECK (status IN ('queued','running','succeeded',
                                               'failed','cancelled')),
                job_dir      TEXT,
                media_path   TEXT,
                pid          INTEGER,
                pgid         INTEGER,
                proc_start   REAL,
                error        TEXT,
                created_at   TEXT NOT NULL,
                started_at   TEXT,
                finished_at  TEXT,
                heartbeat_at TEXT,
                notified_at  TEXT
            )
            """
        )
        self._execute(
            "CREATE INDEX IF NOT EXISTS ix_image_jobs_status "
            "ON image_jobs (status)"
        )
        self._execute(
            "CREATE INDEX IF NOT EXISTS ix_image_jobs_pending_notify "
            "ON image_jobs (status, notified_at)"
        )

    # ---------- create / read ----------

    def create(self, *, session_id: str, persona_key: str, prompt: str) -> ImageJob:
        """Queue a job. The id is server-generated, never caller-supplied."""
        job_id = uuid.uuid4().hex
        self._execute(
            "INSERT INTO image_jobs (id, session_id, persona_key, prompt, "
            "status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, session_id, persona_key, prompt,
             JobStatus.QUEUED.value, utc_now_iso()),
        )
        job = self.get(job_id)
        if job is None:  # pragma: no cover - would mean the insert vanished
            raise RuntimeError(f"image job {job_id} missing immediately after insert")
        logger.info("[ImageJobs] queued %s for session %s", job_id, session_id)
        return job

    def get(self, job_id: str) -> ImageJob | None:
        row = self._fetchone_dict(
            f"SELECT {_COLUMNS} FROM image_jobs WHERE id = ?", (job_id,)
        )
        return ImageJob.from_row(row) if row else None

    def list_for_session(self, session_id: str, limit: int = 20) -> list[ImageJob]:
        rows = self._fetchall_list(
            f"SELECT {_COLUMNS} FROM image_jobs WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        )
        return [ImageJob.from_row(r) for r in rows]

    def active(self) -> list[ImageJob]:
        """Everything not yet finished — queued or running."""
        rows = self._fetchall_list(
            f"SELECT {_COLUMNS} FROM image_jobs WHERE status IN (?, ?) "
            "ORDER BY created_at",
            (JobStatus.QUEUED.value, JobStatus.RUNNING.value),
        )
        return [ImageJob.from_row(r) for r in rows]

    # ---------- the worker's transitions ----------

    def claim_next(self) -> ImageJob | None:
        """Atomically take the oldest queued job. None if there is none.

        One statement, deliberately. The ``AND status = 'queued'`` is not
        redundant with the subquery: between the subquery choosing a row and
        the UPDATE applying, another writer could have claimed it. SQLite's
        single-writer rule means only one of the two UPDATEs can commit, and
        the predicate is what makes the loser match zero rows rather than
        overwrite the winner.
        """
        now = utc_now_iso()
        cur = self._execute(
            "UPDATE image_jobs SET status = ?, started_at = ?, heartbeat_at = ? "
            "WHERE id = (SELECT id FROM image_jobs WHERE status = ? "
            "            ORDER BY created_at LIMIT 1) "
            "  AND status = ?",
            (JobStatus.RUNNING.value, now, now,
             JobStatus.QUEUED.value, JobStatus.QUEUED.value),
        )
        if cur.rowcount != 1:
            return None
        row = self._fetchone_dict(
            f"SELECT {_COLUMNS} FROM image_jobs WHERE status = ? "
            "AND started_at = ? ORDER BY created_at LIMIT 1",
            (JobStatus.RUNNING.value, now),
        )
        return ImageJob.from_row(row) if row else None

    def attach_process(
        self, job_id: str, *, job_dir: str, pid: int, pgid: int, proc_start: float
    ) -> None:
        """Record the subprocess identity, so a restart can find it again.

        ``proc_start`` travels with ``pgid`` because a PID can be recycled and
        signalling a recycled one would kill an unrelated process.
        """
        self._execute(
            "UPDATE image_jobs SET job_dir = ?, pid = ?, pgid = ?, "
            "proc_start = ?, heartbeat_at = ? WHERE id = ?",
            (job_dir, pid, pgid, proc_start, utc_now_iso(), job_id),
        )

    def heartbeat(self, job_id: str) -> None:
        """Backstop liveness for the case the job lock cannot answer."""
        self._execute(
            "UPDATE image_jobs SET heartbeat_at = ? WHERE id = ? AND status = ?",
            (utc_now_iso(), job_id, JobStatus.RUNNING.value),
        )

    def finish(
        self,
        job_id: str,
        *,
        status: JobStatus,
        media_path: str | None = None,
        error: str | None = None,
    ) -> None:
        """Write the terminal state. Refuses to un-finish a finished job.

        The ``status = 'running'`` predicate matters for the reconciliation
        sweeper: if the sweeper has already failed an orphan and the original
        worker then wakes up and tries to succeed it, the second write is
        dropped rather than resurrecting a job somebody was already told about.
        """
        if status not in TERMINAL:
            raise ValueError(f"{status} is not a terminal status")
        self._execute(
            "UPDATE image_jobs SET status = ?, media_path = ?, error = ?, "
            "finished_at = ? WHERE id = ? AND status = ?",
            (status.value, media_path, error, utc_now_iso(), job_id,
             JobStatus.RUNNING.value),
        )

    def requeue(self, job_id: str) -> bool:
        """Put an orphan back in the queue. True if it moved."""
        cur = self._execute(
            "UPDATE image_jobs SET status = ?, pid = NULL, pgid = NULL, "
            "proc_start = NULL, started_at = NULL, heartbeat_at = NULL "
            "WHERE id = ? AND status = ?",
            (JobStatus.QUEUED.value, job_id, JobStatus.RUNNING.value),
        )
        return cur.rowcount > 0

    # ---------- delivery ----------

    def pending_notification(self, limit: int = 10) -> list[ImageJob]:
        """Finished jobs nobody has been told about yet.

        Includes failures on purpose: a generation that died in silence is the
        outcome the user most needs to hear about, and the earlier version of
        this feature's whole problem was a silent backend failure rendering as
        success.
        """
        rows = self._fetchall_list(
            f"SELECT {_COLUMNS} FROM image_jobs WHERE notified_at IS NULL "
            "AND status IN (?, ?, ?) ORDER BY finished_at LIMIT ?",
            (JobStatus.SUCCEEDED.value, JobStatus.FAILED.value,
             JobStatus.CANCELLED.value, limit),
        )
        return [ImageJob.from_row(r) for r in rows]

    def claim_for_notify(self, job_id: str) -> bool:
        """Take responsibility for delivering this one. True if we got it.

        Conditional on ``notified_at IS NULL``, so two deliverers racing — the
        normal completion path and the reconciliation sweeper, say — result in
        exactly one send. Call this BEFORE sending, not after: a crash between
        send and mark would double-deliver, while a crash between mark and send
        loses one notification, which is the cheaper failure.
        """
        cur = self._execute(
            "UPDATE image_jobs SET notified_at = ? "
            "WHERE id = ? AND notified_at IS NULL",
            (utc_now_iso(), job_id),
        )
        return cur.rowcount > 0

    def unclaim_notify(self, job_id: str) -> None:
        """Hand a notification back after a failed send, so it is retried."""
        self._execute(
            "UPDATE image_jobs SET notified_at = NULL WHERE id = ?", (job_id,)
        )
