"""Extract a session's facts when the session ENDS, not only on the interval.

🔴 THE DEFECT. The extraction gate is `user_turns % MEMORY_FACT_EXTRACTION_INTERVAL
== 0`, and production runs an interval of 10. A session that never reaches 10
user turns therefore never extracts anything, and its facts are lost the moment
the rows go.

Measured 2026-10-23 against the live store: of 292 real sessions with at least
one user turn, **287 (98.3%) never reach 10 user turns**. Median 1, mean 2.0.
Only five ever could have fired, and two of those are the same July Telegram
conversation — which is also why the historical `memory_facts` rows describe
the conversation rather than the operator: the rare firings caught arbitrary
windows. A fresh 3-fact, 3-user-turn session was measured storing 0 of 3 in
SQLite and 0 of 3 in the graph while she engaged with all three facts
conversationally.

⚠️ LOWERING THE INTERVAL IS NOT THE FIX. `OLLAMA_NUM_PARALLEL=1`, so a
background extraction is SERIALISED against her generation rather than
overlapped. Halving the interval doubles GPU contention with her replies, and
extraction costs a measured median 5.54 s. The fix is a trigger at the end of
the conversation, which did not exist.

⚠️ AND NOBODY UPSTREAM HAS SHIPPED IT. Cognee PR #5352 named this failure mode
almost verbatim — "those turns are written to the session but never bridged...
the debounce has no timer, so they wait for a next call that may never come" —
and was CLOSED UNMERGED. Mem0's core has no end-of-conversation hook at all;
Letta's compaction is an explicit developer API call; Graphiti's
`summarize_saga` is separate and manual; LangMem's debounce CANCELS AND
DISCARDS the pending payload rather than flushing it. There was nothing to
borrow, which is why the correctness argument is written out in
`fact_window`'s docstring rather than cited.

=== WHAT THIS DOES, AND WHAT IT DELIBERATELY DOES NOT ===

It builds one `FactJob` with `use_cursor=True` and hands it to the existing
worker. That is all. In particular:

  * NO GRAPH READ ON THE REQUEST PATH. The cursor is read by the handler, on
    the worker thread, at execution time — because that is the only place the
    compare-and-advance is serialised. The request path does one SQLite read
    and one in-memory `enqueue()`.
  * NO EXTRACTION ON THE REQUEST PATH. `/reset` must stay fast, and under
    OLLAMA_NUM_PARALLEL=1 an inline extraction would block whatever she is
    saying next.
  * `FactJob.messages` carries the rows BY VALUE, which is why "flush before
    the delete" is sufficient. The ordering requirement is against the READ,
    not a race with the worker: once the job is built, the rows can go.

=== WHY IT RETURNS A STATUS STRING ===

Every outcome is named and returned rather than logged-and-forgotten, so a
caller can report a name instead of silence. A flush that quietly did nothing
is indistinguishable from a flush that worked, and this repo has already
recorded the cost of that shape twice: a scheduled job judged by its successes
instead of its runs, and an alert that stayed silent on the one case its own
docstring claimed to cover.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Returned to callers so a route can report a name, never silence.
ENQUEUED = "enqueued"
DISABLED = "disabled"
NO_WORKER = "no_worker"
NO_OPERATOR = "no_operator"
NO_MESSAGES = "no_messages"
QUEUE_FULL = "queue_full"
FAILED = "failed"

#: A destructive trigger cannot be retried: the rows are about to vanish, so a
#: dropped job is permanent loss rather than deferred work. These are logged
#: loudly; a `sweep` drop is harmless because the cursor did not move.
_DESTRUCTIVE = {"reset", "delete"}


def flush_session_facts(session_id: str, *, reason: str) -> str:
    """Enqueue a cursor-bounded extraction for `session_id`. Returns a status.

    `reason` is one of "reset", "delete" or "sweep" and reaches the worker's
    per-job log line, so production can answer "did this session's facts land,
    and what made it happen" without a new dashboard.
    """
    from .. import startup
    from ..config import get_settings
    from .operator_fact_worker import FactJob

    if not session_id:
        return NO_MESSAGES

    try:
        mem = get_settings().memory
        if not mem.flush_enabled:
            return DISABLED

        # `facts_from_graph=False` leaves no worker, which is also the correct
        # no-op: without the graph there is no cursor to advance.
        worker = startup.get_operator_fact_worker()
        if worker is None:
            return NO_WORKER

        operator_id = _resolve_operator(session_id)
        if not operator_id:
            logger.info(
                "[SessionFlush] session %s (%s): no linked operator — nothing "
                "to attribute facts to", session_id[:8], reason,
            )
            return NO_OPERATOR

        messages = startup.get_message_repo().get_messages_by_session(session_id) or []
        if not any(m.get("role") == "user" for m in messages):
            return NO_MESSAGES

        last = messages[-1]
        job = FactJob(
            user_id=operator_id,
            session_id=session_id,
            persona_key=_persona_of(session_id),
            messages=list(messages),
            use_cursor=True,
            cursor_to=str(last.get("timestamp") or ""),
            cursor_message_id=str(last.get("id") or ""),
            reason=reason,
            session_started_at=str(messages[0].get("timestamp") or ""),
            session_message_count=len(messages),
        )

        if not worker.enqueue(job):
            # ⚠️ For a destructive reason this IS the loss: the rows go next.
            # Named rather than counted, because a number nobody reads is how
            # the last two silent-failure defects in this repo survived.
            level = logger.error if reason in _DESTRUCTIVE else logger.warning
            level(
                "[SessionFlush] session %s (%s): worker queue FULL — "
                "%d message(s) %s", session_id[:8], reason, len(messages),
                "will be DELETED unextracted" if reason in _DESTRUCTIVE
                else "deferred to the next sweep (the cursor did not move)",
            )
            return QUEUE_FULL

        logger.info(
            "[SessionFlush] session %s (%s): enqueued %d message(s), "
            "%d user turn(s)", session_id[:8], reason, len(messages),
            sum(1 for m in messages if m.get("role") == "user"),
        )
        return ENQUEUED

    except Exception as e:  # noqa: BLE001
        # A flush must never break the route it hangs off. /reset deleting
        # messages matters more than extracting them.
        logger.warning(
            "[SessionFlush] session %s (%s) failed (non-fatal): %s",
            session_id[:8], reason, e,
        )
        return FAILED


def _resolve_operator(session_id: str) -> str:
    """Who the facts belong to.

    The pinned single-operator id first, because that is what production runs
    (`SINGLE_OPERATOR_USER_ID=user_be5d9dbe`) and it is also what the read path
    uses — resolving the two differently is how a fact gets written where
    nothing reads it. The `user_sessions` mapping is the fallback for a
    multi-operator future.
    """
    from .. import startup
    from ..config import get_settings

    pinned = (get_settings().memory.single_operator_user_id or "").strip()
    if pinned:
        return pinned
    try:
        # `get_session_user`, from `user_profile_repository.py:190`. The name
        # was verified against the repository rather than guessed: the first
        # version of this called `get_user_id_for_session`, which does not
        # exist, behind a `getattr(...)` that made the whole fallback dead code
        # while looking defensive. Never assert a method you did not read.
        repo = startup.get_user_profile_repo()
        return (repo.get_session_user(session_id) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _persona_of(session_id: str) -> str:
    from .. import startup

    try:
        row = startup.get_session_repo().get_session(session_id) or {}
        return str(row.get("persona_key") or "")
    except Exception:  # noqa: BLE001
        return ""
