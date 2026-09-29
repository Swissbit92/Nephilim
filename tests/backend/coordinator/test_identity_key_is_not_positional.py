"""An identity node's key must not be its POSITION in a card list.

ADR-018's central mechanical promise is that "a rebuild must regenerate CONTENT while
PRESERVING her annotations and edges". The annotation properties (`salience`,
`reinforced_count`, `last_referenced`) are named only in the MERGE's `ON CREATE` branch,
so a rebuild structurally cannot wipe them — that half works.

But preservation of the VALUE is worthless if the value is preserved on the wrong node.
`MERGE (n:Label:CurrentIdentity {persona_id, source_field, source_index})` keyed identity
to LIST POSITION, so an operator reordering a card list silently moved every annotation
after the edit point onto a different entry. Measured on gwen before the fix: moving one
of 21 lore entries from last to first changed the text under ALL 21 lore keys.

`identity_from_card.identity_nodes`' own docstring named the hazard and claimed the guard
was "`source_hash` plus the completeness check". Neither can fire:

  * `source_hash` is a PROPERTY written by the same MERGE that matched the wrong node, so
    `apply_card` overwrites it in place. Nothing ever compares the old value to the new.
  * a reorder changes no leaf and adds none, so coverage is unchanged by construction.

This is the repo's recorded `fix_by_shape_not_by_file` shape: an overloaded field produces
a confident wrong answer rather than a gap, so every test stays green.

The migration was free at the moment it was made — `reinforced_count` was 0 on all 128
live nodes, so no learned annotation existed to mis-assign yet. Ecosystem ADR-012 names
that window explicitly ("the first non-card write closes it permanently"), which is why
this landed before the write path rather than after.
"""
from __future__ import annotations

import copy

import pytest

from src.coordinator.identity_from_card import identity_nodes
from src.coordinator.identity_to_card import card_from_nodes
from src.coordinator.prompt_builder import resolve_persona_to_card

#: The MERGE key as the repository actually writes it.
def _key(n):
    return (n["persona_id"], n["source_field"], n["source_key"])


def _by_key(card):
    return {_key(n): n["text"] for n in identity_nodes(card)}


@pytest.fixture
def gwen():
    return resolve_persona_to_card("gwen")


def test_reordering_a_list_does_not_move_any_key(gwen):
    """THE regression guard. Fails on a positional MERGE key for all 21 lore entries."""
    before = _by_key(gwen)
    c2 = copy.deepcopy(gwen)
    lore = list(c2["lore"])
    lore.insert(0, lore.pop())
    c2["lore"] = lore

    after = _by_key(c2)
    assert set(before) == set(after), "a reorder invented or dropped a key"
    moved = {k for k in before if after[k] != before[k]}
    assert not moved, (
        f"{len(moved)} keys point at different text after a pure reorder — every "
        f"annotation on those nodes has silently retargeted")


def test_reordering_still_reorders_the_REBUILT_CARD(gwen):
    """Stability of identity must not cost fidelity of content.

    `card_from_nodes` orders lists by `source_index`, so the index has to keep tracking
    position even though it no longer keys the node. If this passes while the test above
    fails, identity was made stable by freezing the card, which is the wrong fix.
    """
    c2 = copy.deepcopy(gwen)
    lore = list(c2["lore"])
    lore.insert(0, lore.pop())
    c2["lore"] = lore
    assert card_from_nodes(identity_nodes(c2))["lore"] == c2["lore"]


def test_editing_an_entry_mints_a_new_key(gwen):
    """A text edit SHOULD move the key — it is a different fact.

    The asymmetry is the design: a reorder is silent and accidental, a rewording is
    deliberate and visible. Card-origin content is the operator's to change (ADR-018), so
    losing annotations on a rewording is a consequence they chose; losing them on a
    reorder is one nobody chose. Pinned so a future "fix" for the rewording case cannot
    quietly reintroduce positional keys.
    """
    c2 = copy.deepcopy(gwen)
    c2["lore"] = list(c2["lore"])
    c2["lore"][0] = c2["lore"][0] + " And one more clause."
    assert set(_by_key(gwen)) != set(_by_key(c2))


def test_duplicate_text_in_one_field_still_yields_distinct_nodes():
    """A content-derived key must not collapse two identical entries into one.

    `source_index` distinguished them for free; a bare content hash would not.
    """
    card = {"key": "_scratch", "lore": ["same entry", "same entry", "other"]}
    nodes = identity_nodes(card)
    lore = [n for n in nodes if n["source_field"] == "lore"]
    assert len(lore) == 3
    assert len({n["source_key"] for n in lore}) == 3, "duplicate texts collided"


def test_the_key_is_stable_across_processes(gwen):
    """Keyed on content, so it must not depend on anything process-local."""
    assert _by_key(gwen) == _by_key(copy.deepcopy(gwen))


def test_every_node_carries_both_the_key_and_the_position(gwen):
    for n in identity_nodes(gwen):
        assert n["source_key"], "missing source_key"
        assert isinstance(n["source_index"], int), "source_index must survive as a property"


def test_a_node_with_no_source_key_is_expired_not_skipped():
    """Cypher three-valued logic: a null inside a list makes `NOT x IN y` NULL.

    Watched on the live graph. The pre-migration nodes carry no `source_key`, so
    `[n.source_field, n.source_key] IN $live` evaluated to NULL rather than false, `NOT
    NULL` is NULL, a NULL predicate never matches, and 128 stale nodes were silently left
    live — `apply_card` reported `expired: 0` and the graph held a DUPLICATE identity set
    with no error anywhere.

    Production survived only because `identity_source._passes_floor` rejected the
    over-large read and fell back to the card: a guard written for PARTIAL reads caught a
    DOUBLED one. Asserted here on the Cypher text because the condition is the fix, and a
    live-graph test would not run in the hermetic suite.
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[3] / "src" / "coordinator" / "repositories"
           / "identity_repository.py").read_text()
    assert "n.source_key IS NULL" in src, (
        "the expiry query must handle a null source_key explicitly — `NOT [x, null] IN y` "
        "is NULL, not true, so those rows are skipped")
    i = src.index("REMOVE n:CurrentIdentity")
    window = src[max(0, i - 700):i]
    assert "IS NULL" in window and "NOT [n.source_field, n.source_key] IN $live" in window
