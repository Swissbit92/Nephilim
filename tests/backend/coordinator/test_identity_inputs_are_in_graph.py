"""Every card field that reaches the <identity> block must be IN the identity graph.

WHY THIS EXISTS. ADR-012 makes the graph the system of record: the card is the origin
and a reset target, not a second live copy. That turns "is this field in the graph?"
into "would dropping the card lose this?" -- and the first pass of ADR-018 got it
wrong for two fields, for a reason worth pinning rather than just fixing.

`style` was excluded as a "frontend theme selector" and `voice.tics` as a "voice
exemplar". Both are false: prompt_builder renders `style` as the literal first
sentence of the identity block ("You are Gwen, seductive, analytical, devoted.") and
cv_summarizer passes `style` as `Tone:` and `voice.tics` as `Quirks/Tics:` to the LLM
that generates the paragraph. They were classified by their NAMES, which sound like
presentation, instead of by where they are READ.

So the guard is not "style is a node" -- that is the instance. The guard is that each
identity INPUT is accounted for, either as a node or as a property on the Persona
node, and it names the four inputs explicitly so adding a fifth to the prompt without
adding it here fails loudly.
"""
from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]

#: The card fields cv_summarizer._make_cv_summary and prompt_builder's identity block
#: actually read. Keep in step with cv_summarizer.py's prompt template.
IDENTITY_INPUTS = ("display_name", "style", "lore", "voice.tics")


def _card():
    return json.loads((ROOT / "personas" / "gwen.json").read_text())


def test_every_identity_input_is_a_node_or_a_persona_property():
    from src.coordinator.identity_from_card import identity_nodes

    card = _card()
    nodes = identity_nodes(card)
    fields = {n["source_field"] for n in nodes}

    # display_name is a scalar label, stored as a Persona property by apply_card.
    persona_properties = {"display_name"}

    unaccounted = [
        f for f in IDENTITY_INPUTS
        if f not in fields and f not in persona_properties
    ]
    assert not unaccounted, (
        f"{unaccounted} reach the <identity> block but are not in the identity graph; "
        "under ADR-012 dropping the card would lose them"
    )


def test_style_and_tics_are_traits_with_their_own_kinds():
    from src.coordinator.identity_from_card import identity_nodes

    nodes = identity_nodes(_card())
    style = [n for n in nodes if n["source_field"] == "style"]
    tics = [n for n in nodes if n["source_field"] == "voice.tics"]

    assert len(style) == 1 and style[0]["kind"] == "style"
    assert style[0]["text"] == _card()["style"]
    assert len(tics) == len(_card()["voice"]["tics"])
    assert {t["kind"] for t in tics} == {"tic"}


def test_voice_exemplars_stay_out_and_say_why():
    """greeting/signoff are genuinely consumer-layer -- excluded, with a reason."""
    from src.coordinator.identity_from_card import EXCLUDED_LEAVES

    for leaf in ("voice.greeting", "voice.signoff"):
        assert leaf in EXCLUDED_LEAVES
        assert EXCLUDED_LEAVES[leaf].strip(), f"{leaf} excluded without a reason"


def test_apply_card_writes_display_name_onto_the_persona_node():
    """The Cypher must SET display_name, not merely MERGE the Persona."""
    src = (ROOT / "src" / "coordinator" / "repositories"
           / "identity_repository.py").read_text()
    assert "SET p.display_name = $display_name" in src, (
        "apply_card no longer stores display_name on the Persona node, so the name "
        "rendered in 'You are {who}, ...' exists only in the card"
    )
