---
title: The graph is the source of identity, not extra prompt text
status: Accepted
created: 2026-09-28
last_reviewed_on: 2026-09-28
review_in: 24 months
applies_to: nephilim
ai_summary: >
  Read before touching the identity prompt path. Records the decision ADR-018
  deferred: the graph becomes the SOURCE of the identity block (GRAPH_IDENTITY_SOURCE,
  default off) while adding ZERO tokens, so it does not reopen the injection line
  ADR-018 closed on measured voice loss. Carries the floor assertion that rejects a
  partial graph read, and the reason a graph read inside the lru_cache is safe only
  while no chat-time write path exists.
---

# ADR-019: The graph is the source of identity, not extra prompt text

## Status

Accepted, 2026-09-28. Amends the scope boundary stated in
[ADR-018](018-the-identity-graph-is-built-from-the-card-and-evolves-in-its-edges.md)
without reversing it.

## Context

[ADR-012](../../../docs/decisions/012-the-graph-is-the-system-of-record-for-persona-identity-superseding-the-projection-model.md)
makes the graph the system of record and demotes the persona card to an origin and a
reset target. ADR-018 then built the store — 128 typed nodes for gwen, a content/annotation
split so a rebuild preserves her learning, a `:Baseline` recording her shipped dials.

**The claim was not true.** Nothing read the graph. Dropping the database would have cost
nothing, which is the precise opposite of a system of record. Verified 2026-09-28: zero
importers of `IdentityRepository` anywhere in `src/` outside its own module, and the live
graph held 18 rule nodes, two `Persona` nodes, and **zero** identity nodes.

ADR-018 deferred this deliberately, and stated the boundary it was deferring:

> No IDENTITY-NODE content is wired into her prompt, and that is a separate decision.
> […] Injecting remembered content has already been measured to flatten voice —
> distinctiveness 0.804 with injection off, 0.625 with it on, and three deliberate
> reframings scored 0.708 / 0.542 / 0.500, all *below* the off-baseline.

and named the failure it was guarding against:

> **What is most likely to be regretted:** building a rich identity store and then wiring
> it into the prompt the same way the last attempt was wired, and reading the resulting
> voice collapse as a storage-layer problem when it is the same closed line reopened
> under a new name.

## Decision

**Two changes hide under "wire the graph into the prompt", and only one of them is open.**

| | What it does | Status |
|---|---|---|
| **(a) Add** identity-node content as extra prompt text | larger prompt, more facts | **Closed** — ADR-018, measured |
| **(b) Switch the SOURCE** of the existing identity block from card to graph | same block, same size | **This ADR** |

We adopt (b). `GRAPH_IDENTITY_SOURCE` (default **false**) overlays the graph's modelled
identity subset onto the card before the prompt is built. The prompt is **byte-identical**
either way, and that is asserted mechanically rather than intended:
`test_graph_sourced_identity.py::test_overlay_of_the_graphs_own_nodes_reproduces_the_card`
plus a live check — flag off resolves `source: card`, flag on resolves `source: graph`,
prompt sha `f2084bffb48ecb23` **both ways**.

**The byte-identity assertion is the boundary against (a).** The moment the overlay stops
being byte-identical it has become the closed change, and that test fails. This is why the
boundary is a test and not a sentence in a document: the previous boundary was a sentence,
and a sentence cannot notice when it is crossed.

Byte-identity is reachable only because `identity_nodes` and `card_from_nodes` are
inverses over `SOURCE_FIELD_KIND`, which `test_identity_round_trip.py` pins per persona.
The lossless round trip is what makes this a **sourcing** change rather than a **content**
change.

### The floor assertion, which is the load-bearing part

`identity_overlay` **replaces** any field it is given, so a read carrying *some* of `lore`
deletes the rest. Measured on gwen before the floor existed: a read of 111 of 128 nodes
left her with **4 of 21 lore entries**, built a normal-looking prompt, and reported
nothing. That is the dangerous shape — not an exception, a structurally valid card.

It is also a shape that ships in production systems. Pinecone, 2026-06-18, incident
`0trwz267s120`: queries to infrequently-read namespaces *"incorrectly returning empty
results"* across five regions — a successful 200 with no rows. A store that fails by
returning **less** rather than by raising needs a floor, so `_passes_floor` rejects:

- a read **at the limit** (exact — `identity()` applies `LIMIT` after
  `ORDER BY source_field`, so the loss is whole alphabetically-late fields rather than an
  even degradation);
- a read below **50%** of the card-implied node count (a ratio, because a deletion and a
  partial failure are indistinguishable from the rows — so the judgement is made in the
  safe direction);
- **any single field** below 50% of its card count. The 111/128 case clears the aggregate
  ratio at 87% and is still a silent gutting, so the aggregate alone is too coarse.

A field **absent entirely** is safe and accepted: absence keeps the card value, and only
partial presence deletes. A legitimate single retirement (20 of 21) is still evolution.
Every rejection falls back to the card and logs at ERROR — unlike the empty case, which is
a known pre-seed state and logs nothing.

### Why the guard lives in `identity_source.py`

`IdentityRepository.identity()` has **no** try/except, unlike
`Neo4jRuleRepository.standing_rules()` which documents *"Returns [] on a graph outage
rather than raising."* Called naively from the prompt path, a Neo4j blip would be a 500 on
her turn. Every failure — flag off, no driver, outage, empty graph, a node naming an
undeclared `source_field`, a floor rejection — falls back to the card.

### Why a graph read inside an `lru_cache` is acceptable *today*

A cached prompt can serve stale graph state, which is exactly why the **rules** read was
kept out of the cached builder (`build_graph_rules_block` takes its rows as an argument).
Identity differs only because ADR-018 defers the write path — *"no write path from chat
exists"* — so nothing mutates these nodes mid-session.

That is a property of the current system, not a law.
`test_no_chat_time_identity_write_path_exists` asserts it mechanically and tells the next
person to move the read out to the route if it ever fails.

### One source wins for the whole prompt

`get_or_build_cv_summary` now accepts an already-resolved card. Without it the summary
paragraph described her from the card while every structured block described her from the
graph, and **nothing would have reported the disagreement**.

**Cost, stated rather than hidden:** the fingerprint is computed from whatever card
arrives, so an operator edit to a graph node moves the hash and regenerates that paragraph
through a temperature-0.9 LLM into a **tracked** file whose text is not reproducible. This
was triggered once during testing and recovered from git — which is precisely why ADR-016
tracks those files. It is acceptable only while no chat-time write path exists; if one is
added, this becomes a non-reproducible write on a user turn and must be revisited.

## Consequences

**`GRAPH_IDENTITY_SOURCE` defaults to false.** Enabling it is an explicit operator step.

**Seeding is an operator script with a refusal.** `scripts/utils/seed_identity.py` is
dry-run by default and **refuses a first apply on a card with uncommitted edits**, because
the first apply captures the `:Baseline` under `ON CREATE` and a dial A/B harness that died
mid-run leaves an experimental value on disk. Watched refusing on a card dirtied to
`warmth: 0.1`. gwen applied live: 128 nodes, baseline `2026-09-28T17:55:34Z`. A re-apply
reports `created 0, updated 128` and leaves the baseline timestamp unchanged — the
immutability holds in production, not only in tests.

**What this does NOT do, and what is recommended next.** It does not make the identity
block deterministic. The paragraph is still LLM-written. Research this session points at
replacing `_make_cv_summary` with a **deterministic template over graph nodes** as the
higher-value change — it removes an unaudited temp-0.9 generation from the identity path at
no latency or cache cost, and it is what makes constraint-wise faithfulness metrics
(APC, arXiv:2405.07726) possible at all, since those require identity as discrete
statements. **That changes prompt text and therefore needs the voice gate**, so it is a
separate decision and is not taken here.

**Per-turn node selection is not adopted and is not recommended on this hardware.** The
evidence is contested — a typed-graph persona memory reports large gains over summary
baselines (PGMem, arXiv:2608.01708) while a 7B-4bit local harness reports graph episodic
memory collapsing against flat dense retrieval (AgentMemBench, arXiv:2608.00009) — and the
latency argument is decisive against the naive form: the identity block is the **first**
thing in the system prompt, and llama.cpp reuses a strict longest-common-prefix, so a
per-turn-varying block at position 0 forces a full reprefill every turn. If identity ever
does become dynamic it belongs where `build_constraint_reminder` already sits, immediately
before the user's message.

**Also fixed here, found while reading the path.** `lru_cache` keys on call *shape*, so
`f(key)` and `f(key, include_examples=True)` are distinct entries — measured 0 hits, 2
misses. `startup.py` prewarmed the positional form while `routes/chat.py` calls the keyword
form, so the prewarm never warmed the hot path and every first turn per persona paid a full
build, which can include an LLM call. The prewarm now covers all three shapes and `maxsize`
goes 64 → 128 so warming cannot evict itself.
