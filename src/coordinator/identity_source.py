"""Where a persona's identity is READ from — the graph, or the card it came from.

WHAT THIS CHANGES, AND WHAT IT DELIBERATELY DOES NOT.

ADR-012 makes the graph the system of record and demotes the card to an origin and a
reset target. That claim was not true while nothing read the graph: dropping the database
would have cost nothing, which is the precise opposite of a system of record. This module
makes it true, by overlaying the graph's modelled identity subset onto the card before the
prompt is built.

It does NOT add identity-node content to the prompt. ADR-018 measured that and closed the
line — voice distinctiveness 0.804 with injection off, 0.625 with it on, and three
deliberate reframings at 0.708 / 0.542 / 0.500, every one below the off-baseline — and
named "wiring a rich identity store into the prompt the way the last attempt was wired"
as the thing most likely to be regretted. So the overlay adds ZERO tokens and the prompt
is asserted byte-identical with the flag on or off
(tests/backend/coordinator/test_graph_sourced_identity.py). The graph replaces the card as
the SOURCE; it does not become extra prompt text.

Byte-identity is achievable only because the round trip is lossless: `identity_nodes` and
`card_from_nodes` are inverses over `SOURCE_FIELD_KIND`, verified per-persona by
test_identity_round_trip.py. That is what makes this a sourcing change rather than a
content change.

WHY THE GUARD LIVES HERE. `IdentityRepository.identity()` has no try/except, unlike
`Neo4jRuleRepository.standing_rules()` which documents "Returns [] on a graph outage
rather than raising". Called naively from the prompt path a Neo4j blip would therefore be
a 500 on her turn. Every failure here — flag off, driver absent, outage, empty graph,
persona not in the graph, a node naming an undeclared source_field — falls back to the
card and says so in the returned source.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: Returned alongside the card so callers can log/assert which source won.
SOURCE_CARD = "card"
SOURCE_GRAPH = "graph"

#: Personas already announced as graph-sourced. The prompt is byte-identical either way,
#: so NOTHING in the output reveals which source won -- which makes this the only way an
#: operator can confirm the flag is doing anything. Logged ONCE per persona rather than
#: per turn: the builder is lru_cached so a per-turn log would be misleading anyway, and
#: an INFO line on every turn is how a useful signal becomes noise that gets filtered.
_ANNOUNCED: set = set()


def _overlay(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    """Merge `src` into `dst` in place, recursing into dicts only.

    A modelled leaf REPLACES the card's value rather than merging into it: `lore` is a
    list the graph owns in full, and merging lists would duplicate every entry on every
    rebuild. Nested dicts recurse so that `behavior.traits` can be graph-owned while
    `behavior.some_unmodelled_key` stays card-owned.
    """
    for key, value in src.items():
        if isinstance(value, dict):
            existing = dst.get(key)
            if not isinstance(existing, dict):
                existing = {}
                dst[key] = existing
            _overlay(existing, value)
        else:
            dst[key] = value


def identity_overlay(card: Dict[str, Any], nodes: Any) -> Dict[str, Any]:
    """The card with the graph's modelled subset laid over it. Pure — no I/O.

    Kept separate from the read so it is unit-testable with no graph, the same shape
    `build_graph_rules_block` uses (it takes rows as an argument and never reaches for a
    repository).
    """
    from .identity_to_card import card_from_nodes

    rebuilt = card_from_nodes(nodes)
    merged = dict(card)
    _overlay(merged, rebuilt)
    return merged


def overlay_from_graph(card: Optional[Dict[str, Any]],
                       selector: Optional[str]) -> Tuple[Optional[Dict[str, Any]], str]:
    """Lay the graph's identity over an ALREADY-RESOLVED card. Returns (card, source).

    Takes the card rather than resolving it, for two reasons. It keeps the graph I/O as
    the only thing this function does, matching `build_graph_rules_block`'s shape — the
    renderer takes its rows as an argument and never reaches for a repository. And it
    leaves card resolution on `prompt_builder`'s own patchable name: three test modules
    monkeypatch `pb.resolve_persona_to_card`, and a fresh import inside here would
    silently bypass every one of them, which is how a prompt test starts asserting
    against the real gwen card without anyone noticing.

    `source` is SOURCE_GRAPH only when the graph actually supplied nodes. Every other
    path — flag off, no driver, outage, empty graph, undeclared source_field — returns
    SOURCE_CARD, including the ones that failed.
    """
    if not card:
        return card, SOURCE_CARD

    try:
        from .config import get_settings

        cfg = get_settings().graph
        if not (cfg.enabled and cfg.identity_source):
            return card, SOURCE_CARD

        from . import startup

        driver = startup.get_neo4j_driver()
        if driver is None:
            return card, SOURCE_CARD

        from .repositories.identity_repository import IdentityRepository

        persona_id = card.get("key") or selector
        limit = _read_limit(card)
        nodes = IdentityRepository(driver, cfg.database).identity(
            persona_id, limit=limit
        )
        if not nodes:
            # An empty graph is the normal state before seed_identity has ever run. Not
            # an error, and not worth a warning on every turn.
            return card, SOURCE_CARD

        ok, why = _passes_floor(card, nodes, limit)
        if not ok:
            # LOUD, unlike the empty case: an empty read is a known state, a PARTIAL one
            # is a fault that produces a structurally valid card. Measured on gwen: a
            # read missing 17 nodes left her with 4 of 21 lore entries and nothing
            # reported it.
            logger.error(
                "[Graph] identity read REJECTED for %s (using card): %s", selector, why,
            )
            return card, SOURCE_CARD

        if persona_id not in _ANNOUNCED:
            _ANNOUNCED.add(persona_id)
            logger.info(
                "[Graph] identity SOURCED FROM GRAPH for %s (%d nodes, %s) — "
                "the prompt is byte-identical to the card build; this line is the only "
                "way to tell the flag is active",
                persona_id, len(nodes), why,
            )
        return identity_overlay(card, nodes), SOURCE_GRAPH
    except Exception as exc:  # noqa: BLE001 — a prompt must never fail on a store blip
        logger.warning(
            "[Graph] identity read skipped for %s (non-fatal, using card): %s",
            selector, exc,
        )
        return card, SOURCE_CARD


def _read_limit(card: Dict[str, Any]) -> int:
    """A limit that cannot silently truncate this persona's identity.

    `identity()` defaults to 256 and applies LIMIT after ORDER BY source_field, so an
    over-limit persona loses whole alphabetically-late fields rather than degrading
    evenly — and the result would still be a valid-looking card, which is the dangerous
    shape. Deriving the bound from the card the nodes were built from means the ceiling
    moves with the data instead of being a constant someone has to remember.
    """
    from .identity_from_card import identity_nodes

    try:
        return max(len(identity_nodes(card)) * 2, 64)
    except Exception:  # noqa: BLE001
        return 512

#: A graph supplying fewer than this fraction of the nodes the card implies is treated as
#: a partial read, not as evolution. Chosen conservatively rather than measured: a real
#: deletion retires a handful of entries, whereas a truncated or partially-failed read
#: loses a whole tail. The two are indistinguishable from the rows alone, so the split has
#: to be a judgement — and it is made in the safe direction, because the cost of a false
#: reject is "she is described from the card for one turn" while the cost of a false
#: accept is a silently gutted identity that still renders.
_FLOOR_RATIO = 0.5


def _passes_floor(card: Dict[str, Any], nodes: Any, limit: int) -> Tuple[bool, str]:
    """Reject a read that looks truncated or partial. Fails CLOSED to the card.

    WHY A PRESENCE CHECK IS NOT ENOUGH. `identity_overlay` replaces each field the nodes
    mention, so a read that returns *some* of `lore` replaces all 21 entries with the 4 it
    got — and the result is a structurally valid card that builds a normal-looking prompt.
    Pinecone shipped this exact failure shape on 2026-06-18: queries to infrequently-read
    namespaces "incorrectly returning empty results", a successful 200 with no rows. A
    store that fails by returning less, rather than by raising, needs a floor.

    Two checks, one exact and one heuristic:

    * TRUNCATION is exact. `identity()` applies LIMIT after `ORDER BY source_field`, so a
      read at the limit has lost whole alphabetically-late fields rather than degrading
      evenly. `len(nodes) >= limit` means the ceiling was reached and the tail is gone.
    * PARTIAL is a ratio, because a deletion and a partial failure look identical in the
      rows. See _FLOOR_RATIO.
    """
    n = len(nodes)
    if n >= limit:
        return False, (
            f"read returned {n} nodes at the limit of {limit}, so ORDER BY "
            "source_field has silently dropped the alphabetically-late fields"
        )
    try:
        from .identity_from_card import identity_nodes

        implied = len(identity_nodes(card))
    except Exception:  # noqa: BLE001
        return True, "card-implied count unavailable; truncation check passed"
    if implied and n < implied * _FLOOR_RATIO:
        return False, (
            f"read returned {n} nodes but the card implies {implied} "
            f"(floor {_FLOOR_RATIO:.0%}) — treating as a partial read, not evolution"
        )

    # PER-FIELD, because the aggregate ratio is too coarse to see the failure that
    # matters. Measured on gwen: a read of 111 of 128 nodes is 87% of the total and
    # clears the aggregate floor, while `lore` arrives with 4 of its 21 entries -- and
    # because the overlay REPLACES a field it mentions, that silently deletes 17 lore
    # entries and still renders a valid prompt. A field is checked only when the graph
    # mentions it at all; a field absent from the read keeps its card value untouched,
    # which is the whole reason absence is safe and partial presence is not.
    from collections import Counter

    got = Counter(n_.get("source_field") for n_ in nodes)
    want = Counter(n_["source_field"] for n_ in identity_nodes(card))
    for field, want_n in want.items():
        got_n = got.get(field, 0)
        if got_n and want_n and got_n < want_n * _FLOOR_RATIO:
            return False, (
                f"field {field!r} arrived with {got_n} of {want_n} nodes "
                f"(floor {_FLOOR_RATIO:.0%}); the overlay replaces a field it sees, so "
                "accepting this would silently delete the rest"
            )
    return True, f"{n} nodes, card implies {implied}"
