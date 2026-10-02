# src/coordinator/services/image_gen/supervisor.py
"""Supervising a detached image-generation subprocess.

Pure library: no app, no DB, no routes. Takes a job directory and an argv,
tells you RUNNING / FINISHED(rc) / DIED, and keeps telling you correctly after
the coordinator has restarted.

Five properties, each bought by one specific mechanism, each the fix for a
measured failure rather than a precaution:

1. **The child survives a coordinator restart.** launchd kills every process
   sharing the job's process group when the job dies (`AbandonProcessGroup`
   defaults false), and uvicorn's PGID equals its own PID — verified on the
   live backend. A default `Popen` child inherits that group, so every
   KeepAlive restart would silently destroy five minutes of GPU work.
   `start_new_session=True` gives the child its own session and group.

2. **Liveness survives the restart too, without PID reuse.** The parent takes
   an `flock`, hands the fd to the child via `pass_fds`, and closes its own
   copy; the lock then lives for exactly the child's lifetime, with no
   cooperation from mflux.

   ⚠️ **`flock`, never `lockf`.** A POSIX record lock (`lockf`/`fcntl`) is
   per-PROCESS and is released when the process closes *any* fd to the file —
   so `os.close(lock_fd)` in the parent would drop it instantly and every live
   job would probe as DIED. Measured before this was written: for the same live
   child, flock reports RUNNING and lockf reports DIED.

3. **The exit code survives the restart.** After a restart we are not the
   child's parent and can never `waitpid` it. A `/bin/sh` wrapper writes the
   code to `rc` via `mv -f`, so it appears atomically and "no rc file" is never
   a torn read.

4. **Cancellation reaches grandchildren.** `Popen.kill()` signals only the
   direct child, which is `sh`. `os.killpg` is the job and everything it
   spawned, and it is safe precisely because (1) gave the child its own group.

5. **Success is never inferred from the exit code.** mflux's `save_image`
   catches every exception and the CLI returns None, so it exits 0 having
   written nothing. Success is rc 0 AND the file exists AND it chunk-walks
   clean AND the wrapper left its `verified` marker.
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)

#: Writes the child's exit code atomically so a restarted coordinator can read
#: it. `exec` keeps the pid we recorded; `mv -f` makes `rc` appear whole.
_RC_WRAPPER = '"$@"; printf %s "$?" > rc.tmp && mv -f rc.tmp rc'

LOCK_NAME = "job.lock"
RC_NAME = "rc"
PROGRESS_NAME = "progress.log"
STDOUT_NAME = "stdout.log"
OUTPUT_NAME = "out.png"
VERIFIED_NAME = "verified"


class State(StrEnum):
    RUNNING = "running"
    FINISHED = "finished"  # exited, exit code known
    DIED = "died"  # exited without recording one (SIGKILL, panic, jetsam)


@dataclass(frozen=True)
class Probe:
    state: State
    returncode: int | None = None


@dataclass(frozen=True)
class Spawned:
    pid: int
    pgid: int
    proc_start: float
    popen: subprocess.Popen


def proc_start_time(pid: int) -> float | None:
    """Approximate process start time, for telling a recycled PID apart.

    Uses `ps -o etime=` (ELAPSED time) rather than `lstart`, deliberately.
    `lstart` renders a LOCALE-DEPENDENT date — on this machine it returns
    'Fr.  2 Okt. 09:53:22 2026', which no English strptime format parses. The
    failure was silent and severe: an unparseable date made this return None,
    and `cancel` reads None as "already gone", so it would have reported
    success without killing anything.

    `etime` renders as [[dd-]hh:]mm:ss — digits and separators only, no locale.
    (macOS `ps` has no `etimes`; it rejects the keyword and lists the valid
    ones, which is its own small trap.) One-second resolution is ample: a
    recycled PID is seconds or hours out, never milliseconds.

    Returns None only when the process genuinely does not exist.
    """
    try:
        out = subprocess.run(
            ["/bin/ps", "-o", "etime=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = out.stdout.strip()
    if out.returncode != 0 or not raw:
        return None
    return time.time() - _parse_etime(raw) if _parse_etime(raw) is not None else None


def _parse_etime(raw: str) -> float | None:
    """[[dd-]hh:]mm:ss -> seconds. Returns None on anything unexpected."""
    days = 0
    if "-" in raw:
        d, _, raw = raw.partition("-")
        try:
            days = int(d)
        except ValueError:
            return None
    parts = raw.split(":")
    if not 2 <= len(parts) <= 3:
        return None
    try:
        nums = [int(x) for x in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        nums = [0, *nums]
    hours, minutes, seconds = nums
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _lock_is_held(lock_path: Path) -> bool:
    """True while some process holds the job lock.

    Takes the lock to find out it is free, then releases it immediately. A
    missing file means nothing ever ran.
    """
    if not lock_path.exists():
        return False
    fd = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            return True
        raise
    finally:
        os.close(fd)


def probe(job_dir: Path) -> Probe:
    """RUNNING / FINISHED(rc) / DIED, from the filesystem alone.

    Deliberately reads no in-process state, so a freshly restarted coordinator
    gets the same answer as the process that spawned the job. The order matters:
    check the lock first, because a job that is still running has no `rc` yet
    and would otherwise look DIED.
    """
    job_dir = Path(job_dir)
    if _lock_is_held(job_dir / LOCK_NAME):
        return Probe(State.RUNNING)

    rc_file = job_dir / RC_NAME
    if rc_file.exists():
        try:
            return Probe(State.FINISHED, int(rc_file.read_text().strip()))
        except ValueError:
            logger.warning("[ImageGen] unreadable rc in %s", job_dir)
            return Probe(State.DIED)
    return Probe(State.DIED)


def spawn(job_dir: Path, argv: list[str], env: dict[str, str] | None = None) -> Spawned:
    """Start the generator, detached, holding an inherited lock."""
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)

    lock_fd = os.open(job_dir / LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock_fd)
        raise RuntimeError(f"a job is already running in {job_dir}") from None

    child_env = {**os.environ, **(env or {})}
    # Unbuffered, or the progress log lags and the stall detector reads a
    # stalled job as healthy (the child's stdio is block-buffered to a file).
    child_env["PYTHONUNBUFFERED"] = "1"

    # Files, never PIPE: nothing drains a pipe after a coordinator restart, and
    # a full pipe buffer deadlocks the child. Files also keep the log.
    out_f = open(job_dir / STDOUT_NAME, "wb")
    err_f = open(job_dir / PROGRESS_NAME, "wb")
    try:
        popen = subprocess.Popen(
            ["/bin/sh", "-c", _RC_WRAPPER, "sh", *argv],
            cwd=str(job_dir),
            start_new_session=True,  # escapes launchd's process-group sweep
            pass_fds=(lock_fd,),  # the child becomes the lock's only holder
            stdin=subprocess.DEVNULL,
            stdout=out_f,
            stderr=err_f,
            env=child_env,
        )
    finally:
        out_f.close()
        err_f.close()
        # Drop the parent's copy. The lock survives on the child's fd, which is
        # what makes it an exact liveness signal.
        os.close(lock_fd)

    start = proc_start_time(popen.pid) or time.time()
    logger.info("[ImageGen] spawned pid=%s in %s", popen.pid, job_dir.name)
    return Spawned(pid=popen.pid, pgid=popen.pid, proc_start=start, popen=popen)


def cancel(
    pgid: int,
    recorded_start: float | None = None,
    *,
    grace_seconds: int = 20,
    poll: float = 0.5,
) -> bool:
    """TERM the whole group, then KILL. True if it is gone afterwards.

    Signals the GROUP, not the process: the direct child is `/bin/sh` and
    killing it would leave mflux running.

    `recorded_start` guards against PID reuse — signalling a recycled PGID
    would kill an unrelated process. A mismatch refuses rather than guesses.
    """
    if recorded_start is not None:
        current = proc_start_time(pgid)
        if current is None:
            return True  # already gone
        if abs(current - recorded_start) > 5.0:
            logger.warning(
                "[ImageGen] refusing to signal pgid %s — start time moved, "
                "the PID was recycled",
                pgid,
            )
            return False

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            logger.warning("[ImageGen] not permitted to signal pgid %s", pgid)
            return False

        deadline = time.monotonic() + (grace_seconds if sig == signal.SIGTERM else 10)
        while time.monotonic() < deadline:
            if not _group_signalable(pgid):
                return True
            time.sleep(poll)
        if sig == signal.SIGTERM:
            logger.warning(
                "[ImageGen] pgid %s ignored SIGTERM for %ss — escalating",
                pgid,
                grace_seconds,
            )
    return False


def _group_signalable(pgid: int) -> bool:
    """Is there anything left in this group that we could signal?

    ESRCH is the obvious "gone". EPERM is subtler and must be treated the same
    way: it means the group holds nothing WE may signal, and a caller that
    cannot signal a process cannot kill it either, so looping until the
    deadline would just stall. Letting EPERM escape made `cancel` raise instead
    of reporting — caught by the TERM-ignorer test.
    """
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def progress_size(job_dir: Path) -> int:
    """Bytes in the progress log — the stall signal.

    Size, not lines: mflux's tqdm writes carriage-return updates with no
    newline, so a readline-based watcher sees nothing for the entire run and
    would call a perfectly healthy job stalled.
    """
    try:
        return (Path(job_dir) / PROGRESS_NAME).stat().st_size
    except OSError:
        return 0
