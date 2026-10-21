"""The test write-guard must cover the operator-fact spelling too.

⚠️ Import `src.coordinator`, NOT `coordinator`. They are DISTINCT module objects
in this repo (CLAUDE.md says so, and conftest disables `env_file` on both trees
for the same reason). The guard fixture patches `src.coordinator.graph_driver`,
which is the tree production runs (`uvicorn src.coordinator.server:app`). The
first version of this test imported the other tree, so the guard was not patched
at all and the write sailed through to a stub driver — a failure that looks like
a broken guard and is actually a broken import.

⚠️ THE GUARD PROTECTS ONLY THE PARAMETER NAMES IT KNOWS. `tests/conftest.py`
checked `persona_id` / `p` / `pid`, and `pid` was itself added after an
unguarded path to the live identity graph was found. The ADR-021 operator-fact
repository writes with `operator_id`, so without this it had the same unguarded
path — second instance, which makes the pattern the finding rather than the
instance.
"""
from __future__ import annotations

import pytest


def test_an_operator_id_write_to_a_real_operator_is_refused():
    from src.coordinator import graph_driver

    with pytest.raises(AssertionError, match="operator|persona"):
        graph_driver.write(
            object(), "MERGE (o:Operator {operator_id: $operator_id})", "neo4j",
            operator_id="user_be5d9dbe",
        )


def test_a_scratch_operator_is_allowed_through_the_guard():
    """Writes to a `_`-prefixed owner must still work, or graph tests cannot run."""
    from src.coordinator import graph_driver

    # The guard permits it; the stub driver then fails for an unrelated reason,
    # which is enough to show the guard did not refuse it.
    with pytest.raises(Exception) as e:
        graph_driver.write(
            object(), "MERGE (o:Operator {operator_id: $operator_id})", "neo4j",
            operator_id="_scratch_operator",
        )
    assert "must target a scratch" not in str(e.value)


def test_the_guard_lists_operator_id_among_the_spellings_it_checks():
    """A grep-level pin. A future writer using yet another spelling is NOT
    covered, and this test is where someone should notice that."""
    import inspect
    import pathlib

    src = pathlib.Path(inspect.getfile(
        __import__("tests.conftest", fromlist=["x"])
    )).read_text() if False else pathlib.Path(
        pathlib.Path(__file__).resolve().parents[2] / "conftest.py"
    ).read_text()
    assert 'params.get("operator_id")' in src
