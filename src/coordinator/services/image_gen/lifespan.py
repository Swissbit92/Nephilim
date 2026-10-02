# src/coordinator/services/image_gen/lifespan.py
"""Starting and stopping the generation worker with the app.

A standalone ``@asynccontextmanager`` rather than code inlined into
``server.py``'s lifespan, for two reasons. It can be composed with
``AsyncExitStack`` alongside future ones (FastAPI has no first-party
multi-lifespan primitive), and — the reason that actually bites — it can be
TESTED, which nothing in this repo's lifespan currently can be.

⚠️ **A lifespan only runs inside ``with TestClient(app) as client:``.** This
repo's own convention is the opposite — its testing guide says to use
``TestClient(app)`` *without* the ``with`` precisely because that skips the
lifespan. Any test of this module must therefore deviate from the house style
deliberately, or it will assert against a worker that was never started and
pass for the wrong reason.

Shutdown here is also establishing a pattern rather than following one: today
exactly two things are torn down in ``server.py`` (the APScheduler and the
neo4j driver), and ``FactExtractionWorker.stop()`` exists but has no caller
anywhere in ``src/`` — that worker is simply killed with the process. We stop
properly because the alternative loses the heartbeat and leaves rows reading
RUNNING when the loop is gone.

What shutdown does NOT do is kill a running generation. The subprocess is
detached deliberately; killing it would discard five minutes of GPU work on
every launchd restart, which is the exact failure `start_new_session=True`
was added to prevent. The sweep re-adopts it on the way back up.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ...config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def image_gen_lifespan(app=None) -> AsyncIterator[None]:
    """Sweep orphans, run the worker, stop it cleanly.

    A no-op when ``IMAGE_GEN_ENABLED`` is off, including the sweep: with the
    worker disabled nothing can be mid-flight, and failing rows on a box where
    the feature is switched off would destroy state that a re-enable could
    still have used.
    """
    cfg = get_settings().image_gen
    if not cfg.enabled:
        logger.info("[ImageGen] disabled — worker not started")
        yield
        return

    from ... import startup
    from .reconcile import sweep
    from .worker import ImageGenWorker

    repo = startup.get_image_job_repo()
    arbiter = startup.get_resource_arbiter()

    # Before the worker runs, settle anything left over from the last process.
    # Order matters: a job still generating must be ADOPTED here, or the worker
    # would see a queued-looking row and start a second generation on top of
    # the first one.
    try:
        sweep(repo)
    except Exception:
        # A broken sweep must not stop the app booting; the worst case is a
        # stale row, which the periodic sweep retries.
        logger.exception("[ImageGen] startup sweep failed")

    worker = ImageGenWorker(repo, arbiter, poll_seconds=cfg.poll_seconds)
    worker.start()
    if app is not None:
        app.state.image_gen_worker = worker

    try:
        yield
    finally:
        try:
            worker.stop()
        except Exception:
            logger.exception("[ImageGen] worker shutdown failed")
