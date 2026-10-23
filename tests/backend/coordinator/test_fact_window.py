"""The cursor must tile the conversation with no gap, and fail by repeating.

Pure functions, no graph, no worker, no model. The properties here are the
whole reason the flush is cursor-based rather than window-based, so they are
asserted directly rather than inferred from an integration run.

Surveyed 2026-10-23: every memory system that re-extracts and dedups has
shipped duplicate-data bugs (Mem0 #6515 TOCTOU, Graphiti #1872, #1734); the two
that keep a true watermark (Cognee `pipeline_status`, Letta sleeptime
`last_processed_message_id`) have not. And Cognee PR #5352 described our exact
failure mode -- a short session whose turns "wait for a next call that may never
come" -- then closed unmerged, so there is no upstream implementation to copy
and these properties have to be pinned locally.
"""

from __future__ import annotations

from coordinator.services.fact_window import (
    cap_window_from_oldest,
    count_user_turns,
    window_since_cursor,
    window_since_last_firing,
)


def _convo(turns: int, *, bubbles=lambda t: (t % 3) + 1, start=0):
    """A conversation with multi-message assistant replies, second-resolution
    timestamps, and ids -- the shape production actually produces."""
    msgs, n = [], start
    for t in range(1, turns + 1):
        n += 1
        msgs.append({"id": f"m{n}", "role": "user", "content": f"turn {t}",
                     "timestamp": f"2026-10-23T12:{n // 60:02d}:{n % 60:02d}"})
        for _ in range(bubbles(t)):
            n += 1
            msgs.append({"id": f"m{n}", "role": "assistant", "content": "b",
                         "timestamp": f"2026-10-23T12:{n // 60:02d}:{n % 60:02d}"})
    return msgs


class TestTheShortSessionGap:
    """The measured defect: 98.3% of real sessions never reach the interval."""

    def test_a_three_turn_session_never_reaches_an_interval_of_ten(self):
        # The gate is `user_turns % interval == 0`, and user_turns counts user
        # messages plus this turn. A 3-turn session yields 1, 2, 3 -- never 0 mod 10.
        msgs = _convo(3)
        assert count_user_turns(msgs) == 3
        assert all((t + 1) % 10 != 0 for t in range(3)), (
            "if this ever passes, the gate alone would cover short sessions "
            "and the flush would be unnecessary")

    def test_the_cursor_window_takes_everything_when_never_extracted(self):
        msgs = _convo(3)
        window, to_ts, to_id, has_new = window_since_cursor(msgs, None)
        assert window == msgs
        assert has_new is True
        assert to_ts == msgs[-1]["timestamp"]
        assert to_id == msgs[-1]["id"]
        assert count_user_turns(window) == 3, (
            "all three stated facts must be inside the flush window -- the "
            "measured baseline stored 0 of 3")


class TestTilingHasNoGap:
    def test_consecutive_successful_flushes_cover_every_user_turn(self):
        """The property that matters: a fact stated inside a gap is lost forever."""
        msgs = _convo(40)
        covered, cursor = set(), None
        # Flush after every 7th user turn -- deliberately not a divisor of
        # anything, so boundaries land mid-bubble.
        for upto in range(7, 41, 7):
            seen, cut = 0, len(msgs)
            for i, m in enumerate(msgs):
                if m["role"] == "user":
                    seen += 1
                    if seen > upto:
                        cut = i
                        break
            window, to_ts, _to_id, has_new = window_since_cursor(msgs[:cut], cursor)
            assert has_new
            covered |= {m["id"] for m in window if m["role"] == "user"}
            cursor = to_ts
        # final flush for the tail
        window, _ts, _id, _new = window_since_cursor(msgs, cursor)
        covered |= {m["id"] for m in window if m["role"] == "user"}

        every_user = {m["id"] for m in msgs if m["role"] == "user"}
        assert covered == every_user, (
            f"{len(every_user - covered)} user turns were never in any window: "
            f"{sorted(every_user - covered)[:5]}")

    def test_a_failed_flush_re_covers_rather_than_gapping(self):
        """The cursor advances only after a SUCCESSFUL write, so a dropped job
        costs repeated work and never a hole. This is the whole argument for a
        cursor over window+dedup."""
        msgs = _convo(20)
        cursor = None

        # Flush 1 succeeds over the first 5 user turns.
        cut = [i for i, m in enumerate(msgs) if m["role"] == "user"][5]
        w1, ts1, _id, _new = window_since_cursor(msgs[:cut], cursor)
        cursor = ts1
        covered = {m["id"] for m in w1 if m["role"] == "user"}

        # Flush 2 FAILS (queue full / extractor raised / graph down): the cursor
        # is NOT advanced.
        cut2 = [i for i, m in enumerate(msgs) if m["role"] == "user"][12]
        w2, _ts2, _id2, _n2 = window_since_cursor(msgs[:cut2], cursor)
        assert w2, "the failed window was non-empty"
        # cursor deliberately unchanged

        # Flush 3 succeeds and must re-cover everything flush 2 would have.
        w3, _ts3, _id3, _n3 = window_since_cursor(msgs, cursor)
        covered |= {m["id"] for m in w3 if m["role"] == "user"}

        every_user = {m["id"] for m in msgs if m["role"] == "user"}
        assert covered == every_user, (
            "a failed flush produced a permanent gap -- the cursor must only "
            "advance after a successful write")

    def test_overlap_between_consecutive_windows_is_only_the_boundary_second(self):
        msgs = _convo(12)
        cut = [i for i, m in enumerate(msgs) if m["role"] == "user"][6]
        w1, ts1, _i, _n = window_since_cursor(msgs[:cut], None)
        w2, _ts2, _i2, _n2 = window_since_cursor(msgs, ts1)
        overlap = {m["id"] for m in w1} & {m["id"] for m in w2}
        assert overlap, "closed intervals must share their endpoint"
        stamps = {m["timestamp"] for m in msgs if m["id"] in overlap}
        assert stamps == {ts1}, (
            f"overlap spans more than the boundary second: {sorted(stamps)}")


class TestSecondPrecisionTies:
    """`BaseRepository._now()` is isoformat(timespec="seconds") and she replies
    in bubbles, so several messages sharing one second is routine. A strict `>`
    cursor drops a same-second sibling silently."""

    def test_same_second_siblings_are_never_dropped_across_a_boundary(self):
        same = "2026-10-23T12:00:05"
        msgs = [
            {"id": "a", "role": "user", "content": "first", "timestamp": "2026-10-23T12:00:04"},
            {"id": "b", "role": "user", "content": "my dog is called Wickham", "timestamp": same},
            {"id": "c", "role": "assistant", "content": "r1", "timestamp": same},
            {"id": "d", "role": "user", "content": "and my cat is Pemberton", "timestamp": same},
        ]
        # A flush lands with its cursor exactly on the shared second.
        w1, ts1, _i, _n = window_since_cursor(msgs[:3], None)
        assert ts1 == same
        w2, _ts, _i2, _n2 = window_since_cursor(msgs, ts1)
        ids = {m["id"] for m in w1} | {m["id"] for m in w2}
        assert ids == {"a", "b", "c", "d"}, (
            f"a same-second message was dropped: missing {{'a','b','c','d'}} - {ids}")
        assert "d" in {m["id"] for m in w2}, (
            "the sibling written in the boundary second must be re-included; a "
            "strict `>` cursor loses it")

    def test_has_new_is_false_when_nothing_arrived_after_the_cursor(self):
        msgs = _convo(4)
        last = msgs[-1]["timestamp"]
        window, _ts, _id, has_new = window_since_cursor(msgs, last)
        assert has_new is False, (
            "re-extracting the boundary second would burn an LLM call to "
            "rediscover stored facts, and under OLLAMA_NUM_PARALLEL=1 that "
            "makes her slower to answer")
        assert window, "the boundary second is still returned, by design"


class TestTheCapTrimsFromTheOldestEnd:
    """The cursor advances to the last message of what is returned, so trimming
    the NEWEST end would move it past turns never sent to the extractor."""

    def test_the_cap_keeps_the_oldest_turns_and_reports_the_remainder(self):
        msgs = _convo(10, bubbles=lambda t: 1)
        capped, remaining = cap_window_from_oldest(msgs, 4)
        assert count_user_turns(capped) == 4
        assert remaining == 6
        assert capped[0]["id"] == msgs[0]["id"], "the cap must keep the OLDEST end"

    def test_an_uncapped_window_is_returned_whole(self):
        msgs = _convo(3)
        capped, remaining = cap_window_from_oldest(msgs, 20)
        assert capped == msgs
        assert remaining == 0

    def test_zero_means_no_cap(self):
        msgs = _convo(5)
        capped, remaining = cap_window_from_oldest(msgs, 0)
        assert capped == msgs and remaining == 0


class TestTheIntervalWindowIsUnchanged:
    """`window_since_last_firing` moved module-ward verbatim. These are the
    properties the old nested copy had, re-asserted against the IMPORT so the
    suite stops testing a private duplicate of production."""

    def test_the_window_covers_the_whole_interval(self):
        msgs = _convo(12, bubbles=lambda t: 5)
        w = window_since_last_firing(msgs, 10)
        assert count_user_turns(w) == 10

    def test_the_old_fixed_tail_was_smaller_than_the_interval(self):
        msgs = _convo(11)
        assert count_user_turns(msgs[-20:]) < 10, (
            "the historical [-20:] tail held fewer than 10 user turns, which is "
            "why facts scrolled out before the gate fired")

    def test_a_short_conversation_takes_everything(self):
        msgs = _convo(3)
        assert window_since_last_firing(msgs, 10) == msgs

    def test_no_user_turn_is_skipped_across_firings(self):
        msgs = _convo(30, bubbles=lambda t: (t % 4) + 1)
        covered = set()
        uidx = [i for i, m in enumerate(msgs) if m["role"] == "user"]
        for n in range(10, 31, 10):
            cut = uidx[n] if n < len(uidx) else len(msgs)
            w = window_since_last_firing(msgs[:cut], 10)
            covered |= {m["id"] for m in w if m["role"] == "user"}
        covered |= {m["id"] for m in window_since_last_firing(msgs, 10)
                    if m["role"] == "user"}
        assert covered == {m["id"] for m in msgs if m["role"] == "user"}
