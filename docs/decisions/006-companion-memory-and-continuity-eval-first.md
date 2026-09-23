---
title: Companion memory and continuity (eval-first)
status: Accepted
created: 2026-06-27
last_reviewed_on: 2026-08-23
review_in: 12 months
applies_to: nephilim
---

# ADR-006: Companion memory and continuity (eval-first)

## Status

> **⚠️ 2026-07-06 — MEMORY INJECTION REVERTED on the abliterated model. Both flags OFF on prod.** A full ADR-005 distinctiveness eval on the new daily driver (`huihui_ai/mistral-small-abliterated:24b`, live since 2026-07-05) showed **both** memory mechanisms degrade voice: distinctiveness **0.804 (both OFF) → 0.661 (facts only) → 0.625 (both ON)**; EEVA collapses 0.75→0.25 under either injection and recovers only with both off. The abliterated model itself is a voice *win* (0.804 vs Magidonia 0.732) — the regression was the injection, not the model. **The M5 gate (0.839, "match-or-beat") does NOT hold on abliterated** — it was measured on Magidonia before the model switch, so the M1 per-persona framing was never validated on the model now in prod. `MEMORY_CONTEXT_INJECT=false` + `MEMORY_FACTS_ENABLED=false` on prod. Eval data: `baselines/baseline_abliterated_20260706_*.json`.

> **⛔ 2026-08-11 — THAT REWORK WAS DONE AND FAILED. Memory-injection line CLOSED — do NOT re-attempt prompt-framing on abliterated.** The per-persona framing rework (the Phase-1 prerequisite this ADR always named) was built deterministically (no extra LLM call) and gated on abliterated via the frozen-gallery `--gallery abliterated-8p` canary over the collapse personas (eeva/solace/aurora) vs the committed 8-persona OFF ruler `baseline_abliterated-8p` (per-persona overall-3 **0.792**). Three staged variants, **all REGRESSION**: **C1** minimal (per-persona voice-cue + recency re-anchor as the last line) → **0.708**; **C2** reposition the voice-exemplars block after the memory → **0.542**; **C3** a concrete in-voice exemplar as the recency anchor → **0.500**. **eeva degraded monotonically 0.625 → 0.5 → 0.375** — the more voice machinery added to the injected block, the worse. **Verdict: on the abliterated register-attractor, memory injection costs voice distinctiveness that NO prompt-level framing recovers** — the ceiling is the model, not prompt geometry (all three beat the *old* frame's eeva 0.25, but none match no-injection). **Remaining levers: model-level (a companion model not voice-fragile under injection) or an LLM-paraphrase rewrite of the memory body (rejected: latency at ~16 tok/s + hallucination surface).** Flags stay OFF; the rework branch was discarded (never committed). Reusable win: the frozen-gallery `--gallery` cheap canary made each iteration ~12 min vs ~40 min full-8.

**Proposed — plan only, no code.** Drafted 2026-06-27 from a three-agent research
pass (one internal architecture+vision map + two external web-research passes on
the character-fidelity axis and the long-term-companion axis), run after
[ADR-005](005-persona-architecture-simplification-eval-first.md) Phase B shipped.
Eval-first, staged, reversible; every behavioural change ships behind a flag,
default OFF, A/B-measured against a frozen baseline before any default flip —
the same discipline that worked for ADR-005.

### Implementation status — Phase 0 BUILT, Gate 0 run (2026-06-28)

Phase 0 was built in a worktree (branch `feat/adr-006-phase0-memory-prereqs`) via
the `/crucible:develop` FULL workflow; backend suite 1600→1635 passed, 0
regressions, two qa-gatekeeper PASSes. **Three of the Phase-0 premises below did
not survive contact and are corrected here:**

- **FAISS is NOT wiped-with-data-loss on restart.** The per-session index already
  rebuilds lazily from SQLite (the source of truth) on first chat after a restart —
  so the "continuity bug" is a one-time cold-start re-index *latency*, not data
  loss. Phase 0 therefore ships a lightweight **pre-warm** of recent sessions
  (`MEMORY_PREWARM_SESSIONS`), not disk persistence (which research showed is
  net-negative at our <50K-vector scale: corruption/version/sync risk for a <20ms
  rebuild).
- **The assembled prompt was NOT 1.5–3× larger** from appended context — the
  appended context was **DROPPED entirely**. `handle_session_chat` built six
  context blocks (user-profile, emotional, unlocked-lore, on-demand-lore, rank,
  capability) into a local `system_prompt` used **only for token budgeting**, then
  discarded them: `ChatBody` carried no system-prompt field, so `chat()` rebuilt
  the base prompt + wallet state only. **On-demand lore (ADR-003, flag-ON in prod)
  and emotional state were therefore non-functional on the live session path.**
  This severed seam became **M0** (below).
- **Single-session factual recall is served by history, not the system prompt**, so
  M0's injection moves it only marginally (0.413→0.44); M0's real target is the
  cross-session path (currently xfail pending user-linkage in Phase 1).

**What shipped (all behind default-OFF flags / pure additions):** assembled-prompt
token observability (M2: `[Tokens-assembled]` + Ollama `prompt_eval_count`);
session pre-warm (M1); committed flag defaults aligned to prod + a JWT fail-loud
validator (M3); the memory-depth eval harness — factual-recall, PICon-style
contradiction (substring-first, *not* cosine — cosine is provably wrong for
contradiction), cross-session continuity (M4); and the **M0 seam repair**
(`ChatBody.extra_system_context` carried through and appended in `chat()`, with a
priority-ordered token cap), gated by `MEMORY_CONTEXT_INJECT` (default OFF).

**Gate 0 verdict — FAIL on voice; `MEMORY_CONTEXT_INJECT` stays OFF.** Live eval
(Magidonia-24B, isolated backend, prod untouched): flag-OFF distinctiveness 0.768
/ flatness 0.006 → flag-ON **0.643 / 0.018**. 5/6 NEPHILIM personas regressed;
the non-NEPHILIM control (gojo, which receives no rank/capability/lore injection)
was unchanged at 1.0 — isolating the cause: **injecting the shared NEPHILIM
lore/rank/capability vocabulary into every persona homogenizes voice.** Memory
axis benign (contradiction 0.0→0.0, injection fired correctly, peak prompt
3077/16384 = 19% so the token cap holds). The seam repair + plumbing are **correct
and merged**; only the *block selection* is wrong. **Follow-up before any flip
(M0.1, folds into Phase 1): inject user-profile + emotional state only, drop/rework
the homogenizing lore/rank/capability blocks (and/or XML-tag as metadata with
per-persona framing), then re-gate.** The mechanism (`extra_system_context`) is
reused as-is; only *which* blocks populate it changes.

### 2026-07-04 — dev-iteration & knowledge-graph decisions

Two decisions taken during Phase-1 pre-scoping:

- **Persona-count reduction rejected; eeva+nyx canary pair adopted for development.**
  A proposal to disable all personas except two and harden them first was declined:
  every quality win to date has been a *shared-mechanism* fix (ADR-005 lifted all 7
  at once), personas are near-pure JSON data with negligible cost at rest, and the
  full 7-persona spread is the diagnostic instrument that *caught* the injection
  homogenization (tuning framing on 2 voices risks overfitting and rediscovering the
  blur on re-enable). Instead, **iterate fast on eeva (product-central, the
  injection-collapse case) + nyx (weakest voice, 0.50 attribution) as a dev canary,
  then run the full 7-persona gate before any merge or flag flip** — same discipline
  as ADR-005 Phase B. (Latest per-persona attribution, `baseline_lean-candidate_20260627`:
  aurora/gojo 0.875 > eeva/aegis/cipher 0.75 > solace 0.625 > nyx 0.50.)
- **Knowledge-graph / Neo4j memory rejected on evidence — see Alternatives Considered.**
  The structured-fact instinct is folded into Phase 1's SQLite store as *ontology-lite*
  (temporal validity, provenance/confidence, a controlled predicate vocabulary), not a
  graph database.

### Phase 1 BUILT (2026-07-04) — M1–M4 in a worktree, behind default-OFF flags, eval gate deferred

Built via `/crucible:develop` FULL in worktree `~/nephilim-wt-phase1` (branch
`feature/adr-006-phase1-memory-facts`, off `origin/main`) + a dedicated py3.12 venv;
prod `:8000` untouched. **Nothing merged, nothing flipped** — every behavioural path
stays behind `MEMORY_CONTEXT_INJECT` / `MEMORY_FACTS_ENABLED` (both default OFF). The
empirical eval (the M5 gate) was **deferred** — it monopolizes the single Ollama GPU
the live companion uses, so it is batched for a quiet window rather than run now.

- **M1 — per-persona framing rework** (the twice-failed critical path). New
  `context_framing.py:frame_injected_context()` wraps injected memory in a
  non-echoable `<remembered>` frame with a per-persona, non-imitation preamble;
  prose narrative variants `UserProfile.get_narrative_context` /
  `EmotionalState.to_narrative_context` replace the identical `**Header**\n- field:
  value` skeletons that Gate 0.1 tied to homogenization (old methods kept for their
  other callers). Injection assembly reworked in `chat_session_service`; the
  Ollama-touching guard tests were made hermetic (fixing 4 headless-only failures).
  `run_eval.py` gained a `--personas eeva,nyx` canary flag; nyx got 3
  `voice_signature.exemplars` (was empty). Validated: eval canary + full-7 gate
  **owed** (M5).
- **M2 — two-table ontology-lite store.** `memory_entities` + `memory_facts`
  (subject-predicate-object, bi-temporal `valid_from`/`valid_to` = invalidate-not-
  delete, `confidence`, provenance, `superseded_by`, `~30`-predicate controlled
  vocabulary). `MemoryFactRepository` with recency-wins `supersede_and_add`;
  dual-covered `_ensure_table` + alembic `4memory_facts` (verified `upgrade head`).
- **M3 — fully-async extraction.** `triplet_extractor.py` (Mem0-style few-shot with
  an empty-output abstention example, closed-vocab mapping, verbatim quote-span
  fabrication guard) + `fact_write_policy.py` (recency-wins) + `fact_extraction_
  worker.py` (daemon queue+thread, non-blocking enqueue, failing job dropped not
  fatal). Enqueued off the interactive path at the summarization cadence.
- **M4 — retrieval + framed injection.** `memory_fact_retrieval.py`: prose
  rendering (predicate→clause) + inject-all below `facts_inject_all_threshold` (skip
  vector search) / cosine top-k above, routed through M1's framing. Shared
  `MemoryFactRepository` singleton in `startup` (worker write + retrieval read).

Backend suite **1685 → 1734 passed / 0 failed** (headless; +49 net incl. 4 fixed),
QA-gatekeeper PASS on M1. Config docstrings + flags (`MEMORY_FACTS_ENABLED`,
`MEMORY_FACTS_RETRIEVAL_K`, `MEMORY_FACTS_INJECT_ALL_THRESHOLD`) carry the
gate-before-flip discipline.

### M5 GATE PASSED (2026-07-05) — the first time injection match-or-beats

Ran on scratch `:8001` (prod `:8000` untouched), Magidonia-24B, both flags ON vs a
fresh flag-OFF ruler frozen on this branch. **Full-7 distinctiveness attribution:
OFF 0.786 → ON 0.839 (+0.054), flatness ON 0.0 / OFF 0.012.** For comparison, Gate 0
was −0.125 and Gate 0.1 −0.07/−0.09, both FAILs — M1 framing is the first injection
that beats the ruler. The *shape* confirms the mechanism: the personas that improved
are **aegis +0.25, solace +0.25, nyx +0.125** — the exact advisory/weak voices Gate
0.1 said blur toward the injected text; **eeva** (the Gate-0.1 collapse case
0.625→0.25) is now **flat, no collapse**. Two personas dropped one attribution each
(aurora/cipher −0.125, noise at n=8). Canary (eeva+nyx) was noise-dominated
(0.938→0.875) and correctly deferred to the full-7 for the verdict.

**M3/M4 fact path — live smoke PASS** (the eval's fresh sessions can't exercise it):
seeded 3 facts for a linked user, chatted; injection fired (`124 tokens ... M1
profile/emotional + M4 facts`), eeva recalled both facts accurately **and in her own
voice** ("You're learning Rust… you live in Geneva, where the lake holds the sky like
a mirror" + her signature reframe) — memory as in-voice background knowledge, not a
recited dump. Exactly the design thesis.

**Verdict: both flags eligible to flip.** Per flag-gate discipline the flip is an
ops action on prod `.env` (like the groundedness gate), not a code-default change —
committed defaults stay OFF for instant revert until a soak completes. Owed before
flip: (a) the merge conflict with the concurrently-merged `handle_session_chat` phase-
pipeline refactor on `dev` must be resolved in `finish-branch`; (b) a short live soak
watching real conversations for false injection / voice drift.

### Gate 0.1 verdict (2026-07-03) — FAIL again; block choice is NOT the fix

M0.1 (selective injection: user-profile + emotional state only, commit
`4bd00ebf`) was re-gated on an isolated `:8001` backend (scratch DB, prod
untouched, Magidonia-24B, same frozen flag-OFF ruler 0.768):

- **Voice: FAIL.** Selective-ON distinctiveness **0.679 / 0.696** across two
  fully independent runs (2026-06-28 pre-commit WIP; 2026-07-03 committed code) —
  a stable −0.07 to −0.09, far beyond the ~0.03 temp-0.9 noise band. **eeva
  regresses hardest in both runs (0.625 → 0.25/0.375, confused with solace)**;
  aegis is volatile (0.875/0.5 — per-persona n=8 is coarse); flatness stays
  clean (0.006/0.0). The all-personas-match-or-beat gate clearly fails.
- **Memory axis: benign.** Factual recall above the 0.4 floor (PASS),
  contradiction 0.0 (PASS), cross-session continuity xfail (known — needs
  Phase-1 user linkage). Ops note: the first probe run "failed" only because
  `pytest.ini`'s `--cov-fail-under=60` fails live-probe runs that exercise no
  src code, compounded by a GPU still busy from the persona eval — use
  `--no-cov` for live probe runs.
- **Mechanism correction.** Gate 0 blamed the shared NEPHILIM
  lore/rank/capability *vocabulary*. Gate 0.1 falsifies that as the full story:
  even "persona-neutral" blocks homogenize, because the `[User Profile]` /
  `[Emotional State]` blocks are **identically formatted across all personas**
  and pull every voice toward the same injected text. gojo (which also receives
  these blocks) held 1.0 — strong voices absorb the injection; the advisory
  voices (eeva, solace, aegis) blur. Injection-content *choice* is not the
  lever; **per-persona framing is** — render the same facts in each persona's
  voice, or tag them as explicitly non-echoable metadata, before any re-gate.

**Consequence: `MEMORY_CONTEXT_INJECT` stays OFF** (default and prod). The M0.1
code stays merged — it is strictly better than full injection and correct behind
the flag; the seam, token cap, and tests carry forward. The per-persona framing
rework moves into **Phase 1** scope alongside two-level memory (where
cross-session facts — the injection's real payload — get built properly).
`EVAL_BASE_URL` env override added so the live memory probes can target scratch
backends. Gate baselines frozen under `tests/evaluation/persona_eval/baselines/`
(`gate01-post-m01` and `gate01-selective-recheck`).

## Context

ADR-005 treated persona **voice**. It worked (distinctiveness attribution
0.393→0.732, blind A/B 67/84 ≈ 80%, flatness→0) and — per the external research —
landed squarely on current best practice (lean ~900–1,200-token prompts,
examples-over-specs, voice-last, behavioural-rules-over-adjectives, and a
non-gameable embedding-attribution eval that sidesteps the documented ~69% ceiling
of LLM-as-judge persona scoring, arXiv:2508.10014). **Voice is now near its
ceiling for our setup; further voice tuning has diminishing returns.**

The research makes a sharper point: the axis we have *not* built is the one that
actually defines a **companion** — and nephilim is the ecosystem's P1 "long-term
AI companion" ([VISION.md](../../../VISION.md)). The gap is **memory, emotional-state
persistence, and cross-session continuity.**

Evidence (selected, with confidence):

- **"Pseudo-intimacy" = stateless affect mimicry.** Each turn responds emotionally
  but the state resets; native context windows do **not** constitute mood
  persistence (PMC pseudo-intimacy 2025; arXiv:2504.16939). HIGH.
- **Two-level memory is feasible on our exact stack, no fine-tuning.** HEMA
  (arXiv:2504.16754) and Memoria (arXiv:2512.12686, SQLite+vector, RDF-style
  triplets, recency decay) report factual recall ~41%→87% and large token savings
  — validated on models **weaker** than our 24B. Memoria's stack is isomorphic to
  our SQLite+FAISS. HIGH.
- **The consolidation problem.** Five contradictory facts about one entity do not
  yield a coherent answer; the fix is **entity dedup at write-time with
  recency-wins**, not query-time (hindsight.vectorize.io 2026; Mem0
  arXiv:2504.19413). HIGH.
- **Reflection compounds personalization** but is only affordable at low frequency:
  end-of-session synthesis of higher-order observations (generative agents,
  arXiv:2304.03442). Per-turn reflection is not worth the 2–3× compute. HIGH.
- **Drift:** a 1–2 sentence character re-anchor every ~15–20 turns restores voice
  where context compaction fails (ContextEcho, 23 models, arXiv:2605.24279). HIGH.
- **Continuity:** unresolved-thread bridging (surface an open thread at next
  session open) is the **#1 user-requested missing feature** for companions and
  needs **no** proactive outreach. MED-HIGH.
- **Ethics is a differentiator for us, not a tax.** Companion harms are now
  peer-reviewed and regulated: departure-clinging in 43% of top apps (CHI 2025),
  love-bombing/affect-amplification, the Setzer wrongful-death case, NY's AI
  Companion Safeguard Law in force 2025-11-05. Ethical design conflicts with the
  *engagement-maximizing business model* — and our VISION explicitly defers
  monetization, so we can optimize for user wellbeing for free. **Healthy friction
  (anti-sycophancy) is simultaneously the top anti-attachment-harm mechanism and
  what makes a persona feel real.** HIGH.
- **Beyond-prompting voice levers are out of scope.** Control vectors only shift
  trait-axes (not named-character identity; predict-vs-control ROC-AUC ~0.56,
  arXiv:2502.18862) and Ollama doesn't support them; persona LoRA has **zero
  published voice-distinctiveness delta** and Magidonia is already a full
  fine-tune; DRY/XTC samplers are good but **blocked by Ollama's API** (would
  require migrating the always-on stack to raw `llama-server`). The only free
  voice win is min-p sampling (already in Ollama). None of this is worth doing
  for an axis already at 0.73 attribution.

Our own architecture (internal map, file:line refs in the research transcript)
has three concrete gaps that gate or undermine this work:

1. **FAISS RAG is in-memory only** (`memory_rag.py` per-process `vectorstores`
   dict). Every launchd restart **wipes semantic memory for all active sessions** —
   continuity is core to the vision, so this silently undermines it. Prerequisite.
2. **No token budgeting at the *assembled* prompt size.** The cached builder logs
   its own size, but `chat_session_service.handle_session_chat` then appends
   user-profile + emotional + lore + rank contexts, so the real inference prompt is
   1.5–3× larger and unmeasured. Memory work will add more injected context —
   instrument first.
3. **Deployment fragility.** The live experience depends on flags set only in the
   git-ignored `.env` (`PERSONA_LEAN_PROMPT`, `LORE_ONDEMAND_ENABLED`,
   `LORE_RANK_CONTEXT_ENABLED`); a fresh clone reverts to the legacy/invisible
   experience. Align committed defaults to the validated config.

**Latency reality (measured 2026-06-21):** generation (~16 tok/s)
is the bottleneck, not prefill (~0.4s). Every *added* LLM round-trip (emotion
classification, reflection) costs real wall-clock — so memory features must
prefer cheap/async calls and tight token budgets, never per-turn heavy synthesis.

## Decision

Open a **"companion memory & continuity"** track — the substance under the
VISION's "HERMES-Agents" banner — as the next nephilim phase, treating persona
*voice* as done. Build it **eval-first, staged, flag-gated default-OFF, A/B'd**,
reusing the existing bge-m3 + SQLite + FAISS infrastructure. No big-bang; each
phase is independently shippable and revertible.

### Phase 0 — Prerequisites, observability & eval (no behaviour change)

The foundation. Nothing user-visible flips; this makes the rest measurable and safe.

- **Persist the FAISS store** across restarts (disk-backed per-session +
  lore-corpus index, lazy reload) so continuity survives launchd restarts.
- **Assembled-prompt token instrumentation** — log the *final* inference prompt
  size (cached builder + all appended contexts) and wire it into the
  `MemoryManager` token budget, which currently plans on a stale estimate.
- **Align committed flag defaults** to the validated production config (close the
  deployment-fragility gap) — separate, reviewed change; keep instant-revert.
- **Extend the eval harness** (`tests/evaluation/persona_eval/`) with the
  continuity/memory dimensions the current suite lacks: long-context **factual
  recall** (LongMemEval-style), **contradiction under interrogation** (PICon,
  KAIST-Edlab/PICon-pkg, 50-turn adversarial), and **cross-session continuity**.
  Keep the ADR-005 attribution + flatness metrics.
- **Freeze baselines** on the current architecture before any Phase 1+ change.

**Gate 0:** persistence verified across a real restart; token logging live; new
eval probes produce a believable frozen baseline.

### Phase 1 — Two-level memory (HEMA/Memoria pattern)

- **Rolling narrative summary** (single evolving paragraph, injected first) +
  **structured fact store** in SQLite (subject-predicate-object triplets,
  recency-weighted), retrieved semantically via the existing FAISS/bge-m3 layer.
- **Entity dedup at write-time, recency-wins** conflict resolution (the
  consolidation fix) — facts are a source-of-truth table in SQLite (updates,
  deletions, dedup that FAISS can't do); FAISS is the semantic index over them.
- Fact **extraction on write** is a bounded LLM step; keep it cheap (small output,
  batched at summarization cadence, not every turn).
- **Ontology-lite schema (the evidence-backed slice of the knowledge-graph idea —
  see Alternatives Considered).** The fact table carries: (a) **temporal validity**
  `valid_from` / `valid_to` — supersede by *invalidating*, not deleting, so
  "sister was sick" → "sister recovered" keeps both with validity intervals
  (MemPalace pattern, 96.6% recall@5 on LongMemEval in two SQLite tables);
  (b) **provenance + confidence** columns (source session, extraction confidence) —
  cheap now, painful to retrofit, and what makes contradiction resolution
  debuggable; (c) a **controlled predicate vocabulary** (~20–40 predicates:
  `likes`, `works_as`, `related_to`, `worried_about`, …) — the useful ~10% of an
  "ontology" that makes dedup/conflict tractable, without a formal OWL schema.
- **Per-persona framing of injected facts** (the Gate-0/0.1 prerequisite): facts
  render in the retrieving persona's voice or ship as non-echoable metadata — never
  as identically-formatted blocks. This is the critical path; fact-store structure
  rides behind it.
- Flag `MEMORY_FACTS_ENABLED`, default OFF.

**Kickoff scope & locked decisions (2026-07-04, via `/crucible:develop` FULL).**
Five milestones, QA-gated each, in a `~/nephilim-wt` worktree + py3.12 venv (prod
`:8000` untouched, evals on a scratch `:8001`/DB): **M1** eval-canary tooling
(`--personas` filter on `run_eval.py`; generalize memory probes past the hardcoded
`nephilim_eeva`) **+ the per-persona framing rework validated on the *existing*
profile/emotional M0 blocks first** (de-risks the twice-failed homogenization
before any fact-store investment; a new `_render_injected_context_in_voice(card,
blocks)` reusing each persona's `voice_signature`, rendering facts as third-person
diegetic narration + a non-imitation instruction, memory block repositioned earlier
with the voice-anchor kept last per position-bias research; nyx needs
`voice_signature.exemplars` added) → **M2** fact-store data layer → **M3** async
extraction write path → **M4** fact retrieval + framed injection → **M5** full
7-persona gate + flip decision. Three settled choices: (1) **single coordinated
flip** — hold `MEMORY_CONTEXT_INJECT` *and* `MEMORY_FACTS_ENABLED` OFF until all of
M1–M5 pass, even though M1 alone could flip the existing blocks early; (2)
**two-table `entities` + `facts`** schema (not string-subject single-table) — the
MemPalace/Graphiti converging design, cleaner for later multi-hop; (3) **fully-async
/ background extraction** — off the interactive response path entirely (thread/queue),
not inline on the summarization turn. Canary (eeva+nyx) is *dev-iteration only*; the
gate is always full-7.

**Gate 1:** factual-recall eval match-or-beat baseline; **no contradictory
retrieval** (dedup correctness test); generation-latency budget unbroken;
backend suite 0 regressions.

### Phase 2 — Persistent emotional state

- A **PAD triplet** (Pleasure-Arousal-Dominance, each −1→+1) per (persona, user)
  in SQLite, with a slow decay function, updated per turn by a **cheap**
  classification ("how did this exchange shift <persona>'s mood?"), injected as
  ~2–3 lines. Fixes pseudo-intimacy.
- **Independent affective stance — not pure mirroring.** The companion has its own
  emotional center of gravity; it may *note* a user's mood shift and offer
  grounding rather than amplify it. This directly prevents the "affect
  amplification" dark pattern (CHI 2025 taxonomy).
- Optional appraisal-as-brief-CoT (OCC→PAD) only if the cheap path underperforms.
- Flag `EMOTION_STATE_ENABLED`, default OFF.

**Gate 2:** no flatness regression; blind A/B match-or-beat on voice;
**anti-amplification check** (negative-spiral probe must not escalate); latency
budget held (the per-turn classification call is the cost to watch).

### Phase 3 — Reflection & cross-session continuity

- **End-of-session reflection** — one LLM call over the session summary + memory
  store → 3–5 higher-order observations written into the fact store
  (importance-weighted). Low frequency by design (once per session, async).
- **Unresolved-thread bridging** — tag open threads (unanswered questions,
  incomplete arcs, "I'll come back to this") in session summaries; surface the
  highest-salience one in the companion's opening on the next session. **No
  unsolicited/proactive outreach** — memory-informed responsiveness only.
- Flag `REFLECTION_ENABLED`, default OFF.

**Gate 3:** cross-session continuity eval match-or-beat; reflection cost stays
off the per-turn path; explicit check that no AI-initiated outreach was added.

### Phase 4 — Ethical friction & companion safety

Design-level, partly a legal baseline (NY AI Companion Safeguard Law). Cheap,
on-brand, and quality-positive.

- **Healthy friction / anti-sycophancy** — a per-persona "core beliefs / stances"
  section that licenses persona-authentic disagreement (the top structural
  anti-attachment-harm mechanism *and* a realism win).
- **Graceful exit** — honor "I need space" / closure without clinging; no
  departure-triggered retention messaging.
- **Crisis detection → resource routing** — a lightweight classifier on user
  input that, on distress signals, gives a brief in-character acknowledgment **and
  actively routes to real human help** (never "talk to me instead").
- **Persona-as-attachment-object continuity** — relationship/mood/memory state
  lives in the persona's SQLite record, not the model weights, so a future model
  upgrade doesn't sever the bond (users demonstrably grieve model-version changes).
- Flag-gated where behavioural; crisis routing ships ON once validated.

**Gate 4:** sycophancy probe shows calibrated friction (not over-refusal,
arXiv:2502.14975); crisis-routing red-team; audit that **no engagement-dark-pattern**
(love-bombing, streaks, FOMO, proactive clinging) was introduced.

### Explicitly out of scope (anti-scope)

- **Control vectors / persona LoRA / `llama-server` migration** — blocked by Ollama
  and/or unproven for named-character voice; voice is already solved. Revisit only
  if a specific phase *needs* a sampler (e.g. DRY) it can't get otherwise.
- **Full MemGPT/Letta paged-memory OS** — overkill; Letta's own benchmark shows
  plain filesystem/SQLite beats the heavy frameworks. Use SQLite directly.
- **Per-turn reflection** — 2–3× compute for gains captured cheaply end-of-session.
- **Engagement-maximizing design** — love-bombing, variable-reward streaks, FOMO
  nudges, AI-initiated "thinking of you" outreach, pure emotional mirroring.
  Documented harms and antithetical to the VISION's non-monetization stance.

### Acceptance gate & rollback (MANDATORY — inherits ADR-005's discipline)

1. **Freeze baselines first** (Phase 0) — memory/continuity/friction dimensions on
   the current architecture, per persona where applicable.
2. **Re-test after each phase**, flag ON, A/B vs the frozen baseline.
3. **Per-feature decision rule:** match-or-beat + 0 backend-suite regressions +
   within the latency budget → eligible to flip; worse on any tracked dimension →
   fix or keep OFF for that feature. No global default flip until a feature clears.
4. **Rollback is instant** — default-OFF flags; revert = flip off + reload. Keep
   legacy paths through a soak period.
5. **Memory-specific gates:** dedup correctness (no contradictory retrieval),
   write-time provenance on facts (so a bad extraction is traceable/removable), and
   a generation-latency budget per added LLM call (reflection async; emotion
   classification cheap or batched).

## Consequences

- **Positive:** realizes the P1 companion vision (the depth layer, not just voice);
  durable and compounding (memory/reflection improve with use); an **ethical
  differentiator** we can afford because we don't monetize on engagement; reuses
  bge-m3 + SQLite + FAISS (no new infra, no cloud, privacy preserved); each phase
  independently shippable and revertible; closes three real architecture gaps
  (FAISS persistence, prompt observability, deployment fragility).
- **Negative / risks:**
  - **Latency** — emotion classification (per turn) and reflection (per session)
    add LLM calls; generation is already the bottleneck. *Premortem: this could
    fail if the per-turn emotion call pushes turn latency past tolerance* —
    mitigate with a cheap/batched classifier and a hard latency-budget gate;
    reflection stays async/end-of-session.
  - **Memory write errors propagate** — a bad fact extraction poisons retrieval.
    Mitigate with dedup + provenance + recency-wins + the contradiction eval.
  - **Emotional-state miscalibration** — wrong PAD updates feel "off." Mitigate
    with the anti-amplification check and blind A/B before any flip.
  - **Privacy** — emotional/relationship logs are sensitive. Mitigate by staying
    local-only (already true), data-minimizing the logs, and never sending them
    off-box (the local-only cloud-LLM ban already enforces this).
  - **Scope creep** — the track is broad. Mitigate with hard phase gates and the
    anti-scope list.
- **Reversibility:** every behavioural change is a default-OFF flag; baselines
  frozen first; legacy paths retained through soak. Phase 0 is pure
  infra/observability with no behaviour change.

## Alternatives considered

- **Keep tuning persona voice** — rejected: near the ceiling for our setup;
  the metric itself isn't human-validated beyond a point; diminishing real-world
  return (ADR-005 already delivered the win).
- **Beyond-prompting voice (control vectors / LoRA / llama-server)** — rejected
  for now: blocked by Ollama or unproven for named characters; high infra cost on
  the always-on stack for an axis already solved. Deferred, not deleted.
- **Full agent-memory framework (MemGPT/Letta/Mem0 service)** — rejected: overkill
  for a controlled-schema companion; the simpler SQLite+FAISS pattern benchmarks
  *better*.
- **Knowledge-graph memory (Neo4j / Graphiti-Zep / Mem0-graph)** — rejected on
  evidence (research pass 2026-07-04). The only independent, statistically-tested
  graph-vs-vector comparison found the graph layer's accuracy gain **not
  significant** (~2–3.6%, p>0.05) at 32–40% more tokens and up to 86% higher
  latency (arXiv:2601.07978); Mem0's own ablation shows its graph variant *worse*
  on single- and multi-hop recall (+1.6% overall at 1.8–3.3× latency); ConvoMem
  (arXiv:2511.10523) shows that below ~150–300 conversations — exactly the
  single-user companion regime — simple retrieval **beats** graph-RAG on accuracy,
  not just cost. Vendor benchmarks in this space are demonstrably non-reproducible
  (the public Zep↔Mem0 LOCOMO dispute). Operationally, Graphiti-style ingestion is
  ~1 LLM call per message — brutal on an 18.5 tok/s local 24B — and Neo4j is a new
  always-on service on the trading Mac Mini, the same rejection logic as
  [ADR-001](001-lore-as-typed-markdown-wiki-not-a-graph-db.md). Whether a 24B local
  model extracts KG triples reliably is untested either way; the closest analog (a
  Mem0 production audit) found extraction junk persisted even after upgrading to a
  frontier model, implicating prompt/schema design over model size. **The good
  ideas are kept without the graph DB:** temporal validity, provenance/confidence,
  and a controlled predicate vocabulary fold into Phase 1's SQLite fact store as
  *ontology-lite* (above). **Escalation trigger** (mirroring ADR-001): revisit a
  graph layer only if continuity evals show failures concentrated in multi-hop
  *relational* queries — path networkx-over-SQLite-facts → embedded graph → Neo4j
  read-only projection. (Kùzu, the usual embedded-graph answer, was archived Oct
  2025 after an Apple acqui-hire — adoption risk.)
- **Cloud memory / managed vector service** — rejected: violates the local-first,
  privacy-first stance (emotional logs must never leave the box).
- **Do nothing** — rejected: leaves the P1 companion vision architecturally
  unbuilt; voice alone is not a companion.


---

## Amendment (2026-08-23) — reset completeness, and a citation to re-check

**Session reset now clears the memory artefacts this ADR introduced.** `DELETE /sessions/{id}/messages` previously deleted messages and emotional state only, leaving `conversation_summaries` and the in-memory FAISS index intact. Observed in production: the gwen session held 12 messages and 2 orphaned summaries, the older written 2026-07-05, and `chat_session_service._build_turn_prompt` re-injects **every** stored summary on **every** turn, ungated by `MEMORY_CONTEXT_INJECT` or any other flag. The result was a reset that reported success while the model kept reading a five-week narrative. `EpisodicMemoryRAG.clear_session()` had existed since Phase 3 with **zero callers**, so a cleared session also stayed semantically searchable for the life of the backend process.

`memory_facts` rows are deliberately **left untouched** by a reset. `source_session_id` becomes a dangling pointer, which is the intended trade: facts are extracted precisely so they outlive the conversation that produced them, and this store's whole design is supersede-by-invalidation rather than delete. Clearing a chat window is not the same intent as retracting confirmed facts. Revisit only if provenance display becomes a real feature. Note `MEMORY_FACTS_ENABLED` is OFF on prod, so this has no live effect today either way.

**One citation in this ADR should be re-verified before it is relied on again.** Research on 2026-08-23 reported that the "MemPalace pattern, 96.6% recall@5 on LongMemEval in two SQLite tables" citation used here to support the bi-temporal SQLite design may be inverted — the claim being that MemPalace's primary store is a vector DB, with the SQLite entity/triple layer an optional secondary, and that an independent critique found *enabling* its structuring features reduced accuracy versus plain verbatim retrieval. **This is recorded as flagged, not corrected**: it arrived from research outside the session that wrote this amendment and has not been checked against the primary source here. If it holds, the number supports "verbatim beats structuring" rather than "SQLite bi-temporal structuring is validated", which would weaken — though not by itself overturn — the reasoning in the Decision section. Check the source before citing it in either direction.
