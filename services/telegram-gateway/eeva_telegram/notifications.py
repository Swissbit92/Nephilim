# services/telegram-gateway/eeva_telegram/notifications.py
"""Delivering a result nobody is waiting on a request for.

A generation takes ~331 s; `NephilimClient`'s read timeout is 180 s and the
coordinator's routes are sync. So the answer cannot come back on the request
that asked for it, and this loop fetches it instead.

Design notes that are decisions rather than detail:

**A plain asyncio task, not PTB's JobQueue.** `JobQueue` needs the
`job-queue` extra, which pulls APScheduler; measured in this venv,
`application.job_queue` is `None` because that extra is not installed. The
task is created with ``Application.create_task`` rather than
``asyncio.create_task``: PTB keeps its own strong reference (raw
``create_task`` results can be garbage-collected mid-flight, which the stdlib
docs warn about) and routes an uncaught exception to the registered error
handler instead of letting the loop die with a line in the asyncio log.

**It reuses the chat path's parser and sender.** The coordinator returns a
CHAT-SHAPED payload, so `relay.extract_media` and `handlers._deliver_media`
work unchanged. The alternative — a second parser for a second shape — would
mean the delivery was proven on a code path the real one does not use.

**`CancelledError` is re-raised, never swallowed.** A bare ``except
Exception`` around the loop body would catch the cancellation `post_shutdown`
uses to stop this, and the bot would then hang on shutdown waiting for a task
that has decided not to stop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import httpx

from . import relay
from .nephilim_client import NephilimError, NephilimUnavailableError

logger = logging.getLogger(__name__)

_TASK_KEY = "notify_task"

#: Backoff while the coordinator is unreachable. It restarts under launchd, so
#: this must recover quickly but must not spin during a long outage.
_BACKOFF_START = 2.0
_BACKOFF_MAX = 60.0


async def poll_once(application) -> int:
    """Claim and deliver whatever is ready. Returns how many were delivered.

    Separated from the loop so a test can drive one iteration without timing.
    """
    from . import handlers  # local: handlers imports this module's siblings

    gateway = application.bot_data["gateway"]
    payload = await gateway.client.claim_notifications(
        limit=10, personas=gateway.config.served_personas()
    )
    notifications = payload.get("notifications") or []
    if not notifications:
        return 0

    delivered = 0
    for note in notifications:
        if await _deliver_one(application, gateway, handlers, note):
            delivered += 1
    return delivered


async def _deliver_one(application, gateway, handlers, note: dict[str, Any]) -> bool:
    """Deliver one notification. Hands it back on any failure.

    Everything here is best-effort EXCEPT the hand-back: a notification that
    is claimed and then dropped is five and a half minutes of work the user
    never hears about.
    """
    job_id = note.get("job_id")
    session_id = note.get("session_id")

    if not isinstance(job_id, str) or not isinstance(session_id, str):
        logger.warning("[Notify] skipping a malformed notification: %r", note)
        return False

    found = gateway.store.chat_for_session(session_id)
    if found is None:
        # The session is unknown to this gateway — most likely it belongs to
        # the React UI, or the chat was reset and re-created. Nothing to do and
        # nothing to hand back: re-claiming it forever would be a hot loop.
        logger.info("[Notify] %s is for session %s, which this gateway does not "
                    "know — dropping", job_id, session_id[:8])
        return False

    chat_id, _persona = found
    if not gateway.config.is_allowed(chat_id):
        logger.warning("[Notify] %s targets non-allowlisted chat %s — dropping",
                       job_id, chat_id)
        return False

    bot = application.bot
    limit = gateway.config.message_char_limit
    items = relay.extract_media(note)

    try:
        if items:
            await handlers._deliver_media(bot, chat_id, gateway, items)
        else:
            # A failure, a cancellation, or a success whose file vanished. The
            # coordinator already phrased it for a human; `error` is for us.
            text = note.get("answer") or "Something went wrong with that picture."
            if note.get("error"):
                logger.warning("[Notify] %s finished as %s: %s",
                               job_id, note.get("status"), note.get("error"))
            from . import messaging
            await messaging.send_text(bot, chat_id, text, limit)
    except Exception:
        logger.exception("[Notify] delivery of %s failed — handing it back", job_id)
        await _hand_back(gateway, job_id)
        return False

    return True


async def _hand_back(gateway, job_id: str) -> None:
    """Return a claimed notification so a later poll retries it."""
    try:
        await gateway.client.nack_notification(job_id)
    except Exception:
        # If this fails the notification is lost. Loud, because it is the one
        # unrecoverable outcome in this module.
        logger.exception("[Notify] could not hand %s back; it is now LOST", job_id)


async def poll_loop(application) -> None:
    """Forever: claim, deliver, sleep. Backs off while the backend is down."""
    gateway = application.bot_data["gateway"]
    interval = gateway.config.notify_poll_seconds
    backoff = _BACKOFF_START

    logger.info("[Notify] poller started (every %.1fs)", interval)
    while True:
        try:
            await poll_once(application)
            backoff = _BACKOFF_START  # any success resets the penalty
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            # Re-raised, never swallowed: this is how post_shutdown stops us.
            logger.info("[Notify] poller stopping")
            raise
        except (NephilimUnavailableError, httpx.HTTPError) as exc:
            logger.warning("[Notify] coordinator unreachable (%s) — retrying in %.0fs",
                           type(exc).__name__, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)
        except NephilimError:
            # A 4xx/5xx from a reachable coordinator: likely the endpoint is
            # absent because IMAGE_GEN is off on that build. Back off rather
            # than hammering, but keep trying — a deploy can turn it on.
            logger.warning("[Notify] coordinator refused the claim — retrying in %.0fs",
                           backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)
        except Exception:
            logger.exception("[Notify] unexpected error in the poll loop")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)


def start(application) -> None:
    """Create the poll task. Idempotent.

    ``Application.create_task`` rather than ``asyncio.create_task``, for the
    error-handler routing: an uncaught exception reaches the registered
    handler instead of dying as one line in the asyncio log.

    ⚠️ **PTB is NOT the thing holding the strong reference here.** It warns on
    this call — "Tasks created via `Application.create_task` while the
    application is not running won't be automatically awaited" — because
    ``post_init`` runs before the app is running. Observed live on PTB 22.8.
    The reference that protects the task from garbage collection mid-flight is
    ``bot_data[_TASK_KEY]`` below, which is ours; and the await it will not do
    automatically is done explicitly by ``stop()`` from ``post_shutdown``. An
    earlier version of this docstring credited PTB with both, which is wrong
    in this call position — if the ``bot_data`` line is ever removed as
    redundant, the task becomes collectable and the poller stops silently.
    """
    if application.bot_data.get(_TASK_KEY) is not None:
        return
    task = application.create_task(poll_loop(application))
    application.bot_data[_TASK_KEY] = task
    logger.info("[Notify] poll task created")


async def stop(application) -> None:
    """Cancel the poll task and wait for it to actually finish."""
    task = application.bot_data.pop(_TASK_KEY, None)
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    logger.info("[Notify] poll task stopped")
