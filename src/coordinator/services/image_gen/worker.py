# src/coordinator/services/image_gen/worker.py
"""The thread that actually runs a generation.

Ties together the three pieces built before it: the job table (durable
intent), the arbiter (who gets the machine), and the supervisor (the detached
subprocess). It is a POLLER, not a queue consumer — unlike
``FactExtractionWorker``, whose in-memory ``queue.Queue`` is emptied by a
restart. A 331-second job must survive a restart, so the queue has to be the
table.

The transaction discipline is the load-bearing part, and it is the one thing a
test will not catch: **claim, commit, then run the 331 s job with no
transaction open, then open a new one for the result.** SQLite permits one
writer; `busy_timeout` only helps the waiter and cannot shrink the holder's
window, so a transaction held across the wait would block every other writer —
including every chat turn — for five and a half minutes. Each repository call
here is its own short statement, which is what keeps that true.

Ordering inside the lease is also deliberate:

    take the lease  ->  evict Ollama  ->  spawn  ->  watch  ->  verify  ->  re-pin

The lease comes first because the guards read it; evicting before closing
admission would leave a window where a chat turn reloads the 17 GiB model
between the eviction and the spawn. And the eviction is VERIFIED rather than
assumed — Ollama answers "unload" in 2 ms while the model is still resident.
If it cannot be evicted, the job fails rather than proceeding: 17 GiB plus
20 GB on a 48 GiB box is the outcome this whole subsystem exists to prevent.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from ...config import get_settings
from ...repositories.image_job_repository import JobStatus
from ..ollama_admin import repin, unload
from ..resource_arbiter import MemoryWatchdog, ResourceBusyError
from . import supervisor as sv
from .jobdir import create_job_dir
from .verify import walk_png

logger = logging.getLogger(__name__)

GIB = 2**30


class ImageGenWorker:
    """Polls for queued jobs and runs them one at a time.

    One at a time is not a simplification — it is the hardware. There is one
    GPU, and two concurrent generations would collide exactly the way a
    generation and the chat model do.
    """

    def __init__(self, repo, arbiter, *, poll_seconds: float = 2.0) -> None:
        self._repo = repo
        self._arbiter = arbiter
        self._poll = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: Set while a generation is in flight, for tests and for /status.
        self.current_job_id: str | None = None

    # ---------- lifecycle ----------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="image-gen-worker", daemon=True
        )
        self._thread.start()
        logger.info("[ImageGen] worker started")

    def stop(self, timeout: float = 10.0) -> None:
        """Signal the loop and wait for it to notice.

        Does NOT kill a running generation. The subprocess is detached on
        purpose and survives the coordinator; killing it here would throw away
        five minutes of GPU work every time launchd restarts the backend,
        which is the exact failure ``start_new_session=True`` was added to
        prevent. The startup sweep re-adopts it instead.
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("[ImageGen] worker did not stop within %.0fs", timeout)
            self._thread = None
        logger.info("[ImageGen] worker stopped")

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---------- the loop ----------

    def _run(self) -> None:
        # `wait` rather than `sleep`: stop() must be noticed immediately, not
        # after the remainder of a poll interval.
        while not self._stop.wait(self._poll):
            try:
                job = self._repo.claim_next()
            except Exception:
                logger.exception("[ImageGen] claim failed")
                continue
            if job is None:
                continue
            try:
                self.run_job(job)
            except Exception:
                # Never let the worker thread die: a crash here would silently
                # stop every future generation with no error anyone sees.
                logger.exception("[ImageGen] job %s crashed", job.id)
                try:
                    self._repo.finish(
                        job.id, status=JobStatus.FAILED, error="worker crashed"
                    )
                except Exception:
                    logger.exception("[ImageGen] could not even record the failure")

    # ---------- one job ----------

    def run_job(self, job) -> None:
        """Run one generation to completion. Synchronous; tests call it directly."""
        cfg = get_settings().image_gen
        self.current_job_id = job.id
        try:
            with self._arbiter.exclusive(f"image-gen:{job.id}"):
                self._run_under_lease(job, cfg)
        except ResourceBusyError as exc:
            # Someone else holds the machine. Put it back rather than failing
            # it — the job is still perfectly valid, just not now.
            logger.info("[ImageGen] %s requeued: %s", job.id, exc)
            self._repo.requeue(job.id)
        finally:
            self.current_job_id = None

    def _run_under_lease(self, job, cfg) -> None:
        st = get_settings()

        # 1. Evict the chat model, and VERIFY it left. The 200 is not evidence.
        result = unload(st.ollama.base, st.ollama.model, timeout_seconds=120)
        if not result.unloaded:
            held = (result.still_resident_vram or 0) / GIB
            self._repo.finish(
                job.id,
                status=JobStatus.FAILED,
                error=(
                    f"refused to generate: the chat model is still resident "
                    f"({held:.1f} GiB) after {result.seconds:.0f}s. Running "
                    f"anyway would swap the machine."
                ),
            )
            return

        try:
            self._generate(job, cfg)
        finally:
            # Always re-pin, even on failure. Measured 6.38 s after a real
            # generation (5.11 s from warm cache) — the user should not be the
            # one who pays it on their next message.
            repin(st.ollama.base, st.ollama.model,
                  keep_alive=type(st.ollama).wire_keep_alive(st.ollama.keep_alive))

    def _generate(self, job, cfg) -> None:
        job_dir = create_job_dir(job.id)
        argv = build_argv(cfg, prompt=job.prompt, output=job_dir / sv.OUTPUT_NAME)
        env = {"MFLUX_MEM_LIMIT_BYTES": str(int(cfg.memory_limit_gb) * GIB)}

        spawned = sv.spawn(job_dir, argv, env=env)
        self._repo.attach_process(
            job.id,
            job_dir=str(job_dir),
            pid=spawned.pid,
            pgid=spawned.pgid,
            proc_start=spawned.proc_start,
        )
        logger.info(
            "[ImageGen] %s running as pid %s in %s", job.id, spawned.pid, job_dir.name
        )

        outcome = self._watch(job, spawned, job_dir, cfg)
        if outcome is not None:
            self._repo.finish(job.id, status=JobStatus.FAILED, error=outcome)
            return

        self._finish_from_disk(job, job_dir)

    def _watch(self, job, spawned, job_dir: Path, cfg) -> str | None:
        """Poll until the job ends. Returns an error string, or None if it ended
        on its own terms (which is not the same as succeeding)."""
        watchdog = MemoryWatchdog(
            min_free_bytes=int(cfg.min_free_gb * GIB),
            grace_seconds=cfg.min_free_grace_seconds,
        )
        started = time.monotonic()
        last_progress, last_growth, last_beat = -1, started, started

        while True:
            if sv.probe(job_dir).state is not sv.State.RUNNING:
                return None

            now = time.monotonic()

            if watchdog.sample():
                sv.cancel(spawned.pgid, spawned.proc_start,
                          grace_seconds=cfg.term_grace_seconds)
                return "killed: the machine ran out of memory"

            if now - started > cfg.max_seconds:
                sv.cancel(spawned.pgid, spawned.proc_start,
                          grace_seconds=cfg.term_grace_seconds)
                return f"killed: exceeded the {cfg.max_seconds}s wall clock"

            # Stall detection on BYTE GROWTH, never readline: mflux's tqdm
            # writes carriage-return updates with no newline, so a line-based
            # watcher sees nothing for the whole run and calls a healthy job
            # stalled.
            size = sv.progress_size(job_dir)
            if size != last_progress:
                last_progress, last_growth = size, now
            elif now - last_growth > cfg.stall_seconds:
                sv.cancel(spawned.pgid, spawned.proc_start,
                          grace_seconds=cfg.term_grace_seconds)
                return f"killed: no progress for {cfg.stall_seconds}s"

            if now - last_beat >= 15:
                last_beat = now
                self._repo.heartbeat(job.id)

            # Responds to stop() promptly, but does NOT abandon the job:
            # the subprocess keeps running and the startup sweep re-adopts it.
            if self._stop.wait(1.0):
                logger.info(
                    "[ImageGen] shutting down; leaving %s running as pid %s",
                    job.id, spawned.pid,
                )
                return None

    def _finish_from_disk(self, job, job_dir: Path) -> None:
        """Decide success from the ARTEFACT, never from the exit code.

        mflux's save path swallows every exception and the CLI returns None, so
        it exits 0 having written nothing. Verified against a real run: rc 0
        plus a chunk-walk-clean PNG plus the wrapper's own Pillow decode all
        agreed, which is the only combination treated as success.
        """
        probe = sv.probe(job_dir)
        out = job_dir / sv.OUTPUT_NAME

        if probe.state is sv.State.DIED:
            self._repo.finish(job.id, status=JobStatus.FAILED,
                              error="the generator died without recording an exit code")
            return
        if probe.returncode not in (0, None):
            self._repo.finish(job.id, status=JobStatus.FAILED,
                              error=f"the generator exited {probe.returncode}"
                                    f"{_tail(job_dir)}")
            return

        verdict = walk_png(out)
        if not verdict.ok:
            self._repo.finish(job.id, status=JobStatus.FAILED,
                              error=f"no usable image: {verdict.reason}")
            return
        if not (job_dir / sv.VERIFIED_NAME).exists():
            # The wrapper decodes with Pillow and leaves this marker. Two
            # independent checks; disagreement means something is wrong that
            # neither alone would catch.
            self._repo.finish(job.id, status=JobStatus.FAILED,
                              error="the image failed the generator's own decode check")
            return

        self._repo.finish(job.id, status=JobStatus.SUCCEEDED, media_path=str(out))
        logger.info("[ImageGen] %s succeeded: %s (%dx%d)",
                    job.id, out.name, verdict.width, verdict.height)


def build_argv(cfg, *, prompt: str, output: Path) -> list[str]:
    """The mflux command line.

    ``--model`` takes ``qwen-image-2.1``. It is NOT ``dev`` — that is a Flux
    alias, and passing it makes the Qwen CLI exit 1 in two seconds with
    ``ModelConfigError``. Learned by doing it.
    """
    return [
        cfg.python_bin,
        str(Path(__file__).resolve().parents[4] / "scripts" / "image_gen"
            / "mflux_qwen_wrapped.py"),
        "--model", cfg.model_alias,
        "--quantize", str(cfg.quantize),
        "--steps", str(cfg.steps),
        "--width", str(cfg.width),
        "--height", str(cfg.height),
        "--prompt", prompt,
        "--output", str(output),
    ]


def _tail(job_dir: Path, limit: int = 300) -> str:
    """The generator's own last words, so a failure says something useful."""
    try:
        text = (job_dir / sv.PROGRESS_NAME).read_text(errors="replace")
    except OSError:
        return ""
    last = text.strip().splitlines()[-1:] or [""]
    return f" — {last[0][-limit:]}" if last[0] else ""
