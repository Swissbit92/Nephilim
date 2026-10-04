"""The one module that owns the Neo4j driver — ADR-010's in-process contract, ADR-014's store.

NOTHING ELSE IMPORTS `neo4j`. That is the whole point of this file existing
separately from the repository that uses it: ADR-010 decided against a protocol
boundary (no MCP, no A2A) on the grounds that a closed verb surface in one process
is the honest shape at single-operator scale — but it only stays honest if the
seam is real. A second module importing GraphDatabase is the failure this file is
the guard against, and it wants a grep check of the kind already used to stop
persona-key prefix matching spreading to seven call sites.

THE CONTRACT IS WRITTEN REMOTE-SHAPED WHILE RUNNING IN-PROCESS: serialisable
values in and out, never a live driver object across the seam; no shared session
or transaction between calls; errors returned or raised as plain types, never as
driver internals. That is the future-proofing ADR-010 bought — moving this behind
a protocol later becomes wrapping it rather than rewriting its callers.

WHY `_plain()` EXISTS AND IS NOT OPTIONAL: `Record.data()` converts a Node into a
dict of properties, but it leaves Neo4j's own temporal and spatial types as DRIVER
OBJECTS — `neo4j.time.DateTime`, `Date`, `Time`, `Duration`, `spatial.Point`. Those
are not JSON-serialisable, so `.data()` alone does NOT satisfy the contract above,
and the first timestamp to cross the seam would violate it silently.
"""

from __future__ import annotations

import logging
from typing import Any

from neo4j import Driver, GraphDatabase, RoutingControl
from neo4j.graph import Node, Path, Relationship
from neo4j.spatial import Point
from neo4j.time import Date, DateTime, Duration, Time

from .neo4j_utils import assert_graph_available, require_graph_configured

logger = logging.getLogger(__name__)


def build_driver(
    base_url: str,
    user: str,
    password: str,
    *,
    max_pool_size: int = 20,
    verify: bool = False,
) -> Driver:
    """Construct the process-wide driver. Raises GraphNotConfigured / GraphUnavailable.

    `GraphDatabase.driver()` does NOT connect — it parses the URI, validates the
    config and builds a pool object. No socket, no DNS, no I/O. So construction
    CANNOT fail because Neo4j is down; it raises only on configuration errors.

    `verify=False` IS THE DEFAULT, and that is a correction to this module's first
    draft. Verifying at boot looks like fail-fast and is actually fail-permanent:
    the caller swallows the exception and leaves the driver None, so a container
    that is thirty seconds slow to accept connections disables the graph for the
    ENTIRE life of the process, recoverable only by a restart. Since the pool is
    lazy, the honest arrangement is to construct unconditionally and let the first
    real query surface an outage — which it will, as a handled error on one turn
    rather than a silent capability loss for a day.

    Use `probe()` for liveness, from a health endpoint, where a transient failure
    costs one red line instead of a subsystem.

    `bolt://` rather than `neo4j://` is deliberate. The latter triggers
    routing-table discovery, which exists for clusters — an Enterprise feature —
    and on a single Community instance it only adds a round-trip.
    """
    require_graph_configured(base_url, password)
    driver = GraphDatabase.driver(
        base_url,
        auth=(user, password),
        # The driver default is 100, sized for a cluster serving many callers.
        # This is one operator on a box that pins ~17GB for Ollama.
        max_connection_pool_size=max_pool_size,
        connection_acquisition_timeout=30.0,
    )
    if verify:
        try:
            assert_graph_available(driver, base_url)
        except Exception:
            # Do not leak the pool on a failed boot. Driver 6.x no longer closes
            # itself in __del__, so an abandoned driver holds its connections for
            # the life of the process.
            driver.close()
            raise
    return driver


def probe(driver: Driver | None, base_url: str) -> tuple[bool, str]:
    """Bounded liveness check for a health endpoint. Never raises, never fatal.

    Deliberately NOT called during startup — see build_driver. Passes no keyword
    arguments to verify_connectivity(): any kwarg there triggers a PreviewWarning
    in 6.x. Bound the wait through the driver's construction timeouts instead.
    """
    if driver is None:
        return False, "disabled"
    try:
        driver.verify_connectivity()
        return True, "ok"
    except Exception as e:  # noqa: BLE001 - documented as ":raises Exception:"
        # Realistically ServiceUnavailable, AuthError, or — new in 6.0 —
        # ConnectionAcquisitionTimeoutError, which moved from Neo4jError to
        # DriverError. A 5.x-shaped `except Neo4jError` would miss the last one.
        return False, f"error: {type(e).__name__}: {e}"


def _plain(value: Any) -> Any:
    """Coerce driver-native values to stdlib/JSON-safe ones. Recursive.

    Defence in depth rather than belt-and-braces: the primary discipline is that
    every read RETURNs named scalars and never a whole node, so driver objects
    should not arrive here at all. This catches the one that does — and one is
    enough to break a caller that assumed it could serialise the result.
    """
    if isinstance(value, (DateTime, Date, Time)):
        # isoformat() on the driver type, not to_native() then isoformat(): the
        # native conversion narrows nanosecond precision to microseconds, and for a
        # bi-temporal store the timestamp IS the identity of a row's validity.
        return value.isoformat()
    if isinstance(value, Duration):
        return str(value)
    if isinstance(value, Point):
        # list(value) for the coordinates AND a separate srid lookup, because Point
        # subclasses tuple: the coordinates are the tuple payload and srid is a
        # property beside it. json.dumps on a raw Point therefore SUCCEEDS and
        # silently emits a bare array with the srid gone. Duration has the same
        # shape (it subclasses tuple[int,int,int,int]) — so these two are the
        # dangerous pair: Date/Time/DateTime at least raise TypeError and get
        # caught in testing, while these two corrupt quietly.
        return {"srid": value.srid, "coordinates": list(value)}
    if isinstance(value, (Node, Relationship, Path)):
        raise TypeError(
            f"graph type {type(value).__name__} reached the serialisation seam. "
            "A verb must project named properties (RETURN r.rule_id AS rule_id), "
            "never a whole entity — Record.data() flattens a Node to its properties "
            "and DROPS its labels and element_id, and flattens a Relationship to "
            "(start, type, end) dropping every property. Silently allowing it would "
            "lose data that the caller cannot know is missing."
        )
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def read(driver: Driver, cypher: str, database: str, **params: Any) -> list[dict[str, Any]]:
    """Run a read and return plain dicts. The only read path in the codebase.

    `execute_query` is the 6.x recommendation and is a MANAGED transaction — it
    retries transient failures, which explicit sessions do not. It returns an
    EagerResult (a NamedTuple of records/summary/keys): fully buffered, so there
    is no open cursor bound to a live session after this returns. That property is
    what makes it safe at the seam — but it also means every read verb MUST carry
    a LIMIT, because `records` is a materialised list.

    `routing_` is passed explicitly even though this is a single Community
    instance with no routing table: the access mode still goes on the wire, the
    default is WRITE rather than READ, and 6.0 validates it on non-routing drivers
    too. Zero cost, and it is the only line that would need to change if this ever
    moved behind `neo4j://`.

    `dict(r)`, not `r.data()`: data() runs the driver's RecordExporter, which
    flattens Nodes and Relationships lossily before `_plain` could object. Reading
    the raw values lets the graph-type guard actually fire.

    CAVEAT on `**params`: a Cypher parameter whose name ends in a single
    underscore cannot be passed this way — execute_query raises ValueError for it,
    reserving that shape for its own `database_`/`routing_` keywords. No parameter
    in this codebase is named that way; if one ever is, route it through
    `parameters_` instead.
    """
    records, _summary, _keys = driver.execute_query(
        cypher,
        database_=database,
        routing_=RoutingControl.READ,
        **params,
    )
    return [_plain(dict(r)) for r in records]


def write(driver: Driver, cypher: str, database: str, **params: Any) -> list[dict[str, Any]]:
    """Run a write and return plain dicts. Managed transaction, same as `read`."""
    records, _summary, _keys = driver.execute_query(
        cypher,
        database_=database,
        routing_=RoutingControl.WRITE,
        **params,
    )
    return [_plain(dict(r)) for r in records]
