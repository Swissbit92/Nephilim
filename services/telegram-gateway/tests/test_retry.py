"""send_with_retry — the classification, and the trap underneath it.

The load-bearing test is test_bad_request_is_not_retried. BadRequest SUBCLASSES
NetworkError in PTB, so the obvious `except NetworkError: retry` hammers every
malformed request three times and fails identically. Nothing about that is
visible in a passing suite unless something asserts it directly.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from telegram.error import (
    BadRequest,
    ChatMigrated,
    Conflict,
    Forbidden,
    InvalidToken,
    NetworkError,
    RetryAfter,
    TimedOut,
)

from eeva_telegram.retry import send_with_retry


class _Recorder:
    """Counts attempts and records sleeps, so no test ever really sleeps."""

    def __init__(self, *, fail_with=None, fail_times=0):
        self.calls = 0
        self.slept: list[float] = []
        self._fail_with = fail_with
        self._fail_times = fail_times

    async def factory(self):
        self.calls += 1
        if self._fail_with and self.calls <= self._fail_times:
            raise self._fail_with
        return "sent"

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


# ---------- the happy path ----------


async def test_succeeds_first_try_without_sleeping():
    r = _Recorder()
    assert await send_with_retry(r.factory, what="t", sleep=r.sleep) is True
    assert (r.calls, r.slept) == (1, [])


async def test_recovers_on_a_later_attempt():
    r = _Recorder(fail_with=NetworkError("flaky"), fail_times=2)
    assert await send_with_retry(r.factory, what="t", sleep=r.sleep) is True
    assert r.calls == 3
    assert r.slept == [1.0, 3.0]  # matches eeva-dca / eeva-exec


# ---------- the trap ----------


async def test_bad_request_is_not_retried():
    """BadRequest subclasses NetworkError. Catching NetworkError first would
    retry a malformed request three times to fail identically each time."""
    r = _Recorder(fail_with=BadRequest("malformed"), fail_times=99)
    assert await send_with_retry(r.factory, what="t", sleep=r.sleep) is False
    assert r.calls == 1
    assert r.slept == []


def test_bad_request_really_is_a_networkerror():
    """Pins the premise. If PTB ever changes this, the ordering above stops
    mattering and this test says so rather than leaving a stale comment."""
    assert issubclass(BadRequest, NetworkError)


@pytest.mark.parametrize(
    "exc",
    [Forbidden("blocked"), InvalidToken(), Conflict("two pollers"), ChatMigrated(123)],
)
async def test_permanent_failures_are_not_retried(exc):
    r = _Recorder(fail_with=exc, fail_times=99)
    assert await send_with_retry(r.factory, what="t", sleep=r.sleep) is False
    assert r.calls == 1


async def test_timed_out_is_retried():
    """TimedOut is also a NetworkError subclass, but it is transient — and
    ambiguous on a media send, which is why we accept a possible duplicate."""
    r = _Recorder(fail_with=TimedOut(), fail_times=1)
    assert await send_with_retry(r.factory, what="t", sleep=r.sleep) is True
    assert r.calls == 2


# ---------- rate limiting ----------


async def test_retry_after_uses_the_servers_number_not_our_backoff():
    r = _Recorder(fail_with=RetryAfter(timedelta(seconds=7)), fail_times=1)
    assert await send_with_retry(r.factory, what="t", sleep=r.sleep) is True
    assert r.slept == [pytest.approx(7.1)]  # 7 + 0.1, not our 1.0


async def test_retry_after_accepts_an_int():
    """RetryAfter.retry_after is int|timedelta since PTB 22.2."""
    r = _Recorder(fail_with=RetryAfter(5), fail_times=1)
    assert await send_with_retry(r.factory, what="t", sleep=r.sleep) is True
    assert r.slept == [pytest.approx(5.1)]


# ---------- exhaustion and safety ----------


async def test_gives_up_after_three_attempts():
    r = _Recorder(fail_with=NetworkError("down"), fail_times=99)
    assert await send_with_retry(r.factory, what="t", sleep=r.sleep) is False
    assert r.calls == 3
    assert r.slept == [1.0, 3.0]  # no sleep after the last attempt


async def test_never_raises_even_on_an_unexpected_error():
    """A failure to deliver must not become a second failure on top of it."""

    async def boom():
        raise ValueError("something nobody predicted")

    assert await send_with_retry(boom, what="t", sleep=lambda _s: _noop()) is False


async def _noop():
    return None


async def test_calls_a_fresh_coroutine_each_attempt():
    """A coroutine object cannot be awaited twice — the async analogue of the
    EOF'd file handle that would upload an empty body and report success."""
    seen = []

    async def factory():
        seen.append(object())
        if len(seen) < 2:
            raise NetworkError("once")
        return "ok"

    async def sleep(_):
        return None

    assert await send_with_retry(factory, what="t", sleep=sleep) is True
    assert len(seen) == 2
    assert seen[0] is not seen[1]
