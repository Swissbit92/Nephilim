"""Removing already-sent images from the chat on /reset.

Two facts from the Bot API's own implementation shape everything here, and
neither is in the documentation:

1. **One stale id poisons the whole batch.** ``deleteMessages`` pre-flight
   loops over every id and ``return``s an error on the first one it cannot
   delete — no partial application, no per-id result. So a single message over
   48h old makes a 100-id call delete *nothing*. The documented "if some of the
   specified messages can't be found, they are skipped" covers only ids that do
   not resolve at all; it does not cover ids that resolve but are too old.

   Consequence: **partition by age locally, before calling.** That is not an
   optimisation, it is the only way the call succeeds at all.

2. **``True`` does not mean "deleted".** An all-not-found batch returns
   ``True`` having removed nothing. A count derived from the return value would
   be fiction, so every number this module reports comes from our own
   partition — what we *attempted* — never from the API's answer.

The 48h limit is measured from the message's own ``date`` and the comparison is
``<=``, so exactly 48h still works. We use a 47h cutoff anyway: clock skew
between this host and Telegram's makes the final minutes a coin flip, and
guessing wrong costs the entire batch rather than one message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from telegram import Bot

from .retry import send_with_retry

logger = logging.getLogger(__name__)

#: Telegram's wall is 48h. The margin is for clock skew, and it is deliberately
#: generous because the cost of misjudging is a whole failed batch.
_DELETABLE_WINDOW = timedelta(hours=47)

#: deleteMessages accepts 1-100 ids. PTB does NOT validate this client-side —
#: its own constants are documentation only — so the chunking is ours to do.
_BATCH = 100


@dataclass(frozen=True)
class CleanupResult:
    """What we attempted and what we could not attempt. Counts, never booleans.

    ``removed`` is what we asked Telegram to delete and it accepted; ``retained``
    is what we did not ask about because it is past the window. A boolean could
    not express "4 of 23", which is the normal case.
    """

    removed: int = 0
    retained: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.removed + self.retained + self.failed

    @property
    def complete(self) -> bool:
        """Computed, never asserted. A caller reading only a success flag must
        not be able to conclude everything is gone."""
        return self.retained == 0 and self.failed == 0


def _parse(ts: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def partition_by_age(
    records: list[tuple[int, str]], *, now: datetime | None = None
) -> tuple[list[int], list[int]]:
    """Split [(message_id, sent_at)] into (deletable, too_old).

    A record whose timestamp cannot be parsed is treated as too old. Guessing
    "probably recent" on a bad timestamp risks poisoning a whole batch; the
    conservative direction costs at most one image left in the chat.
    """
    moment = now or datetime.now(UTC)
    deletable: list[int] = []
    too_old: list[int] = []
    for message_id, sent_at in records:
        parsed = _parse(sent_at)
        if parsed is not None and moment - parsed < _DELETABLE_WINDOW:
            deletable.append(message_id)
        else:
            too_old.append(message_id)
    return deletable, too_old


async def remove_sent_media(
    bot: Bot,
    chat_id: int,
    records: list[tuple[int, str]],
    *,
    now: datetime | None = None,
) -> CleanupResult:
    """Delete what Telegram still allows us to, and count the rest honestly.

    `now` is injectable for the same reason `partition_by_age` takes one, and
    it was missing here — which made this function's tests ROT BY
    CONSTRUCTION. They built records relative to a frozen NOW in the test
    file while the function read the real clock, so they passed on the day
    they were written and went red three days later when every fixture
    aged past the 47-hour window. A deterministic test of a time-dependent
    function needs the clock passed in, not frozen on one side of the call.
    """
    if not records:
        return CleanupResult()

    deletable, too_old = partition_by_age(records, now=now)
    if too_old:
        logger.info(
            "[Media] %d message(s) past Telegram's 48h deletion window for chat_id=%s",
            len(too_old),
            chat_id,
        )

    removed = 0
    failed = 0
    for start in range(0, len(deletable), _BATCH):
        chunk = deletable[start : start + _BATCH]
        ok = await send_with_retry(
            lambda c=chunk: bot.delete_messages(chat_id=chat_id, message_ids=c),
            what=f"deleteMessages[{len(chunk)}]",
        )
        # Our own partition is the source of truth for the count; the API's
        # True/False only tells us whether the call errored.
        if ok:
            removed += len(chunk)
        else:
            failed += len(chunk)

    return CleanupResult(removed=removed, retained=len(too_old), failed=failed)


def describe(result: CleanupResult, quarantined: int) -> str:
    """One plain-text line about what happened to the images.

    Never says "wiped", "all" or "everything" unless it is true. The user finds
    out otherwise by scrolling up, and discovering the bot overstated a privacy
    action costs its credibility on every later one. Telegram leaves no
    tombstone for a deleted message, so these counts are the only signal there
    is that anything happened at all.
    """
    if result.total == 0 and quarantined == 0:
        return "No images to remove."

    parts: list[str] = []
    if quarantined:
        parts.append(
            f"{quarantined} image(s) moved off my disk (held 30 days, then deleted)."
        )
    if result.removed:
        parts.append(f"{result.removed} removed from this chat.")
    if result.retained:
        parts.append(
            f"{result.retained} left here — Telegram only lets me delete messages "
            "under 48 hours old. You can remove those yourself by long-pressing them."
        )
    if result.failed:
        parts.append(f"{result.failed} I couldn't remove from the chat.")
    return " ".join(parts)
