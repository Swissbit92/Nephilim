"""card -> identity nodes -> card must be lossless for the modelled subset.

WHY THIS IS THE ACCEPTANCE CRITERION AND NOT PROMPT EQUIVALENCE. ADR-012 makes the
graph the system of record, so the question that matters is "would dropping the card
lose anything?". The tempting test -- assert a graph read reproduces the <identity>
block -- cannot be written: that block is an LLM paragraph generated at temperature
0.9, so it differs on every call. test_summaries_are_tracked.py exists because that
text is not reproducible. Pushing the comparison to BEFORE the LLM makes it exact.

The second test here matters as much as the first. A round-trip check that has only
ever been run on matching data has never been observed distinguishing anything, and
this repo has already shipped two guards that passed for that reason. So the diff is
fed an induced loss and must report it.
"""
from __future__ import annotations

import copy
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _card(key: str = "gwen"):
    return json.loads((ROOT / "personas" / f"{key}.json").read_text())


@pytest.mark.parametrize("persona", ["gwen"])
def test_round_trip_is_lossless_for_modelled_fields(persona):
    from src.coordinator.identity_from_card import identity_nodes
    from src.coordinator.identity_to_card import round_trip_diff

    card = _card(persona)
    report = round_trip_diff(card, identity_nodes(card))
    assert report["identical"], (
        f"round-trip lost data: missing={report['missing_from_graph']} "
        f"changed={report['changed']} extra={report['not_in_card']}"
    )
    assert report["fields_compared"] >= 20, (
        "suspiciously few fields compared — a projection that compares almost nothing "
        "reports 'identical' for the wrong reason"
    )


def test_the_diff_actually_detects_a_loss():
    """Validate the instrument: drop one node and the diff must name the field."""
    from src.coordinator.identity_from_card import identity_nodes
    from src.coordinator.identity_to_card import round_trip_diff

    card = _card()
    nodes = identity_nodes(card)
    lore = [n for n in nodes if n["source_field"] == "lore"]
    assert len(lore) > 1
    damaged = [n for n in nodes if n is not lore[-1]]

    report = round_trip_diff(card, damaged)
    assert not report["identical"]
    assert "lore" in report["changed"], report


def test_the_diff_detects_a_reordering():
    """source_index, not arrival order, must define list order."""
    from src.coordinator.identity_from_card import identity_nodes
    from src.coordinator.identity_to_card import card_from_nodes

    card = _card()
    nodes = identity_nodes(card)
    rebuilt_shuffled = card_from_nodes(list(reversed(nodes)))
    rebuilt_normal = card_from_nodes(nodes)
    assert rebuilt_shuffled == rebuilt_normal, (
        "rebuild depends on iteration order, so a differently-ordered graph read "
        "would produce a different card"
    )
    assert rebuilt_normal["lore"] == [x.strip() for x in card["lore"] if x.strip()]


def test_a_single_element_list_does_not_collapse_to_a_scalar():
    """The reason SOURCE_FIELD_KIND is declared rather than inferred."""
    from src.coordinator.identity_from_card import identity_nodes
    from src.coordinator.identity_to_card import card_from_nodes

    card = _card()
    card["lore"] = ["the only entry"]
    rebuilt = card_from_nodes(identity_nodes(card))
    assert rebuilt["lore"] == ["the only entry"], (
        "a one-element list came back as a scalar — inference would do this silently"
    )


def test_an_undeclared_source_field_raises_rather_than_guessing():
    from src.coordinator.identity_to_card import card_from_nodes, UndeclaredSourceField

    with pytest.raises(UndeclaredSourceField):
        card_from_nodes([{"source_field": "behavior.invented", "source_index": 0,
                         "text": "x"}])


def test_source_field_kind_covers_every_node_a_builder_emits():
    """A builder added without a SOURCE_FIELD_KIND entry breaks reconstruction."""
    from src.coordinator.identity_from_card import identity_nodes, SOURCE_FIELD_KIND

    for path in sorted((ROOT / "personas").glob("*.json")):
        card = json.loads(path.read_text())
        undeclared = {n["source_field"] for n in identity_nodes(card)} - set(SOURCE_FIELD_KIND)
        assert not undeclared, f"{path.name}: {sorted(undeclared)} not in SOURCE_FIELD_KIND"
