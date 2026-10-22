---
title: Agentic memory is one graph with scoped truth
status: Accepted
created: 2026-10-17
last_reviewed_on: 2026-10-22
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

## AMENDMENT 2026-10-22 — the read path opened WITHOUT this ADR's gate, the schema changed in four names, and one modelling decision is reversed

Five statements in this document are now false. They are left in place below
(reversals stay visible) and corrected here.

### 1. "The read path is CLOSED until one experiment says otherwise" — FALSE

Stated in five places: the `ai_summary`, the `#### ⚠️` heading, the Status line
("the **read path is gated** on the experiment above"), and "**The read path
stays CLOSED, for a new reason.**"

The read path is **open and live**. The numbered recall frame reads the identity
graph on every turn (`MEMORY_SELF_FACTS`), and `what_do_you_know_about_me`
serves the operator's facts from `(:Fact)-[:ABOUT]->(:Operator)`.

🔴 **The Phase 2 gate was never run, and nothing here should be read as saying
it passed.** The gate was "triples vs prose, appended late, measured on gwen,
scored on recall AND voice". What actually opened the path was a *different*
measurement — self-recall 8/15 → 15/15, with the deflection fix taking wrongly-
deflected answers 4/12 → 1/12. That is a recall result, not the recall-and-voice
comparison this document specified.

The voice half was finally measured on 2026-10-22, and it is recorded honestly:

| arm | distinctiveness | attribution |
|---|---|---|
| OFF | 1.000 | 8/8 to gwen |
| ON (760 tokens) | 0.875 | 7/8 to gwen, 1 to `nephilim_solace` |

**No collapse** — ADR-006's EEVA/solace collapse to 0.25 does not reproduce, and
gwen's ON arm is *exactly* her score in the historical 8-persona ruler (0.875).
⚠️ But the repo's own gate reports **REGRESSION**, because its `TOL = 0.01` sits
against a metric whose smallest possible step is 1/8 = 0.125 — so it cannot
return "within noise" on this instrument at all. One flip of eight is p=1.0
paired; six one-way flips are needed for p<0.05. **Whether a subtle voice cost
exists is still unmeasured, and this instrument cannot measure it.**

⚠️ Also: the historical **0.804 OFF arm is unrecoverable** — no file on disk
contains 0.804 or 0.661, only the 0.625 ON arm. ADR-006's comparison can be
reproduced in protocol only, never in number.

### 2. The schema block names four things that were never built

The block reading

    (:Thread)-[:HAS_EPISODE]->(:Episodic)-[:NEXT_EPISODE]->(:Episodic)
    (:Episodic)-[:MENTIONS]->(:Entity)
    (:Entity)-[:ASSERTS {...}]->(:Entity)

is wrong in four names. What ships is `:Episode` + `CONTINUES`
(`episode_repository.py`, 229 nodes / 228 edges) and
`(:Fact)-[:ABOUT]->(:Operator)` (`operator_fact_repository.py`). `:Episodic`,
`NEXT_EPISODE`, `MENTIONS` and `ASSERTS` exist nowhere in `graph_surface.py`.

**`Episode`, not `:Episodic`, was forced by the question set** — `prf.cq03`
demands `Episode`, C17 compares bare strings, and `:Episodic` would have been
undemanded-and-unexempted while `Episode` showed as demanded-and-unbuilt.

### 3. "Facts are edge properties, not nodes" — REVERSED

This document followed Graphiti in writing every fact as a relationship carrying
the predicate as a property. The shipped design makes `:Fact` a **node**, on a
ground this ADR did not consider: `profile_data["facts"]` is a blob of bare
strings with nowhere to hang a subject or a speaker, and the consequence was
measured — after the self-facts section shipped she recited her own appearance
and the extractor filed it under HIM (`Assistant's eyes are a deep ocean blue`
in the operator's fact list, 38 such rows in ~20 minutes). A node carries
`origin` and `asserted_by`; an edge property had nowhere to put them.

### 4. `:Thread` and `PART_OF` are REFUSED, not pending

`prf.cq03` demands them. They are deliberately not built, because grouping
sessions into topical threads needs scene-boundary detection and the only
groupings available without it are the two the question's own negative fixture
forbids ("She must NOT merge two separate threads into one"): one thread per
operator merges every scene, one thread per session makes `Thread` a synonym for
`Episode`. A test asserts the refusal, so building it later must argue past it.

### 5. ⚠️ THE CONTRADICTION ORDERING IN THIS ADR IS NOT LITERATURE-SUPPORTED

This document says: interval logic first, LLM only for the remainder, and
**never infer contradiction from recency**. Research on 2026-10-22 read
Graphiti's source and found the opposite on both counts —
`resolve_extracted_edge()`'s own docstring orders it "dedupe it, date it, then
apply contradictions" (LLM dedup **first**), and `_apply_temporal_invalidation()`
is plain **newest-wins**: "a candidate that starts later supersedes this edge".

ADR-021's ordering is therefore a **stricter, unprecedented design bet**, not a
borrowed best practice. It may well be the better choice — recency-wins is a
known footgun — but it must be defended on its own merits, and this amendment
records that no reference implementation does it this way.

One premise *is* confirmed: `temporal_edge_utils.py` runs date parsing and
future-tense detection as pure regex with no LLM, so "interval logic can run
without an LLM" is true.

### 6. Phase 0 and Phase 1 are partly done

- **Phase 0**: `asserted_by` **DONE** (from the SQLite `role` column, never an
  LLM), `valid_to`/`expired_at` **DONE** on `:Fact`. Still open: the tombstone
  log, the `identity_resolution` crosswalk, `extractor_version`/
  `ontology_version`.
- **Phase 1**: episodes **DONE** as pointers; threads **refused** (see 4).
- **Phase 4**: "user turns only" **DONE** — both extraction paths physically
  redact assistant content.

⚠️ `asserted_by = "user"` is a **window-level** claim, not per-fact.
`extract_facts` returns bare strings with no provenance, so a fact cannot be
traced to a row; what makes it sound is that non-user turns are *physically
absent* from the extractor's input. The per-fact version needs a quote span —
`TripletExtractor` already returns one and `apply_triples` already accepts a
`message_id` that **nothing ever passes**.

---

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

## AMENDMENT 2026-10-18 — Phase 2 ran. It did not decide the gate, and it changed what gates Phase 3.

5 arms × 35 hand-labelled probes × 2 reps, 16 turns of real conversation per
generation, pre-registered before any generation and settled against the register.

| arm | recall | card facts | hallucination |
|---|---|---|---|
| A — facts as prose in the system prompt | **0.796** | 0.800 | 0.938 |
| B — same facts on the user turn | 0.722 | 0.700 | 0.812 |
| C — turn-shape directive softened | 0.815 | 0.800 | 0.875 |
| D — both | 0.611 | 0.600 | 0.812 |
| E — facts only through a `recall` tool | 0.537 | 0.800 | 0.688 |

**The gate is UNDECIDED, and that is the recorded outcome rather than a nearest
winner.** No arm beat the baseline — prereg falsifier (1). The defect Phase 2 was
built to explain never reproduced: `card.eye_colour` was answered correctly **10
of 10 across every arm**. Position and instruction conflict are both wrong as
explanations, and the only significant result is that **D is −18.5 points (7 down
/ 0 up, p=0.0156)** — combining two harmless interventions hurt.

**What this changes for Phase 3, which this ADR gates on Phase 2.** The gate was
inherited from ADR-006 as *"does injecting facts cost voice?"* — measured there at
0.804 → 0.625. **Phase 2 found no voice cliff and a different blocker entirely:**
hallucination on the 8 negative controls runs **0.688–0.938**, baseline 0.938. She
confidently answers 94% of questions she cannot possibly know, inventing a
hometown, a twin sister, a skin tone and the operator's job. **Phase 3 should be
gated on ABSTENTION, not on distinctiveness.** Recall at ~0.80 with facts injected
is already usable; a companion that fabricates 94% of what she does not know is
not, and no arm here moved it.

**The read path stays CLOSED, for a new reason.** Not "we have not measured it"
but "we measured it and the blocker is elsewhere".

**The typed-tool / MCP arm is the most informative result and it is good news for
that architecture, not bad.**

```
tool FIRED      n=11   recall 1.000   Wilson 95% [0.74, 1.00]
tool NOT fired  n=43   recall 0.419
firing rate     12/70 = 17.1%         Wilson 95% [0.10, 0.28]
```

A tool-delivered fact was used **perfectly in every observed case** — the question
the literature could not answer, because nobody has isolated delivery mechanism
from retrieval quality. Arm E loses overall only because she calls the tool 17% of
the time. **The ceiling is the highest of any arm; the gate is delivery, not
representation.** So the MCP path is not refuted, it is quantified: it needs the
firing rate moved, and `run_ab.py --arms E_tool` is the instrument to re-run on
any model change. ⚠️ `n=11` on the fired cases, interval down to 0.72 — a clean
direction, not a precise number.

⚠️ **The first run of arm E was invalid and nearly shipped as a finding.** The
harness had no tool-execution loop, so a fired call scored its empty content:
recall **0.000 across 13 fired calls** against 0.439 across 41 unfired. That is a
tidy story about tool-delivered facts being unreliable, and it was a wiring error.

⚠️ **One session, 35 probes, no variance estimate.** Read any gap under ~15 points
as noise. This is a smoke test and a regression pin, not a ruler — every
comparable published benchmark is 1–3 orders of magnitude larger on both axes.

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
