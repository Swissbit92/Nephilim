---
title: The identity graph is built from the card and evolves in its edges
status: Accepted
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

## Built 2026-09-28, and the A/B that justified it

**118 identity nodes** from gwen's card — 29 `LoreEntry`, 41 `Trait`, 22 `Boundary`,
26 `Expertise` — behind `IdentityRepository.apply_card`, which is both the build and the
rebuild because the difference between them is whether a node already exists, and MERGE
already knows that. Two methods would be two paths that must agree.

**The A/B measured the split as load-bearing rather than assuming it:**

| arm | annotations survived a card edit | content refreshed |
|---|---|---|
| A — one unconditional `SET`, the semantics `seed_rules` has today | **0 of 3** | yes |
| B — the content/annotation split | **3 of 3** | yes |

Both refreshed content, so the difference is only the thing under test. Under ADR-012
arm A is not an inconvenience; it is permanent loss with no second copy.

Preservation is **structural**: annotation properties are named only in the `ON CREATE`
branch, so the rebuild path cannot reach them. A property cannot be wiped by a statement
that never mentions it.

### Four bugs found before it shipped, three from research and one mine

1. **`SET n += $map` DELETES a key whose value is null** (Neo4j's SET docs). The
   annotation defaults carried `last_referenced: None`, which would have deleted the key
   rather than initialising it. Properties are now assigned individually — also immune to
   `SET n = $map`, which replaces the whole property set and is one character away. A test
   greps for both forms and was watched failing on a real one.
2. **The resurrection trap.** Soft-expiring a node while keeping its label meant a later
   rebuild's MERGE on the same positional key would bind the *expired* node and bring it
   back; Neo4j's own MERGE docs demonstrate a pattern binding six nodes and updating all
   six. Fixed with a `:CurrentIdentity` secondary label that MERGE matches on — mirroring
   `:CurrentRule` for the same reason, since Neo4j has no partial index and no `WHERE`
   inside a MERGE pattern. An expired node keeps its primary label and its edge, so
   history stays traversable.
3. **118 separate transactions** where a sole copy wants one. Neo4j confirms
   `IN TRANSACTIONS` does not roll back already-committed batches, which is the wrong
   failure mode here.
4. **Coverage was measured at the wrong granularity, and that one was mine.** The
   key-level check reported 33-of-33 card fields complete while **nine leaves went
   nowhere** — seven sliders whose exclusion reason lived only in a code comment, and two
   genuinely forgotten (`behavior.relationship_to_user`,
   `behavior.clarifying_questions`), both real identity content. The check now works at
   leaf level: **244 leaves, 118 consumed, 126 excluded, 0 unaccounted.** A coverage claim
   coarser than the data is technically true and misleading, which is worse than none.

### The completeness check reports three states, not two

PASS / FAIL / **COULD NOT DETERMINE**, on Nagios's convention — a check that cannot verify
anything must not return success. All four failure paths were watched firing: a new card
key, a new leaf under an already-modelled key, a stale exclusion, and an unreadable card.

Stale exclusions are flagged, the same family as mypy's unused-ignore and ESLint's
unused-disable-directive: an exclusion is a recorded decision, and one about a field that
no longer exists is misleading rather than inert.

### Still true, and still deliberate

Nothing is wired into her prompt. `reinforce()` is her only write and it names three
annotation properties and nothing else, so it cannot reach content even when called oddly
— the same discipline as the hard-wall guard, applied by omission rather than validation.

## Related

- [ADR-014](014-the-rule-store-is-a-neo4j-projection-superseding-the-adr-001-and-adr-006-rejections.md) — the three layers, and the graph as a droppable projection
- [ADR-017](017-learned-preferences-may-change-soft-walls-never-hard-walls.md) — the write gates this will reuse, and hard-wall immutability
- [ADR-006](006-companion-memory-and-continuity-eval-first.md) — the measured voice cost of injecting memory, and why facts stayed in SQLite
