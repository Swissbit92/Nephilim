"""Removing sent images on /reset.

The load-bearing tests are the partition ones. Telegram's deleteMessages
pre-flight loops every id and returns an error on the first it cannot delete —
so ONE message over 48h old makes a 100-id call delete NOTHING. Partitioning
locally is not an optimisation, it is the only way the call succeeds.

And the counts must never come from the API: deleteMessages returns True for
an all-not-found batch having deleted nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eeva_telegram.media_cleanup import (
    CleanupResult,
    describe,
    partition_by_age,
    remove_sent_media,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _rec(mid: int, hours_ago: float) -> tuple[int, str]:
    return (mid, (NOW - timedelta(hours=hours_ago)).isoformat())


# ---------- partition ----------


def test_recent_messages_are_deletable():
    deletable, old = partition_by_age([_rec(1, 1), _rec(2, 10)], now=NOW)
    assert (deletable, old) == ([1, 2], [])


def test_messages_past_the_window_are_withheld():
    deletable, old = partition_by_age([_rec(1, 50)], now=NOW)
    assert (deletable, old) == ([], [1])


def test_one_stale_message_does_not_withhold_the_fresh_ones():
    """The whole point. Sending all four to Telegram would delete none of
    them, because one stale id fails the entire batch."""
    deletable, old = partition_by_age(
        [_rec(1, 1), _rec(2, 60), _rec(3, 2), _rec(4, 99)], now=NOW
    )
    assert deletable == [1, 3]
    assert old == [2, 4]


def test_the_cutoff_has_margin_under_telegrams_48h():
    """47h, not 48: clock skew between this host and Telegram's makes the last
    minutes a coin flip, and guessing wrong costs the whole batch."""
    assert partition_by_age([_rec(1, 46.9)], now=NOW)[0] == [1]
    assert partition_by_age([_rec(1, 47.1)], now=NOW)[1] == [1]


@pytest.mark.parametrize("bad", ["", "not-a-date", None, "2026-13-45"])
def test_an_unparseable_timestamp_is_treated_as_too_old(bad):
    """Conservative direction on purpose: guessing 'probably recent' risks
    poisoning a batch, while guessing 'too old' costs one image left behind."""
    assert partition_by_age([(1, bad)], now=NOW)[1] == [1]


def test_a_naive_timestamp_is_assumed_utc():
    naive = (NOW - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    assert partition_by_age([(1, naive)], now=NOW)[0] == [1]


# ---------- the result object ----------


def test_complete_is_computed_not_asserted():
    assert CleanupResult(removed=3).complete is True
    assert CleanupResult(removed=3, retained=1).complete is False
    assert CleanupResult(removed=3, failed=1).complete is False


# ---------- delivery ----------


class _Bot:
    def __init__(self, fail=False):
        self.batches: list[list[int]] = []
        self._fail = fail

    async def delete_messages(self, chat_id, message_ids):
        self.batches.append(list(message_ids))
        if self._fail:
            from telegram.error import BadRequest

            raise BadRequest("message can't be deleted")
        return True


async def test_nothing_recorded_is_not_an_error():
    assert await remove_sent_media(_Bot(), 111, []) == CleanupResult()


async def test_only_the_deletable_are_sent_to_telegram():
    bot = _Bot()
    records = [_rec(1, 1), _rec(2, 99), _rec(3, 2)]
    result = await remove_sent_media(bot, 111, records, now=NOW)
    assert bot.batches == [[1, 3]]
    assert (result.removed, result.retained) == (2, 1)


async def test_batches_are_chunked_at_100():
    bot = _Bot()
    records = [_rec(i, 1) for i in range(250)]
    result = await remove_sent_media(bot, 111, records, now=NOW)
    assert [len(b) for b in bot.batches] == [100, 100, 50]
    assert result.removed == 250


async def test_a_refused_batch_counts_as_failed_not_removed():
    """Never report a number we did not compute. A BadRequest means the whole
    batch was refused, so none of it is 'removed'."""
    bot = _Bot(fail=True)
    result = await remove_sent_media(bot, 111, [_rec(1, 1), _rec(2, 1)], now=NOW)
    assert (result.removed, result.failed) == (0, 2)
    assert result.complete is False


# ---------- the wording ----------


def test_describe_says_nothing_to_do_when_there_is_nothing():
    assert describe(CleanupResult(), 0) == "No images to remove."


def test_describe_never_claims_more_than_happened():
    text = describe(CleanupResult(removed=4, retained=19), quarantined=23)
    assert "4 removed" in text
    assert "19 left" in text
    assert "48 hours" in text
    for absolute in ("wiped", "all images", "everything", "erased"):
        assert absolute not in text.lower()


def test_describe_mentions_the_quarantine_window():
    text = describe(CleanupResult(removed=1), quarantined=1)
    assert "30 days" in text
