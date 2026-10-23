"""The session-end flush: the service, and the ORDER it must be called in.

The ordering tests are the load-bearing ones. `/reset` deletes the messages the
flush needs to read, so a flush placed after the delete reads an empty
transcript and reports success — a silent no-op that looks exactly like a
working flush. An order pin catches that; a behavioural test with fakes does
not, because the fake message repo keeps returning rows.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest


# --------------------------------------------------------------------------- #
# ORDER PINS — these fail until the routes call the flush.
# --------------------------------------------------------------------------- #
def _first_lineno(fn, call_name: str):
    """Line of the first call to `call_name` inside `fn`, or None."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else (
            f.attr if isinstance(f, ast.Attribute) else "")
        if name == call_name:
            return node.lineno
    return None


class TestTheFlushRunsBeforeTheRowsGo:
    """🔴 THE ORDERING HAZARD, MADE CHECKABLE.

    `FactJob.messages` carries the rows by value, so once the job is built the
    rows can be deleted safely. The requirement is therefore against the READ,
    not a race with the worker — but a flush placed after the delete still
    reads nothing and still returns "enqueued", so the failure is silent.
    """

    def test_reset_flushes_before_deleting_the_messages(self):
        from coordinator.routes import sessions as S

        flush = _first_lineno(S.clear_session_messages, "flush_session_facts")
        delete = _first_lineno(S.clear_session_messages, "delete_messages_by_session")
        assert flush is not None, (
            "clear_session_messages (/reset) does not flush. A session shorter "
            "than the extraction interval loses every fact stated in it, and "
            "98.3% of real sessions are shorter than the interval.")
        assert delete is not None, "the delete call moved; this pin is now blind"
        assert flush < delete, (
            f"the flush is at line {flush} and the delete at {delete} — it "
            "reads an already-emptied transcript and reports success")

    def test_delete_session_flushes_before_the_cascade(self):
        from coordinator.routes import sessions as S

        flush = _first_lineno(S.delete_session, "flush_session_facts")
        delete = _first_lineno(S.delete_session, "delete_session")
        assert flush is not None, (
            "delete_session does not flush; deleting a session silently "
            "discards every fact stated in it")
        assert delete is not None, "the repo delete call moved; this pin is blind"
        assert flush < delete, (
            f"the flush is at line {flush} and the cascade at {delete}")

    def test_the_cursor_is_not_rewound_by_a_reset(self):
        """A reset must NOT clear the cursor.

        It is monotonic and rewinding it can only re-cover messages that no
        longer exist (a no-op) or, if rows reappear through `/sessions/import`,
        cause a double write. `_clear_derived_session_state` deliberately does
        not touch it.
        """
        from coordinator.routes import sessions as S

        src = inspect.getsource(S._clear_derived_session_state)
        for forbidden in ("fact_cursor", "advance_fact_cursor"):
            assert forbidden not in src, (
                f"_clear_derived_session_state touches {forbidden}; the cursor "
                "is monotonic and must never be rewound")


# --------------------------------------------------------------------------- #
# THE SERVICE — fakes only. No graph, no worker thread, no model.
# --------------------------------------------------------------------------- #
class _Worker:
    def __init__(self, accept=True):
        self.jobs = []
        self._accept = accept

    def enqueue(self, job):
        if not self._accept:
            return False
        self.jobs.append(job)
        return True


class _Messages:
    def __init__(self, rows):
        self._rows = rows

    def get_messages_by_session(self, session_id):
        return list(self._rows)


class _Sessions:
    def get_session(self, session_id):
        return {"id": session_id, "persona_key": "gwen"}


class _Profiles:
    """`get_session_user` is the REAL method name on
    `UserProfileRepository` (user_profile_repository.py:190). The first version
    of the service called `get_user_id_for_session`, which does not exist, and
    hid it behind a `getattr` — so the fallback was dead code that looked
    defensive. This fake is spelled from the repository, not from memory."""

    def __init__(self, user_id="op-from-mapping"):
        self._uid = user_id

    def get_session_user(self, session_id):
        return self._uid


def _rows(n_user=3):
    out = []
    for i in range(1, n_user + 1):
        out.append({"id": f"u{i}", "role": "user", "content": f"fact {i}",
                    "timestamp": f"2026-10-23T12:00:{i:02d}"})
        out.append({"id": f"a{i}", "role": "assistant", "content": "reply",
                    "timestamp": f"2026-10-23T12:00:{i:02d}"})
    return out


@pytest.fixture()
def wired(monkeypatch):
    """Patch the startup singletons the service resolves at call time."""
    from coordinator import startup
    from coordinator.services import session_flush_service as F

    state = {"worker": _Worker(), "rows": _rows(3)}

    monkeypatch.setattr(startup, "get_operator_fact_worker",
                        lambda: state["worker"], raising=False)
    monkeypatch.setattr(startup, "get_message_repo",
                        lambda: _Messages(state["rows"]), raising=False)
    monkeypatch.setattr(startup, "get_session_repo", lambda: _Sessions(),
                        raising=False)
    monkeypatch.setattr(startup, "get_user_profile_repo", lambda: _Profiles(),
                        raising=False)
    # Production pins SINGLE_OPERATOR_USER_ID; the suite does not load `.env`,
    # so pin it here or the service correctly reports `no_operator` and every
    # test below measures the wrong thing.
    from coordinator.config import get_settings
    monkeypatch.setattr(get_settings().memory, "single_operator_user_id",
                        "user_be5d9dbe", raising=False)
    return F, state


class TestTheServiceCoversTheMeasuredCase:
    def test_a_three_turn_session_enqueues_all_three_user_turns(self, wired):
        """The measured baseline: 3 facts in 3 user turns stored 0 of 3."""
        F, state = wired
        assert F.flush_session_facts("sess-short", reason="reset") == F.ENQUEUED
        job = state["worker"].jobs[0]
        assert sum(1 for m in job.messages if m["role"] == "user") == 3
        assert job.use_cursor is True
        assert job.reason == "reset"
        assert job.cursor_to == "2026-10-23T12:00:03"
        assert job.cursor_message_id == "a3"
        assert job.session_started_at == "2026-10-23T12:00:01"

    def test_a_single_turn_session_is_still_flushed(self, wired):
        """Median real session length is ONE user turn."""
        F, state = wired
        state["rows"] = _rows(1)
        assert F.flush_session_facts("sess-1", reason="sweep") == F.ENQUEUED
        assert len(state["worker"].jobs) == 1


class TestEveryOutcomeIsNamed:
    """A flush that quietly did nothing must be distinguishable from one that
    worked. This repo has twice paid for the opposite shape."""

    def test_no_worker(self, wired, monkeypatch):
        from coordinator import startup
        F, _s = wired
        monkeypatch.setattr(startup, "get_operator_fact_worker", lambda: None,
                            raising=False)
        assert F.flush_session_facts("s", reason="reset") == F.NO_WORKER

    def test_no_messages_when_no_user_turn(self, wired):
        F, state = wired
        state["rows"] = [{"id": "a1", "role": "assistant", "content": "hi",
                          "timestamp": "2026-10-23T12:00:01"}]
        assert F.flush_session_facts("s", reason="reset") == F.NO_MESSAGES

    def test_no_messages_on_an_empty_session(self, wired):
        F, state = wired
        state["rows"] = []
        assert F.flush_session_facts("s", reason="reset") == F.NO_MESSAGES

    def test_queue_full_is_reported_not_swallowed(self, wired):
        F, state = wired
        state["worker"] = _Worker(accept=False)
        assert F.flush_session_facts("s", reason="reset") == F.QUEUE_FULL

    def test_disabled_by_flag(self, wired, monkeypatch):
        from coordinator.config import get_settings
        F, _s = wired
        s = get_settings()
        monkeypatch.setattr(type(s.memory), "flush_enabled",
                            property(lambda self: False), raising=False)
        try:
            assert F.flush_session_facts("s", reason="reset") == F.DISABLED
        finally:
            pass

    def test_a_raising_dependency_never_breaks_the_route(self, wired, monkeypatch):
        from coordinator import startup
        F, _s = wired

        def _boom():
            raise RuntimeError("repo exploded")

        monkeypatch.setattr(startup, "get_message_repo", _boom, raising=False)
        assert F.flush_session_facts("s", reason="reset") == F.FAILED, (
            "/reset deleting messages matters more than extracting them; the "
            "flush must never break the route it hangs off")

    def test_an_empty_session_id_is_refused(self, wired):
        F, _s = wired
        assert F.flush_session_facts("", reason="reset") == F.NO_MESSAGES


class TestTheOperatorIsResolvedTheSameWayTheReadPathDoes:
    def test_the_pinned_single_operator_is_used(self, wired):
        F, state = wired
        F.flush_session_facts("s", reason="reset")
        assert state["worker"].jobs[0].user_id == "user_be5d9dbe", (
            "resolving the operator differently from the read path is how a "
            "fact gets written where nothing reads it")
