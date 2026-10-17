"""Node shapes for the persona identity graph (ADR-018, ecosystem ADR-012).

THE ONE IDEA THIS FILE EXISTS FOR. Every property a node may carry is DECLARED here,
and anything else is refused at write time. A property belonging to no declared class is
the `polarity` bug's shape: one write path treats it as regenerable, another as
accumulated, and nothing catches the mismatch until the behaviour is visibly wrong.

WHAT USED TO BE HERE, AND WHY IT IS NOT (2026-10-16). This file declared a second class,
ANNOTATION — `salience`, `reinforced_count`, `last_referenced` — described as "written by
her over time, never regenerated". It was deleted because it never happened. Measured on
the live graph: 256 nodes carried those properties and returned exactly ONE distinct row,
`(0, 0.0)`. The counter never incremented, not once, in the whole life of the graph; and
`last_referenced` sat on 0 of 256 nodes, because assigning null removes a property rather
than initialising it. Edges, where ADR-018 said "evolution lives", carried no properties
of any kind. So the class was unimplemented in both halves, and a declaration nobody can
satisfy is worse than no declaration — it makes two tests pass while asserting nothing.

THE CREATE-ONCE / REFRESH-EVERY-REBUILD DISTINCTION SURVIVES, with a real referent.
`node_id` and `created_at` are set in the Cypher's `ON CREATE` branch and never again: a
surrogate id that churned would orphan every edge, and a transaction time that reset on
rebuild cannot be reconstructed afterwards. That is the same mechanism the ADR-018 A/B
measured (0 of 3 vs 3 of 3 survival across a card edit) — the A/B tested the MECHANISM,
not the counter, so the evidence stands and only its subject has changed.

IF LEARNING IS ADDED LATER, re-introduce a second class THEN, with the property that
learning actually needs. The measured argument against the old one was not "the idea is
wrong" but "an access COUNT is the wrong instrument": repetition without discounting
correlated sources counts how often a fact was copied, not independently confirmed, and
`reinforce()` incremented on ACCESS rather than on re-assertion. Published evidence is
absent rather than negative — Generative Agents never ablated the recency/importance
terms it is cited for, A-MEM and MemGPT omit such a field by design, MemoryBank ships one
with no ablation, and the one direct test of a frequency-containing pruner reports
+0.001 token F1 with a CI spanning zero (arXiv 2608.28978, four signals bundled).

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

# ── definitions ───────────────────────────────────────────────────────────────
# MANDATORY for every term, enforced by scripts/checks/every_declared_term_has_a
# _definition.py. Not decoration: an enforced vocabulary whose terms mean nothing in
# particular is how data goes to the vaguest available bucket. Measured in this repo —
# `has_opinion_on` had an unconstrained range and took half of everything the extractor
# ever wrote.
#
# A definition must DISTINGUISH, not restate. "behaviour: a behaviour" is useless; the
# useful form says what goes here AND what goes next door instead.
#
# ⚠️ These are for the human and for the check. They are NOT prompt material — adding
# identity content to her prompt is a closed change (0.804 -> 0.625, measured), and the
# extractor prompt is separately capped by schema-bloat (pruning beat the full schema at
# ~6x fewer tokens).

IDENTITY_ORIGIN_DEFINITIONS: dict[str, str] = {
    "card": "Written by the operator in the persona JSON. Regenerated from the card on "
            "every rebuild and immutable by her. The reset target.",
    "learned": "Acquired through conversation and kept across a reset of the other "
               "learned classes — the narrow set a reset deliberately drops, which is "
               "what makes the card an exact fallback.",
    "conversation": "Stated in dialogue and taken at face value. Weaker than `learned`: "
                    "it records that something was said, not that it was integrated.",
    "inferred": "Derived by the system rather than asserted by anyone. Never treat as "
                "the user's own words, and never render as a quote.",
}

LORE_KIND_DEFINITIONS: dict[str, str] = {
    "lore": "A fact about her history, world or appearance — something true of her that "
            "she did not choose. Describes what IS, not what she does.",
    "signature_move": "A recurring thing she DOES on purpose, recognisable as hers. If "
                      "it is a disposition rather than an act, it is a Trait instead.",
}

TRAIT_KIND_DEFINITIONS: dict[str, str] = {
    # ⚠️ `behaviour` holds 34 live nodes against a stated revisit threshold of 15-20
    # (see the flatness note above). These four definitions exist to make the boundary
    # decidable so the overload can be split deliberately rather than by taste.
    "behaviour": "What she DOES, observably, as a pattern — an action another person "
                 "could watch her perform, usually stated as a disposition "
                 "(\"seductive\", \"eager\"). If it explains WHY she does it, use "
                 "`psychological`; if it is what she FEELS, use `emotional`; if it is a "
                 "standing rule about how an exchange proceeds, use `policy`.",
    "policy": "A standing rule governing HOW AN EXCHANGE PROCEEDS — what she does about "
              "small talk, clarifying questions, emoji, formality, pace. Procedural and "
              "conditional, not aesthetic: `style` is what she sounds LIKE, a policy is a "
              "rule she follows. Split out of `behaviour` on 2026-10-17 because that "
              "bucket held both bare dispositions and full procedural clauses.",
    "psychological": "WHY she is as she is — a motive, a wound, a drive, a defence. "
                     "Explanatory rather than observable. Never a feeling in the moment "
                     "(`emotional`) and never an action (`behaviour`).",
    "emotional": "What she FEELS, as a standing disposition rather than a mood — her "
                 "affective baseline. A momentary mood belongs to the session, not here.",
    "style": "HOW she presents rather than what she does — register, aesthetic, "
             "self-presentation. Observable like `behaviour`, but about manner rather "
             "than act.",
    "tic": "A specific, repeated verbal habit — a phrase, an emoji, a punctuation "
           "signature. Narrower than `style`: a tic is quotable, a style is not.",
}

BOUNDARY_KIND_DEFINITIONS: dict[str, str] = {
    "ethics": "A line she holds because of what she believes is right. Hers, and she "
              "would defend it unprompted.",
    "content": "A subject or act that is out of bounds regardless of her beliefs — a "
               "capability declaration rather than a conviction. ⚠️ Mixed polarity: an "
               "entry here may read as a permission or a prohibition, which is why "
               "`boundaries.content` is deliberately not rendered as instructions.",
    "personal": "A limit about her own privacy or history — what she will not discuss "
                "about herself, as distinct from what she will not do.",
}

EXPERTISE_LEVEL_DEFINITIONS: dict[str, str] = {
    "strong": "She can lead on this and volunteer detail unprompted. Confidence here is "
              "in character rather than a claim of fact.",
    "familiar": "She can follow and contribute but should not lead. The level that "
                "should read as interested rather than authoritative.",
    "avoid": "She deflects rather than engages, in character and without moralising. "
             "Not a safety refusal — a disposition.",
}


TRAIT_KINDS: FrozenSet[str] = frozenset({"behaviour", "psychological", "emotional",
                                         "style", "tic", "policy"})
BOUNDARY_KINDS: FrozenSet[str] = frozenset({"ethics", "content", "personal"})
EXPERTISE_LEVELS: FrozenSet[str] = frozenset({"strong", "familiar", "avoid"})


class Shape(NamedTuple):
    """One node kind: its label, its natural key, and the content/annotation split."""

    label: str
    #: MERGE key. Positional, matching the rule store's
    #: `(persona_id, source_field, source_key)` convention rather than
    #: content-addressing. Deliberate: a card EDIT should update a node in place, and a
    #: content-addressed key would instead create a new node on every edit — churning
    #: `node_id`, orphaning the node's edges, and resetting a `created_at` that cannot
    #: be reconstructed once lost.
    key: tuple[str, ...]
    content: FrozenSet[str]
    #: Per-property controlled vocabulary, checked before any write.
    vocabularies: Dict[str, FrozenSet[str]]

    def validated(self, props: Dict[str, Any]) -> Dict[str, Any]:
        """Return `props` unchanged, having refused anything undeclared.

        Named for what it does. This was `split()`, returning a (content, annotation)
        pair, until the annotation class was deleted — a method that no longer splits
        should not still be called split, because the next reader budgets for a
        partition that is not there.

        An unknown property is an ERROR, not something to pass through: a property
        belonging to no declared class is exactly the `polarity` bug's shape, where one
        write path treats it as regenerable and another as accumulated and nothing
        catches the mismatch until the behaviour is visibly wrong.
        """
        unknown = set(props) - set(self.content)
        if unknown:
            raise ValueError(
                f"{self.label}: {sorted(unknown)} is not a declared property of this "
                f"node. Declare it in the shape, or do not write it — an undeclared "
                f"property is how a rebuild silently picks the wrong behaviour."
            )
        for prop, vocab in self.vocabularies.items():
            if prop in props and props[prop] not in vocab:
                raise ValueError(
                    f"{self.label}.{prop}={props[prop]!r} not in {sorted(vocab)}"
                )
        return dict(props)


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
    #: Content-derived, and it is the MERGE KEY. See identity_from_card._source_key.
    "source_key",
})

LORE_ENTRY = Shape(
    label="LoreEntry",
    key=("persona_id", "source_field", "source_key"),
    content=_BASE_CONTENT | {"kind"},
    vocabularies={"origin": IDENTITY_ORIGINS, "kind": LORE_KINDS},
)

TRAIT = Shape(
    label="Trait",
    key=("persona_id", "source_field", "source_key"),
    content=_BASE_CONTENT | {"kind"},
    vocabularies={"origin": IDENTITY_ORIGINS, "kind": TRAIT_KINDS},
)

BOUNDARY = Shape(
    label="Boundary",
    key=("persona_id", "source_field", "source_key"),
    content=_BASE_CONTENT | {"kind", "priority"},
    vocabularies={"origin": IDENTITY_ORIGINS, "kind": BOUNDARY_KINDS},
)

EXPERTISE = Shape(
    label="Expertise",
    key=("persona_id", "source_field", "source_key"),
    content=_BASE_CONTENT | {"level"},
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
