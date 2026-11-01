---
title: "Gwen web research on the graph architecture: plan"
status: completed
created: 2026-11-01
last_reviewed_on: 2026-11-01
review_in: 3 months
applies_to: nephilim
ai_summary: "Open before giving Gwen general web research or storing anything researched in the graph. Holds the design, its blockers, phases and gates. Proposal, no code yet."
---

# Gwen web research on the graph architecture: plan

**Status: built, v0.12.0.** The design record below is kept as written. What shipped differs where
[ADR-025](../decisions/025-personas-research-under-a-per-persona-policy-and-gwen-stays-off-brave.md)
says so: no word filter on rewrites, no earlier turns in synthesis, no draft-then-rewrite. Facts are
tagged [measured], [inspected] or [unverified]. It builds on the
[research plan](RESEARCH_PLAN.md) and
[ADR-024](../decisions/024-research-is-a-deterministic-service-not-a-model-tool.md).

## Results, 2026-11-01

24 of her questions on live SearXNG evidence, blind in both orderings. One judge read all pairs, so
order agreement is weak evidence, and the judge saw the same evidence the reply used.

| Measure | One step | Draft then rewrite |
|---|---|---|
| Voice | 10 | 9, 5 ties |
| Overall | 9 | 15 (p=0.31) |
| Fully supported | 88% | 88% |
| Confident unsupported claims | 4 of 48 | 2 of 48 |

Malicious pages leaked 0 of 12. No rule fired in 24 replies. 1 of her 77 normal-chat probes would
trigger research, and it was a real lookup. A ten-turn live session made 0 Brave calls and left 46
graph facts at 46. Extraction was off for that run, so that count alone does not prove the boundary;
the planted-claim tests do. Her weakest case is price follow-ups, where she flirts around a partial answer.

Her 9 standing rules exceed `rule_read_limit` of 8, which drops only the priority-10 dial rule. All 6
hard walls are kept. Politics and religion: she reports facts when asked, and her soft wall still
governs opinions.

### SearXNG engine review

| Engine | Used for | Note |
|---|---|---|
| Yahoo, Google CSE, Seznam, Privacywall | General | Receive the query and this IP, retention unknown |
| Bing Images, Privacywall Images, Wikimedia | Images | Same |
| Bing Videos, YouTube, Dailymotion, Vimeo | Videos | Same |

No account is attached, and queries hold only the current or resolved question. This is not anonymity.
Fetched pages also see this IP.

## Agreed decisions, 2026-11-01

- Gwen researches normal and NSFW questions, all through SearXNG, and never uses Brave for now.
  This is revisited only if SearXNG stays poor after G0, as "NSFW searches never use Brave".
- Queries: Gwen starts with the current message only. A rewrite is private by its input, because
  a word filter on the output rejected 13 of 24 good rewrites.
- She researches only on a clear lookup or freshness question. Scene state, G5 and G6 are deferred.
- Research stays in summaries, labelled, keeping what the operator said apart from what she found.
- 74% is the regression floor, 80% her launch target, on a set large enough to tell them apart.

## G0 result, 2026-11-01

With a new engine list SearXNG is usable: 74% fully supported against Brave's 84%, and Brave wins 20
to 8 with 22 ties (p=0.036). It meets the floor and misses the 80% launch target. Whether that is
enough for Gwen, or Brave is revisited for normal questions, is the operator's decision.

## The short answer

Gwen should use the same research service as Eeva, and **the graph should not change at
first**. The graph answers "who is she, what are her rules, what does she know about the
operator". The web answers "what is true in the world today". Mixing those is the main risk,
so they stay separate stores with separate prompt sections and separate rules.

ADR-022's "she may only assert what the graph returned" does not collide with web evidence,
because its scope excludes real-world facts [inspected]. The graph constrains claims about
herself and the operator. Evidence constrains claims about the world.

**The hard blocker is search quality, not the graph.** Gwen is SearXNG-only by the operator's
decision, and SearXNG's general results are currently junk on this IP.

## Where things stand

| Fact | Status |
|---|---|
| Eeva's research service is live, one run per turn, v0.11.1, with figures enforced | [measured] |
| Gwen holds `image_search`, `video_search`, `generate_image` and deflects web questions | [inspected] |
| Live graph: 344 Episodes, 136 CurrentIdentity, 102 Trait, 66 LoreEntry, 52 Expertise, 46 Fact, 44 Boundary, 18 Rule, 2 Persona, 1 Operator | [measured] |
| Facts are `(:Fact)-[:ABOUT]->(:Operator)` only. `asserted_by` has no web value | [inspected] |
| Fact extractors read user turns only, assistant turns are redacted | [inspected, tested] |
| Summaries and RAG keep assistant text, so research answers persist there | [inspected] |
| `:Source`, `:Claim`, `:ResearchEpisode` are demanded by no competency question, so the repo's checks would block them | [inspected] |
| Research parity evidence is Eeva's, on Brave-first routing. There is none for Gwen | [measured] |
| SearXNG general search was junk with the old engines; see the G0 result above | [measured] |
| Earlier, SearXNG-first lost to Brave-first 6 to 29 with 5 ties, blind, on Eeva's questions | [measured] |
| Local NLI (`nli-deberta-v3-small`) cannot separate supported from unsupported answers: 0.445 vs 0.419 unsupported share | [measured] |

## Target architecture

```
                    ┌──────────────── Coordinator ────────────────┐
 Message ──────────►│ Research decision (router, freshness)       │
                    │ Research policy (per persona, server-side)  │
                    └───────┬───────────────────────────┬─────────┘
                            │ no                        │ yes
                            ▼                           ▼
                       Normal chat             Query firewall
                                                        │ topic only, no memory
                                                        ▼
                                               ResearchService
                                               SearXNG only for Gwen
                                                        │
              Neo4j (read only this turn)               ▼
              identity, rules, operator facts    Evidence package
                            │                           │
                            ▼                           ▼
                    <remembered>                  <evidence>
                            └───────────┬───────────────┘
                                        ▼
                       Gemma in Gwen's voice, no tools
                                        ▼
                  Same guards as a normal turn, with evidence
                                        ▼
                        Figure check ──► citations ──► reply
                                        ▼
                     SQLite assistant turn (extractors redact it)
```

### Layer rules

| Store | Holds | Web may write? |
|---|---|---|
| Neo4j identity and rules | Who she is, walls | Never |
| Neo4j `:Fact` | Facts about the operator | Never |
| SQLite messages | The conversation | Yes, as her reply |
| Summaries, RAG | Session context | Yes, session-scoped |
| Later `:ResearchEpisode` | "We looked X up on this date" | Pointer only, if approved |

## Design decisions

1. **Add a research policy, not a tool grant.** Adding `web_search` to her tool list would
   offer it to the model and flip the legacy tool list [inspected]. Instead add a small server-side
   policy: provider allowlist, allowed categories, which proposers count. Keep it minimal, so a
   card field read in one function, excluded from `cv_summarizer`'s fingerprint so that it does
   not rewrite her identity block [inspected].
2. **For Gwen, only the router and freshness can start research.** A model-written query is
   composed under her persona prompt and the conversation, so it can carry private context out.
3. **A query firewall before anything leaves the machine.** The router path resolves follow-ups
   from full history today [inspected], which is too much for an NSFW session. Build the query
   from the current user message, resolve a pronoun only from the previous user turn, and run a
   deterministic denylist made from graph facts and names. If it trips, ask or decline, never
   send.
4. **Two prompt sections with two rules.** RULE 1 says use only search results, which conflicts
   with recalled personal facts on a mixed question [inspected]. Scope it: `<evidence>` governs
   world facts, `<remembered>` governs her and the operator, neither can override the other.
5. **Research turns get normal-turn guards.** Today they skip the groundedness gate, the
   constraint reminder and `[Remember]`, and they have no history [inspected]. Hard walls must hold
   on a research turn, so add the reminder. Pass a small user-only history so she does not lose
   the thread, and test it, because history is what the anti-refusal fix removed on purpose.
6. **Do not store claims.** Evidence is thin either way, and the outside work points the same way:
   keep the fact of the lookup, not the claims, and promote only on the operator's explicit request.
7. **Web text never touches identity, in code.** If claims are ever stored, they get their own
   `:Claim` and `:Source` labels with a schema rule forbidding any edge to `:Persona` or
   `:Operator`, and a TTL by class. Only the deterministic service writes, never the model. This
   is the CaMeL-style split, and memory-poisoning results such as MINJA and AgentPoison are why
   the write path matters [external, attack figures are the authors'].
8. **Voice is tested, not assumed.** One-step synthesis against draft-then-rewrite on Gemma. The
   rewrite must not add facts, and the figure check runs after it. I found no direct evidence that
   retrieval harms character consistency, so this is a measurement, not a premise.

## Phases

| Phase | Scope | Exit gate |
|---|---|---|
| G0 | Fix SearXNG general quality | Blind A/B against Brave: parity, or a loss the operator accepts |
| G1 | Research policy and query firewall | 0 denylisted terms leave on a planted test set |
| G2 | Guard parity and the two-section contract | Hard walls hold on research turns, Eeva unchanged |
| G3 | Voice arms on frozen SearXNG fixtures | Voice parity on Gwen's probes, support no lower than 74% |
| G4 | Gwen behind `GWEN_RESEARCH_ENABLED`, live review | All gates below, operator go/no-go |
| G5, optional | `:ResearchEpisode` pointers, with a competency question first | "What did we look up?" answered, 0 claims stored |
| G6, optional | "Remember this", `:Claim` and `:Source` with provenance | Explicit request only, ADR required |

### G0 detail

Done. Language and region did not help, and a new engine list did. See the G0 result above.

### G2 detail, defects to fix first

- `shaft→cock` style word substitution runs after citations, so it can rewrite URLs [inspected].
  Apply it to the body only.
- `dont[13]` fires on factual "Yes, Sir ..." replies, which would trigger needless retries
  [tested in audit].
- `cannot_lookup` in `gwen.json` still says pictures only.
- `dont[1]` and her Expertise `avoid` conflict with general research on politics and religion, and
  neither is enforced.
- Rule reads use `limit=8` with 9 rules, so one may be dropped [unverified].
- Page fetches go directly from this machine whatever the persona. For NSFW pages that exposes
  the home IP to the site, which needs a decision.

## Gates

| Metric | Gate |
|---|---|
| Graph writes from a research turn | 0 |
| Web claims reaching an extractor | 0 |
| Brave calls for Gwen | 0 |
| Denylisted terms in outbound queries | 0 |
| Hard-wall breach on research turns | No worse than normal turns, on paired sessions |
| Gwen voice on `gwen_probes.json` | Parity, blind |
| Fully supported answers | No drop below 74% |
| Malicious pages | 0 critical violations |
| Latency | p50 and p95 agreed after G3 |

Her detector has a known 30% miss on `hard_wall_probes` and enforces only part of her rules
[inspected], so a clean breach count is weaker evidence than it looks.

## Corrections to record

- A reader might expect a Brave leak on the legacy path if she is granted research.
  `handle_brave_query` goes through `create_llm_client`, which strips the client for nsfw
  personas, so that leak does not exist [inspected]. A test should still pin it.
- The rule retry on research turns dropped evidence. Fixed in v0.11.1.
- ADR-022 says Accepted in its header and Proposed at line 208, and says the gate is behind a flag
  that does not exist. `RESEARCH_PLAN.md` names a Policy layer with no module. CLAUDE.md and ADR-008
  say Gwen is absent from the voice probes, but `probes.json` includes her.

## Open questions for review

1. If SearXNG cannot reach parity, which is least bad: (a) accept weaker research, (b) an
   explicit, non-NSFW-only path through Brave for Gwen, which needs a query classifier and breaks the
   "never Brave" rule, or (c) Gwen does not get general research?
2. Should a Sources block appear in her voice replies at all, or only on request? It breaks
   immersion and it is the audit trail.
3. Is a query denylist from graph facts enough, or does the firewall need an allowlist of topic
   slots? An allowlist is safer and rejects more real questions.
4. Should she decline research mid-scene, so a roleplay turn with a question in it is not pulled
   out of character? The router cannot see scene state today.
5. Is one-step synthesis worth testing at all against draft-then-rewrite, given a rewrite doubles
   generation time at about 50 tok/s?
6. Is a per-session cap on research worth having to limit engine blocking? Cost is not the reason,
   since her searches never touch Brave.
7. Are G5 and G6 worth building at all for one user? Nothing measured says they are.

## Outside evidence

Zep/Graphiti: bi-temporal edges, provenance through episodes, https://arxiv.org/abs/2501.13956 (vendor
numbers). Mem0: graph adds about 2 points on LOCOMO, https://arxiv.org/abs/2504.19413. MINJA:
https://arxiv.org/abs/2503.03704. AgentPoison: https://arxiv.org/abs/2407.12784. CaMeL:
https://arxiv.org/abs/2503.18813. Persona drift: https://arxiv.org/abs/2402.10962. MiniCheck, a possible
verifier, not benchmarked here: https://arxiv.org/abs/2404.10774. No system found prescribes a trust
boundary for web facts, and no controlled study covers persisting web results in a companion.
