"""ADR-006 Phase 1 (M3) — fully-async fact extraction worker.

Per the user's decision, triplet extraction runs OFF the interactive path: the
chat turn enqueues a job and returns immediately; a single daemon thread drains the
queue, calls the (LLM-backed) TripletExtractor, and applies the recency-wins write
policy. A failing job is logged and dropped — it must never crash the worker or
block a chat response.

Kept deliberately simple (one worker, bounded queue): single-user companion scale,
extraction batched at the summarization cadence. Testable synchronously via
``process_job`` and ``join`` without starting the thread.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .fact_write_policy import apply_triples
from .repositories.memory_fact_repository import MemoryFactRepository

logger = logging.getLogger(__name__)


@dataclass
class ExtractionJob:
    user_id: str
    messages: List[Dict[str, Any]]
    session_id: Optional[str] = None


class FactExtractionWorker:
    """Background queue+thread that turns transcripts into stored facts.

    ``extractor_provider`` is a zero-arg callable returning an object with
    ``extract_triples(messages) -> list`` (deferred so the LLM client is built
    lazily, off the request path). ``repo`` is the fact store.
    """

    def __init__(
        self,
        extractor_provider: Callable[[], Any],
        repo: MemoryFactRepository,
        max_queue: int = 256,
    ):
        self._extractor_provider = extractor_provider
        self._repo = repo
        self._q: "queue.Queue[Optional[ExtractionJob]]" = queue.Queue(maxsize=max_queue)
        self._thread: Optional[threading.Thread] = None
        self._extractor: Any = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._run, name="fact-extraction-worker", daemon=True
        )
        self._thread.start()
        logger.info("[FactWorker] started")

    def enqueue(self, job: ExtractionJob) -> bool:
        """Non-blocking submit. Returns False if the queue is full (job dropped)."""
        try:
            self._q.put_nowait(job)
            return True
        except queue.Full:
            logger.warning("[FactWorker] queue full — dropping extraction job")
            return False

    def process_job(self, job: ExtractionJob) -> int:
        """Run one job synchronously (used by the worker loop and by tests)."""
        from .services.resource_arbiter import guard_chat_model

        # This thread loops forever and fires whenever a turn enqueues work, so
        # it is the one background producer that can start a 17 GiB load in the
        # middle of a generation with nothing having asked it to.
        guard_chat_model("fact extraction")
        if self._extractor is None:
            self._extractor = self._extractor_provider()
        triples = self._extractor.extract_triples(job.messages)
        return apply_triples(self._repo, job.user_id, triples, session_id=job.session_id)

    def join(self, timeout: Optional[float] = None) -> None:
        """Block until the queue is drained (test/shutdown helper)."""
        self._q.join()

    def stop(self) -> None:
        if not self._started:
            return
        self._q.put(None)  # sentinel
        if self._thread:
            self._thread.join(timeout=5)
        self._started = False

    def _run(self) -> None:
        while True:
            job = self._q.get()
            try:
                if job is None:  # shutdown sentinel
                    return
                self.process_job(job)
            except Exception as e:  # never let the worker die
                logger.warning(f"[FactWorker] job failed: {e}")
            finally:
                self._q.task_done()
