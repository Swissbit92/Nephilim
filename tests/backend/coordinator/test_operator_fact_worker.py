"""Extraction runs OFF the request thread, and the writer records provenance.

🔴 WHY ASYNC, AND WHY THAT DEPARTS FROM THE RESEARCH. Extraction costs a
MEASURED median 5.54 s on this box (5 reps, 5.26-6.11 s, live model, live
transcript) and it ran inline, on the request thread, where the user waits for
it. The provenance research recommends a SYNCHRONOUS write on the strength of
Mem0's published p95 of 1.44 s — this box measures 4x that, and the standing
rule here is to replay a borrowed constant against local data before trusting
it (a borrowed threshold once scored 31% where a fitted one scored 57%). Zep's
documented hours-long staleness came from bounded CONCURRENT ingestion at
scale, which one job on one thread does not have.

The pattern itself is well precedented — Zep, ECHO (arXiv 2608.21755) and CoMIC
(arXiv 2606.00756) all split cheap inline capture from async consolidation.
"""
from __future__ import annotations

import threading
import time

import pytest

from coordinator.services.operator_fact_worker import FactJob, OperatorFactWorker


def _job(**kw):
    return FactJob(user_id=kw.get("user_id", "_u"), session_id=kw.get("session_id", "s1"),
                   persona_key="gwen", messages=kw.get("messages", [{"role": "user"}]))


class TestTheWorkerLifecycle:
    def test_jobs_are_processed_off_the_calling_thread(self):
        seen, caller = [], threading.get_ident()
        done = threading.Event()

        def handler(job):
            seen.append(threading.get_ident())
            done.set()
            return 1

        w = OperatorFactWorker(handler)
        w.start()
        try:
            assert w.enqueue(_job()) is True
            assert done.wait(5), "the worker never ran the job"
        finally:
            w.stop()
        assert seen and seen[0] != caller, (
            "the whole point is that this does NOT run on the request thread"
        )

    def test_stop_drains_and_joins(self):
        w = OperatorFactWorker(lambda job: 0)
        w.start()
        w.stop()
        assert w._thread is None

    def test_stop_is_safe_before_start_and_twice(self):
        w = OperatorFactWorker(lambda job: 0)
        w.stop()          # never started
        w.start()
        w.stop()
        w.stop()          # already stopped

    def test_a_raising_handler_does_not_kill_the_thread(self):
        calls, done = [], threading.Event()

        def handler(job):
            calls.append(job.session_id)
            if job.session_id == "boom":
                raise RuntimeError("bad job")
            done.set()
            return 0

        w = OperatorFactWorker(handler)
        w.start()
        try:
            w.enqueue(_job(session_id="boom"))
            w.enqueue(_job(session_id="ok"))
            assert done.wait(5), "the thread died on the first bad job"
        finally:
            w.stop()
        assert calls == ["boom", "ok"]

    def test_a_full_queue_refuses_LOUDLY_rather_than_silently(self, caplog):
        """A silently dropped job is indistinguishable from a conversation that
        yielded no facts — the exact ambiguity this milestone exists to remove."""
        import logging

        block = threading.Event()
        w = OperatorFactWorker(lambda job: block.wait(10) or 0, max_queue=1)
        w.start()
        try:
            w.enqueue(_job(session_id="a"))   # taken by the thread, which blocks
            time.sleep(0.2)
            w.enqueue(_job(session_id="b"))   # fills the queue
            with caplog.at_level(logging.WARNING,
                                 logger="coordinator.services.operator_fact_worker"):
                accepted = w.enqueue(_job(session_id="c"))
            assert accepted is False
            assert any("FULL" in r.getMessage() for r in caplog.records), (
                "a dropped window must be logged at WARNING, not debug"
            )
        finally:
            block.set()
            w.stop()

    def test_enqueue_refuses_once_stopping(self):
        w = OperatorFactWorker(lambda job: 0)
        w.start()
        w.stop()
        assert w.enqueue(_job()) is False


class TestTheWriterRecordsProvenance:
    def _repo(self, existing=()):
        class _R:
            def __init__(self):
                self.added = []
                self._existing = [{"text": t} for t in existing]

            def current_facts(self, operator_id, limit=256):
                return list(self._existing)

            def add_fact(self, operator_id, text, *, asserted_by,
                         origin="learned", source_session_id=None):
                self.added.append((text, asserted_by, origin, source_session_id))
                return "fid"
        return _R()

    def _handler(self, repo, facts, background=()):
        from coordinator.services import operator_fact_writer as w

        class _FX:
            def extract_facts(self, messages, persona_key=None):
                return {"facts": list(facts), "background": list(background),
                        "topics": [], "preferences": {}, "holdings": {},
                        "user_name": None}

        class _Settings:
            class memory:
                facts_from_graph = True
            class ollama:
                temp_fact_extraction = 0.3

        import coordinator.fact_extractor as fe
        import coordinator.llm_client as lc
        orig_fx, orig_cl = fe.FactExtractor, lc.create_llm_client
        fe.FactExtractor = lambda *a, **k: _FX()
        lc.create_llm_client = lambda *a, **k: object()
        try:
            return w.make_handler(lambda: repo, lambda: _Settings)(
                _job(user_id="op", session_id="sess-9")
            )
        finally:
            fe.FactExtractor, lc.create_llm_client = orig_fx, orig_cl

    def test_asserted_by_is_user_and_never_a_model(self):
        """⚠️ WINDOW-LEVEL claim, not per-fact. `extract_facts` returns bare
        strings with no provenance, so a fact cannot be traced to a row. What
        makes "user" sound is that `_format_messages` physically REDACTS every
        non-user turn — so nothing else was there to derive from."""
        repo = self._repo()
        assert self._handler(repo, ["Ben climbs"]) == 1
        text, asserted_by, origin, sess = repo.added[0]
        assert asserted_by == "user"
        assert origin == "learned"
        assert sess == "sess-9", "the session must be recorded for cmp.cq02"

    def test_an_existing_fact_is_not_rewritten(self):
        """`add_fact` has NO dedup, so writing every N turns would duplicate
        forever."""
        repo = self._repo(existing=["Ben climbs"])
        assert self._handler(repo, ["Ben climbs"]) == 0
        assert repo.added == []

    def test_dedup_is_case_and_whitespace_insensitive(self):
        """⚠️ The SQLite WRITE side dedups case-SENSITIVELY while its READ side
        dedups case-INSENSITIVELY — they disagree, which is one source of the
        duplicates already in there. The graph must not inherit the mismatch."""
        repo = self._repo(existing=["Ben climbs"])
        assert self._handler(repo, ["  BEN   CLIMBS.  "]) == 0

    def test_dedup_is_exact_containment_not_a_similarity_threshold(self):
        """A paraphrase IS written — under-dedup is a visible cost, and there is
        no fitted threshold to borrow: Graphiti's constants are confirmed
        unfitted at TWO numbers now (0.6 cosine, Jaccard 0.9), and nobody
        publishes a fitting method for this problem class."""
        repo = self._repo(existing=["Ben climbs on tuesdays"])
        assert self._handler(repo, ["Ben goes bouldering on tuesday evenings"]) == 1

    def test_background_items_are_written_too(self):
        repo = self._repo()
        assert self._handler(repo, [], background=["Ben plays GTA"]) == 1

    def test_nothing_extracted_writes_nothing(self):
        repo = self._repo()
        assert self._handler(repo, []) == 0
        assert repo.added == []

    def test_a_missing_repository_is_loud_and_writes_nothing(self, caplog):
        import logging

        from coordinator.services import operator_fact_writer as w

        class _FX:
            def extract_facts(self, m, persona_key=None):
                return {"facts": ["x"], "background": []}

        class _S:
            class memory:
                facts_from_graph = True
            class ollama:
                temp_fact_extraction = 0.3

        import coordinator.fact_extractor as fe
        import coordinator.llm_client as lc
        orig_fx, orig_cl = fe.FactExtractor, lc.create_llm_client
        fe.FactExtractor = lambda *a, **k: _FX()
        lc.create_llm_client = lambda *a, **k: object()
        try:
            with caplog.at_level(logging.WARNING,
                                 logger="coordinator.services.operator_fact_writer"):
                n = w.make_handler(lambda: None, lambda: _S)(_job())
        finally:
            fe.FactExtractor, lc.create_llm_client = orig_fx, orig_cl
        assert n == 0
        assert any("no graph repository" in r.getMessage() for r in caplog.records)


class TestShutdownIsRegistered:
    def test_stop_has_a_caller_in_the_lifespan(self):
        """⚠️ The pre-existing FactExtractionWorker has the identical shape and
        its stop() has NO caller, so its queue is lost on every restart. That is
        a recorded gap and must not be repeated."""
        import inspect

        from coordinator import server

        src = inspect.getsource(server._shutdown)
        assert "get_operator_fact_worker" in src
        assert "worker.stop()" in src

    def test_it_stops_before_the_graph_driver_closes(self):
        """The shutdown block's own rule: producers of graph work stop BEFORE
        close_graph_driver(), or a draining job hits a closed driver."""
        import ast
        import inspect
        import textwrap

        from coordinator import server

        # 🔴 AST LINE NUMBERS, NOT STRING OFFSETS. The first version used
        # src.index() and failed — because "close_graph_driver()" appears twice
        # in the DOCSTRING before the real call, so index() found the prose.
        # Third prose-match mistake in this file alone; the AST has no comments
        # or docstrings to trip over.
        tree = ast.parse(textwrap.dedent(inspect.getsource(server._shutdown)))
        calls = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                name = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
                if name in ("stop", "close_graph_driver"):
                    calls.setdefault(name, n.lineno)
        assert "stop" in calls, "the worker's stop() is never called"
        assert "close_graph_driver" in calls
        assert calls["stop"] < calls["close_graph_driver"], (
            f"producers of graph work must stop first; stop at line "
            f"{calls['stop']}, close at {calls['close_graph_driver']}"
        )


class TestNoEarlyReturnInThePostTurnHook:
    """🔴 THE REGRESSION I ALMOST SHIPPED, THREE TIMES IN ONE SESSION.

    `_apply_post_turn_updates` does several unrelated things in sequence. When
    the worker takes over extraction the inline path must be SKIPPED — and the
    obvious way to write that is `return`, which looks right and silently
    switches off everything after it: `_track_nephilim_progression`, the rank
    ceremony, and capability unlocks.

    The same mistake was caught in `_build_turn_prompt` on 2026-10-18 (it would
    have dropped ADR-011's author note and the summary-context block). A flag is
    the correct shape in a function that is a sequence of independent steps.
    """

    def test_the_hook_never_returns_early(self):
        import ast
        import inspect
        import textwrap

        from coordinator.services import chat_session_service as css

        tree = ast.parse(textwrap.dedent(inspect.getsource(css._apply_post_turn_updates)))
        fn = tree.body[0]
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
        # A bare `return` at the very end is harmless; anything earlier is not.
        offenders = [n.lineno for n in returns]
        assert not offenders, (
            "an early return here silently skips the NEPHILIM progression block "
            f"(rank-ups, resonance, capability unlocks); returns at {offenders}"
        )

    def test_progression_still_runs_after_the_extraction_block(self):
        import ast
        import inspect
        import textwrap

        from coordinator.services import chat_session_service as css

        src = textwrap.dedent(inspect.getsource(css._apply_post_turn_updates))
        tree = ast.parse(src)
        lines = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
                if name in ("enqueue", "_track_nephilim_progression"):
                    lines.setdefault(name, n.lineno)
        assert "enqueue" in lines, "the worker is never enqueued"
        assert "_track_nephilim_progression" in lines
        assert lines["enqueue"] < lines["_track_nephilim_progression"], (
            "progression must come after the extraction block, and must still "
            "be reachable from it"
        )

    def test_the_inline_path_is_guarded_by_a_flag(self):
        import inspect

        from coordinator.services import chat_session_service as css

        src = inspect.getsource(css._apply_post_turn_updates)
        assert "_worker_owns_extraction" in src
        assert "if not _worker_owns_extraction:" in src, (
            "the SQLite write must be skipped when the worker owns it, or "
            "extraction runs twice and the two stores disagree"
        )
