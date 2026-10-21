"""`appearance` is a lore kind that reaches the graph and NOT the prose prompt.

Why the kind exists: `idn.cq01` ("What do you look like?") was unanswerable, and
its own docstring recorded the gap as the DATA's rather than the query's — her
appearance sat inside 21 free-text lore strings of median length 188 characters
which mostly describe what she DOES. Measured 2026-10-21 on the live backend:
she refused her own hair colour, eye colour and height on 5 of 15 probes while
`blue eyes`, `155`, `petite` and `red hair` were all already in the built prompt.

🔴 Why it must not reach the prose prompt: ADR-019 CLOSED "add identity content
as extra prompt text" on a measured voice cost (0.804 -> 0.625; three reframes
0.708 / 0.542 / 0.500). The field is for the graph and the numbered self-facts
lookup, which is a different mechanism — judgement converted to lookup.

⚠️ THE TRAP THIS PINS, reproduced immediately on the first attempt. Adding the
field re-fingerprinted the card, so an LLM rewrote the cached `<identity>`
paragraph and the prompt went 4246 -> 4435 chars with her self-description
silently replaced. That is the ADR-016 drift verbatim, which cost a correction
once already for `emotional_profile.sliders`. `personas/_summaries/` being
tracked in git is the only reason it was visible — it showed up as a one-file
diff.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from coordinator.cv_summarizer import _FINGERPRINT_EXCLUDE, _normalize_for_fingerprint
from coordinator.identity_from_card import (
    MODELLED_FIELDS,
    SOURCE_FIELD_KIND,
    identity_nodes,
)
from coordinator.identity_shapes import (
    LORE_KIND_DEFINITIONS,
    LORE_KINDS,
    SHAPES,
)

_CARD = pathlib.Path(__file__).resolve().parents[3] / "personas" / "gwen.json"


def _card() -> dict:
    return json.loads(_CARD.read_text())


class TestTheVocabulary:
    def test_appearance_is_a_lore_kind(self):
        assert "appearance" in LORE_KINDS

    def test_every_lore_kind_carries_a_definition(self):
        """The repo's own standing invariant: a declared term is defined."""
        assert set(LORE_KINDS) == set(LORE_KIND_DEFINITIONS)

    def test_the_definition_says_why_it_was_split_from_lore(self):
        d = LORE_KIND_DEFINITIONS["appearance"]
        assert "idn.cq01" in d, "must name the question that was unanswerable"
        assert len(d) >= 60

    def test_lore_no_longer_claims_appearance(self):
        """Two kinds both claiming appearance is the ambiguity that drove the
        behaviour/policy split. `lore` was narrowed when `appearance` was added."""
        assert "appearance" not in LORE_KIND_DEFINITIONS["lore"].split("NARROWED")[0]

    def test_it_adds_no_new_label(self):
        """A new LABEL would need a competency question first (C17, run
        backwards). A new KIND does not — `LoreEntry` is already demanded."""
        assert set(SHAPES) == {"LoreEntry", "Trait", "Boundary", "Expertise"}


class TestTheCardMapping:
    def test_appearance_is_declared_as_a_list_field(self):
        assert SOURCE_FIELD_KIND["appearance"] == "list"
        assert "appearance" in MODELLED_FIELDS

    def test_the_card_produces_appearance_nodes(self):
        nodes = identity_nodes(_card())
        app = [n for n in nodes if n.get("kind") == "appearance"]
        assert app, "gwen's card declares appearance facts"
        assert all(n["source_field"] == "appearance" for n in app)

    def test_every_appearance_line_names_her_explicitly(self):
        """arXiv 2508.12630 measures +7.6pp factual recall for an explicit
        subject on the fact itself. Measured here: a fact written without one
        ("Is vegetarian", subject in the heading) was answered "I'm vegetarian",
        claiming the OPERATOR's trait as her own."""
        app = [n for n in identity_nodes(_card()) if n.get("kind") == "appearance"]
        unnamed = [n["text"] for n in app if not n["text"].startswith("Gwen")]
        assert not unnamed, f"these lines carry no subject: {unnamed}"

    def test_appearance_sorts_before_lore(self):
        """`idn.cq01` orders by (source_field, source_index), so a reader taking
        the first N nodes gets checkable physical facts rather than style prose.
        This is inherited from the sort, not re-applied by the caller."""
        assert "appearance" < "lore"

    def test_a_card_without_the_field_is_unaffected(self):
        card = _card()
        card.pop("appearance", None)
        nodes = identity_nodes(card)
        assert not [n for n in nodes if n.get("kind") == "appearance"]
        assert len(nodes) == 128, (
            "removing the field must return the pre-2026-10-21 node count"
        )


class TestItNeverReachesTheProsePrompt:
    """ADR-019's boundary. The moment these fail, the change has become the
    closed one (identity content as extra prompt text)."""

    def test_appearance_is_excluded_from_the_cv_fingerprint(self):
        assert "appearance" in _FINGERPRINT_EXCLUDE

    def test_the_fingerprint_ignores_the_field_entirely(self):
        with_field = _normalize_for_fingerprint(_card())
        without = _card()
        without.pop("appearance", None)
        assert _normalize_for_fingerprint(without) == with_field, (
            "the normalized card must be identical with and without the field, "
            "or a cached <identity> summary is invalidated and an LLM rewrites it"
        )

    def test_the_built_prompt_is_byte_identical_without_the_field(self):
        """The assertion that IS the boundary, not a proxy for it."""
        from coordinator.prompt_builder import build_system_prompt

        build_system_prompt.cache_clear()
        with_field = build_system_prompt("gwen")

        stripped = _card()
        stripped.pop("appearance", None)
        backup = _CARD.read_text()
        _CARD.write_text(json.dumps(stripped, indent=2, ensure_ascii=False) + "\n")
        build_system_prompt.cache_clear()
        try:
            without_field = build_system_prompt("gwen")
        finally:
            _CARD.write_text(backup)
            build_system_prompt.cache_clear()

        h = lambda t: hashlib.sha256(t.encode()).hexdigest()[:16]  # noqa: E731
        assert with_field == without_field, (
            f"appearance leaked into the prose prompt: "
            f"{h(without_field)} ({len(without_field)}) -> "
            f"{h(with_field)} ({len(with_field)})"
        )
