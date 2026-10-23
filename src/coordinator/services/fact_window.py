"""Which messages an extraction run is responsible for.

Two window functions, one per trigger shape, kept together because the property
that matters is a relationship BETWEEN them: consecutive runs must tile the
conversation with no gap, and a fact stated inside a gap is lost forever.

ONE DEFINITION, IMPORTED BY EVERYONE. Both extraction gates in
`chat_session_service` used to carry their own copy of the interval window, and
the test suite carried a THIRD copy it exercised instead of production. Two of
the three were wrong at different times. The copies are gone; this is the only
one.

=== WHY A CURSOR AND NOT A SLIDING WINDOW ===

`window_since_last_firing` is correct for a gate that fires on a fixed cadence
and never misses. It is wrong for a flush, because it answers "the last N user
turns" rather than "everything not yet extracted", and those differ precisely
when a firing was dropped.

Surveyed 2026-10-23, the production memory systems split cleanly:

  * re-extract everything + post-hoc dedup -- Mem0, Graphiti, LangMem. All
    three have shipped confirmed duplicate-data bugs: Mem0 #6515 (a TOCTOU race
    where the dedup snapshot is stale by the time the hash check runs, so
    concurrent adds both insert), Graphiti #1872 (duplicate edges for one fact),
    Graphiti #1734 (identically-named nodes unmerged because candidate retrieval
    is embedding-similarity-based and misses exact string matches below the
    cosine threshold).
  * a true watermark -- Cognee (`pipeline_status` per row,
    `AUTO_IMPROVE_WATERMARK`) and Letta's sleeptime agent
    (`Group.last_processed_message_id`, a persisted column advanced atomically).
    Neither has that class of bug reported.

So the cursor is not a preference. It is the design that the one systematic
difference in the field points at.

⚠️ And nobody has shipped the fix for OUR failure mode. Cognee PR #5352 named
it almost verbatim -- "the debounce has no timer, so they wait for a next call
that may never come" -- and was CLOSED UNMERGED. There is no upstream
implementation to borrow, which is why the tiling argument is written out in
full below rather than cited.

=== WHY THE CURSOR WINDOW IS CLOSED AT BOTH ENDS (>=, NOT >) ===

`BaseRepository._now()` is `isoformat(timespec="seconds")` -- SECOND precision.
She replies in multi-message bubbles, and `get_messages_by_session` orders by
`timestamp ASC` with no secondary key, so several messages sharing one second is
routine rather than exotic. A strict `>` cursor drops a same-second sibling,
silently and unrepeatably.

`>=` re-includes exactly the boundary second. The overlap is one bubble group,
and facts re-extracted from it are already in `current_facts`, where the
writer's `_norm` exact-containment dedup drops them. Bounded repeated work in
exchange for no silent loss, which is the trade the evidence above demands.

=== THE TILING ARGUMENT ===

Let `c0` be the epoch (no cursor yet) and `ci` the timestamp written after the
i-th SUCCESSFULLY COMPLETED extraction. Window `Wi = {m : c(i-1) <= ts(m) <= ci}`.

  * No gap. Consecutive windows are closed intervals sharing an endpoint, so
    their union is `{m : ts(m) <= cn}`. Every message at or below the cursor has
    been inside at least one window -- because `ci` is only ever set to the
    timestamp of a message that was IN `Wi`. A message above `cn` is in the next
    window.
  * No loss on failure. `ci` is written only after the handler completes without
    raising. A full queue (`enqueue()` returns False), a process death with a
    job queued, a raising extractor, or an unavailable graph repo all leave the
    cursor at `c(i-1)`, so the next window restarts there. The failure mode is
    REPEATED WORK, never a hole.
  * Bounded duplication. Overlap between consecutive windows is exactly the
    messages sharing timestamp `ci` -- one bubble group.
  * Concurrent triggers cannot double-write. The worker is a single daemon
    thread, so overlapping jobs execute sequentially, and the handler re-reads
    the stored cursor at EXECUTION time and skips any job whose candidate is
    already covered. That compare-and-advance has to live at the single
    consumer, not at the enqueue site -- the enqueue site is exactly where
    Mem0 #6515's race lives.

=== AND WHY THERE IS NO SQLITE FALLBACK CURSOR ===

A high-water mark must live in the SAME STORE as the write it authorises, or it
can advance past a window that was never persisted. The facts' system of record
is the graph, so the cursor belongs on the graph.

This is deliberately UNLIKE `chat_session_service._operator_facts`, which falls
back to SQLite when the graph is unavailable. That is a READ path, where a stale
answer beats a failed turn. This is a WRITE path, where a stale cursor beats a
lost fact: if Neo4j is down the fact write does not happen, so a SQLite cursor
would advance over an unwritten window and convert a transient outage into
permanent loss. The correct degradation is NO ADVANCE -- re-coverage next time.
"""

from __future__ import annotations

from typing import Any


def count_user_turns(msgs: list[dict[str, Any]]) -> int:
    """User messages in `msgs`.

    The gate counts USER messages rather than rows because she replies in
    multi-message bubbles: counting every row advances the counter by 2 or 3
    unpredictably, so hitting `% interval == 0` becomes a matter of luck. That
    defect made extraction close to non-functional for the life of the feature.
    """
    return sum(1 for m in msgs if m.get("role") == "user")


def window_since_last_firing(msgs: list, interval: int) -> list:
    """Every message since the Nth-from-last USER turn.

    🔴 ONE DEFINITION FOR BOTH EXTRACTION PATHS. A fixed `[-20:]` tail
    is SMALLER than an interval of 10, because she replies in
    multi-message bubbles — 20 messages is about 7 user turns. Measured
    on production: the gate fired correctly and extraction still
    returned `0 facts`, because the stated facts had scrolled out of the
    window before the firing.

    Consecutive firings tile the conversation with no gap, which is the
    property that matters: a fact stated inside a gap is lost forever.
    """
    uidx = [i for i, m in enumerate(msgs) if m.get("role") == "user"]
    start = uidx[-interval] if len(uidx) > interval else 0
    return msgs[start:]


def window_since_cursor(
    msgs: list[dict[str, Any]],
    cursor_at: str | None,
) -> tuple[list[dict[str, Any]], str, str, bool]:
    """Everything at or after `cursor_at`, plus where the cursor would move to.

    Returns `(window, advance_to_ts, advance_to_id, has_new)`.

    `has_new` is the one the caller must branch on: it is True only when some
    message is STRICTLY after the cursor. A window can be non-empty while
    `has_new` is False -- that is the boundary second being re-included -- and
    extracting it again would burn an LLM call to rediscover facts already
    stored. Under `OLLAMA_NUM_PARALLEL=1` an extraction is serialised against
    her generation, so a pointless call is not free: it makes her slower to
    answer.

    `cursor_at` of None or "" means "never extracted", and the window is
    everything.
    """
    if not msgs:
        return [], "", "", False

    if not cursor_at:
        last = msgs[-1]
        return list(msgs), str(last.get("timestamp") or ""), str(last.get("id") or ""), True

    window = [m for m in msgs if str(m.get("timestamp") or "") >= cursor_at]
    has_new = any(str(m.get("timestamp") or "") > cursor_at for m in msgs)
    if not window:
        return [], "", "", has_new

    last = window[-1]
    return window, str(last.get("timestamp") or ""), str(last.get("id") or ""), has_new


def cap_window_from_oldest(
    msgs: list[dict[str, Any]],
    max_user_turns: int,
) -> tuple[list[dict[str, Any]], int]:
    """The oldest `max_user_turns` user turns of `msgs`, and what is left over.

    CAPPED FROM THE OLDEST END, DELIBERATELY. The cursor advances to the last
    message of whatever is returned here, so trimming the NEWEST end would move
    the cursor past turns that were never sent to the extractor -- a gap, which
    is the one failure this whole module exists to prevent. Trimming the oldest
    end instead means the cursor advances only over what was actually covered,
    and the remainder is picked up by the next trigger.
    """
    if max_user_turns <= 0:
        return list(msgs), 0

    seen, cut = 0, len(msgs)
    for i, m in enumerate(msgs):
        if m.get("role") == "user":
            seen += 1
            if seen > max_user_turns:
                cut = i
                break
    if cut >= len(msgs):
        return list(msgs), 0

    remaining = count_user_turns(msgs[cut:])
    return list(msgs[:cut]), remaining
