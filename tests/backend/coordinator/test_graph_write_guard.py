"""The autouse graph-write guard must cover EVERY spelling of a write target.

`_block_graph_writes_to_real_personas` exists because the Neo4j driver speaks Bolt over
raw sockets, so `_block_production_backend`'s urlopen/httpx guards never covered it. It
checked `persona_id` and `p`.

The identity repository spells it `pid`. So `apply_card`, `_capture_baseline` and
`_set_current_dials` all sailed straight through to the live identity graph, and
`reinforce` addressed nodes by bare ULID, which the guard could not inspect at all.
That was measured, not theorised: before the fix both of the first two tests below
failed by NOT raising, and the write reached the real driver.

This matters more than it did when the graph held a re-seedable projection. ADR-012
makes the graph the system of record for persona identity, so a stray test write is no
longer recoverable from the card.
"""
import os

import pytest

from src.coordinator import graph_driver
from tests.conftest import SCRATCH_PERSONA_PREFIX


def test_guard_catches_a_pid_keyed_write():
    """`IdentityRepository.apply_card` passes pid=, never persona_id=."""
    with pytest.raises(AssertionError, match="WRITE to the graph"):
        graph_driver.write(object(), "MATCH (n) RETURN n", "neo4j", pid="gwen")


def test_guard_catches_a_node_addressed_write():
    """`reinforce` passes only a ULID, which names no persona.

    The guard must resolve the owner, and refuse when it cannot. A stub driver cannot
    be read, so this asserts the FAIL-CLOSED branch.
    """
    with pytest.raises(AssertionError, match="cannot be resolved|resolves to persona"):
        graph_driver.write(object(), "MATCH (n) RETURN n", "neo4j",
                           nid="01M3MJGSHDQ4Q2BEX637QYFVR9", d=0.1, now="x")


@pytest.mark.parametrize("key", ["persona_id", "p", "pid"])
def test_a_scratch_persona_is_still_allowed_through(key, monkeypatch):
    """The guard must not simply block everything.

    A guard that refuses every write would pass the tests above while making
    `test_neo4j_rule_repository.py` and `test_identity_baseline.py` impossible — they
    legitimately need a live graph and confine themselves to a scratch persona. So the
    allowed path is asserted too: reaching the real driver is the PASS here, and the
    stub's AttributeError is how we observe that it got there.
    """
    with pytest.raises(AttributeError, match="execute_query"):
        graph_driver.write(object(), "MATCH (n) RETURN n", "neo4j",
                           **{key: f"{SCRATCH_PERSONA_PREFIX}scratch"})


def test_the_escape_hatch_is_the_same_one_the_http_guard_uses():
    """One documented bypass, not a second convention to remember."""
    assert os.environ.get("ALLOW_PROD_BACKEND") != "1", (
        "this suite must not run with the guard bypassed")
