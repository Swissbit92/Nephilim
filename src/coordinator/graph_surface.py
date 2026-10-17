"""Every label and relationship type this code builds, declared in one place.

This exists so C17 can be ENFORCED rather than printed. The competency-question
set states the criterion as *"every label, relationship type and temporal field in
the schema is demanded by at least one question. Anything demanded by none does not
get built."* Run forwards (does the schema answer every question?) the set checks
itself. Run backwards it needs the other operand — what was actually built — and
until this module existed, there was nowhere to read that from, so the check
printed the demanded surface and compared it to nothing.

**Two provenances, both real.** The identity half is derived, not restated:
`SHAPES`, `EDGES` and the two `BASELINE_*` constants in `identity_shapes.py` already
are the source of truth, and copying them here would create a second one. The rule
and proposal half lives inside Cypher string literals, which nothing can enumerate,
so those are listed by hand — and `scripts/checks/the_built_graph_surface_is_declared.py`
re-derives them from the source on every invariant run, so the hand-written half
cannot drift without failing.

⚠️ **"Built" here means "some Cypher in this repo writes it" — NOT "a live request path
reaches it".** Three of these names are written only by code nothing outside `tests/`
calls: `CORRECTS` (`correct_rule`), and `:ProposedRule`/`HAS_PROPOSAL`
(`rule_proposals.RuleProposalStore`, which has zero non-test importers — verified
2026-10-17). ADR-017 stages that deliberately ("if this is ever wired to chat,
`GRAPH_ENFORCE_RULES` stops being optional"), so this is not a defect. But a competency
question written for a mechanism nothing can trigger satisfies C17's letter while leaving
its purpose — do not build what the system does not need — untouched, and that distinction
has to be visible here rather than inferred. **Reachability is a separate criterion this
module does not assert.** Adding it would need call-graph analysis from the route layer,
which is not attempted.

**`MECHANISM_EXEMPTIONS` is the one legitimate escape, and it is narrow.** [Received from
this session's research pass, 2026-10-17; the primary sources were fetched by a subagent
and are not independently re-verified here.] Grüninger & Fox (IJCAI-95 §3), who state the
backwards criterion, do not carve out a category for representational machinery — and their own worked example violates the rule they
state, since no competency question in that paper asks about `Poss(a,s)` or `S₀`.
Nothing downstream closed the gap: surveyed 2026-10-17 across NeOn's ORSD, ROBOT
`report`, the OBO Foundry dashboard's 14 principles, LinkML and schema.org, no
production pipeline enforces demand-traceability at all, let alone defines an
exemption class for it. So the category is defined here.

**There are TWO kinds of exemption here, and reaching for the wrong template is how the
next one gets justified badly.** `:CurrentRule` and `:CurrentIdentity` are *persistent and
readable* — a question could point at them and would be wrong only because they are pure
mechanism. `:_PropertyKeyRegistry` is *transient and unobservable*: it never survives its
own transaction, so no read can reach it at all, which makes it closer to an index than to
a label. Both are legitimate; they are not the same argument. Note which one applies when
you add an entry.

⚠️ **Do not teach the drift check to skip a CREATE-then-DELETE-in-one-statement label**
instead of exempting it. That looks tidier and opens a real loophole: any undeclared label
could then be hidden by pairing it with an immediate delete.

A name earns an exemption ONLY if a competency question could never mention it
without becoming a question about Cypher. `:CurrentRule` qualifies: it exists because
Neo4j has no partial index and `MERGE` would otherwise resurrect a soft-expired node,
which is a fact about the write semantics of one database, not about Gwen. A domain
concept never qualifies, however inconvenient — `:ProposedRule` was undemanded on
2026-10-17 and the answer was to write the operator's question down, not to exempt it.

Every exemption states the hazard it prevents, because an exemption with no reason is
indistinguishable from a field quietly dropped to make a check go green — the pattern
ESLint needs a separate plugin to enforce, Checkstyle's suppression format cannot
express at all, and SHACL's `sh:deactivated` explicitly declines to document.
"""

from __future__ import annotations

from .identity_shapes import BASELINE_EDGE, BASELINE_LABEL, EDGES, SHAPES

#: Labels written by the rule and proposal paths, which build their Cypher as string
#: literals. One entry per label, naming where it is created — the file is the thing a
#: reader needs when the enforcement check says a name is undemanded.
_RULE_PATH_LABELS: dict[str, str] = {
    "Persona": "identity_repository.apply_card / neo4j_rule_repository.seed_rules",
    "Rule": "neo4j_rule_repository.seed_rules",
    "CurrentRule": "neo4j_rule_repository.seed_rules (secondary label)",
    "CurrentIdentity": "identity_repository.apply_card (secondary label)",
    "ProposedRule": "rule_proposals.propose",
    "_PropertyKeyRegistry": "neo4j_rule_repository._REGISTER_KEYS (created and DELETEd "
                            "in the same statement; never survives the transaction)",
}

#: Relationship types written by the rule and proposal paths.
_RULE_PATH_RELS: dict[str, str] = {
    "HAS_RULE": "neo4j_rule_repository.seed_rules — (:Persona)->(:Rule)",
    "HAS_PROPOSAL": "rule_proposals.propose — (:Persona)->(:ProposedRule)",
    "SUPERSEDES": "neo4j_rule_repository.supersede_rule — new->old, per ADR-017",
    "CORRECTS": "neo4j_rule_repository.correct_rule — new->old, operator rewrite",
}

#: Labels this code creates. Identity half derived from SHAPES so it cannot drift.
BUILT_LABELS: dict[str, str] = {
    **dict.fromkeys(SHAPES, "identity_shapes.SHAPES"),
    BASELINE_LABEL: "identity_shapes.BASELINE_LABEL",
    **_RULE_PATH_LABELS,
}

#: Relationship types this code creates. Identity half derived from EDGES.
BUILT_RELS: dict[str, str] = {
    **dict.fromkeys(EDGES, "identity_shapes.EDGES"),
    BASELINE_EDGE: "identity_shapes.BASELINE_EDGE",
    **_RULE_PATH_RELS,
}

#: Names no competency question can demand without becoming a question about Cypher.
#: The value is the write-semantics hazard the name prevents, not a restatement of what
#: it is. Read the module docstring before adding one; a domain concept never belongs
#: here, and the test asserts this set stays small.
MECHANISM_EXEMPTIONS: dict[str, str] = {
    "CurrentRule": (
        "Neo4j Community has no partial index and no predicate on MERGE, so "
        "`MERGE (r:Rule {rule_id: ...})` matches a soft-expired rule and resurrects it. "
        "The secondary label is the predicate: expiry REMOVEs it, so the expired node "
        "keeps its history and becomes invisible to the MERGE. No question about Gwen "
        "can mention it, because it is a fact about one database's write semantics."
    ),
    "CurrentIdentity": (
        "The same guard on the identity rebuild path, for the same reason — "
        "`MERGE (n:Trait:CurrentIdentity {natural key})` cannot match a node whose "
        "label the previous expiry removed. Without it apply_card revives every "
        "trait the card no longer contains."
    ),
    "_PropertyKeyRegistry": (
        "Neo4j emits a warning for a property name its key store has never seen, which "
        "is the ONLY available detection for a misspelled property in a WHERE clause — "
        "in a store with no type constraints, `WHERE r.expried_at IS NULL` is a silent "
        "always-null filter, not an error. The keys are registered by creating a node "
        "carrying them and DELETEing it in the same statement, so the label never "
        "survives the transaction and no graph read can reach it. Measured 2026-09-26: "
        "with the keys registered the real read emits 0 notifications while the "
        "misspelling still emits 1. A question about Gwen cannot name it, because it is "
        "a fact about one database's notification behaviour."
    ),
}

#: Anything in MECHANISM_EXEMPTIONS must actually be built, or the exemption is dead
#: and hides nothing — the stale-suppression failure `reportUnusedDisableDirectives`
#: exists to catch in ESLint and which Checkstyle cannot detect at all.
EXEMPT: frozenset[str] = frozenset(MECHANISM_EXEMPTIONS)
