---
title: The rule store is a Neo4j projection — superseding the ADR-001 and ADR-006 rejections
status: Accepted
created: 2026-09-26
last_reviewed_on: 2026-09-26
review_in: 12 months
applies_to: nephilim
---

# ADR-014: The rule store is a Neo4j projection — superseding the ADR-001 and ADR-006 rejections

## Context

**The knowledge graph is decided at the ecosystem level and is not reopened here.**
[SEMANTIC_PLATFORM.md](../../../docs/architecture/SEMANTIC_PLATFORM.md),
[NEO4J_COMPONENT.md](../../../docs/architecture/NEO4J_COMPONENT.md) and ecosystem
ADRs 009/010/011 carry that decision and its reasoning; the ecosystem
`docs/INVARIANTS.md` makes reopening it out of scope. This ADR exists for a
narrower and purely local reason: **two ADRs in *this* repo say the opposite, and
a repo whose documentation contradicts its running system silently reverts.**

`SEMANTIC_MIGRATION.md` already recorded the supersession from the other side —
*"the name appears in ADR-001/006, CHANGELOG and ROADMAP as a previously-rejected
option. Net-new. Day 1 per the concept document's decision."* This is nephilim's
record catching up.

### What ADR-001 actually rejected, and the rung that has rotted

[ADR-001](001-lore-as-typed-markdown-wiki-not-a-graph-db.md) says *"Do NOT
introduce Neo4j (or any graph DB) **for this**"* — and `for this` is
load-bearing. Its Context scopes it to hand-authored worldbuilding canon in
`docs/lore/`, ~30 entities, git-authored, PR-reviewed, *"verified — not consumed
at runtime"*. Three of its four grounds are lore-specific and do not transfer:
git-native authoring, overkill at ~30 nodes, and the LLM consuming lore as text.

**One ground does transfer and is honoured below**: *"another always-on service
violates simplicity at solo scale."* That is a real constraint on a box running
live trading, and it is why this decision buys a rebuildable projection rather
than a system of record.

**One clause is now void.** ADR-001's stated escalation path is *networkx
in-memory → MongoDB `$graphLookup` → Neo4j read-only projection*. The middle rung
no longer exists: [ADR-002](002-remove-mongodb-mcp.md) removed MongoDB entirely,
deleted `MongoDBSettings`, and dropped `pymongo`. A path through a store this repo
does not have is not a path.

**ADR-001 is otherwise unamended and still governs lore.** The lore wiki stays
markdown. `SEMANTIC_PLATFORM.md` agrees explicitly and by name: *"Do not migrate
the lore wiki to YAML: it is content, it works (see nephilim ADR-001)."*

### What ADR-006 rejected, and the honest state of its trigger

[ADR-006](006-companion-memory-and-continuity-eval-first.md) rejected
knowledge-graph memory **for the fact store**, on measured evidence, and set an
escalation trigger: *revisit a graph layer only if continuity evals show failures
concentrated in multi-hop relational queries.*

**That trigger has not fired, and this ADR does not pretend it has.** The
memory-injection line was closed 2026-08-11 and no such eval evidence exists. The
supersession is by ecosystem-level architectural decision, not by ADR-006's own
condition being met. Recording that plainly is the point: a future reader must be
able to see that the override was deliberate rather than discover a quietly
inverted ADR.

**And this slice does not enter ADR-006's territory at all.** ADR-006 is about
*facts*. This is about **standing behavioural rules** — a class it never
considered. Facts stay in SQLite for now; when they move, that is a separate
decision with its own ADR, and ADR-006's measured evidence is an input to it.

### The local problem this actually solves

Measured 2026-09-26 on `dev`: **zero of gwen's rules reach the model.** Two
independent causes. `PERSONA_CONSTRAINTS_IN_PROMPT` defaults `False`, no card opts
in, and it is absent from `.env` — so the whole constraints path is dark on prod.
And `prompt_builder.py:377-393` records that even with the flag on, her ~775-char
block against a 150-token budget front-pops three sections, losing `do`, `dont`
**and** the bond. A persona whose 15 prohibitions were just classified into hard
walls, soft walls and dials currently receives none of them.

## Decision

**Neo4j holds a projection of gwen's standing rules. It is not a system of
record.** The card in git is the origin; the projection is rebuildable and
droppable with one command. This is the ecosystem invariant —
*"nothing lives only in the graph, by construction"* — and it is what satisfies
ADR-001's one transferable ground: an always-on service whose loss costs
capability rather than data is a different risk from one whose loss costs data.

**Scope is rules, not facts.** `memory_facts` stays in SQLite.

**Rules are nodes; facts remain relationships.** Not aesthetics: a Neo4j
relationship cannot be the endpoint of another relationship, and a rule needs two
things pointed *at* it — a supersession chain and a provenance edge. The cost of
the alternative is visible in Graphiti, the leading production graph memory for
agents: because its facts live on edges, its provenance is an array of opaque
UUID strings inside a property, and it has **no supersession edge at all** — so
"what did I believe before X" is a scan-and-reconstruct there, not a traversal.
That is a capability this design requires.

**`:CurrentRule` is a label, not a timestamp predicate.** Neo4j indexes do not
store nulls, so an index on `expired_at` can never serve `expired_at IS NULL` —
the hot predicate — while still costing on every write. A label-scoped index is
Neo4j's only partial index. Null stays the semantic truth; the label is derived
state, reconciled by an integrity query. **Graphiti ships the useless index; we
do not.** The same mistake already exists locally in
`idx_memory_facts_valid ON memory_facts(valid_to)`.

**The read is `ORDER BY priority DESC, rule_id DESC`, with `rule_id` a sortable
unique id**, and determinism is a property of that being a TOTAL order — nothing
more. Not `created_at`: seeding a card writes all 15 rules in one
transaction, so they share a timestamp and exactly the rules most likely to tie on
priority would stay tied. Not `elementId()`: not stable across a dump/restore.
Only a `UNIQUE` property gives a total order, which is what makes "the same
question returns the same answer every turn" true by construction rather than by
luck.

**Fail-closed lives in Python, because no edition of Neo4j can enforce it.**
Community enforces uniqueness only — property existence, node keys and type
constraints are Enterprise, and *no* edition has a value-domain constraint. So
`rule_type` defaulting to `hard_wall` is enforced at the write boundary and again
by `coalesce()` on read, with an integrity query asserted in the test suite.
Labels-as-enum was considered and rejected: it makes absence impossible but not
multiplicity, so `(:Rule:HardWall:Dial)` would be silently legal and fail-closed
would become fail-ambiguous.

**We are inventing the bi-temporal shape, deliberately.** SQL has a standard for
this; RDF has three; **Neo4j has none** — no time-travel syntax, no period types,
no temporal index — and the literature still treats it as open. The two closest
implementations disagree with each other. The four timestamp names are therefore a
local standard, used identically on rules and facts.

**Flag off by default, and scoped to one persona.** The build target is
`gwen_dev`, a separate card with `active: false`. Live gwen is untouched — not
behind a flag, behind a different persona. A byte copy was rejected: the persona
name index is last-write-wins in sorted filename order, so `gwen_dev.json`
carrying `key: "gwen"` would hijack every lookup of the live persona.

## Status

Accepted — **one claim in this ADR was measured and found wrong, 2026-09-26.**

An earlier draft said the read is deterministic *and* index-backed with no Sort
operator, as though those were one claim. They are two, and only the first holds.

Measured with EXPLAIN on 5.26.31, index ONLINE: the real read plans as
`NodeByLabelScan` + `Top`. A **three-column** composite on
`(persona_id, priority, rule_id)` is never used at all — it was dead weight paying
write cost on every insert — while a two-column `(persona_id, priority)` is
seekable. And `Top` appears in every variant: **a composite index does not supply
`ORDER BY priority DESC, rule_id DESC` on this version.** `USING INDEX` is refused
outright, because `expired_at IS NULL` cannot be index-served — the same fact that
made `:CurrentRule` a label rather than a predicate.

At 9 rows the planner is right to scan, and stays right into the hundreds. So:

- **Determinism** comes from the ORDER BY being a TOTAL order — a priority plus a
  unique, fixed-width, lexicographically-sortable id. It holds under a scan, under
  a seek, under `Sort`, under `Top`, and after a restore that changes the planner's
  mind. It is what this design actually promises, and it is what the tests assert.
- **Index-backing** is a performance property, planner-dependent, and correctly
  absent at this size. The tests deliberately do NOT assert plan shape: pinning
  planner behaviour at a scale that does not represent production would fail for
  reasons unrelated to correctness.

Recorded rather than quietly fixed, because a test asserting only
`"Sort" not in plan` would have passed throughout — `Sort` is genuinely absent and
`Top` does the work.

## What reading her answers changed (2026-09-26)

The slice shipped, then 15 paired probes at temperature 0 with a fixed seed were read
side by side — control against treatment, identical card, the only difference being
the injected rules. Four things came out of it, and three were corrections.

**The rules work, unevenly.** One hard wall was demonstrably fixed: asked to "pretend
you're shy and have never done any of this before", the control adopts the innocent
persona and the treated arm refuses in character. Most probes showed no difference at
all, and that is the honest headline — of six hard walls, one has a measured effect.

**Prohibitions were the wrong form for four of them.** Rewriting "never pretend to be
pure or innocent" as "if he asks you to act shy, tell him no in your own filthy words
and remind him what you are" is what fixed it. Two were left negated on purpose:
there is no positive paraphrase of a consent or dignity boundary that still reads as
absolute.

**The clinical refusal was not borrowing, it was obedience.** She refused with "I
cannot and will not be shy or innocent", and `LEAN_SAFETY` says, unconditionally:
*"When refusing, ALWAYS begin with 'I cannot and will not'"*. So a rule saying "refuse
in character" asked for two incompatible things and the absolute instruction won. The
fix was removing the word "refuse" from the rule. Verbatim spans lifted from the
safety block went 2 to 0 across 15 replies — a weak *rate* (p=0.50) but a mechanism
read directly out of the prompt, which is the part that is checkable.

**RANK IS NOW EVIDENCE-DRIVEN, and the first version of it was inverted.** All six
hard walls were priority 100, so a random ULID decided which two reached the per-turn
echo. Ranking them by cost-if-violated then put the only rule with a measured effect
FOURTH — so a trim to three dropped it, which a limit-of-3 run confirmed. Measured:
the two rules occupying the top slots were never violated in the control arm at all,
or were violated equally in both. Rank is therefore cost × observed need: a rule that
holds without reinforcement should not occupy a scarce slot, however costly a
violation would be.

**One rule cannot be fixed in the prompt at all.** "Address him as Daddy, and only
Daddy" stays half-obeyed under every phrasing and placement, because the operator's
direct instruction wins an instruction-hierarchy contest that open models lose about
half the time. It is enforced after generation instead
(`src/coordinator/rule_compliance.py`, `GRAPH_ENFORCE_RULES`, off by default): detect,
regenerate once with a reinforcement line, never rewrite her words. That is the
intended end state for any rule that becomes deterministically checkable — it then
needs the prompt least of all, which is why it now ranks last.

**Statistical floor, stated so later readers do not over-read these numbers.** With 15
paired probes, six improvements and zero regressions is the minimum for p&lt;0.05. A
15/15 to 13/15 change is p=0.50 and is not a finding; an earlier write-up reported one
as if it were.

## Consequences

**Easier.** Rules reach the model at all, for the first time. The read is
deterministic by construction rather than by a trim loop's append order.
Supersession is a traversal. Provenance is an edge.

**Harder.** Neo4j Community **cannot back up while running** — `neo4j-admin
database dump` is offline-only there, so a nightly backup means stopping the
container for ~30s, and the `system` database must be dumped separately or a
restore loses authentication. That is a real difference from this ecosystem's
MongoDB job, which backs up live. Community also supports exactly one user
database, so a rebuild swaps Docker volumes rather than aliasing databases.

**The driver is a new kind of citizen.** It is the first resource in this app that
genuinely needs closing — `server.py`'s shutdown block currently holds one entry,
the scheduler. And the 6.x driver no longer closes itself on garbage collection,
so an unclosed driver leaks connections silently in a long-lived process.

**One guard does not cover it.** `tests/conftest.py`'s autouse
`_block_production_backend` patches `urlopen` and `httpx` to stop a test run
writing to production — it once caught a suite writing 476 messages into the prod
DB. The Bolt driver uses raw sockets, so **that guard does not protect the graph.**
Tests must point at a separate `NEO4J_BASE_URL` or the protection is absent.

**Deliberately not done.** No alembic migration: `alembic/env.py` hardcodes
`sqlite:///` and sets `target_metadata = None`, so the "dual-covered DDL"
contract does not extend to a non-SQLite store. Neo4j schema lives only in
`_ensure_constraints()`.

**Revisit trigger.** If the projection is ever read when it cannot be rebuilt from
the card plus SQLite, this decision has been violated rather than revisited. If
facts move into the graph, that needs its own ADR answering ADR-006's evidence.

## Related

- [ADR-001](001-lore-as-typed-markdown-wiki-not-a-graph-db.md) - scoped to lore; still governs it; its `$graphLookup` rung is void
- [ADR-006](006-companion-memory-and-continuity-eval-first.md) - rejected a graph for FACTS; superseded only as to rules
- [ADR-002](002-remove-mongodb-mcp.md) - why ADR-001's escalation path no longer exists
- [ecosystem ADR-011](../../../docs/decisions/011-the-graph-is-the-persona-s-runtime-identity-the-card-is-her-seed.md) - card as seed, durable store as identity, graph as projection
- [ecosystem ADR-010](../../../docs/decisions/010-personas-reach-the-graph-through-an-in-process-contract-not-a-protocol.md) - the in-process verb contract this implements
- [competency questions](../../../docs/architecture/competency_questions/) - `rul.cq01` and `rul.cq03` are what this slice must answer
