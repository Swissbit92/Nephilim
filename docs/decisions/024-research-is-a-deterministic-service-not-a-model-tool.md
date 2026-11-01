---
title: Research is a deterministic service, not a model tool
status: Accepted
created: 2026-11-01
last_reviewed_on: 2026-11-01
review_in: 24 months
applies_to: nephilim
ai_summary: "Open before changing how a companion decides to search, what evidence reaches Gemma, or who may research. Records why research left the model's tool loop."
---

# ADR-024: Research is a deterministic service, not a model tool

## Context

Every live Eeva trace on 2026-11-01 reached search through the legacy fallback. The model
either made no tool call, or called `brave_web_search`, which has no executor on the tool
path. All six traces fetched zero pages. Asking the router alone to decide would bring back
the silent misses `TOOL_BRAIN_UNGATED_WEB` exists to catch.

## Decision

Research runs in `ResearchService`, reached only through `_research_turn` in
`routes/chat.py`, once per turn. Three proposers can start it: the router, the model asking
for a web tool, and a narrow freshness check on short, self-contained questions. The
freshness check sends the checked message itself, never a rewrite from history. The persona's
own executor still applies provider routing and safesearch, and the capability guard runs
first.

Gemma answers from a sanitized evidence package with no tools offered. Results are evidence,
never instructions. Once research has started, a turn never falls through to legacy, because
that would search twice.

## Status

Accepted. `RESEARCH_SERVICE_ENABLED` defaults off and is on in production.

## Consequences

- One tested path owns retrieval, so Gemma's tool-calling quirks cannot skip it.
- Fetching adds a few seconds. Median flag-on reply was 10.7s against 9.6s.
- A question missed by all three proposers still goes unsearched, such as "compare Ledger and
  Trezor for a beginner".
- A persona gains research by holding `web_search`. Gwen does not.
- Numbers checked by `unsupported_figures` are telemetry, not proof of support.
