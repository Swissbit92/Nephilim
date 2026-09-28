"""Node shapes for the persona identity graph (ADR-018, ecosystem ADR-012).

THE ONE IDEA THIS FILE EXISTS FOR. Every node has two classes of property:

    CONTENT     comes from the card. What the thing IS. Regenerated on every
                rebuild. She cannot change it.
    ANNOTATION  written by her over time. What the thing has come to MEAN.
                NEVER regenerated. Preserved across a rebuild.

She cannot change what her hair is. She can record that it has come to matter more.

WHY THE SPLIT MUST BE DECLARED RATHER THAN IMPLIED. A rebuild that cannot tell the two
apart has exactly two options and both are wrong: regenerate everything and wipe her
learning, or regenerate nothing and let a card edit never take effect. Under ecosystem
ADR-012 the graph is the SYSTEM OF RECORD, not a droppable projection, so wiping her
learning is no longer an inconvenience — it is data loss with no second copy.

WHY THE SHAPES LIVE IN PYTHON. Neo4j Community enforces uniqueness and nothing else: no
property-existence constraints, no property types, no triggers, and this instance has
zero APOC procedures. Every shape rule is enforced here or not at all. A separate schema
file would be a second artifact that can drift from the code that writes — precisely the
failure that let `polarity` be absent from one write path and render a rule as its own
opposite.

WHY THESE LABELS AND NO OTHERS. `check.py`'s C17 prints the union of labels the 30
competency questions demand, and the rule is that nothing gets built which no question
demands. LoreEntry, Trait, Boundary and Expertise are admissible because idn.cq01-cq05
ask for them. Adding a label here without writing a question first is exactly what the
gate exists to refuse.
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, NamedTuple

# ── vocabularies ──────────────────────────────────────────────────────────────
#
# `learned` is NEW. `ORIGINS` in neo4j_rule_repository has three values; ADR-018
# specifies four and the fourth had no code. It matters because `learned` has different
# reset semantics from `conversation`/`inferred`: a reset drops precisely the learned
# set, which is what makes the card an exact fallback.
IDENTITY_ORIGINS: FrozenSet[str] = frozenset({"card", "learned", "conversation", "inferred"})

# Flat, not hierarchical, and that is a decision rather than laziness. Nothing here has
# levels — there is no "appearance -> hair -> colour" chain anything queries — so a
# broader/narrower edge would be pure overhead: extra hops, no parent-uniqueness
# constraint available on Community to keep it honest, and one more thing to keep
# consistent on every rebuild. Revisit when a single `kind` passes ~15-20 nodes.
LORE_KINDS: FrozenSet[str] = frozenset({"lore", "signature_move"})
TRAIT_KINDS: FrozenSet[str] = frozenset({"behaviour", "psychological", "emotional",
                                         "style", "tic"})
BOUNDARY_KINDS: FrozenSet[str] = frozenset({"ethics", "content", "personal"})
EXPERTISE_LEVELS: FrozenSet[str] = frozenset({"strong", "familiar", "avoid"})


class Shape(NamedTuple):
    """One node kind: its label, its natural key, and the content/annotation split."""

    label: str
    #: MERGE key. Positional, matching the rule store's
    #: `(persona_id, source_field, source_index)` convention rather than
    #: content-addressing. Deliberate: a card EDIT should update a node in place, and a
    #: content-addressed key would instead create a new node and orphan every annotation
    #: attached to the old one.
    key: tuple[str, ...]
    content: FrozenSet[str]
    annotation: FrozenSet[str]
    #: Per-property controlled vocabulary, checked before any write.
    vocabularies: Dict[str, FrozenSet[str]]

    def all_properties(self) -> FrozenSet[str]:
        return self.content | self.annotation

    def split(self, props: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Partition a property dict into (content, annotation). Refuses anything else.

        An unknown property is an ERROR, not something to pass through. A property
        belonging to neither class is exactly the `polarity` bug's shape: one write path
        treats it as regenerable, another as accumulated, and nothing catches the
        mismatch until the behaviour is visibly wrong.
        """
        unknown = set(props) - set(self.all_properties())
        if unknown:
            raise ValueError(
                f"{self.label}: {sorted(unknown)} belong to neither content nor "
                f"annotation. Declare which, or do not write it — a property in neither "
                f"class is how a rebuild silently picks the wrong behaviour."
            )
        for prop, vocab in self.vocabularies.items():
            if prop in props and props[prop] not in vocab:
                raise ValueError(
                    f"{self.label}.{prop}={props[prop]!r} not in {sorted(vocab)}"
                )
        return (
            {k: v for k, v in props.items() if k in self.content},
            {k: v for k, v in props.items() if k in self.annotation},
        )


# Shared by every identity node. `origin` and the bi-temporal four are the same names
# the rule store already uses — this extends those conventions rather than inventing a
# parallel set.
_BASE_CONTENT: FrozenSet[str] = frozenset({
    "node_id", "persona_id", "text", "origin",
    "source_field", "source_index",
    "valid_from", "valid_to", "created_at", "expired_at",
    #: Hash of the source string at build time. The cheapest way to tell "the card was
    #: edited after this node was built" from "still current", and the provenance field
    #: most systems skip.
    "source_hash",
})

#: Hers. The only properties she may write on a card-derived node.
_BASE_ANNOTATION: FrozenSet[str] = frozenset({
    "salience", "reinforced_count", "last_referenced",
})

LORE_ENTRY = Shape(
    label="LoreEntry",
    key=("persona_id", "source_field", "source_index"),
    content=_BASE_CONTENT | {"kind"},
    annotation=_BASE_ANNOTATION,
    vocabularies={"origin": IDENTITY_ORIGINS, "kind": LORE_KINDS},
)

TRAIT = Shape(
    label="Trait",
    key=("persona_id", "source_field", "source_index"),
    content=_BASE_CONTENT | {"kind"},
    annotation=_BASE_ANNOTATION,
    vocabularies={"origin": IDENTITY_ORIGINS, "kind": TRAIT_KINDS},
)

BOUNDARY = Shape(
    label="Boundary",
    key=("persona_id", "source_field", "source_index"),
    content=_BASE_CONTENT | {"kind", "priority"},
    annotation=_BASE_ANNOTATION,
    vocabularies={"origin": IDENTITY_ORIGINS, "kind": BOUNDARY_KINDS},
)

EXPERTISE = Shape(
    label="Expertise",
    key=("persona_id", "source_field", "source_index"),
    content=_BASE_CONTENT | {"level"},
    annotation=_BASE_ANNOTATION,
    vocabularies={"origin": IDENTITY_ORIGINS, "level": EXPERTISE_LEVELS},
)

SHAPES: Dict[str, Shape] = {s.label: s for s in (LORE_ENTRY, TRAIT, BOUNDARY, EXPERTISE)}

#: The seven declared dials, in card order. Named explicitly rather than read from the
#: card so a card that gains or loses a dial is a DECISION rather than a silent schema
#: change -- the baseline's whole job is to be comparable over time.
SLIDER_NAMES: tuple[str, ...] = (
    "warmth", "assertiveness", "playfulness", "skepticism",
    "competitiveness", "manipulativeness", "sluttiness",
)

#: The :Baseline label is NOT a Shape. A Shape carries the content/annotation split and
#: is rebuilt from the card on every apply_card; a baseline is the opposite of that -- it
#: is written ONCE and never again, because a starting point that gets refreshed is not a
#: starting point. evolution.yaml names it the day-1 irreversible item in its domain:
#: "change cannot be measured against a starting point that was never recorded", and no
#: later work recovers it. So it is deliberately outside SHAPES, where the rebuild
#: machinery cannot reach it.
BASELINE_LABEL = "Baseline"

#: The baseline's edge, declared here rather than in EDGES on purpose. EDGES is the
#: shape-edge table and test_every_shape_has_exactly_one_edge_with_typed_endpoints
#: asserts it maps 1:1 onto SHAPES -- an invariant worth keeping, since an untyped or
#: orphan relation is the modelling defect Noy & McGuinness warn about. :Baseline is not
#: a Shape, so its edge does not belong in that table; it is named here so the type is
#: still declared in one place rather than living only inside a Cypher string.
BASELINE_EDGE = "HAS_BASELINE"


#: Relationship types, one per shape, with TYPED ENDPOINTS. The endpoint types are the
#: load-bearing part: Noy & McGuinness name an unconstrained range ("do not make the
#: range THING") as the modelling defect, and that is what `has_opinion_on` violated in
#: the fact store — half of everything it ever wrote landed in its two vaguest
#: predicates. The literature bounds relation GENERICITY, not relation count.
EDGES: Dict[str, tuple[str, str]] = {
    "HAS_LORE": ("Persona", "LoreEntry"),
    "HAS_TRAIT": ("Persona", "Trait"),
    "HAS_BOUNDARY": ("Persona", "Boundary"),
    "HAS_EXPERTISE": ("Persona", "Expertise"),
}
