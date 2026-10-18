"""What the graph knows, fetched once per turn, before she speaks.

ADR-022 part 1. **Unconditional and deterministic — never a tool call.** The model
does not decide whether to consult the graph, because that decision is the
measured failure: across 70 generations she called a `recall` tool 12 times, a
**17.1%** firing rate (Wilson 95% [0.10, 0.28]), while recall when it DID fire was
11/11. The mechanism worked and the choosing did not, so the choosing is removed.
This is the same move the image path already made — `generate_image` stopped being
a tool call and became `generation_intent()`, 18/18 on boundary cases, shipped on
an exact McNemar p=0.00098.

**THE RESULT DOES NOT ENTER THE PROMPT, and that is load-bearing.** ADR-019 splits
"wire the graph into the prompt" into (a) ADD identity content as extra prompt text
— **closed**, measured at a distinctiveness cost of 0.804 → 0.625 — and (b) SWITCH
the source of the existing block, which it adopts with byte-identity as the
enforced boundary against (a). A first draft of ADR-022 proposed (a) without
noticing. What this module returns is the ground truth an assertion gate checks a
draft AGAINST; it is never concatenated into `system` or the user turn. The
byte-identity test stays green, and it is the thing that would catch a regression
here.

**Failure policy, copied deliberately rather than inherited.** `graph_queries`
does NOT swallow outages — `graph_driver.read` propagates, by design. So the
try/except lives here, in the shape of the rules read at `routes/chat.py:842-844`,
and for the reason stated at `neo4j_rule_repository.py:318-319`: *a graph that is
down must cost rules, not the turn.* A degraded read is reported as degraded and
never as an empty graph, because "the graph says nothing about this" and "the
graph could not be reached" must never collapse into the same answer — the second
one must not be allowed to license an abstention.

**Latency.** A second graph read is a pooled, buffered `execute_query` on the same
request thread; the turn is dominated by generation at ~16 tok/s. The cost worth
watching is not this read.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Ceiling on facts carried into the gate. Not a prompt budget — nothing here is
#: rendered — but an unbounded set would make the gate's own classification prompt
#: grow without limit, and that one IS a model call.
MAX_FACTS = 64


@dataclass(frozen=True)
class GroundingContext:
    """Everything the graph will support her asserting on this turn.

    `degraded` is the field that must never be ignored. An empty `facts` with
    `degraded=False` means the graph genuinely holds nothing; an empty `facts`
    with `degraded=True` means the read failed. A gate that treats the second as
    the first converts a Neo4j restart into a wave of abstentions.
    """

    persona_id: str
    facts: tuple[str, ...] = ()
    degraded: bool = False
    reason: str = "ok"
    elapsed_ms: int = 0
    sources: tuple[str, ...] = field(default=())

    @property
    def usable(self) -> bool:
        """True when this context may be used to REFUSE something.

        A degraded read may still be used to SUPPORT an assertion — anything it
        returned is real. It may never be used to deny one.
        """
        return not self.degraded

    def summary(self) -> dict[str, Any]:
        return {"facts": len(self.facts), "degraded": self.degraded,
                "reason": self.reason, "elapsed_ms": self.elapsed_ms,
                "sources": list(self.sources)}


def _empty(persona_id: str, reason: str, started: float) -> GroundingContext:
    return GroundingContext(
        persona_id=persona_id, degraded=True, reason=reason,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


def build_grounding_context(persona_id: str,
                            driver: Any = None,
                            database: str | None = None) -> GroundingContext:
    """Read everything the graph can say about this persona, once.

    Deliberately composed from `graph_queries.CATALOG` rather than hand-written
    Cypher: those functions are the typed query layer, each carries the competency
    question it answers, and each is unit-tested. A second set of Cypher here
    would be a second thing to keep correct.

    ⚠️ Only the questions whose answers are ASSERTABLE are used. `idn.cq04` is a
    completeness changeset and `idn.cq06` is a structural refusal — neither is a
    fact she could state, and including them would put audit output into a set
    that exists to decide what she may say.
    """
    started = time.monotonic()
    try:
        from .. import startup
        from ..config import get_settings
        from ..graph_queries import CATALOG

        cfg = get_settings().graph
        # `GRAPH_ENABLED` governs DEFAULT driver resolution, not an explicitly
        # injected one. A caller that hands in a driver has already resolved
        # availability, and letting a global flag veto an injected dependency
        # makes this module untestable without env manipulation — which the
        # hermetic settings fixture in `tests/conftest.py` deliberately prevents.
        if driver is None:
            if not cfg.enabled:
                return _empty(persona_id, "graph_disabled", started)
            driver = startup.get_neo4j_driver()
        if driver is None:
            return _empty(persona_id, "no_driver", started)
        drv = driver
        db = database or cfg.database

        # The assertable subset. Each entry answers a competency question whose
        # answer is a claim she could make out loud.
        assertable = ("idn.cq01", "idn.cq02", "idn.cq03", "rul.cq03")
        facts: list[str] = []
        used: list[str] = []
        for cq in assertable:
            q = CATALOG.get(cq)
            if q is None:
                continue
            rows = q.fn(drv, db, persona_id)
            texts = _texts_of(rows)
            if texts:
                used.append(cq)
                facts.extend(texts)

        return GroundingContext(
            persona_id=persona_id,
            facts=tuple(facts[:MAX_FACTS]),
            degraded=False,
            reason="ok",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            sources=tuple(used),
        )
    except Exception as exc:  # noqa: BLE001 — see the module docstring
        logger.warning("[Grounding] read failed for %s (non-fatal, DEGRADED): %s",
                       persona_id, exc)
        return _empty(persona_id, f"error:{type(exc).__name__}", started)


def _texts_of(rows: Any) -> list[str]:
    """Flatten a catalog answer into assertable strings.

    Catalog functions return a list of dicts, a dict of lists (`rul.cq03`'s three
    buckets), or a dict. Handled here rather than by normalising the catalog,
    because the catalog's shapes are dictated by the questions they answer and
    bending them to suit this caller would be the tail wagging the dog.
    """
    out: list[str] = []
    if isinstance(rows, dict):
        for value in rows.values():
            if isinstance(value, list):
                out.extend(_texts_of(value))
        return out
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and isinstance(r.get("text"), str):
                out.append(r["text"])
    return out
