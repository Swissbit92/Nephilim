"""Extraction off the request thread: capture now, consolidate later.

🔴 WHY THIS EXISTS, MEASURED. Legacy fact extraction ran INLINE in
`_apply_post_turn_updates`, on the request thread, and its LLM call costs a
**median 5.54 s** (5 reps, 5.26–6.11 s, live model, live transcript). The user
waits for it. At the configured interval of 10 that is one turn in ten being
5.5 s slower, and lowering the interval to catch short sessions would have made
it worse rather than better.

This is the pattern the field converged on independently — Zep, ECHO
(arXiv 2608.21755) and CoMIC (arXiv 2606.00756) all split a cheap inline capture
from an asynchronous consolidation step. MemBench (arXiv 2506.21605) is the one
benchmark that publishes write latency at all and puts comparable systems at
0.106–2.48 s (MemGPT) to 8–18 s (MemoryBank), so 5.54 s is unremarkable for the
work being done and entirely unacceptable on a user-facing turn.

⚠️ **`stop()` IS REGISTERED WITH THE LIFESPAN.** The pre-existing
`FactExtractionWorker` has the same shape and its `stop()` has no caller, so its
queue is lost on every restart. That is a recorded gap and this worker does not
repeat it.

⚠️ **A QUEUED JOB IS STILL LOST IF THE PROCESS DIES UNCLEANLY.** This is a
daemon thread with an in-memory queue, not a durable outbox. The exposure is
bounded by the interval: at most one window of conversation. Making it durable
means a table and a claim protocol, which is ADR-021's job and not this one's —
stated here so nobody discovers it later and calls it a surprise.
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FactJob:
    """One window of conversation to mine for facts about the operator.

    `messages` are the raw SQLite rows, so they carry `role` and `id` — which is
    what lets `asserted_by` come from the role column rather than a model.
    """

    user_id: str
    session_id: str
    persona_key: str
    messages: list[dict[str, Any]]


class OperatorFactWorker:
    """Single daemon thread, bounded queue, drains on shutdown."""

    def __init__(self, handler: Callable[[FactJob], int],
                 max_queue: int = 64) -> None:
        self._handler = handler
        self._q: "queue.Queue[Optional[FactJob]]" = queue.Queue(maxsize=max_queue)
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()

    # ---------- lifecycle -------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="operator-fact-worker", daemon=True
        )
        self._thread.start()
        logger.info("[OperatorFactWorker] started")

    def stop(self, timeout: float = 10.0) -> None:
        """Drain and join. Registered with the lifespan — see the module note."""
        if self._thread is None:
            return
        self._stopping.set()
        try:
            self._q.put_nowait(None)      # sentinel
        except queue.Full:
            # A full queue at shutdown means the sentinel cannot get in; the
            # daemon thread dies with the process either way, but say so rather
            # than block on a join that will time out.
            logger.warning("[OperatorFactWorker] queue full at stop; "
                           "%d job(s) dropped", self._q.qsize())
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            logger.warning("[OperatorFactWorker] did not stop within %.1fs", timeout)
        else:
            logger.info("[OperatorFactWorker] stopped cleanly")
        self._thread = None

    # ---------- queue -----------------------------------------------------

    def enqueue(self, job: FactJob) -> bool:
        """Non-blocking. Returns False and SAYS SO when the queue is full.

        A silently dropped job is indistinguishable from a conversation that
        yielded no facts, which is the ambiguity this whole milestone exists to
        remove — so the drop is logged at WARNING, not debug.
        """
        if self._stopping.is_set():
            return False
        try:
            self._q.put_nowait(job)
            return True
        except queue.Full:
            logger.warning(
                "[OperatorFactWorker] queue FULL (%d) — dropped a window for "
                "session %s. Facts in it are lost.",
                self._q.maxsize, job.session_id[:8],
            )
            return False

    def qsize(self) -> int:
        return self._q.qsize()

    # ---------- the loop --------------------------------------------------

    def _run(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                return
            try:
                written = self._handler(job)
                logger.info(
                    "[OperatorFactWorker] session %s: wrote %d fact(s)",
                    job.session_id[:8], written,
                )
            except Exception as e:  # noqa: BLE001 - a bad job must not kill the thread
                logger.warning(
                    "[OperatorFactWorker] job failed for session %s (non-fatal): %s",
                    job.session_id[:8], e,
                )
            finally:
                self._q.task_done()
