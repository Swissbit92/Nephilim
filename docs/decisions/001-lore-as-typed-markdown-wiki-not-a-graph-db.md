---
title: Lore as typed-markdown-wiki not a graph DB
status: Accepted
created: 2026-05-29
last_reviewed_on: 2026-05-29
review_in: 12 months
applies_to: nephilim
---

# ADR-001: Lore as typed-markdown-wiki not a graph DB

## Status

Accepted — **still governs lore.** Amended 2026-09-26 by
[ADR-014](014-the-rule-store-is-a-neo4j-projection-superseding-the-adr-001-and-adr-006-rejections.md)
with two clarifications and one correction.

**Scope clarifier.** *"Do NOT introduce Neo4j (or any graph DB) for this"* is
scoped to hand-authored lore content, as this ADR's own Context says. It is not a
repo-wide ban on graph stores. The lore wiki stays markdown, and the ecosystem
architecture agrees by name: *"Do not migrate the lore wiki to YAML: it is
content, it works (see nephilim ADR-001)."*

**The one ground that transfers, and is honoured.** *"Another always-on service
violates simplicity at solo scale"* is a real constraint on a box running live
trading. ADR-014 answers it by making the graph a rebuildable projection rather
than a system of record — a service whose loss costs capability, not data.

**Correction: the escalation path below is void at the middle rung.** It reads
*networkx in-memory → MongoDB `$graphLookup` → Neo4j read-only projection*, but
[ADR-002](002-remove-mongodb-mcp.md) removed MongoDB entirely — deleted
`MongoDBSettings`, dropped `pymongo`. A path through a store this repo no longer
has is not a path. Read it as *networkx in-memory → Neo4j read-only projection*.

## Context

NEPHILIM's worldbuilding lived in six monolithic markdown files under `docs/lore/` (BUSINESS_PLAN, THE_CHRONICLE, LORE_BIBLE_DRAFT, NEPHILIM_LORE, NEPHILIM_FACTIONS, NEPHILIM_RANKS) with no schema, no cross-links, and no validation. This produced real drift: two incompatible house-naming systems, three names for the antagonist, eight Chronicle-only entities, and a corrupted README. The lore is hand-authored creative canon and — verified — is not consumed at runtime (personas draw lore from `personas/*.json`, assembled in `prompt_builder.py`).

Typed relationships between entities were needed (patrons, oppositions, locations). The obvious question: use a graph database (Neo4j)?

## Decision

Adopt a **typed-markdown-wiki** (OmegaWiki / Karpathy "LLM-Wiki" pattern). Each entity is one markdown file under `docs/lore/wiki/` with YAML frontmatter declaring `entity_type`, `entity_id`, `canon`, `aliases`, and typed `relationships`. Markdown is the single source of truth. A small Python engine (`scripts/utils/lore_wiki.py`) validates the graph (`check`) and regenerates the index (`index`); a graph is derived on demand (`graph`, networkx), never stored separately.

**Do NOT introduce Neo4j (or any graph DB) for this.**

## Consequences

Easier: git-diffable / PR-reviewable / editable canon; entities load individually (cheap for a local Ollama context); CI-gateable integrity (dangling links, missing inverses, alias collisions, persona↔JSON consistency, prose name-drift); zero new runtime infrastructure.

Harder: multi-hop traversal must be derived in code rather than queried in Cypher; authoring is manual file creation. Both are acceptable at current scale (~30 entities).

## Alternatives Considered

- **Neo4j / graph DB** — rejected: opaque binary store breaks git-native authoring; another always-on service violates "simplicity at solo scale"; overkill for ~30 nodes; MongoDB `$graphLookup` is available if querying is ever needed; the LLM consumes lore as text, so files avoid a serialisation round-trip.
- **Leave monolithic docs as-is** — rejected: the drift that motivated this work would recur with no mechanism to catch it.

If multi-hop traversal becomes the primary access pattern or the graph grows to thousands of densely-linked nodes, escalation path is: networkx in-memory → MongoDB `$graphLookup` → Neo4j read-only projection — keeping markdown canonical throughout.

## References

- `scripts/utils/lore_wiki.py` — the wiki engine
- [`docs/lore/wiki/`](../lore/wiki/) — the entity graph
- [`docs/lore/README.md`](../lore/README.md) — lore directory map
