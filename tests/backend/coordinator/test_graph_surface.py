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
#: stops meaning anything. Three names qualified out of twenty.
#:
#: ⚠️ It was 2 for the first hour. `_PropertyKeyRegistry` was missed because the scan's
#: label pattern required `[A-Z]` after the colon and that label starts with `_`, so the
#: check written to make exactly this impossible reported a clean pass on a label built
#: from a live route. Found by review, not by the check.
EXEMPTIONS_AS_OF_2026_10_17 = 3


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


@pytest.mark.parametrize("name,hazard", [
    ("CurrentRule", "MERGE"),
    ("CurrentIdentity", "MERGE"),
    ("_PropertyKeyRegistry", "WHERE"),
])
def test_the_exempt_names_are_the_ones_that_earned_it(name, hazard):
    """Pins WHICH three, so a swap is visible.

    Two are secondary labels that exist because Neo4j Community has no partial index and
    no predicate on MERGE; the third is a node created and deleted in one statement so
    the key store has seen the property names. Replace any of them with a domain label
    and the count test still passes; this is what catches that.
    """
    assert name in gs.MECHANISM_EXEMPTIONS
    assert hazard in gs.MECHANISM_EXEMPTIONS[name]


def _drift_check():
    import importlib.util
    import pathlib

    root = pathlib.Path(gs.__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "_drift", root / "scripts" / "checks" / "the_built_graph_surface_is_declared.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("cypher,expect_label,expect_rel,why", [
    ("CREATE (x:_PropertyKeyRegistry {expired_at: ''}) DELETE x",
     "_PropertyKeyRegistry", None,
     "a LEADING UNDERSCORE label. This was invisible for the drift check's first hour "
     "because the pattern required [A-Z] after the colon, so a label built from a live "
     "route was undeclared, unexempted, and reported as a clean pass"),
    ('f"MERGE (n:{label}:_SecondaryOnlyHere {{k: 1}})"',
     "_SecondaryOnlyHere", None,
     "a secondary label introduced ONLY through the f-string-hole idiom. Deleting the "
     "hole instead of replacing it leaves `(n::Label`, a double colon no pattern parses, "
     "so the whole clause matches nothing"),
    ("CREATE (a)-[:_ODD_REL]->(b)", None, "_ODD_REL",
     "the same underscore gap on the relationship pattern"),
])
def test_the_scan_sees_the_label_classes_it_once_could_not(
        tmp_path, monkeypatch, cypher, expect_label, expect_rel, why):
    """Drives `derive()`, NOT the regexes, so the CALL SITE is what is pinned.

    An earlier version of this test matched `_LABEL` and `_FSTRING_HOLE` directly and
    stayed green when the sentinel substitution was removed from `derive()` — the
    assert-the-flag-not-the-call-site failure this repo has hit before. A synthetic
    source file is the only way to assert that the scan, as actually wired, sees these.
    """
    drift = _drift_check()
    src = tmp_path / "synthetic_writer.py"
    src.write_text(f'"""A docstring that must be IGNORED: (:DocOnly)-[:DOC_ONLY]->(x)."""\n'
                   f'Q = {cypher if cypher.startswith("f") else repr(cypher)}\n')
    monkeypatch.setattr(drift, "CYPHER_SOURCES", (src,))

    labels, rels = drift.derive()
    if expect_label:
        assert expect_label in labels, f"the scan cannot see {why}; it saw {sorted(labels)}"
    if expect_rel:
        assert expect_rel in rels, f"the scan cannot see {why}; it saw {sorted(rels)}"
    assert "DocOnly" not in labels and "DOC_ONLY" not in rels, (
        "the docstring exclusion is gone — planned is not built, and "
        "neo4j_rule_repository's docstring describes a future write path in Cypher")


def test_the_scan_refuses_to_pass_on_an_empty_result(tmp_path, monkeypatch):
    """An empty scan agrees with any manifest, so it must be exit 2 and not exit 0."""
    drift = _drift_check()
    src = tmp_path / "no_cypher.py"
    src.write_text('X = "nothing graph-shaped in here at all"\n')
    monkeypatch.setattr(drift, "CYPHER_SOURCES", (src,))
    assert drift.main() == 2
