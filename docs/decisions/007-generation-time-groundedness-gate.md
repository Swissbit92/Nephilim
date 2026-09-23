---
title: Generation-time groundedness gate
status: Proposed
created: 2026-07-04
last_reviewed_on: 2026-08-23
review_in: 12 months
applies_to: nephilim
---

# ADR-007: Generation-time groundedness gate

## Status

**Proposed — core mechanism built, default OFF, eval-first validation before enabling.** Built via `/crucible:develop` FULL workflow on `feature/groundedness-and-routing-fixes` following the 2026-07-04 Telegram incident (session `dcc3693d-0aff-41ef-bd92-d943d008cb3e`): E.E.V.A. fabricated a specific FIFA World Cup 2026 match result (score, date, opponent) with zero grounding, then agreed with and elaborated on her own fabrication when the user "confirmed" it. Backend suite 1671→1690 (19 new tests), 0 regressions.

## Context

The coordinator already has two web-search anti-hallucination guards (`SearchSettings`, ADR-adjacent but never its own ADR): `query_resolution_enabled` (resolves deictic follow-ups before hitting Brave) and `relevance_gate_enabled` (abstains on off-topic search results). Both are correctly designed — and both are architecturally unreachable in the failure mode this incident exposed.

A full code trace (Explore agent, verified line-by-line against the running code) established: **once the intent router (`classify_query_intent`) decides no tool is needed (`QueryIntent.NEEDS_NEITHER`), `routes/chat.py`'s `if not tools:` branch calls a bare `_complete_or_503()` LLM completion with zero involvement of `ToolCallingService`.** All three existing anti-hallucination guards — "no results → I don't know", "LLM skipped an offered tool → I don't know", and the relevance gate — live entirely inside `tool_calling_service.py::complete_with_tools()`, which this branch never calls.

The incident's specific trigger: `ForceSearchService.FORCE_PATTERNS` has a `"latest"` entry but not `"last"`, and the semantic router's `web_search` example set (`tools/semantic_router.py`) is 100% crypto/market-news phrased with zero sports/current-events coverage. "What was their last match?" therefore scored under the routing threshold on both the force-search keyword layer and the semantic layer, fell through to `NEEDS_NEITHER`, and the bare completion confabulated freely.

This is a **routing-miss** failure, not a **bad-search-result** failure — the existing guards assume routing got it right and search was at least attempted. Nothing in the codebase checks the *content of a response that never tried to search* for whether it's making claims that would need grounding. Web research on check-worthiness / claim-detection literature (AFaCTA, ProvenanceGuard) confirms this is a distinct, well-studied gap: a **post-hoc, generation-time factual-claim classifier**, decoupled from whatever the upstream router decided, is the standard mitigation — because the router is the component that failed, so nothing downstream of *only* the router can catch it.

## Decision

Add a `GroundednessGateService` that runs after a draft response is generated with no tool call this turn (both `routes/chat.py`'s `if not tools:` branch and the `brave_tools`-absent `else:` fallback branch — both are bare completions). It asks the same loaded persona LLM a second, cheap yes/no question: does the draft assert a specific, falsifiable, temporally-scoped real-world claim (score/date/outcome/statistic) with nothing backing it this turn? If yes, the draft is replaced with a fixed, voice-neutral honest-abstention string ("I don't actually have grounded, up-to-date information on that... Want me to search for it?") instead of being returned as-is.

Scoping, to guard the named top risk (false-abstention on legitimate answers):
- The classifier prompt explicitly excludes in-character persona/worldbuilding lore, general timeless knowledge, opinions, and already-grounded (tool-backed) turns.
- Fail-open on any classifier error or when `GROUNDEDNESS_GATE_ENABLED` is off (default) — byte-identical to legacy in both cases, same principle as the existing `SearchRelevanceService`.
- `tests/evaluation/groundedness_eval_set.json` is the paired should-abstain / should-NOT-abstain corpus this must be validated against before any production enablement, mirroring the false-abstention-rate discipline `tune_routing_threshold.py` already established for the routing threshold.

**Deliberately deferred, not silently dropped:** the plan's original design also called for tagging a flagged turn as `unverified` in conversation-state metadata, so a later turn where the user appears to "confirm" that claim re-triggers the gate instead of treating the confirmation as corroborating evidence (the exact "they were eliminated" reinforcement-loop shape from the incident). This requires a conversation-state schema change and was judged out of scope for this pass. The core gate still closes the loop's actual root cause in the common case: if the gate is enabled, the fabrication that would have needed reinforcing is never created in the first place. The residual risk — a pre-existing unflagged claim from before the gate was enabled, or a classifier false-negative, being reinforced on a later turn — is real but narrower, and is tracked in the roadmap (`GROUNDEDNESS_REINFORCEMENT_CHECK_ENABLED` flag reserved, unimplemented) rather than solved here.

Also folded into the same failure class (belt-and-suspenders, better routing reduces how often the gate needs to fire at all): `FORCE_PATTERNS` and the semantic router's `web_search` example set need sports/temporal/outcome coverage extended (tracked as a follow-up milestone in this same fix chain, not yet implemented as of this ADR's authoring).

## Consequences

- **Positive:** closes the specific incident's root cause (confident fabrication with zero grounding attempt); architecturally correct placement (decoupled from the router, since the router is what failed); reuses the existing LLM with no new model/infra, same latency shape as `query_resolution_service`'s single extra rewrite call, and only pays that cost on the no-tools/fallback paths, not every turn; fail-open design means it can only ever add abstentions, never regress an already-correct answer path.
- **Negative / risks:**
  - **False-abstention** is the primary risk — the gate refusing to answer something E.E.V.A. legitimately knows or re-litigating settled lore. *Premortem: this could fail if the classifier prompt is too aggressive and starts flagging ordinary persona chat.* Mitigated by explicit prompt scoping and the `groundedness_eval_set.json` should-pass corpus (lore, general knowledge, already-grounded turns); must clear a false-abstention bar before any production enablement, not ship on vibes.
  - **Extra latency** on every no-tools/fallback turn once enabled (one additional LLM completion). Acceptable for a single/dual-user local deployment at current turn volume; would need revisiting if load ever increased materially.
  - **Reinforcement-loop residual risk** (see Decision) — a known, tracked, unimplemented gap, not a claimed-complete fix.
  - **Doesn't fix routing itself** — a query can still fall through to `NEEDS_NEITHER` when it shouldn't; the gate is a safety net catching what routing misses, not a routing fix. The FORCE_PATTERNS/semantic-router example-set extension (tracked separately) is the complementary "fix the miss" side of this same incident.

### Acceptance gate & rollback (inherits ADR-005/006's discipline)

1. **Freeze baseline first** — done: `tests/evaluation/baseline_manifest.json`, frozen 2026-07-04 before any code change in this fix chain.
2. **Validate against `groundedness_eval_set.json`** before flipping `GROUNDEDNESS_GATE_ENABLED` in production — both the should-abstain recall AND the should-pass (false-abstention) precision must clear an explicit bar (to be set when the eval harness is built), matching how `tune_routing_threshold.py` pins the costly-class precision first.
3. **Rollback is instant** — default-OFF flag; revert = flip off + reload. No legacy-path removal, ever, for this mechanism (it's additive-only by construction).
4. **No global default flip until the eval clears** — same rule as every other flag in this codebase.


---

## Amendment (2026-08-23) — the exclusion missed the scene the persona is narrating

**Two things this ADR asserts are no longer true of the running system, and one is a divergence rather than a change.**

**1. The gate is live in production, not default OFF.** `.env:159` sets `GROUNDEDNESS_GATE_ENABLED=true` (the variable is absent from `.env.example` entirely). The acceptance gate above — "no global default flip until the eval clears" — does not appear to have been recorded as cleared anywhere in this document. Flagging the divergence rather than silently reconciling it: either the eval ran and this ADR was never updated, or the flag was flipped ahead of its own rule. Worth establishing which before the next change to this mechanism.

**2. The predicted failure happened, in the form the premortem named.** *"This could fail if the classifier prompt is too aggressive and starts flagging ordinary persona chat."* Observed 2026-08-23 on the gwen persona: the gate fired mid-scene and replaced an in-character reply with `_ABSTAIN_MESSAGE`. The identical user message succeeded on retry, because the draft is sampled at the persona's temperature (0.9) while the classifier runs at 0.0 — so the classifier judged different input the second time, and the failure is intermittent rather than deterministic.

The mechanism: the exclusion list covered *in-character fictional/persona lore or worldbuilding* — a persona's **backstory** and the world's fiction. It did not cover the **scene the persona is narrating right now**. First-person in-scene narration ("my hands are trembling") is specific, present-tense and falsifiable-*sounding*, which is precisely the shape both flag clauses describe; and `_FLAG_LIVE_STATE`'s "the USER'S OWN CURRENT STATE presented as known" read as covering a character describing itself.

**Changes:**
- `_NO_FLAG_ROLEPLAY_SCENE` — an explicit exclusion for in-scene narration: the character's own body, posture, sensations, actions and the surrounding fiction. This is creative writing being composed, never checkable by search; present tense and physical detail are how fiction is written, not evidence of a factual assertion.
- `_FLAG_LIVE_STATE` scoped to **real accounts and money** ("REAL ACCOUNT OR PORTFOLIO STATE"), which is what the 2026-08-12 incident actually added it for.

**Guards against over-correction** — narrowing an abstention gate is the failure mode this file's own history warns about, in both directions:
- The 2026-08-12 catch case is regression-pinned (`test_groundedness_roleplay.py::test_real_live_state_claim_still_flagged`).
- The `live_state=False` A/B revert path is pinned and still produces a prompt without the clause.
- The backstory exclusion is **kept**, not replaced.

**A testing gap this exposed, which matters more than the fix.** `test_routes_chat.py` pins `groundedness.gate_enabled=False` in its shared `_make_settings()` fixture. Every gate path in that suite is therefore dark, which is how a gate defect that was live in production coexisted with a fully green suite. New gate tests run with the flag explicitly ON. The general lesson: a fixture that pins a production flag to its non-production value converts an entire suite into evidence about a configuration nobody runs.
