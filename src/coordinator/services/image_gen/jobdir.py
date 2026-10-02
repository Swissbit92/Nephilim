# src/coordinator/services/image_gen/jobdir.py
"""Where a generation job runs.

A FRESH directory per job, and that is load-bearing rather than tidy: mflux's
``resolve_output_path`` silently renames ``out.png`` to ``out_1.png`` when the
target already exists, so a reused directory means the path the supervisor
waits for is not the path the generator wrote. An empty directory makes the
output path deterministic.

Paths are built through ``media_storage``'s containment guard, so a job id can
never escape the media root even though job ids are ours and not user input —
the guard costs nothing and the one place it was skipped is where the earlier
symlink escape got in.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..media_storage import MediaStorageError, media_root

_JOB_ID_RE = re.compile(r"[0-9a-f]{32}")


def job_root() -> Path:
    return media_root() / "jobs"


def job_dir(job_id: str) -> Path:
    """Resolve a job's directory, refusing anything that escapes the root."""
    if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
        raise MediaStorageError("job_id must be 32 lowercase hex characters")
    root = media_root()
    candidate = (root / "jobs" / job_id).resolve()
    if not candidate.is_relative_to(root):
        raise MediaStorageError("resolved job directory escapes the media root")
    return candidate


def create_job_dir(job_id: str) -> Path:
    """Make an EMPTY directory for the job, clearing any stale one first."""
    path = job_dir(job_id)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def discard_job_dir(job_id: str) -> None:
    """Remove a job's scratch directory. Never raises."""
    try:
        path = job_dir(job_id)
    except MediaStorageError:
        return
    shutil.rmtree(path, ignore_errors=True)
