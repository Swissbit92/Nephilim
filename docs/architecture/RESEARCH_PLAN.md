---
title: "Companion web research: architecture and plan"
status: active
created: 2026-11-01
last_reviewed_on: 2026-11-01
review_in: 3 months
applies_to: nephilim
ai_summary: "Open when working on web search, fetching, research answers or their evals. Holds the research architecture, search routing, phase status and release gates."
---

# Companion web research: architecture and plan

The product is a companion that can look things up when it is useful, not a research
assistant with personas. Each persona keeps their own voice and permissions, answers are
checked against their sources, and outside content cannot rewrite what she knows about the
operator. The cost requirement is no new spending, with Brave usage measured.

## Three rules

- Gemma decides how to say something, never whether evidence gets retrieved.
- Web text is evidence, never instructions.
- A claim from outside never becomes a fact about the operator on its own.

## Layers

| Layer | Job | Decides |
|---|---|---|
| Channels | Telegram gateway, React UI | Nothing |
| Coordinator | Owns the turn | Whether research runs, once per turn |
| Policy | Server-side persona permissions | What each persona may use |
| Research | Finds and prepares evidence, deterministically | What evidence is good enough |
| Persona + memory | Gemma voices the answer, the graph remembers | How it is said, what may be stored |

## Research turn

```
Message
  ▼
Router proposes ────┐
Model proposes ─────┤
Freshness proposes ─┤
                 ▼
Coordinator decides once ──no──► normal chat
                 ▼
Is this persona allowed? ──no──► in-character deflection
                 ▼
ResearchService (one path, one run)
  Search ──► fetch if needed ──► sanitize ──► best passages
    ▲                                            │
    └───── one more search if evidence thin ◄────┤
                                                 ▼
Evidence package: sources, passages, dates
                 ▼
Gemma, no tools, in persona voice (short by default)
                 ▼
Figure check (regenerate once, then drop) ──► citations for used sources ──► reply + trace
```

`_research_turn` in `routes/chat.py` is the only place this runs. It never falls through to
the legacy path, because that would search a second time for one turn.

## Search routing

| Search | Primary | Fallback | If that fails |
|---|---|---|---|
| Text or news, ordinary persona | Brave | SearXNG | No results |
| Image or video, any persona | SearXNG | None | No results |
| Any search by an `nsfw` persona | SearXNG | Never Brave | No results |

SearXNG forwards queries to its engines from this machine's IP with no account attached, so
it is not local.

## Memory boundary

Research answers are stored as assistant turns, and both fact extractors redact assistant
content, so a page cannot become an operator fact by this route. Audited 2026-11-01 and held
by `test_research_memory_boundary.py`, which plants a claim and inspects extractor input.
Summaries and RAG do persist assistant text inside a session's context. Text the operator
pastes counts as their own statement. "Remember this researched fact, with its source" is a
later feature.

## Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | SearXNG, fail-closed routing, telemetry, alert, A/B | Done, v0.10.1 |
| 1a | Frozen fixtures and v0.10.1 baseline | Done |
| 0.5 | Fallback diagnosis, trust rule, single decision, memory audit | Done |
| 1b | Adversarial and persona checks, gate calibration | Done |
| 2 | Research service and blind A/B | Done, v0.11.0, flag on |
| 3 | Gwen uses the shared service | Conditional, undecided |
| Deferred | Deep research, including Codex | No work committed |

## Results, 2026-11-01

Frozen set: 50 questions, 200 pages, captured at v0.10.1 before any change. Full page text
stays in `~/nephilim-eval/` because this repo is public; `scripts/research/research_eval/manifest.json`
holds a hash per question. Judges were blind, a different model family from Gemma, and saw
each pair in both orderings. A pair that flipped with order counts as a tie.

| Run | Old pipeline | New pipeline | Ties | p |
|---|---|---|---|---|
| First candidate, judges missed 18% of its passages | 18 | 10 | 22 | 0.18 |
| Corrected candidate and evidence | 8 | 15 | 27 | 0.21 |

Read this as parity, not a win. The second run came after seeing the first result, and changed
both the candidate (8 passages per source, not 4) and the judges' evidence. The gate, set
before scoring it, was no worse than parity and no drop in support.

| Measure | Old | New |
|---|---|---|
| Answers fully supported by evidence | 74% | 79% |
| Malicious-page leaks, 12 attacks on the first candidate | 2 | 0 |
| Eeva voice, blind on 12 live questions | 6 | 5, 1 tie |
| Median live reply | 9.6s | 10.7s |
| Adaptive vs always fetching | 6 | 5, 39 ties |

## Release gates

Calibrated from the baseline. The 95% candidate for fully supported answers is dropped: this
model measured 74% at v0.10.1 and 79% at v0.11.0, so the release gate is no drop below the 74%
baseline. A later release that holds a higher figure may raise it, in this table, with the run
that shows it.

| Metric | Gate |
|---|---|
| Research routing | All deterministic route tests pass |
| Research runs per turn | Never more than 1 |
| Fully supported answers | No drop from 74% |
| Malicious pages | 0 critical violations |
| Provider policy | 0 unauthorized backend calls |
| Memory | 0 web claims reach an extractor |
| Answer quality, persona voice | Parity or better, blind |
| Cost | No new spending, Brave queries reported |

On 50 questions these catch large regressions and cannot prove small gains.

## Open

- Questions all three proposers miss, such as comparisons with no freshness word.
- Brave billing is unconfirmed. About 150 Brave queries went to evals on 2026-10-31 and 11-01.
- Gwen's web research and the deferred deep-research tier are undecided.
