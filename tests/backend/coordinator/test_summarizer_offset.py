"""Regression tests for the post-reset summarizer offset.

``_check_and_summarize`` derives "how many messages are not yet summarized"
from ``summary_count * interval``. If summaries ever outnumber the messages
that remain — the state a reset used to leave behind, and which a summary
written concurrently with a reset can still produce — that arithmetic goes
negative and summarization silently stops until the message count climbs back
past the stale offset. Nothing raises; the feature just quietly stops working.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from src.coordinator.services.chat_session_service import _check_and_summarize

INTERVAL = 30


def _deps(*, message_count: int, summary_count: int):
    message_repo = MagicMock()
    message_repo.get_messages_by_session.return_value = [
        {"role": "user", "content": f"m{i}"} for i in range(message_count)
    ]
    summary_repo = MagicMock()
    summary_repo.count_summaries.return_value = summary_count

    summarizer = MagicMock()
    summarizer.llm_client = object()  # already wired; skip the lazy-init branch
    summarizer.summarize_segment.return_value = "a summary"

    return {
        "message_repo": message_repo,
        "summary_repo": summary_repo,
        "conversation_summarizer": summarizer,
    }


def _run(deps):
    cfg = MagicMock()
    cfg.memory.summarization_interval = INTERVAL
    # _check_and_summarize wraps its whole body in `except Exception` and only
    # logs, so an unstubbed collaborator would be indistinguishable from
    # "summarization was not due". Stub the card lookup explicitly.
    with (
        patch("src.coordinator.services.chat_session_service.get_settings", return_value=cfg),
        patch(
            "src.coordinator.services.chat_session_service.get_persona_card",
            return_value={"display_name": "Gwen"},
        ),
    ):
        _check_and_summarize("sess-1", "gwen", deps)


def test_stale_summary_count_does_not_disable_summarization():
    """The post-reset state: 2 summaries claim 60 messages, 12 actually exist.

    Before the clamp this yielded ``12 - 60 = -48``, so no summary could be
    written until the session passed 90 messages. After the clamp the offset
    floors at the real message count and the feature simply waits for the next
    full interval, as it would in a fresh session.
    """
    deps = _deps(message_count=12, summary_count=2)
    _run(deps)
    # 12 messages is under one interval, so nothing to summarize yet — but the
    # important part is that it did not compute a negative backlog.
    deps["conversation_summarizer"].summarize_segment.assert_not_called()


def test_stale_offset_is_reported_not_silent(caplog):
    """The clamp does NOT restore summarization sooner — the backlog still has
    to climb past the stale offset. What it does is stop the state being
    invisible: the old code computed a negative backlog and said nothing, so a
    permanently-disabled summarizer looked exactly like a quiet session.

    The real repair for this state is clearing summaries on reset (see
    ``routes/sessions.py::_clear_derived_session_state``); this is the
    defence-in-depth for a summary racing a reset.
    """
    deps = _deps(message_count=12, summary_count=2)
    with caplog.at_level("WARNING"):
        _run(deps)
    assert any("Stale summary count" in r.message for r in caplog.records)
    deps["conversation_summarizer"].summarize_segment.assert_not_called()


def test_start_index_stays_in_bounds_under_a_stale_count():
    """With the offset clamped, the slice handed to the summarizer can never
    start past the end of the message list (which would silently summarize an
    empty segment)."""
    deps = _deps(message_count=INTERVAL * 3, summary_count=2)
    _run(deps)
    call = deps["conversation_summarizer"].summarize_segment.call_args
    assert call is not None
    assert len(call.kwargs["messages"]) == INTERVAL


def test_normal_progression_is_unchanged():
    """The ordinary case must behave exactly as before the clamp."""
    deps = _deps(message_count=INTERVAL * 2, summary_count=1)
    _run(deps)
    deps["conversation_summarizer"].summarize_segment.assert_called_once()


def test_no_summary_before_first_interval():
    deps = _deps(message_count=INTERVAL - 1, summary_count=0)
    _run(deps)
    deps["conversation_summarizer"].summarize_segment.assert_not_called()
