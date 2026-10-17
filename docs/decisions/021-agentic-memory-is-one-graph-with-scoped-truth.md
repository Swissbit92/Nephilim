---
title: Agentic memory is one graph with scoped truth
status: Accepted
created: 2026-10-17
last_reviewed_on: 2026-10-17
review_in: 12 months
applies_to: nephilim
ai_summary: >
  The design for companion memory: episodes, facts and persona evolution in the
  Neo4j graph, conversation text staying in SQLite. Read before building anything
  memory-shaped, before adding a node label, and before touching the fact store.
  Records the four-timestamp model, the write path, the scene-scoped-truth rule,
  and the one experiment the read path is gated on. Supersedes ADR-006's
  fact-store rejection for SCOPE only; ADR-006's voice measurements stand and the
  read path is still closed until the experiment in here says otherwise.
---

# ADR-021: Agentic memory is one graph with scoped truth

## Context

Three things forced this decision now.

**The graph became the system of record and the fact store did not follow it.**
Ecosystem ADR-012 made the Neo4j graph authoritative for persona identity. ADR-014
scoped itself to rules and said so explicitly — *"Scope is rules, not facts.
`memory_facts` stays in SQLite"* — and deferred the rest: *"when they move, that is
a separate decision with its own ADR."* This is that ADR. The result of not writing
it was two stores for "what she knows", with nothing connecting them and the SQLite
half switched off since 2026-07-06.

**The owner's priority is now stated.** Asked to choose between her *evolving* and
her *remembering him*, the answer was both, and remembering first. That ordering
settles what gets built first and it is not derivable from the code.

**The counter deletion cleared the ground.** On 2026-10-16 the identity graph's
`reinforced_count`/`salience`/`last_referenced` were deleted after measuring that
they had never been written — 256 nodes, one distinct row, `(0, 0.0)`. ADR-018's
title says identity *"evolves in its edges"*; the implementation had put evolution
in node properties, which is why nothing ever wrote it. The title was right.

### What ADR-006 requires this document to answer

ADR-006 rejected a graph **for the fact store** on measured evidence and set a
revisit trigger: *"revisit a graph layer only if continuity evals show failures
concentrated in multi-hop relational queries."*

**That trigger has NOT fired, and this ADR does not claim it has.** There is no
continuity eval, so it cannot have fired. Saying otherwise would be the third time
this repo manufactured a trigger to license work already decided on other grounds.

What changed is the *alternative*, not the evidence. ADR-006 compared SQLite against
a graph as a greenfield choice. Since ADR-012 the comparison is **two stores versus
one**, with the graph already authoritative for identity and already holding 277
nodes. ADR-006's own numbers — graph gain ~2-3.6% at p>0.05, 32-40% more tokens, up
to 86% higher latency, and ConvoMem's finding that simple retrieval beats graph-RAG
below ~150-300 conversations — are about **retrieval accuracy at scale**, which is
not the question a single-store decision turns on. They remain true and they remain
the reason the retrieval design below is deliberately unambitious.

## Decision

### One graph. Text stays in SQLite.

| Where | What | Why |
|---|---|---|
| Neo4j | episodes (pointers), entities, facts, threads, persona traits, rules | one store for "what she knows" |
| SQLite | every message, verbatim | already durable, already backed up, and the graph cannot be backed up while running |
| context window | working memory | see below — nobody puts this in a graph |

**Working memory is not in the graph, and that is not a compromise.** CoALA defines
it as holding information *"for the current decision cycle"* and names no data
structure. Checked by source grep across Graphiti, Zep, Letta, LangGraph, mem0 and
Cognee: none hold working memory as nodes or edges — the words `working`,
`transient`, `scratch` and `frontier` do not appear in Graphiti's `nodes.py` or
`edges.py`, and "working memory" does not appear in the Zep paper at all. It is
universally the context window or a non-graph cache.

### Schema

```
(:Thread)-[:HAS_EPISODE]->(:Episodic)-[:NEXT_EPISODE]->(:Episodic)
(:Episodic)-[:MENTIONS]->(:Entity)
(:Entity)-[:ASSERTS {...}]->(:Entity)        # a fact
(:Persona)-[:HAS_TRAIT {...}]->(:TraitValue) # her evolution, same edge shape
```

`:Episodic` holds `session_id` + `message_id` and **never message text**. That is
already an invariant: `idn.cq06` requires the graph to REFUSE "what did she say three
weeks ago", and duplicating the message log into the one store that cannot be backed
up live would be a regression.

**Facts are edge properties, not nodes.** This follows Graphiti, which writes every
fact as a generic relationship carrying the predicate as a *property*. We keep our own
33-predicate controlled vocabulary rather than Graphiti's open extraction, and we use
a named relationship type (`:ASSERTS`) rather than its generic `:RELATES_TO`, because
our vocabulary is closed and a typed edge is cheaper to query. The cost is accepted and
stated: **Cypher cannot point an edge at another edge**, so anything needing a fact
*about* a fact must be promoted to a node. Rules already are nodes, which is why
ADR-017's supersession chain works.

### Four timestamps, on the edge

| | |
|---|---|
| `valid_at` / `invalid_at` | when it was true in the world |
| `created_at` / `expired_at` | when we believed it |

All four on the **edge**, not the node. An entity does not become untrue; a *claim*
about it does. Nodes carry `created_at` only. This is Graphiti's model verbatim.

Point-in-time reads are the same query shape with the pair swapped:

```cypher
-- what was TRUE as of T
WHERE e.valid_at <= $T AND (e.invalid_at IS NULL OR e.invalid_at > $T)
-- what we BELIEVED as of T
WHERE e.created_at <= $T AND (e.expired_at IS NULL OR e.expired_at > $T)
```

⚠️ **The `IS NULL` branch is load-bearing and its absence is silent.** Cypher's
three-valued logic makes a NULL comparison neither true nor false, so
`e.invalid_at > $T` alone drops every still-valid fact and returns a clean, plausible,
wrong answer. This repo has already shipped exactly this bug once —
`NOT [field, null] IN $live` left 128 stale nodes live with `expired: 0` and no error.
**Test the point-in-time query against a never-superseded fact**, not only against a
superseded one: a guard exercised solely by the invalidated case passes green while
dropping the live case.

**Invalidate, never delete.** Two `SET`s, no new label, no new node. This is the one
point of unanimous agreement across every system that models time at all. Verified in
Graphiti's `resolve_edge_contradictions`: on a detected overlap it sets
`edge.invalid_at = resolved_edge.valid_at` and `expired_at` to now, and the edge stays.

### Scene-scoped truth

Some state is real and must never become durable: what she is wearing in this scene,
what we are pretending right now, the current mood.

**No LLM-agent paper formalises this.** Eight arXiv queries combining roleplay with
memory returned zero results each. The mechanism is nonetheless settled practice
outside our field, and the clearest formalism is **Inform 7** (interactive fiction,
shipped 2006):

> `Train Stop is happening` — true now, dies with the scene
> `Train Stop has happened` — durable, persists

Both can be true at once. Rules attach with a `during <scene>` clause. Rasa ships the
same decision as a config toggle (`carry_over_slots_to_new_session`,
`session_expiration_time`); SillyTavern offers one lorebook mechanism at two scopes,
chat-bound or character-bound, as an explicit authoring choice.

**Our rule: scene state never enters the graph.** It lives in the session row, the same
place LangGraph keeps thread state. The *fact that a scene happened* is durable (an
`:Episodic` and its `:Thread`); its contents are not asserted as facts about anybody.
The test is CoALA's and it is structural rather than a content list: **does this outlive
one decision cycle?** Nobody publishes a content list — checked across Letta, LangGraph,
Cognee, mem0 and Zep — so inventing one here would be inventing precedent.

### Semantics: definitions are mandatory, formal ontology is not

Added 2026-10-17, after the schema above was accepted, because the question "does this
include semantics?" exposed that it did not.

**The gap.** This repo enforced its vocabularies strictly and defined none of them — 55
controlled terms across 8 vocabularies, every one a bare string whose meaning lived in a
Python comment. The live graph carries 30 distinct property keys and **not one** is a
definition, label, description or note.

**That is a data problem, not a documentation problem.** 34 live nodes are tagged
`behaviour` and 20 `psychological` with nothing stating the difference, and the same shape
has already cost real data here: `has_opinion_on` had an unconstrained range and **half of
everything the extractor ever wrote landed in the two vaguest predicates** — three of
twelve live rows were facts about the *assistant's own reasoning*. Noy & McGuinness name
an unconstrained range ("do not make the range THING") as the modelling defect, and the
literature bounds relation **genericity**, not relation count.

**Decision: a definition per term, enforced — and nothing more.** Now a standing
constraint with a wired check, [INVARIANTS.md](../INVARIANTS.md) → "Every declared term
carries a definition". 55/55 defined, and the check was watched failing three ways: an
undefined new term, a definition that restates its term, and a definition orphaned from
its vocabulary.

**Declined on evidence rather than taste:** SKOS, RDFS, OWL, PROV-O, SHACL, and every
external vocabulary (schema.org, FOAF, Dublin Core, OWL-Time, Wikidata, BFO/DOLCE/SUMO).
They solve interoperability between parties who cannot talk to each other, at term counts
no one person can hold — thesauri exist for 40k–400k terms; this is 55. schema.org is also
a licence trap on import (CC BY-SA share-alike), and SKOS has no deprecation mechanism at
all. The ecosystem's own ontology research reached the same verdict one level up — "adopt
none of the candidates wholesale" — and gates the formal apparatus behind a stated
trigger: **a second person or consumer.** That trigger governs here too.

**Hierarchy declined, and this was the counter-intuitive one.** A `broader`/`narrower`
tree over 33 predicates would make extraction *worse*: it turns one decode decision into
two, each a fresh chance to pick wrong. The measured lever is showing **fewer** options per
call — schema pruning beat the full schema at ~6x fewer tokens, and small models degrade
sharply with schema bloat — not organising more of them. Flat is the correctly-sized
artifact, and Graphiti does the same.

**Validation is two layers, because one is not enough.** Neo4j Community gives uniqueness
only — no property existence, no types, no node keys, zero APOC. That gap is smaller than
it looks: **most of what needs validating is not a shape at all.** SHACL would not have
caught the `polarity` inversion either, because "did this supersession preserve an
invariant across properties" is not "does this node have property X of type Y". So:

1. **Write-time vocabulary checks** — already built, and stronger than a database
   constraint because they reject before a row exists. Keep them fail-loud.
2. **Periodic integrity queries that should return zero rows** — already built as
   `check_integrity()`, and what actually caught the four-way `supersede_rule` break.
   This is dbt's own definition of a test, arrived at here independently.

SHACL via neosemantics remains the decided-but-unbuilt route for shape constraints if they
are ever wanted. Nothing in this ADR needs it.

**Conformance is not correctness, and nothing here depends on confusing them.** Measured:
13B models reach 83–94% ontology conformance alongside **22–38% F1** — the model reliably
picks a valid predicate while getting the fact wrong. Definitions buy conformance and human
clarity. Any future claim that they buy accuracy needs its own measurement.

⚠️ **Definitions never reach her prompt.** Adding identity content to the prompt is the
closed change (0.804 → 0.625; reframings 0.708 / 0.542 / 0.500), and the extractor prompt
is separately capped by schema bloat. If definitions ever reach a model, they reach it
pruned to the few terms that call could plausibly use.

**One safety property, now deliberate rather than accidental.** The schema above carries
the predicate as a **property** on a single edge type, not as the relationship type
itself. That is also an injection boundary: Cypher cannot parameterise a relationship
type, so a predicate used as a type would be string-interpolated into a query. Keeping
the predicate a property and the vocabulary closed is a security control, not only a
data-quality one.

### Write path

Three rules, each from how the reference implementations actually work.

1. **The LLM never writes Cypher.** It returns a typed object; deterministic code fills
   a pre-written parameterised query. Confirmed as Graphiti's actual pattern — every LLM
   call returns a Pydantic model and the write is
   `MERGE (source)-[e:RELATES_TO {uuid: $edge_data.uuid}]->(target) SET e = $edge_data`.
   A garbled fact cannot become a clause because it is bound as a parameter. On a
   schema-validation failure, **extract nothing** rather than parsing leniently.

2. **Contradiction: deterministic first, LLM only for the remainder.** Interval-overlap
   logic decides what it can. Graphiti's LLM prompt for the rest is explicitly
   conservative — *"NEVER mark facts as contradicted unless the new fact explicitly makes
   the old fact false"* — and forbids inferring contradiction from recency. We adopt that
   wording. A conflict that cannot be resolved becomes a **`FactConflict` node** marking
   the facts `current` / `superseded` / `conflicting`, which is Cognee's pattern and is
   better than a silent overwrite.

3. **Dedup by embedding + full-text, not by LLM.** Graphiti's named constants are
   `NODE_DEDUP_COSINE_MIN_SCORE = 0.6` and `NODE_DEDUP_CANDIDATE_LIMIT = 15`, with the
   LLM only as escalation for the unresolved remainder. ⚠️ **These are borrowed
   constants and must be refitted before they gate anything** — this repo has measured a
   borrowed threshold scoring 31% against real data where a fitted one scored 57%.

**`asserted_by` is required on every fact**, set deterministically from the SQLite
`role` column, never by LLM judgment. Graphiti has no such field — verified by reading
`extract_edges.py` and `extract_nodes.py` — and its absence is visible in our own 12
live rows: three are facts about the assistant's own reasoning
(`is_currently_engaged_with: analyzing the user's question`) and one stored her phrasing
back at the user (`has_goal: start something of your own`). Facts are extracted from
**user turns**; assistant turns are a separate queryable class and never contaminate
user-fact retrieval.

**Five things Graphiti lacks that we add**, four of which were already day-1 items on
the ecosystem migration list: `expired_at` (transaction time cannot be reconstructed
later), `asserted_by`, an append-only `identity_resolution` crosswalk so a wrong entity
merge is undoable, a **tombstone log outside the graph** keyed on a natural key rather
than a UUID so a rebuild cannot resurrect a retracted fact, and `extractor_version` +
`ontology_version` stamped on every write so an old extraction can be replayed against
a new ontology. A predicate rename is handled by an append-only alias map consulted at
**read** time; historical edges are never rewritten. Retire, do not rewrite — the same
rule as invalidate-not-delete, one level up.

### Read path: deliberately unambitious, and gated

**Episodes are read by time window, not by walking the chain.** Graphiti's own
`retrieve_episodes` is `MATCH (e:Episodic) WHERE e.valid_at <= $reference_time ... ORDER
BY e.valid_at DESC LIMIT $num_episodes` with `EPISODE_WINDOW_LEN = 3`. The
`:NEXT_EPISODE` edges exist on disk and the shipped retrieval path never traverses them.
We build the chain for provenance and for the summariser's cursor, and we read by
timestamp within a `:Thread`. *"What are we in the middle of?"* is answered by thread
scoping plus ordering, not by a traversal.

**Memory is appended late, never placed in the system prompt.** Measured: memory
refreshed every turn in the system prompt costs 1.3x tokens at 18 turns, 1.6x at 36 and
1.9x at 54, with identical output, because any change at or before the cache breakpoint
invalidates that block and everything after it. Static first, dynamic last.

#### ⚠️ The read path is CLOSED until one experiment says otherwise

ADR-006 measured that injecting remembered content costs voice: **0.804 → 0.661
(facts only) → 0.625 (both)**, and three reframings made it worse — **0.708 / 0.542 /
0.500** against a 0.792 ruler. The instruction stands: *do not re-attempt
prompt-framing.*

**But the decline was monotonic across three prose variants.** If phrasing were the
lever, some variants would have helped. Monotonic decline says the axis is not phrasing
— it is **modality or position**, and all three arms were prose in the system prompt.

Two further facts sharpen this into a cheap experiment rather than a re-litigation:

- **gwen was not in the eval that closed the line.** The 2026-07-06 run covered seven
  personas and she was not one of them; the 2026-08-11 rework covered eeva, solace and
  aurora — the three advisory personas, which are the class that collapses. **gojo**, the
  one strong voice tested, scored **1.0 with memory and without**.
- **No published benchmark measures recall and voice together.** LongMemEval, LoCoMo,
  PerLTQA and both vendor evals optimise recall, latency and tokens and are silent on
  style. Our blind-attribution metric is not a shortcut here; it is ahead of the field.

So the gate is: **same facts, rendered as compact triples and appended late, measured on
gwen, scored on recall AND voice.** A fourth prose reframe is still forbidden. If voice
holds, the read path opens; if it does not, the graph still earns its place on write-side
provenance and the facts wait for a model change — which ROADMAP already sequences.

There is a mechanism for why modality might matter, and it is a hypothesis, not a
finding: induction heads complete `[A][B]` patterns, and *"You also remember that he
likes RPGs"* is exactly the shape a model extends in kind, where a bare triple is a
weaker completion target. **Nobody has studied structured-vs-prose for voice
preservation.** Zep ships templated prose with no published rationale.

### Her evolution uses the same edge

`(:Persona)-[:HAS_TRAIT {valid_at, invalid_at, episodes:[...]}]->(:TraitValue)`.

A trait moves: close the old edge, open a new one, and `episodes` names the conversation
that moved it. That yields *"this trait moved, and here is what moved it"* with no new
machinery, and it is what ADR-018's title meant. Letta's core-memory blocks — the most
mainstream equivalent — have no versioning, no timestamps and no link from an edit back
to its cause.

⚠️ **Hard walls remain unwritable** (ADR-017). CoALA states the principle independently:
writing to procedural memory *"is significantly riskier than writing to episodic or
semantic memory."*

### Procedural memory: our design is unprecedented, and the prior art is older than LLMs

A determined search found an active 2026 cluster on procedural-memory-as-graph —
Procedural Graphs (arXiv 2609.09153) organising *"(procedure, relation, procedure)
triplets for what-to-do questions"* with validate-before-commit; AIP (arXiv 2606.04781,
co-authored by Neo4j's Chief Scientist) with schema-validated, node-addressable repair
measured at one task going 0/5 → 5/5; HiSkill (arXiv 2607.25853) with typed
compatibility/support/recovery edges; and a 124-paper survey (arXiv 2607.10113) in which
rollback is a named governance mechanism.

**But rule polarity plus priority on persona behavioural rules returned zero results.**
The nearest prior art is **classical production systems** — the conflict set and its
resolution strategies (rule order, explicit priority, recency) from the Rete/CLIPS era.
ADR-014's design has no published competitor to check against, and the literature to
borrow from is 1980s expert systems rather than 2026 agent papers. Recorded so the next
person does not go looking for a paper that does not exist.

## Status

Accepted — for the write path, the schema and the scope. The **read path is gated** on
the experiment above and is not accepted by this document.

## Consequences

**Easier.** One store for what she knows. Point-in-time answers become queries rather
than reconstruction. *"What are we in the middle of?"* becomes answerable
(`prf.cq03`, previously unrepresentable). Her evolution becomes auditable to the
conversation that caused it (`evo.cq01`/`evo.cq02`). `tmp.cq03`/`tmp.cq04` become
answerable once `expired_at` exists.

**Harder.** Two stores must agree on what an episode is, and the pointer is the only
link. Extraction quality becomes load-bearing and is currently unmeasured — the "2
useful facts out of 12" figure is a hand judgement over 12 rows, not a scored eval, and
ROADMAP already mandates a 100-item hand-labelled set before any detector gates
anything. A graph that holds conversation-derived facts can no longer be rebuilt from
the card, so the nightly backup carries more weight than it did yesterday.

**Follow-up, in order.**

1. `expired_at` + `asserted_by` + the tombstone log. Two nullable columns and one table;
   irreversible if deferred, because transaction time cannot be reconstructed.
2. Episodes and threads from the existing SQLite log — pointers only, no extraction, no
   LLM. Answers `prf.cq03`.
3. The read-path experiment. Triples vs prose vs late-append, on gwen, scored on recall
   and voice.
4. Extraction, last, and only once (3) says which rendering survives her voice. With a
   hand-labelled set first.

**Deliberately not decided here.** Consolidation cadence (Graphiti's saga summariser is
pull-based — nothing calls it automatically, so the trigger is ours to choose and we have
no basis yet for choosing). Community/cluster summaries. Pruning: no system researched
has a TTL, decay function or archival tier, so bounded growth is novel territory rather
than a pattern to retrofit, and `reinforced_count` is **not** coming back without new
evidence.

## Related

- [ADR-006](006-companion-memory-and-continuity-eval-first.md) — the voice measurements,
  which stand, and the fact-store rejection this supersedes in scope only
- [ADR-014](014-the-rule-store-is-a-neo4j-projection-superseding-the-adr-001-and-adr-006-rejections.md)
  — "scope is rules, not facts", and the separate-ADR requirement this satisfies
- [ADR-017](017-learned-preferences-may-change-soft-walls-never-hard-walls.md) — hard
  walls stay unwritable
- [ADR-018](018-the-identity-graph-is-built-from-the-card-and-evolves-in-its-edges.md) —
  the identity store, its 2026-10-16 amendment, and the episodes it deferred to here
- [ADR-019](019-the-graph-is-the-source-of-identity-not-extra-prompt-text.md) — adding
  prompt bytes is the closed change; switching a source is the free one
