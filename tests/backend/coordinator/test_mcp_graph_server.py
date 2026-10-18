"""The MCP server: the surface it exposes, and the boundary it claims.

No live model and no subprocess — the tools are plain functions and the server
object can be inspected directly, which is where nearly all the useful coverage
is. The one thing that genuinely needs a graph (the rollback boundary) is marked
and skips without one.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import pathlib

import pytest

_SERVER = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "mcp" / "graph_server.py"
pytest.importorskip("mcp", reason="mcp SDK not installed")


@pytest.fixture(scope="module")
def srv():
    spec = importlib.util.spec_from_file_location("_graph_server", _SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.register_catalog()
    return mod


def _tools(mod) -> dict:
    return {t.name: t for t in asyncio.run(mod.mcp.list_tools())}


def test_every_answered_question_is_exposed_as_a_tool(srv):
    """The tool list is GENERATED from the catalog, and this is what keeps it so.

    Hand-writing the tools would let the exposed surface drift from the answered
    surface — a question answerable in code but invisible over MCP, or worse a
    tool pointing at a function that no longer answers anything.
    """
    from src.coordinator.graph_queries import CATALOG

    exposed = set(_tools(srv))
    answered = {q.fn.__name__ for q in CATALOG.values()}
    missing = answered - exposed
    assert not missing, f"answered but not exposed over MCP: {sorted(missing)}"


def test_every_tool_is_actually_CALLABLE_not_merely_exposed(srv):
    """The test that was missing, and the bug it would have caught.

    The first generated wrapper used `**kwargs`, which FastMCP renders as a
    REQUIRED `kwargs` field in the JSON schema. Every tool listed cleanly,
    declared itself read-only, and failed on the first real call with "Field
    required". Only an end-to-end call found it — so this asserts the generated
    SCHEMA has no required parameter the catalog function does not actually have.
    """
    import inspect

    from src.coordinator.graph_queries import CATALOG

    # `is_the_card_fully_in_the_graph` is hand-registered because it needs the card
    # from disk as well as the graph, so its tool signature deliberately differs
    # from the catalog function's. It is covered by its own assertion below.
    HAND_REGISTERED = {"is_the_card_fully_in_the_graph"}
    by_name = {q.fn.__name__: q for q in CATALOG.values()}
    checked = 0
    for name, tool in _tools(srv).items():
        if name not in by_name or name in HAND_REGISTERED:
            continue
        checked += 1
        schema = tool.inputSchema or {}
        required = set(schema.get("required") or [])
        real = {
            p for p, prm in inspect.signature(by_name[name].fn).parameters.items()
            if p not in ("driver", "database") and prm.default is inspect.Parameter.empty
        }
        assert required == real, (
            f"{name}: schema requires {sorted(required)} but the function requires "
            f"{sorted(real)} — a caller filling the schema would fail or be refused"
        )
        assert "kwargs" not in (schema.get("properties") or {}), (
            f"{name}: `kwargs` leaked into the schema as a parameter"
        )
    assert checked >= 8, (
        f"only {checked} generated tools were checked — if the catalog shrank to "
        f"nothing this test would pass vacuously, which is the failure mode it "
        f"exists to prevent"
    )

    # The hand-registered one: asserted to take persona_id and nothing required.
    hand = _tools(srv)["is_the_card_fully_in_the_graph"]
    props = (hand.inputSchema or {}).get("properties") or {}
    assert "persona_id" in props, "the hand-registered tool lost its only parameter"
    assert "kwargs" not in props


def test_every_tool_declares_itself_read_only(srv):
    """Advisory, not a boundary — but clients use it to decide what needs a
    confirmation, so a write-shaped tool that forgot the flag is a real defect."""
    for name, t in _tools(srv).items():
        ann = t.annotations
        assert ann is not None, f"{name}: no annotations"
        assert ann.readOnlyHint is True, f"{name}: not declared read-only"
        assert ann.destructiveHint is False, f"{name}: declared destructive"


def test_a_degraded_query_says_so_in_its_tool_description(srv):
    """A caller reading the tool list must be able to see that tmp.cq01-03 answer
    a question about `:Fact` by retargeting `:Rule`. Hiding that would let the
    tool list claim coverage the schema does not have."""
    from src.coordinator.graph_queries import CATALOG

    tools = _tools(srv)
    for q in CATALOG.values():
        if q.degraded:
            desc = tools[q.fn.__name__].description or ""
            assert "DEGRADED" in desc, f"{q.cq}: degraded but the tool does not say so"


def test_the_raw_cypher_tool_is_absent_by_default(srv):
    """It is opt-in via NEPHILIM_MCP_ALLOW_CYPHER, and `main()` is what adds it.

    Registering it unconditionally and relying on a runtime check would make the
    tool visible in every client's tool list, which is both a context cost and an
    invitation.
    """
    assert "cypher_read" not in _tools(srv)


@pytest.mark.parametrize("query", [
    "CREATE (x:Evil) RETURN x",
    "MATCH (n:Persona) SET n.hacked = true RETURN n.persona_id AS p",
    "MATCH (n) DETACH DELETE n",
    "MATCH (n:Rule) REMOVE n:CurrentRule RETURN n.rule_id AS r",
    "DROP INDEX rule_history_read",
    "match (n:Persona) set n.x = 1 return n.persona_id as p",
])
def test_write_clauses_are_rejected_before_execution(srv, query):
    """Guard 2 of 3. Runs without a graph because the reject happens first."""
    out = srv.cypher_read(query)
    assert '"error"' in out and "write clause rejected" in out, f"not rejected: {query}"


@pytest.mark.parametrize("query", [
    "MATCH (n:Persona) RETURN n.created_at AS created LIMIT 1",
    "MATCH (n:Trait) RETURN n.text AS t, n.source_field AS f LIMIT 5",
])
def test_the_reject_does_not_fire_on_innocent_reads(srv, query):
    """A property called `created_at` contains no write clause, and a guard that
    matched substrings would reject half the legitimate reads in this schema."""
    assert "write clause rejected" not in srv.cypher_read(query)


def test_the_inventory_tool_states_what_is_NOT_answerable(srv):
    """The honest half. A tool list showing 10 answered questions and nothing
    else implies the other 21 are merely unimplemented; 4 of them must never be
    implemented and 16 need a schema that does not exist."""
    import json

    out = json.loads(srv.list_competency_questions())
    assert out["not_answered"], "no statement of what cannot be answered"
    for must_not in ("wal.cq01", "hab.cq01", "wel.cq01", "idn.cq05"):
        assert must_not in out["not_answered"], f"{must_not} not named"
        assert must_not not in out["answered"], f"{must_not} must never be answered"


@pytest.mark.skipif(not os.getenv("NEO4J_BASE_URL"), reason="needs a live graph")
def test_rollback_is_the_actual_boundary(srv):
    """Guard 3 of 3, and the only one that is a real guarantee.

    Neo4j Community has no RBAC and the driver documents read-routing as carrying
    no security guarantee, so this is the one layer that holds if the static
    reject is ever bypassed. Asserted by driving a write THROUGH the tool's own
    transaction handling rather than around it.
    """
    import json

    # Build the driver from the environment rather than through settings:
    # `conftest._hermetic_settings` disables every settings class's env_file, so
    # `get_settings().graph.enabled` is False inside the suite even when the graph
    # is up. This is the same reason every other live graph test here builds its
    # own driver, and why `scripts/checks/graph_tests.sh` exists to run them.
    from src.coordinator.graph_driver import build_driver

    drv = build_driver(os.environ["NEO4J_BASE_URL"],
                       os.getenv("NEO4J_USER", "neo4j"),
                       os.environ["NEO4J_PASSWORD"])
    srv._driver, srv._database = drv, os.getenv("NEO4J_DATABASE", "neo4j")
    with drv.session(database=srv._database) as s:
        tx = s.begin_transaction()
        try:
            tx.run("CREATE (x:_McpRollbackProbe {m: 1})").consume()
            inside = tx.run("MATCH (x:_McpRollbackProbe) RETURN count(x) AS n").single()["n"]
        finally:
            tx.rollback()
        after = s.run("MATCH (x:_McpRollbackProbe) RETURN count(x) AS n").single()["n"]
    assert inside == 1, "the probe never ran, so this proves nothing"
    assert after == 0, "a write SURVIVED rollback — the read-only boundary is not real"

    try:
        out = json.loads(srv.cypher_read("MATCH (n:Persona) RETURN n.persona_id AS p LIMIT 2"))
        assert out["count"] >= 1, "reads must still work inside the rolled-back transaction"
    finally:
        drv.close()
        srv._driver = None
