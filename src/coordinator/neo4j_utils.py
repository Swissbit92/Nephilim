"""Startup checks for the graph store — ADR-014.

Two functions and two exceptions, mirroring `ollama_utils.py`, and for the same
reason its docstring gives: "unset" and "unreachable" have different fixes, so
collapsing them into one error sends people to restart a service that is already
running.

The ordering rule from `startup.py` applies here too and is the whole point of
having two functions: `require_graph_configured` runs FIRST, because
`assert_graph_available` would happily pass against whatever database is listening
on a default port — including production, from a test run.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class GraphNotConfigured(RuntimeError):
    """GRAPH_ENABLED is true but the connection details are missing.

    Distinct from GraphUnavailable on purpose. `assert_graph_available` cannot
    catch this case and never could: handed an empty URL it has nothing to probe,
    and handed a plausible default it would connect to something and report
    success. That is the silent failure this exception converts into a loud one —
    the same shape as ModelNotConfigured, which exists because an unset
    PERSONA_MODEL fell back to a real model that happened to be pulled and a test
    exercised a 9B smoke model at 4096 context for months while reporting green.
    """


class GraphUnavailable(RuntimeError):
    """Configured, but nothing answered. The graph is down, or the URL is wrong."""


def require_graph_configured(base_url: str, password: str) -> tuple[str, str]:
    """Reject empty connection details. Returns them so call sites can inline.

    Called at startup, never at import: `config/__init__.py` builds the settings
    singleton at import time, so raising during import would turn a missing .env
    into a collection error across the whole test suite instead of one readable
    startup message.
    """
    missing = []
    if not (base_url or "").strip():
        missing.append("NEO4J_BASE_URL")
    if not (password or "").strip():
        missing.append("NEO4J_PASSWORD")
    if missing:
        raise GraphNotConfigured(
            f"GRAPH_ENABLED is true but {' and '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} not set.\n"
            "Refusing to guess a connection rather than falling back to a default "
            "— a default would connect to whatever graph happens to be on that "
            "port, including production from a test run, and report success.\n"
            "Fix: set these in .env, e.g.\n"
            "  NEO4J_BASE_URL=bolt://127.0.0.1:7688\n"
            "  NEO4J_PASSWORD=<the value in the root .env>\n"
            "Note the port is 7688, not the conventional 7687 — see "
            "docker-compose.graph.yml for why.\n"
            "Or set GRAPH_ENABLED=false to run without the graph, which is the "
            "default and is a supported mode, not a degraded one."
        )
    return base_url, password


def assert_graph_available(driver: object, base_url: str) -> None:
    """Prove the configured graph answers. Raises GraphUnavailable if not.

    Takes an already-constructed driver rather than building one, because
    `GraphDatabase.driver()` does NOT connect eagerly — constructing it proves
    nothing. `verify_connectivity()` is the call that actually opens a connection,
    so it is the only thing here worth doing.

    This is deliberately NOT called from `initialize_all()`. Every optional
    subsystem in this app logs-and-continues on failure and leaves its global
    None (Brave, Jupiter, the scheduler, the fact worker); only the model check
    and `init_db()` may abort a boot. A graph outage must cost capability, not
    the whole server — that is the same ADR-014 property that answers ADR-001's
    objection to another always-on service. Call this from the graph's own init,
    inside the flag guard, and let the caller swallow it.
    """
    try:
        driver.verify_connectivity()  # type: ignore[attr-defined]
    except Exception as e:  # noqa: BLE001 - the driver raises several unrelated types
        raise GraphUnavailable(
            f"Could not reach Neo4j at {base_url}.\n"
            f"Original error: {type(e).__name__}: {e}\n"
            "Check it is up:\n"
            "  docker compose -f docker-compose.graph.yml ps\n"
            "  docker compose -f docker-compose.graph.yml up -d\n"
            "A container reporting 'unhealthy' while answering queries usually "
            "means the healthcheck's credentials are wrong, not the database."
        ) from e
    logger.info("[Graph] reachable at %s", base_url)
