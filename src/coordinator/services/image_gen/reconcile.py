# src/coordinator/services/image_gen/reconcile.py
"""Deciding what happened to a job while the coordinator was not running.

A generation is detached on purpose: `start_new_session=True` keeps it alive
across a launchd restart, so after a restart there can be a row saying RUNNING
whose process we are not the parent of and can never ``waitpid``.

**The job lock answers this, not a timeout.** Distributed queues expire a lease
and assume death because they have nothing better; on one machine with a real
subprocess we can simply ask. ``supervisor.probe()`` reads the ``flock`` the
child inherited and reports RUNNING / FINISHED(rc) / DIED as fact. A job that
is still generating is left alone and re-adopted; only one that is genuinely
gone is failed.

That ordering matters because the two errors are not symmetric. Failing a live
job throws away five minutes of GPU work and tells the user it broke while it
is still running. Leaving a dead job marked RUNNING merely delays an error.
So: only ever fail on positive evidence of death.

The heartbeat is the fallback for the case the lock cannot cover — a job
directory deleted underneath us, or a row whose ``job_dir`` was never written
because the crash landed between the claim and the spawn. It is second,
because it is a guess where the lock is a measurement.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ...repositories.image_job_repository import JobStatus
from . import supervisor as sv
from .verify import walk_png

logger = logging.getLogger(__name__)

#: A RUNNING row with no usable job_dir and no heartbeat this recent is
#: presumed dead. Generous against the measured 331 s runtime: the heartbeat
#: ticks every 15 s, so 20 minutes of silence is ~80 missed beats.
STALE_AFTER = timedelta(minutes=20)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=UTC)
    except ValueError:
        return None


def reconcile_job(repo, job, *, now: datetime | None = None) -> str:
    """Settle one RUNNING row. Returns what was decided, for logging/tests.

    One of: ``adopted`` (still generating), ``succeeded``, ``failed``,
    ``requeued`` (never actually started), or ``left`` (not ours to judge).
    """
    now = now or datetime.now(UTC)

    if job.status != JobStatus.RUNNING:
        return "left"

    # A crash between the claim and the spawn: the row is RUNNING but no
    # process was ever recorded. Nothing ran, so this is safe to requeue —
    # requeue rather than fail, because the user's request is still valid.
    if not job.job_dir:
        repo.requeue(job.id)
        logger.info("[ImageGen] %s requeued — it never started", job.id)
        return "requeued"

    job_dir = Path(job.job_dir)
    if not job_dir.exists():
        repo.finish(job.id, status=JobStatus.FAILED,
                    error="the job directory is gone")
        return "failed"

    probe = sv.probe(job_dir)

    if probe.state is sv.State.RUNNING:
        # Still generating, detached, from before the restart. Leave it be —
        # the worker re-adopts it rather than starting a second one.
        logger.info("[ImageGen] %s is still running (pid %s) — adopting", job.id, job.pid)
        return "adopted"

    if probe.state is sv.State.FINISHED and probe.returncode == 0:
        out = job_dir / sv.OUTPUT_NAME
        verdict = walk_png(out)
        if verdict.ok and (job_dir / sv.VERIFIED_NAME).exists():
            repo.finish(job.id, status=JobStatus.SUCCEEDED, media_path=str(out))
            logger.info("[ImageGen] %s finished while we were down — succeeded", job.id)
            return "succeeded"
        repo.finish(job.id, status=JobStatus.FAILED,
                    error=f"exited 0 but produced no usable image: {verdict.reason}")
        return "failed"

    if probe.state is sv.State.FINISHED:
        repo.finish(job.id, status=JobStatus.FAILED,
                    error=f"the generator exited {probe.returncode}")
        return "failed"

    # DIED: the lock is free and no exit code was recorded — SIGKILL, a panic,
    # or jetsam. Positive evidence of death, so failing is correct.
    repo.finish(job.id, status=JobStatus.FAILED,
                error="the generator died without recording an exit code")
    logger.warning("[ImageGen] %s died while we were down", job.id)
    return "failed"


def sweep(repo, *, now: datetime | None = None) -> dict[str, int]:
    """Settle every unfinished row. Run at startup and periodically.

    Returns a count per outcome so the caller can log one line instead of one
    per job, and so a test can assert on what happened rather than on a log.
    """
    now = now or datetime.now(UTC)
    counts: dict[str, int] = {}

    for job in repo.active():
        if job.status == JobStatus.QUEUED:
            # Queued rows need no reconciliation: nothing started, and the
            # worker will pick them up. The only hazard would be a queued job
            # so old it is no longer wanted, which is a product decision, not
            # a correctness one.
            continue
        try:
            outcome = reconcile_job(repo, job, now=now)
        except Exception:
            logger.exception("[ImageGen] reconciling %s failed", job.id)
            outcome = "error"
        counts[outcome] = counts.get(outcome, 0) + 1

    # The heartbeat backstop, for rows the lock could not judge. Deliberately
    # applied only to rows reconcile_job ADOPTED: if the lock says a job is
    # running, it is running — a stale heartbeat then means the heartbeat
    # writer is stuck, not the generator. Logged, never acted on, because
    # killing a demonstrably live job on a secondary signal is the asymmetry
    # this module exists to avoid.
    for job in repo.active():
        if job.status != JobStatus.RUNNING:
            continue
        beat = _parse(job.heartbeat_at)
        if beat is not None and now - beat > STALE_AFTER:
            logger.warning(
                "[ImageGen] %s holds its lock but has not beaten since %s — "
                "the generator is alive and the heartbeat writer is not",
                job.id, job.heartbeat_at,
            )

    if counts:
        logger.info("[ImageGen] startup sweep: %s", counts)
    return counts
