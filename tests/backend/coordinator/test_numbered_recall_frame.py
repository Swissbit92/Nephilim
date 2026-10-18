"""The numbered recall frame (ADR-022) — the format the A/B measured best.

What is pinned here is the SHAPE, because the shape is the finding: numbering
turns a judgement into a lookup, and the measured win (invention 0.875 -> 0.000,
wrongly-refused 0.375 -> 0.208) is attributable to that and to the find-it-first
instruction, not to anything else that changed.
"""

from __future__ import annotations

import pytest

from src.coordinator.context_framing import (
    RECALL_INSTRUCTION,
    frame_injected_context,
    frame_numbered_facts,
)


def test_nothing_to_recall_injects_nothing():
    """The dormant path must stay byte-identical. An empty frame would change the
    prompt for every persona with no memory, which is all of them today."""
    assert frame_numbered_facts("gwen", None, []) is None
    assert frame_numbered_facts("gwen", None, [], []) is None
    assert frame_numbered_facts("gwen", None, ["   "], ["  "]) is None


def test_facts_are_numbered_continuously_across_both_sections():
    """The numbers are the citation target. If the two sections restarted at [1]
    there would be two facts called [1] and the lookup becomes ambiguous."""
    out = frame_numbered_facts("gwen", None, ["a", "b"], ["c"])
    assert "[1] a" in out and "[2] b" in out and "[3] c" in out
    assert out.count("[1]") == 1


def test_the_find_it_first_instruction_is_present_and_ordered_lookup_before_decline():
    """THE mechanism. The decline clause alone took invention to 0.000 and
    wrongly-refused to 0.708 — she declined things she had been told. Adding the
    lookup BEFORE the decline took wrongly-refused to 0.208 while holding
    invention at zero. Order is the finding, not decoration."""
    out = frame_numbered_facts("gwen", None, ["x"])
    assert RECALL_INSTRUCTION in out
    look = out.index("FIND IT IN THE")
    decline = out.index("say you don't know")
    assert look < decline, "the decline clause precedes the lookup — that is arm B"


def test_the_instruction_keeps_the_decline_clause_byte_identical_to_the_arm_it_beat():
    """The A/B is only attributable to the lookup if the refusal rule did not also
    change. These exact words were in both arms."""
    for phrase in ("say you don't know", "do not guess and do not fill it in",
                   "Inventing a detail about his real life is worse"):
        assert phrase in RECALL_INSTRUCTION


def test_the_persona_is_named_and_told_not_to_recite_the_numbers():
    """0 of 40 measured replies leaked a bracket number. The instruction not to is
    why this is a note-to-self rather than a format she imitates — which is the
    one defence against the mechanism the module docstring warns about."""
    out = frame_numbered_facts("gwen", None, ["x"])
    assert "gwen" in out
    assert "never recite" in out.lower() and "numbers" in out.lower()


def test_it_is_wrapped_in_the_non_echoable_tag_like_the_prose_frame():
    """Same wrapper as `frame_injected_context`, so downstream handling of
    `<remembered>` does not need a second case."""
    out = frame_numbered_facts("gwen", None, ["x"])
    assert out.startswith("<remembered>") and out.endswith("</remembered>")


def test_the_prose_frame_still_exists_and_is_unchanged():
    """ADR-022 ADDS a renderer; it does not replace the one ADR-006 built. The
    prose frame is the voice-safe option and the numbered one is the
    accuracy-best option, and which is correct is NOT settled — the
    distinctiveness run was deliberately skipped by operator decision."""
    assert frame_injected_context("gwen", None, "some narrative") is not None
    assert frame_injected_context("gwen", None, "") is None


@pytest.mark.parametrize("n", [1, 5, 40])
def test_numbering_survives_any_list_length(n):
    out = frame_numbered_facts("gwen", None, [f"fact {i}" for i in range(n)])
    assert f"[{n}]" in out
