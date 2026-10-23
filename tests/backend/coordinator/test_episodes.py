"""`:Episode` + `CONTINUES` — ADR-021 Phase 1, pointers only.

`prf.cq03` ("What are we in the middle of?") demands labels [Thread, Episode],
rels [CONTINUES, PART_OF], props [last_touched, status]. Its negative fixture:
"She must NOT merge two separate threads into one... This is the one question in
the whole set that is genuinely GRAPH-shaped rather than table-shaped: it needs
ordered edges between episodes, not rows with timestamps."

🔴 Thread and PART_OF are NOT BUILT, and that is a refusal rather than an
omission — see `episode_repository`'s docstring. Grouping sessions into topical
threads needs scene-boundary detection, excluded from this phase, and the only
groupings available without it are the two the fixture forbids: one thread per
operator merges every scene, one thread per session makes Thread a synonym for
Episode. C17 errors on a label BUILT and undemanded, never on one demanded and
unbuilt, so declining costs nothing and inventing would cost correctness.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("NEO4J_BASE_URL"),
    reason="needs NEO4J_BASE_URL — run via scripts/checks/graph_tests.sh",
)

_OP = "_scratch_episodes"


@pytest.fixture()
def repo():
    from src.coordinator.graph_driver import build_driver, write
    from src.coordinator.repositories.episode_repository import EpisodeRepository

    drv = build_driver(os.environ["NEO4J_BASE_URL"],
                       os.getenv("NEO4J_USER", "neo4j"),
                       os.environ["NEO4J_PASSWORD"])
    r = EpisodeRepository(drv, os.getenv("NEO4J_DATABASE", "neo4j"))
    r.ensure_schema()
    yield r
    write(drv, "MATCH (e:Episode {operator_id: $operator_id}) DETACH DELETE e",
          r._database, operator_id=_OP)
    drv.close()


class TestPointersOnly:
    def test_no_message_text_is_stored(self, repo):
        """ADR-021: message text stays in SQLite, and `idn.cq06` requires the
        graph to REFUSE "what did she say three weeks ago". Copying text here
        would make that answerable, and the refusal is the design."""
        import inspect

        from src.coordinator.repositories import episode_repository as m

        src = inspect.getsource(m)
        for forbidden in ("content", "message_text", "e.text"):
            assert f"{forbidden}=" not in src and f"e.{forbidden}" not in src, forbidden

    def test_an_episode_records_the_session_pointer(self, repo):
        eid = repo.record_episode(_OP, "sess-a", started_at="2026-10-01T10:00:00+00:00",
                                  message_count=12, persona_key="gwen")
        assert eid
        rows = repo.recent_episodes(_OP)
        row = next(r for r in rows if r["session_id"] == "sess-a")
        assert row["message_count"] == 12
        assert row["persona_key"] == "gwen"

    def test_recording_the_same_session_twice_is_idempotent(self, repo):
        a = repo.record_episode(_OP, "sess-b", started_at="2026-10-01T10:00:00+00:00")
        b = repo.record_episode(_OP, "sess-b", started_at="2026-10-01T10:00:00+00:00")
        assert a == b, "the session is the natural key; a re-run must not fork"
        assert len([r for r in repo.recent_episodes(_OP)
                    if r["session_id"] == "sess-b"]) == 1

    def test_a_null_ended_at_does_not_delete_a_previous_value(self, repo):
        """⚠️ `SET e += $map` DELETES any key whose value is null — a recorded
        trap in this repo. `ended_at` is therefore assigned in its own
        statement, so a later call without it writes nothing rather than
        removing what was set."""
        repo.record_episode(_OP, "sess-c", started_at="2026-10-01T10:00:00+00:00",
                            ended_at="2026-10-01T11:00:00+00:00")
        repo.record_episode(_OP, "sess-c", started_at="2026-10-01T10:00:00+00:00")
        row = next(r for r in repo.recent_episodes(_OP) if r["session_id"] == "sess-c")
        assert row["ended_at"] == "2026-10-01T11:00:00+00:00"


class TestOrderedEdges:
    """The graph-shaped part: ordered edges, not rows with timestamps."""

    def test_continues_chains_in_chronological_order(self, repo):
        repo.record_episode(_OP, "s1", started_at="2026-10-01T10:00:00+00:00")
        repo.record_episode(_OP, "s2", started_at="2026-10-02T10:00:00+00:00")
        repo.record_episode(_OP, "s3", started_at="2026-10-03T10:00:00+00:00")
        assert repo.link_chronology(_OP) == 2
        rows = {r["session_id"]: r["continues_session_id"]
                for r in repo.recent_episodes(_OP)}
        assert rows["s3"] == "s2"
        assert rows["s2"] == "s1"
        assert rows["s1"] is None, "the first episode continues nothing"

    def test_linking_twice_does_not_fan_out(self, repo):
        repo.record_episode(_OP, "t1", started_at="2026-10-01T10:00:00+00:00")
        repo.record_episode(_OP, "t2", started_at="2026-10-02T10:00:00+00:00")
        repo.link_chronology(_OP)
        repo.link_chronology(_OP)
        from src.coordinator.graph_driver import read

        n = read(repo._driver,
                 "MATCH (:Episode {operator_id: $operator_id})-[c:CONTINUES]->() "
                 "RETURN count(c) AS c LIMIT 1",
                 repo._database, operator_id=_OP)[0]["c"]
        assert n == 1, f"MERGE must make the link idempotent; got {n} edges"

    def test_a_single_episode_makes_no_edge(self, repo):
        repo.record_episode(_OP, "lonely", started_at="2026-10-01T10:00:00+00:00")
        assert repo.link_chronology(_OP) == 0


class TestThreadIsRefusedNotForgotten:
    def test_thread_and_part_of_are_not_built(self):
        """The refusal, asserted. If someone later builds Thread without
        boundary detection, this is where the argument has to happen."""
        from src.coordinator.graph_surface import BUILT_LABELS, BUILT_RELS

        assert "Thread" not in BUILT_LABELS
        assert "PART_OF" not in BUILT_RELS

    def test_episode_and_continues_are_built_and_declared(self):
        from src.coordinator.graph_surface import BUILT_LABELS, BUILT_RELS

        assert "Episode" in BUILT_LABELS
        assert "CONTINUES" in BUILT_RELS

    def test_the_label_is_Episode_not_Episodic(self):
        """⚠️ ADR-021's prose says `:Episodic`; the question set demands
        `Episode`. C17 compares bare strings, so `:Episodic` would have been
        undemanded-and-unexempted while `Episode` showed as demanded-and-
        unbuilt. Caught before a line was written."""
        from src.coordinator.graph_surface import BUILT_LABELS

        assert "Episodic" not in BUILT_LABELS

    def test_the_refusal_is_documented_where_someone_would_look(self):
        import inspect

        from src.coordinator.repositories import episode_repository as m

        doc = m.__doc__ or ""
        assert "NOT BUILT" in doc
        assert "prf.cq03" in doc, "the refusal must name the question it declines"


class TestEveryWriteIsGuardable:
    def test_each_write_names_its_owner(self):
        """⚠️ The test write-guard can only refuse writes whose OWNER it can
        read off the parameters. A write keyed only on episode ids is invisible
        to it, so a test could link CONTINUES between LIVE episodes."""
        import ast
        import inspect

        from src.coordinator.repositories import episode_repository as m

        tree = ast.parse(inspect.getsource(m))
        offenders = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "write":
                kw = {k.arg for k in n.keywords}
                if kw and not ({"operator_id", "persona_id", "pid", "p"} & kw):
                    offenders.append(ast.unparse(n)[:60])
        assert not offenders, f"writes with no owner parameter: {offenders}"


class TestFactCursor:
    """The fact-extraction high-water mark, hosted on the episode it describes.

    🔴 WHY THE CURSOR LIVES HERE AND NOT IN SQLITE. A high-water mark must live
    in the SAME STORE as the write it authorises, or it can advance past a
    window that was never persisted. The facts' system of record is the graph.
    If Neo4j is unavailable the fact write does not happen, so a SQLite cursor
    would advance over an unwritten window and turn a transient outage into
    permanent loss. The correct degradation is NO ADVANCE.

    This also gives `:Episode` its first live producer: before this, the only
    callers of `record_episode` were `scripts/utils/backfill_episodes.py` and
    these tests, so 229 nodes existed and nothing in `src/` ever wrote one.
    """

    def test_a_session_with_no_episode_has_no_cursor(self, repo):
        assert repo.fact_cursor(_OP, "sess-never-seen") is None

    def test_advance_then_read_round_trips(self, repo):
        repo.advance_fact_cursor(
            _OP, "sess-a", cursor_at="2026-10-23T12:00:05",
            cursor_message_id="m42", message_count=7, persona_key="gwen",
            started_at="2026-10-23T11:59:00")
        got = repo.fact_cursor(_OP, "sess-a")
        assert got == {"at": "2026-10-23T12:00:05", "message_id": "m42"}

    def test_it_does_not_clobber_started_at_on_a_backfilled_episode(self, repo):
        """🔴 THE REGRESSION THAT WOULD CORRUPT 229 LIVE NODES.

        `record_episode` SETs `started_at` unconditionally. A flush knows only
        its own window, so if `advance_fact_cursor` did the same it would
        rewrite the true start of every session it touched. Hence `ON CREATE
        SET` for that one field, and hence this test.
        """
        repo.record_episode(_OP, "sess-b", started_at="2026-07-05T15:39:00",
                            message_count=129, persona_key="gwen")
        repo.advance_fact_cursor(
            _OP, "sess-b", cursor_at="2026-10-23T12:00:05",
            cursor_message_id="m99", message_count=129, persona_key="gwen",
            started_at="2026-10-23T11:00:00")   # the window's first message
        rows = repo.recent_episodes(_OP, limit=50)
        row = next(r for r in rows if r["session_id"] == "sess-b")
        assert row["started_at"] == "2026-07-05T15:39:00", (
            "advance_fact_cursor overwrote started_at; the backfilled history "
            "of this session is gone")

    def test_it_creates_the_episode_when_none_exists(self, repo):
        eid = repo.advance_fact_cursor(
            _OP, "sess-c", cursor_at="2026-10-23T12:00:05",
            cursor_message_id="m1", started_at="2026-10-23T12:00:00")
        assert eid
        assert repo.fact_cursor(_OP, "sess-c")["at"] == "2026-10-23T12:00:05"

    def test_it_sets_last_touched(self, repo):
        repo.advance_fact_cursor(_OP, "sess-d", cursor_at="2026-10-23T12:00:05")
        rows = repo.recent_episodes(_OP, limit=50)
        row = next(r for r in rows if r["session_id"] == "sess-d")
        assert row.get("last_touched"), "last_touched was not written"

    def test_the_repository_accepts_a_rewind_because_the_handler_owns_monotonicity(
            self, repo):
        """The responsibility boundary, pinned.

        Monotonicity is enforced at the single consumer — the worker thread —
        which re-reads the stored cursor at execution time. Enforcing it here
        too would split the compare-and-advance across two places, which is the
        shape of Mem0 #6515's TOCTOU race. So the repo is deliberately dumb and
        this test says so, to stop a future reader "fixing" it.
        """
        repo.advance_fact_cursor(_OP, "sess-e", cursor_at="2026-10-23T12:00:10")
        repo.advance_fact_cursor(_OP, "sess-e", cursor_at="2026-10-23T12:00:01")
        assert repo.fact_cursor(_OP, "sess-e")["at"] == "2026-10-23T12:00:01", (
            "the repo refused a rewind; monotonicity belongs to the handler")

    def test_two_sessions_keep_separate_cursors(self, repo):
        repo.advance_fact_cursor(_OP, "sess-f", cursor_at="2026-10-23T12:00:01")
        repo.advance_fact_cursor(_OP, "sess-g", cursor_at="2026-10-23T13:00:01")
        assert repo.fact_cursor(_OP, "sess-f")["at"] == "2026-10-23T12:00:01"
        assert repo.fact_cursor(_OP, "sess-g")["at"] == "2026-10-23T13:00:01"
