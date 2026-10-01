"""Retry for outbound Telegram calls.

Until now every send was issued exactly once. In the sibling repos that cost a
notification for thirteen months, and the lesson is recorded as eeva-dca's
INV-1: the notification is the only evidence the user has that anything
happened, so losing it silently is worse than the failure it was reporting.

Same constants and the same two load-bearing details as
``eeva-dca/eeva_dca/notifier.py`` and ``eeva-exec/eeva_exec/notifier.py``. A
third notifier with its own backoff curve is how a fleet drifts.

Two differences from those, both forced by the library rather than chosen:

* They classify on an HTTP **status code**; PTB raises **exception types**, so
  the policy is a tuple of classes. Which brings the trap below.
* They are sync; this is async, so the unit of work is a **coroutine factory**
  rather than a prepared request. A coroutine object cannot be awaited twice
  (``RuntimeError: cannot reuse already awaited coroutine``) — the async
  analogue of the EOF'd file handle those docstrings warn about.

⚠️ **``BadRequest`` SUBCLASSES ``NetworkError`` in PTB.** Verified on 22.8:
``BadRequest <- NetworkError <- TelegramError``. So ``except NetworkError:
retry`` hammers every malformed request three times with backoff and fails
identically each time. The non-retryable classes must be caught FIRST. This is
the same shape as an overloaded field producing a confident wrong answer: the
suite stays green because ``BadRequest`` genuinely *is* a ``NetworkError``.

**AIORateLimiter is deliberately NOT used.** It would add the ``aiolimiter``
dependency to buy two things, and it delivers neither here: its proactive
throttle applies only when ``chat_id`` is negative or a string (i.e. groups) —
a private chat has a positive id and is never throttled — and its retry covers
``RetryAfter`` only, with ``max_retries`` defaulting to 0. This module handles
``RetryAfter`` by honouring the server's own ``retry_after`` instead of
guessing a backoff, which is strictly better. Telegram documents that short
bursts over ~1 msg/sec are tolerated, so a reactive 429 handler beats slowing
every normal send for a rare case.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from telegram.error import (
    BadRequest,
    ChatMigrated,
    Conflict,
    Forbidden,
    InvalidToken,
    NetworkError,
    PassportDecryptionError,
    RetryAfter,
)

logger = logging.getLogger(__name__)

# Matched to eeva-dca and eeva-exec. Do not diverge without changing all three.
_SEND_ATTEMPTS = 3
_BACKOFF_SECONDS = (1.0, 3.0)

#: Retrying these burns the rate limit to fail identically. Caught BEFORE
#: NetworkError because BadRequest is a subclass of it.
#:
#: ChatMigrated is here rather than retried because the chat id itself is now
#: wrong — it carries ``new_chat_id`` and wants re-dispatch, not repetition.
#: Conflict means a second poller; retrying makes it worse. InvalidToken and
#: EndPointNotFound are permanent.
_NON_RETRYABLE: tuple[type[Exception], ...] = (
    BadRequest,
    Forbidden,
    InvalidToken,
    Conflict,
    ChatMigrated,
    PassportDecryptionError,
)

try:  # pragma: no cover - present from PTB 20.8, guarded so a pin can't break import
    from telegram.error import EndPointNotFound

    _NON_RETRYABLE = (*_NON_RETRYABLE, EndPointNotFound)
except ImportError:  # pragma: no cover
    pass


def _retry_after_seconds(exc: RetryAfter) -> float:
    """Normalise ``RetryAfter.retry_after``, which is int|timedelta since 22.2."""
    value = exc.retry_after
    if isinstance(value, timedelta):
        return value.total_seconds()
    return float(value)


async def send_with_retry(
    factory: Callable[[], Awaitable[object]],
    *,
    what: str,
    attempts: int = _SEND_ATTEMPTS,
    backoff: tuple[float, ...] = _BACKOFF_SECONDS,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> bool:
    """Run ``factory()`` until Telegram accepts it or the failure is permanent.

    Returns whether it was accepted. **Never raises** — a failure to deliver
    must not become a second failure on top of it. The caller decides whether a
    False is worth telling the user about.

    ``factory`` builds a FRESH coroutine per attempt. Passing a coroutine object
    would fail on attempt 2; passing an open file handle would upload an empty
    body and report success, which is the worst outcome a retry can produce.

    ⚠️ A ``TimedOut`` on a media send is **ambiguous**, not failed: the bytes may
    have arrived and only the acknowledgement was lost. The Bot API has no
    idempotency key, so there is no way to make this exactly-once. We retry, and
    therefore accept a possible duplicate image over a possible silent loss.
    That trade is deliberate and belongs in the open: for a once-per-image
    notification, a duplicate is visible and recoverable while a loss is neither.
    """
    # Resolved at CALL time, never bound as a default: a default argument
    # captures asyncio.sleep at import and no test can replace it — and a suite
    # that really sleeps 4s per retry case is a suite people delete.
    sleep = sleep or asyncio.sleep

    for attempt in range(1, attempts + 1):
        last = attempt == attempts
        try:
            await factory()
            if attempt > 1:
                logger.info("Telegram %s accepted on attempt %d", what, attempt)
            return True
        except _NON_RETRYABLE as exc:
            logger.warning(
                "Telegram %s failed permanently (%s): %s", what, type(exc).__name__, exc
            )
            return False
        except RetryAfter as exc:
            wait = _retry_after_seconds(exc) + 0.1
            logger.warning(
                "Telegram %s rate-limited, waiting %.1fs (attempt %d/%d)",
                what,
                wait,
                attempt,
                attempts,
            )
            if last:
                return False
            await sleep(wait)  # the server's number, not ours
        except NetworkError as exc:  # includes TimedOut; MUST come after the above
            logger.warning(
                "Telegram %s failed (%s) attempt %d/%d: %s",
                what,
                type(exc).__name__,
                attempt,
                attempts,
                exc,
            )
            if last:
                return False
            await sleep(backoff[min(attempt - 1, len(backoff) - 1)])
        except Exception as exc:  # noqa: BLE001 - delivery must not raise
            logger.exception("Telegram %s raised unexpectedly: %s", what, exc)
            return False

    return False
