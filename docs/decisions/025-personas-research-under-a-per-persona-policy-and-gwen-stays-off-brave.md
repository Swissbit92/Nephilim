---
title: Personas research under a per-persona policy, and Gwen stays off Brave
status: Accepted
created: 2026-11-01
last_reviewed_on: 2026-11-01
review_in: 12 months
applies_to: nephilim
ai_summary: "Open before granting a persona web research, changing who may reach Brave, or changing how Gwen's queries are built. Records why she uses SearXNG only and how her queries are kept private."
---

# ADR-025: Personas research under a per-persona policy, and Gwen stays off Brave

## Context

Gwen had no general web research, and full Brave was the simplest way to give it to her. Brave's
terms, updated 1 Sep 2026, bar use of the API "in connection with any ... obscene, abusive, or
otherwise offensive content", and a suspension would also take out Eeva's search. The operator chose
not to ask Brave. Clauses 3(b)(i) and (xiii) also bar storing results and using them to evaluate or
benchmark AI services.

## Decision

Each persona has one provider: Eeva uses Brave, Gwen uses SearXNG, with no classifier per search.
`research_policy.py` decides who may research. Her executor has no Brave client at all, so no policy
value can route her there.

For Gwen only the router, freshness and follow-up proposers count, because a query the model writes
is composed under her persona and the scene. Her rewriter sees her last two user turns and nothing
else. That restricted input is the privacy boundary, with a denylist of remembered names on top. A
word filter on the rewrite's output was tried and removed: it rejected "place" and "german".

Earlier turns stay out of synthesis. Given them she answered the previous question instead.

Web text never writes to identity, rules or `:Fact`. Research replies are labelled in summaries and
RAG, and a summary is built from messages, never from another summary, so a label cannot decay.

No evals run on Brave results. The fixtures that did were deleted on 2026-11-01.

## Status

Accepted. `RESEARCH_NSFW_ENABLED` defaults off.

## Consequences

- Her research is weaker than Eeva's: SearXNG scored 74% fully supported against Brave's 84% in G0.
- SearXNG's engines see her queries and this IP, and their retention is unknown.
- SearXNG scrapes other engines, which may breach their terms and can stop working.
- Words the operator types in the current message leave with that message.
- A persona can talk past a question. A reply that ignores the evidence is regenerated once, and one
  that still does keeps no sources. Weak answers that touch the evidence still get through.
- Draft-then-rewrite was tested and not adopted: no measurable gain for double the generation time.
