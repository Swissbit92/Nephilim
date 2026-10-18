#!/usr/bin/env python3
"""Read-only MCP server over nephilim's graph, for the operator and Claude Code.

**This is for the OPERATOR, not for gwen.** The distinction is the whole design.
MCP exists to give MULTIPLE consumers one standard way into one source — Claude
Code, Claude Desktop, a future IDE — which is a problem the operator has and the
companion does not. gwen's coordinator is the only consumer of her own graph and
runs in the same process, so routing her through a protocol would buy nothing and
cost the two things she can least afford: context budget (her persona constraints
already overflow it) and tool-calling reliability (measured 5/5 at zero history
to 0/5 at depth 16 against her real conversation; 3 tool calls in 278 live turns).

**ADR-010 said "no MCP, no A2A" and that is deliberately superseded here**
(2026-10-18, operator's call: the decision predates this architecture). It is
recorded rather than ignored, because a decision log that silently contradicts
itself is worse than one that shows its reversals. `graph_driver`'s own docstring
anticipated this exact move: the verb surface was written remote-shaped while
running in-process precisely so that "moving this behind a protocol later becomes
wrapping it rather than rewriting its callers". This server is that wrapping, and
it does not rewrite a single caller.

**The tool list is GENERATED from `graph_queries.CATALOG`**, never hand-written,
so a question answered in code is exposed here without a second registration to
keep in sync. A test asserts the two sets are equal.

**Read-only is enforced, not asserted.** Three independent layers, because the
two obvious ones are not boundaries at all:

  1. The typed catalog. Ten fixed, parameterised queries — an allowlist by
     construction. This is the real boundary and the reason there is no generic
     "run any Cypher" tool by default.
  2. A static reject of write clauses on the one escape-hatch tool.
  3. **Every escape-hatch query runs inside a transaction that is ALWAYS rolled
     back.** Verified on this database 2026-10-18: reads return normally, a
     `CREATE` is visible inside the transaction and leaves zero rows after.

Why three and not one: Neo4j Community has **no RBAC**, so there is no server-side
read-only user; and the Python driver's docs say outright that `routing_="r"` is a
cluster-routing hint with "no security guarantee". Neo4j's own `mcp-neo4j-cypher`
server ships raw Cypher behind a bare `--read-only` flag with no static analysis
and no rollback. That is the weaker pattern and is not copied here.

`readOnlyHint` is set on every tool, but note the MCP spec treats annotations as
**advisory hints for the client UI, not a security boundary**. They are declared
because clients use them to decide what needs a confirmation — not because they
protect anything.

Usage:
    .venv/bin/python scripts/mcp/graph_server.py          # stdio, typed tools only
    NEPHILIM_MCP_ALLOW_CYPHER=1 .venv/bin/python scripts/mcp/graph_server.py

Registered for Claude Code via `.mcp.json` at the repo root.
"""

from __future__ import annotations

import inspect
import json
import os
import pathlib
import re
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from src.coordinator import graph_queries as gq  # noqa: E402
from src.coordinator.config import get_settings  # noqa: E402
from src.coordinator.graph_driver import build_driver  # noqa: E402

mcp = FastMCP("nephilim-graph")

#: Every tool this server exposes is a read. Advisory for the client UI only —
#: the enforcement is the typed catalog plus the rollback, not this flag.
_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                             idempotentHint=True, openWorldHint=False)

#: Clauses that mutate. Matched as whole words so a property called `created_at`
#: or a persona whose text contains "delete" cannot trip it.
_WRITE_CLAUSES = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|CALL\s*\{[^}]*\b(?:CREATE|MERGE|DELETE|SET)\b)\b",
    re.I,
)

_driver = None
_database = "neo4j"


def _connect():
    """One driver for the life of the process, built lazily and closed on exit.

    Driver 6.x no longer closes itself in `__del__`, so an abandoned driver holds
    its pooled connections for the life of the process — `server.py`'s lifespan
    gained a shutdown hook for exactly this.
    """
    global _driver, _database
    if _driver is None:
        cfg = get_settings().graph
        if not cfg.enabled:
            raise RuntimeError("GRAPH_ENABLED is false — this server has nothing to read")
        _driver = build_driver(cfg.base_url, cfg.username, cfg.password)
        _database = cfg.database
    return _driver, _database


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


def _error(exc: Exception) -> str:
    """Errors come back as a RESULT, not a raised exception.

    MCP distinguishes protocol errors (bad tool name, bad arguments) from tool
    execution errors returned in a normal result. A query that fails is the
    second kind: the caller should see the text and react, not get a transport
    fault. Shape matches the ecosystem's other MCP server.
    """
    return _json({"error": f"{type(exc).__name__}: {exc}"})


# ---------- the typed tools, generated from the catalog ----------------------


def _make_tool(cq: str, q: gq.Query):
    """Wrap one catalog entry as an MCP tool, injecting the driver.

    Generated rather than hand-written so the exposed surface cannot drift from
    the answered surface. `test_mcp_graph_server.py` asserts the two sets match
    AND that every one of them is callable.

    ⚠️ **The wrapper copies the real signature; it must not use `**kwargs`.** The
    first version did, and FastMCP turned it into a REQUIRED `kwargs` field in
    the generated JSON schema — so every catalog tool existed, listed cleanly,
    declared itself read-only, and failed on the first call with "Field
    required". The unit test passed because it asserted the tool was EXPOSED;
    only an end-to-end call caught it. Existence is not callability.
    """
    params = [
        prm for name, prm in inspect.signature(q.fn).parameters.items()
        if name not in ("driver", "database")
    ]

    def tool(*args: Any, **kwargs: Any) -> str:
        try:
            drv, db = _connect()
            return _json(q.fn(drv, db, *args, **kwargs))
        except Exception as exc:  # noqa: BLE001 — see _error
            return _error(exc)

    tool.__signature__ = inspect.Signature(params, return_annotation=str)
    tool.__annotations__ = {
        p.name: (p.annotation if p.annotation is not inspect.Parameter.empty else Any)
        for p in params
    } | {"return": str}

    warning = (
        f"\n\n⚠️ DEGRADED: {q.degraded_because}" if q.degraded else ""
    )
    tool.__name__ = q.fn.__name__
    tool.__doc__ = f"{q.cq} — {q.question}\n\n{(q.fn.__doc__ or '').strip()}{warning}"
    return tool


def register_catalog() -> list[str]:
    names = []
    for cq, q in sorted(gq.CATALOG.items()):
        # idn.cq04 needs the card as well as the graph; it is registered by hand
        # below rather than given a `card` parameter no caller could fill.
        if cq == "idn.cq04":
            continue
        fn = _make_tool(cq, q)
        mcp.add_tool(fn, name=q.fn.__name__, description=fn.__doc__,
                     annotations=_READ_ONLY)
        names.append(q.fn.__name__)
    return names


@mcp.tool(annotations=_READ_ONLY)
def is_the_card_fully_in_the_graph(persona_id: str = "gwen") -> str:
    """idn.cq04 — Is everything on her card actually in the graph?

    Loads the persona card from disk and diffs it against the graph on
    `(source_field, source_key)`. An empty `missing` list IS the answer, not an
    absence of one.
    """
    try:
        drv, db = _connect()
        card = json.loads((ROOT / "personas" / f"{persona_id}.json").read_text())
        return _json(gq.is_the_card_fully_in_the_graph(drv, db, persona_id, card))
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@mcp.tool(annotations=_READ_ONLY)
def list_competency_questions() -> str:
    """What this graph can and cannot answer, and why — the honest inventory.

    Returns every question that has an executable query, flags the degraded ones
    with the reason, and states the count that is deliberately unanswered. Use
    this before assuming a question is answerable: 16 of the 31 need schema that
    does not exist yet, and 4 must never be given a query at all.
    """
    return _json({
        "answered": {
            cq: {"question": q.question, "tool": q.fn.__name__,
                 "degraded": q.degraded, "degraded_because": q.degraded_because or None}
            for cq, q in sorted(gq.CATALOG.items())
        },
        "not_answered": (
            "16 questions need the Fact/Episode/Preference/scoping schema, which "
            "ADR-021 designs and whose read path is explicitly closed pending one "
            "experiment. 4 more must never carry a query: wal.cq01, hab.cq01 and "
            "wel.cq01 are class:scoping, and idn.cq05 is deliberately "
            "uninstrumented."
        ),
    })


# ---------- the escape hatch, off by default and genuinely bounded -----------


def cypher_read(query: str, limit: int = 100) -> str:
    """Run one read-only Cypher statement. OFF unless NEPHILIM_MCP_ALLOW_CYPHER=1.

    For the operator's own exploration. Three independent guards, because neither
    of the usual two is a boundary: Neo4j Community has no RBAC, and the driver's
    read routing is documented as carrying "no security guarantee".

      1. this tool is absent unless the env var is set
      2. write clauses are rejected before execution
      3. the statement runs in a transaction that is ALWAYS rolled back, so a
         write that slipped past (2) still persists nothing

    Verified on this database: a CREATE is visible inside the transaction and
    leaves zero rows after rollback.
    """
    try:
        if _WRITE_CLAUSES.search(query):
            return _json({"error": "write clause rejected — this tool is read-only",
                          "matched": _WRITE_CLAUSES.search(query).group(0)})
        drv, db = _connect()
        with drv.session(database=db) as session:
            tx = session.begin_transaction()
            try:
                rows = [r.data() for r in tx.run(query)][: int(limit)]
            finally:
                # ALWAYS. Not on error — always. This is the boundary.
                tx.rollback()
        return _json({"rows": rows, "count": len(rows),
                      "note": "ran inside a transaction that was rolled back"})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def main(argv: list[str] | None = None) -> int:
    register_catalog()
    if os.getenv("NEPHILIM_MCP_ALLOW_CYPHER") == "1":
        mcp.add_tool(cypher_read, name="cypher_read",
                     description=cypher_read.__doc__, annotations=_READ_ONLY)
    try:
        mcp.run(transport="stdio")
    finally:
        if _driver is not None:
            _driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
