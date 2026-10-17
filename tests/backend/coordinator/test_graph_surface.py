"""The built schema surface, and the one escape hatch from C17.

The manifest-matches-the-source half is `scripts/checks/the_built_graph_surface_is_declared.py`
and the demanded-by-a-question half is the ecosystem repo's
`docs/architecture/competency_questions/check.py`. What is left for a unit test is the
shape of the exemption list itself — the part that would rot quietly, because an
exemption that grows an entry a month still passes both of those.
"""

from __future__ import annotations

import pytest

from src.coordinator import graph_surface as gs
from src.coordinator.identity_shapes import (
    BASELINE_EDGE,
    BASELINE_LABEL,
    EDGES,
    SHAPES,
)

#: The number of mechanism exemptions on the day the class was defined. This is a PIN,
#: not a budget: the point is that adding one is a decision somebody makes on purpose,
#: with a line in INVARIANTS.md, rather than the quiet way a demand-traceability check
#: stops meaning anything. Two names qualified out of nineteen.
EXEMPTIONS_AS_OF_2026_10_17 = 2


def test_every_exemption_is_actually_built():
    """A dead exemption hides nothing and reads as if it did.

    ESLint needs `reportUnusedDisableDirectives` for exactly this and Checkstyle cannot
    detect it at all, so it is asserted here rather than hoped for.
    """
    built = set(gs.BUILT_LABELS) | set(gs.BUILT_RELS)
    orphans = set(gs.MECHANISM_EXEMPTIONS) - built
    assert not orphans, f"exempted but nothing builds them: {sorted(orphans)}"


def test_every_exemption_names_a_hazard_rather_than_restating_the_name():
    for name, reason in gs.MECHANISM_EXEMPTIONS.items():
        assert len(reason.strip()) >= 60, (
            f"{name}'s exemption is {len(reason.strip())} chars. State the "
            f"write-semantics hazard it prevents — a reason-less exemption is "
            f"indistinguishable from a field dropped to make a check go green."
        )
        assert name not in reason.split()[0:1], (
            f"{name}'s exemption opens by restating the name")


def test_the_exemption_list_has_not_quietly_grown():
    assert len(gs.MECHANISM_EXEMPTIONS) == EXEMPTIONS_AS_OF_2026_10_17, (
        f"{len(gs.MECHANISM_EXEMPTIONS)} mechanism exemptions, pinned at "
        f"{EXEMPTIONS_AS_OF_2026_10_17}. A new one needs an INVARIANTS.md line saying "
        f"why no competency question could ever mention it without becoming a question "
        f"about Cypher. A domain concept never qualifies — `:ProposedRule` was "
        f"undemanded on 2026-10-17 and the answer was to write the operator's question "
        f"down (rul.cq04), not to exempt it."
    )


def test_the_identity_half_is_derived_and_not_copied():
    """If someone restates SHAPES here, this file becomes a second source of truth."""
    for label in SHAPES:
        assert gs.BUILT_LABELS[label] == "identity_shapes.SHAPES"
    for rel in EDGES:
        assert gs.BUILT_RELS[rel] == "identity_shapes.EDGES"
    assert gs.BUILT_LABELS[BASELINE_LABEL] == "identity_shapes.BASELINE_LABEL"
    assert gs.BUILT_RELS[BASELINE_EDGE] == "identity_shapes.BASELINE_EDGE"


@pytest.mark.parametrize("name", ["CurrentRule", "CurrentIdentity"])
def test_the_merge_resurrection_guards_are_the_exempt_pair(name):
    """Pins WHICH two, so a swap is visible.

    Both are secondary labels that exist because Neo4j Community has no partial index
    and no predicate on MERGE. Replace one with a domain label and the count test still
    passes; this is what catches that.
    """
    assert name in gs.MECHANISM_EXEMPTIONS
    assert "MERGE" in gs.MECHANISM_EXEMPTIONS[name]
