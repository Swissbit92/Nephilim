---
title: The identity graph is built from the card and evolves in its edges
status: Proposed
created: 2026-09-28
last_reviewed_on: 2026-09-28
review_in: 12 months
applies_to: nephilim
ai_summary: >
  The agreed SCOPE for extending the graph from rules-only to a persona identity
  store. Read before building anything in the identity graph, before adding a node
  label, or before wiring any of it into a prompt. Records what is deliberately
  deferred (write policy, episodes, prompt injection) and the finding that the card
  is mostly PROSE, which bounds how finely it can be modelled without invention.
---

# ADR-018: The identity graph is built from the card and evolves in its edges

## Context

The graph today holds **only rules** — 2 `Persona` nodes, 18 `Rule` nodes, one
relationship type. It is a star, not a knowledge graph. Facts about the operator live in
SQLite (12 rows, flags off); lore lives in markdown; conversation history lives in
SQLite.

ADR-014 established the three layers: the **card in git is the origin**, the graph is a
**droppable projection**, and that is what makes "reset her" meaningful. This ADR extends
the projection from rules to identity, and fixes its scope before any of it is built.

## Resolved 2026-09-28: the graph IS the system of record

The first draft of this ADR drew `card → graph` and **contradicted ADR-011**, which
specified a durable store between them and recorded that an earlier draft of *itself* had
made the graph authoritative and been corrected for it. That contradiction is now closed in
this ADR's favour: [ecosystem ADR-012](../../../docs/decisions/012-the-graph-is-the-system-of-record-for-persona-identity-superseding-the-projection-model.md)
supersedes ADR-011. The durable store will not be built.

**So the diagram below is correct, and the cost is real:** dropping the graph no longer
refreshes her, it forgets her. A rebuild from the card produces her factory state. The
nightly backup was loaded and proved by restore before this was accepted — it is what
carries the weight now.

Two related decisions taken the same day:

- **`lore_sync.py` keeps writing the card.** The real chain is `wiki → card → graph`, a
  two-stage build with a mutable middle. Accepted cost: "reset is exact" means exact to the
  last sync. Because lore nodes will be keyed positionally like rules, a reflow can
  silently retarget annotations — so lore needs the same fail-loud source pin
  `seed_graph.load_rules` already applies.
- **Property names stay `snake_case`**, diverging from Neo4j's `camelCase` recommendation.
  The codebase and existing graph are snake_case; churning working code for style costs
  more than the inconsistency. A choice, not an oversight.

## Decision

```
card (git, hand-written)  ──build──▶  GRAPH  ──drop & rebuild──▶  card
        immutable by her          what she reads          reset is exact
                                  edges evolve
```

**While the graph exists she reads the graph as her identity. The card is the fallback,
not a parallel input.** One read path, not two merged.

### The cost of that, and the check it requires

If the graph *is* what she reads, it must be **provably complete**. Otherwise dropping
and rebuilding does not merely lose her learning — it silently loses a trait nobody
remembered to model, and nothing announces it.

gwen's card has **33 top-level keys / 68 leaf fields**, and roughly half are not identity
at all:

| identity — must be modelled | plumbing — must stay out |
|---|---|
| `lore`, `behavior`, `emotional_profile`, `boundaries`, `psychological_profile`, `voice`, `do`, `dont`, `user_relationship`, `expertise`, `signature_moves`, `escalation_policy`, `dialogue_prefs`, `example_phrases` | `tools`, `toolsets`, `mcp_access`, `model_preferences`, `image`, `avatar`, `logo`, `bg`, `key`, `coordinator_label`, `style`, `nsfw`, `rarity`, `celestial_order`, `emoji`, `word_substitutions` |

**So: every card field must be either modelled in the graph or explicitly listed as
deliberately excluded. A field allowed to be neither is the failure mode.** This is the
same shape as the existing guard that stops a persona silently losing its `do` and
`dont`, and it is what makes the fallback genuinely safe rather than nominally safe.

### What she may and may not change

Every node and edge has **two classes of property**, and the split is the load-bearing
part of this design:

- **CONTENT properties** come from the card. What the thing *is*. Immutable by her,
  regenerated on every rebuild.
- **ANNOTATION properties** are hers. What the thing has come to *mean*. Never
  regenerated — preserved across a rebuild.

| | may she change it | on rebuild |
|---|---|---|
| `origin: card` node **content** — the lore text, the trait name, the boundary | **no** (same rule as hard walls) | regenerated from the card |
| `origin: card` node **annotations** — salience, times reinforced, last referenced | **yes** | **preserved** |
| `origin: learned` nodes — something she has actually acquired | yes | not regenerated; dropped on reset |
| **edges**, and **edge properties** | **yes** | **preserved** |

So she cannot change what her hair *is*. She can record that it has come to matter more.
That is the whole of her evolution, and it fits in properties and edges without touching a
single thing the operator authored.

**The mechanical consequence, and it is not optional:** the schema must DECLARE which
properties are content and which are annotations, per node type. A rebuild that cannot
tell them apart has only two options, and both are wrong — regenerate everything and wipe
her learning, or regenerate nothing and let a card edit never take effect. The declared
split is what makes "rebuild preserves her edges" implementable rather than aspirational.

**The graph's value is the edges, not the nodes.** A card is a flat document: it can say
*"long red hair"* and it can say *"he loves her hair"*. It cannot say *"her red hair → he
responded to it → she plays it up more now"*. Her card lists forty traits with equal
weight forever; the graph can record which ones actually matter and let that shift.

**This implies a hard mechanical requirement: a rebuild must regenerate CONTENT while
PRESERVING her annotations and edges.** Otherwise every card edit wipes her learning. `seed_rules`
currently overwrites in place, so this is new work and not a detail.

### The card is mostly PROSE, which bounds how finely it can be modelled

Measured, and it changes the design. Her appearance is not a structured field — it is
embedded in **21 free-text `lore` strings** ("striking blue eyes that water…"). Same for
`signature_moves` (8 prose strings) and `psychological_profile`.

So there are two granularities available:

1. **Model at the granularity the card already has** — a lore entry is a node, a trait is
   a node, an expertise item is a node. Deterministic, complete, verifiable, no LLM.
2. **Extract finer structure from the prose** — "blue eyes", "155cm", "red hair" as
   separate nodes. Requires an LLM, and therefore inherits the junk problem: the existing
   extractor produced **2 useful facts out of 12**, with a wrong one
   (`has_relationship: friend`), two duplicates, and one fact that was about her own
   processing rather than about him.

**Option 1 is chosen.** Nothing is invented, the completeness check is mechanical, and
edges still attach at a useful granularity — she can reinforce *"lore entry 3"* or
*"trait: submissive"* perfectly well. Finer extraction stays available later, on evidence,
and would be a separate decision with its own measurement.

## Consequences

**No IDENTITY-NODE content is wired into her prompt, and that is a separate decision.**
Stated precisely because the looser phrasing was wrong: graph RULES already reach the
system prompt today via `routes/chat.py`, rendered as an untrimmable `<rules>` section.
The plumbing an identity node would ride in on already exists and is one function call
away — which is exactly why the boundary has to name what it excludes. Injecting
remembered content has already been measured to flatten voice — distinctiveness 0.804
with injection off, 0.625 with it on, and three deliberate reframings scored 0.708 /
0.542 / 0.500, all *below* the off-baseline. Her whole identity currently reaches her in
~120 tokens of compressed summary; rendering a modelled card back into the prompt would
make it larger, which is the direction that measured worse. Building the store and
feeding the prompt are two decisions and will not be made together.

**The write policy is deliberately deferred**, and the schema is built so that deferring
it costs nothing: every node, edge and property carries `origin ∈ card | learned |
conversation | inferred` from day one, and **no write path from chat exists**. Whatever
policy is chosen later is then a filter over data that already exists, not a migration.
The gate machinery from [ADR-017](017-learned-preferences-may-change-soft-walls-never-hard-walls.md)
transfers directly — verbatim quote, content-hash-bound approval, deterministic checks,
TTL proposals — so the open question is *which tiers*, not *what mechanism*.

Parked precisely: when may she create a `learned` node; when may she change an edge
property and how often; may she ever retire or delete, and is that distinct from "no
longer true"; and which of those need approval versus none.

**Episodes and the short-term/long-term memory split are deferred to their own
research.** "We watched a film on Tuesday" is a different kind of thing from "she likes
horror", conversation history already lives durably in SQLite, and duplicating 896
assistant messages into a store that **cannot be backed up while running** would be a
regression. Provenance stays an edge property (`source_session_id` /
`source_message_id`), which already lets anything reading the graph jump back to the
authoritative row — the same mechanism Graphiti's Episode nodes provide, without the
duplication.

**Node labels stay in single digits and specificity lives in properties.** Every
production system that publishes a schema converges on this; nobody ships 33 relationship
types. The existing rule store already follows it (`rule_type` is a property, not three
labels).

**What is most likely to be regretted:** building a rich identity store and then wiring
it into the prompt the same way the last attempt was wired, and reading the resulting
voice collapse as a storage-layer problem when it is the same closed line reopened under
a new name. The mitigation is the scope boundary above, stated as a decision rather than
an intention.

## Related

- [ADR-014](014-the-rule-store-is-a-neo4j-projection-superseding-the-adr-001-and-adr-006-rejections.md) — the three layers, and the graph as a droppable projection
- [ADR-017](017-learned-preferences-may-change-soft-walls-never-hard-walls.md) — the write gates this will reuse, and hard-wall immutability
- [ADR-006](006-companion-memory-and-continuity-eval-first.md) — the measured voice cost of injecting memory, and why facts stayed in SQLite
