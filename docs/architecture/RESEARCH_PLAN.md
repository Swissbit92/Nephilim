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
Router proposes ─┐
Model proposes ──┤
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
Citations ──► reply + trace
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
| 1b | Adversarial and persona checks, gate calibration | See results below |
| 2 | Research service and blind A/B | See results below |
| 3 | Gwen uses the shared service | Conditional, undecided |
| Deferred | Deep research, including Codex | No work committed |
