---
title: Fact extraction is bounded by a cursor in the graph, and a session end is a trigger
status: Accepted
created: 2026-10-23
last_reviewed_on: 2026-10-23
review_in: 24 months
applies_to: nephilim
ai_summary: >
  Why operator-fact extraction is bounded by a high-water mark on :Episode rather
  than by a sliding window, and why a session ending is a trigger alongside the
  periodic gate. Read before touching the extraction gate, the flush, the cursor,
  or any "which messages have we already mined" question. Carries the measured
  reason (98.3% of real sessions never reach the interval), the tiling argument in
  full, the survey showing re-extract+dedup designs ship duplicate bugs while
  watermark designs do not, and the rule that a watermark must live in the store
  it authorises — which is why there is deliberately no SQLite fallback cursor.
---

# ADR-023: Fact extraction is bounded by a cursor in the graph, and a session end is a trigger

## Context

Operator facts are extracted from the transcript by a gate in
`_apply_post_turn_updates`: `user_turns % MEMORY_FACT_EXTRACTION_INTERVAL == 0`,
with the live interval at 10. [ADR-022](022-the-graph-is-consulted-on-every-turn-and-she-may-only-assert-what-it-returned.md)
made the graph the read path for those facts and the write path was fixed to
match, verified at 3/3 stored and 3/3 recalled in a fresh session.

That verification used a session padded past the interval. **It hid the defect.**

**Measured 2026-10-23 against the live store.** Of 292 real sessions with at
least one user turn, **287 (98.3%) never reach 10 user turns**. Median **1**,
mean **2.0**. Exactly five could ever have fired the gate, and two of those are
the same July Telegram conversation. A fresh 3-fact, 3-user-turn session stored
**0 of 3** in SQLite and **0 of 3** in the graph, while she engaged with all
three facts conversationally — so they were heard, and then discarded.

This also explains a finding previously filed as an extraction-quality problem:
the historical `memory_facts` rows that describe *the conversation* rather than
the operator. The gate fired so rarely that it caught arbitrary windows.

Two constraints shaped the options:

- **Lowering the interval is not available.** `OLLAMA_NUM_PARALLEL=1`, so a
  background extraction is *serialised* against her generation rather than
  overlapped. Extraction costs a measured median **5.54 s**. Halving the
  interval doubles GPU contention with her replies.
- **There was no end-of-conversation signal.** The Telegram gateway has no
  `/new`, no `/end` and no idle timeout; the web UI has none either. The only
  explicit "this is over" events are `/reset`
  (`DELETE /sessions/{id}/messages`) and `DELETE /sessions/{id}`.

## Decision

**Fact extraction is bounded by a persisted high-water mark, and a session
ending is a trigger alongside the periodic gate.**

1. **The cursor lives on `:Episode` in Neo4j** — `fact_cursor_at` plus
   `fact_cursor_message_id`, written by `EpisodeRepository.advance_fact_cursor`.
   This gives `:Episode` its first live producer; until now only
   `scripts/utils/backfill_episodes.py` ever wrote one.
2. **The window is closed at both ends** (`>=`, not `>`).
3. **The cursor advances only after a completed extraction**, including one that
   wrote zero facts.
4. **Triggers:** the periodic gate (unchanged, still window-based until
   `MEMORY_FACT_CURSOR_FOR_GATE` is taken up), `/reset`, `DELETE /sessions/{id}`,
   and an idle sweeper keyed on `chat_sessions.updated_at`.
5. **There is deliberately no SQLite fallback cursor.**
6. **There is deliberately no flush at shutdown.**

### Why a cursor and not a sliding window

Surveyed 2026-10-23. The field splits cleanly, and the split predicts the bugs:

| Design | Systems | Duplicate-data bugs reported |
|---|---|---|
| Re-extract + post-hoc dedup | Mem0, Graphiti, LangMem | **Yes, all three.** Mem0 #6515 (TOCTOU: the dedup snapshot is stale by the time the hash check runs, so concurrent writers both insert), Graphiti #1872 (duplicate edges for one fact), #1734 (identically-named nodes unmerged — candidate retrieval is embedding-similarity-based and misses exact string matches below the cosine threshold) |
| True watermark | Cognee (`pipeline_status`, `AUTO_IMPROVE_WATERMARK`), Letta sleeptime (`Group.last_processed_message_id`) | **None found** |

⚠️ **And nobody upstream has shipped the fix for our failure mode.** Cognee PR
#5352 described it almost verbatim — *"those turns are written to the session but
never bridged… the debounce has no timer, so they wait for a next call that may
never come"* — and was **closed unmerged**. `idle_consolidation.py` does not
exist on their `main`. There was nothing to borrow, which is why the tiling
argument below is written out in full in `services/fact_window.py` rather than
cited.

### The tiling argument

Let `c₀` be the epoch (no cursor) and `cᵢ` the timestamp written after the i-th
**successfully completed** extraction. Window `Wᵢ = {m : cᵢ₋₁ ≤ ts(m) ≤ cᵢ}`.

- **No gap.** Consecutive windows are closed intervals sharing an endpoint, so
  their union is `{m : ts(m) ≤ cₙ}`. Every message at or below the cursor has
  been inside at least one window, because `cᵢ` is only ever set to the
  timestamp of a message that was *in* `Wᵢ`. Anything above `cₙ` is in the next
  window.
- **No loss on failure.** `cᵢ` is written only after the handler returns without
  raising. A full queue (`enqueue()` returns `False`), a process death with a
  job queued, a raising extractor, or an unavailable graph repo all leave the
  cursor at `cᵢ₋₁`. The failure mode is **repeated work, never a hole.**
- **Bounded duplication.** Overlap is exactly the messages sharing timestamp
  `cᵢ` — one bubble group — and the writer's `_norm` exact-containment dedup
  drops facts already held.
- **Concurrent triggers cannot double-write.** The worker is a single daemon
  thread, so overlapping jobs run sequentially, and the handler re-reads the
  stored cursor at **execution** time and skips a job already covered.

### Why the window is closed at both ends

`BaseRepository._now()` is `isoformat(timespec="seconds")` — **second
precision** — and she replies in multi-message bubbles, while
`get_messages_by_session` orders by `timestamp ASC` with no secondary key. Several
messages sharing one second is routine. A strict `>` cursor drops a same-second
sibling, silently and unrepeatably. Verified: switching to `>` turns three tests
red.

### Why the cursor must live in the graph

**A high-water mark must live in the same store as the write it authorises, or
it can advance past a window that was never persisted.** The facts' system of
record is the graph, so the cursor belongs there.

A SQLite cursor with a graph fallback is strictly worse and feels safer: if
Neo4j is down the fact write does not happen, so the cursor would advance over
an unwritten window and convert a transient outage into **permanent loss**.

⚠️ This is the **opposite** of the read path. `_operator_facts` falls back to
SQLite because a stale answer beats a failed turn. Read paths and write paths
want opposite fallbacks, and the word "fallback" hides that.

### Why the cursor advances on a factless window

A window containing no facts has still been looked at. Refusing to mark it done
means every quiet session is re-extracted on every trigger, forever, and the
boundary never moves. The cursor records *what has been examined*, not *what was
found*.

### Why there is no flush at shutdown

`enqueue()` returns `False` once `stop()` has been called, so a shutdown flush
would have to precede `worker.stop()` — but `stop()` joins with a 10 s timeout
while one extraction costs a median 5.54 s. A shutdown flush of more than one
session enqueues work the join abandons: it *looks* like coverage and is a loss
machine. It is also unnecessary, because the cursor is persisted — an unflushed
session is picked up by the first sweep after restart. This is a dividend of
choosing a cursor over window+dedup, and it leaves the lifespan ordering pin
untouched.

### Why the sweeper is the load-bearing trigger, and why it ships off

`/reset` and `DELETE` only cover sessions the user explicitly ends. At a median
of **one** user turn, the dominant way a conversation ends here is **silence**,
so without a sweep the flush reaches a minority of the 287 affected sessions.

It nonetheless ships with `MEMORY_FLUSH_SWEEP_SECONDS=0`. 323 historical
sessions are already idle, so a sweeper running before
`scripts/utils/seed_fact_cursors.py` has set each session's cursor would
re-extract months of transcript at 5.54 s each while contending with her
replies. **Seed, then flip, then restart** — ADR-006 recorded the cost of
flipping a flag before the deploy.

The sweeper is a daemon thread rather than a job on the existing APScheduler,
which is gated on Jupiter: hosting fact flushing there would couple it to an
unrelated feature's lifecycle.

## Status

**Accepted** 2026-10-23, shipped in v0.9.2. The periodic gate is unchanged;
`MEMORY_FACT_CURSOR_FOR_GATE` (moving the gate onto the cursor too) is **not
built** and remains an open roadmap item.

## Consequences

**Good.**
- A fact stated in a short session reaches both stores. 98.3% of real sessions
  were previously unable to extract anything.
- `:Episode` acquires a live producer and a purpose beyond the backfill, and
  `recent_episodes` now projects `last_touched` and `fact_cursor_at`, so the
  flush is verifiable in production rather than only in tests.
- Zero new labels or relationships: C17 unchanged at 14 labels / 11 rels, and
  none of the three "listed, not globbed" registries needed an entry.
- A found-in-passing defect is fixed: `if repo is None: return 0` sat above the
  SQLite profile update, so a Neo4j outage lost **both** stores and
  `_operator_facts`' SQLite fallback had nothing to fall back to.
- Every flush outcome is a named status surfaced in the route response.

**Bad / accepted.**
- Correctness now depends on Neo4j being reachable for the flush to make
  progress. The degradation is re-coverage rather than loss, but a long outage
  means a long unextracted backlog.
- The boundary second is re-extracted, costing one LLM call's overlap per
  trigger.
- Paraphrase is still not deduped (dedup is exact by design), and the flush
  **increases** the rate at which near-duplicates and contradictions accumulate
  because it writes facts from sessions that previously wrote none. ⚠️ The store
  already holds a live contradiction — "The user has a dog named Tulliver" and
  "The user's dog is called Wickham", both current, both injected every turn.
  ADR-021's embedding work owns dedup; the contradiction is a roadmap item.
- `cleanup_orphaned_sessions` is deliberately **not** wired: it is a bulk delete
  at startup with no per-session loop, and enqueuing hundreds of jobs into a
  64-deep queue would return `False` for most of them — coverage that looks like
  coverage and is not.
- The sweeper does a full scan of `chat_sessions` (292 rows) per pass. No index
  on `updated_at`; the row count is recorded in the code so a future reader
  knows when that stops being true.

## Alternatives considered

- **Lower `MEMORY_FACT_EXTRACTION_INTERVAL`.** Rejected on a local
  measurement: `OLLAMA_NUM_PARALLEL=1` serialises extraction against
  generation, so this trades lost facts for slower replies and still loses a
  one-turn session.
- **Reuse the sliding window for the flush and let dedup absorb overlap.** The
  original plan. Rejected on the survey above: it is the design family with
  shipped duplicate bugs, and this store already has a contradiction problem
  that more re-extraction makes worse.
- **A SQLite cursor table.** Rejected: it authorises a graph write it cannot
  observe. See above.
- **An end-of-conversation signal from the Telegram gateway.** Not rejected on
  merit — it does not exist, and adding one is a gateway change that would still
  not cover the web UI or an abandoned conversation. The idle sweep covers all
  three.
- **A flush at shutdown.** Rejected on the join arithmetic above.

## Related

- [ADR-022](022-the-graph-is-consulted-on-every-turn-and-she-may-only-assert-what-it-returned.md) — the read path this write path feeds.
- [ADR-021](021-agentic-memory-is-one-graph-with-scoped-truth.md) — the schema, and the owner of dedup and contradiction handling.
- [ADR-018](018-the-identity-graph-is-built-from-the-card-and-evolves-in-its-edges.md) — `:Episode` and `CONTINUES`.
- `services/fact_window.py` — the tiling argument, in code, next to the functions it constrains.
- [LESSONS_LEARNED](../LESSONS_LEARNED.md) 2026-10-23 — the blind guard, and the silently-dead sweeper.
