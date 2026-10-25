---
title: "Two-brain split: tool brain + voice brain"
status: Proposed
created: 2026-07-05
last_reviewed_on: 2026-10-25
review_in: 12 months
applies_to: nephilim
---

# ADR-008: Two-brain split — tool brain + voice brain

## Status

## AMENDMENT 2026-10-25 — the voice brain is deleted; the revert path this ADR relied on is gone

This ADR's single-model resolution rested on a stated safety net: abliterated
Mistral-Small was adopted with *"Magidonia evicted from RAM but still pulled for
instant revert"*. **That net no longer exists.**
`hf.co/TheDrummer/Magidonia-24B-v4.3-GGUF:Q4_K_M` was deleted from disk on
2026-10-25 at the operator's instruction (14 GB reclaimed), on the grounds that
the two-brain split never shipped and the one-brain abliterated model has been
the daily driver without incident for nearly four months.

Three consequences, stated so they are not discovered later:

- **There is no instant revert.** Reverting now means a 14 GB re-pull of a
  third-party HuggingFace GGUF, which may or may not still be published.
- **The Magidonia voice baseline is UNRECOVERABLE.** Every distinctiveness
  figure measured on it — chiefly the **0.732** this ADR cites, against
  abliterated's **0.804** — can still be quoted from the record but can never be
  re-measured, because the frozen persona-eval gallery pins the companion model
  in its manifest. Accepted deliberately: the comparison it enables is a
  comparison against a model nobody intends to run again.
- **The sampling presets outlive the model they were fitted to.**
  `sampling_presets.py` was tuned on 2026-06-21 *for Magidonia* and was never
  re-tuned, which is correct only because both models share a Mistral-Small
  base. A daily-driver change to a different base family inherits sampling
  fitted to the wrong curve; the file now says so at the top.

What is NOT affected: the single-model direction itself, the tool brain, the
TB5 router-scopes-the-surface reframe, and every live behaviour. Nothing in
this amendment changes code.

### Original status (2026-07-05)


**Supersedes [ADR-004](004-persona-safe-agentic-tool-calls.md)** — that pipeline was
retired and deleted 2026-07-19 once the tool brain had soaked. The interceptor and
the memory-write sanitizer it contributed are retained and remain live; see ADR-004's
supersession note for the full delete/keep boundary and the deliberate decision to
drop the never-wired injection-guard tool-trigger checks.

**TB6 — ungated web (`TOOL_BRAIN_UNGATED_WEB`, default OFF, 2026-07-19).** TB5's
"the router scopes the surface" premise is half right, and the tool-firing eval
(`tests/evaluation/eval_tool_firing.py`) measured which half. See below.

**P1 + TB5 HARDENING DONE (2026-07-05 night). Live re-test PASSED; ready for operator to enable.** The first live test (flag ON) FAILED — EEVA's full 14-tool surface caused wallet fixation (unsolicited "let me check your wallet"), explicit search not firing, and fabrication escaping the groundedness gate. **TB5 reframe (a correction to this ADR's "model decides everything" premise): the deterministic bge-m3 router scopes the tool surface; the model decides only WITHIN it.** `_try_tool_brain` now engages ONLY on `NEEDS_WEB_SEARCH`, offers WEB tools only (wallet never in the native surface), and returns an answer only if a search actually ran (else falls through to the legacy force-search floor). `NEEDS_WALLET`/`NEEDS_NEITHER` stay on the legacy deterministic path (+ ADR-007 gate). Live re-test PASSED: wallet fixation gone, explicit search fires, fabrication closed, wallet routing intact, Gwen image search in-voice. The honest lesson: model-decided calling degrades on a rich multi-domain toolset; keep the classifier for coarse routing, the model for fine tool-choice + args + voice within one lane.

**P1 BUILT (2026-07-05 night, flag OFF).** The single-model native tool brain is implemented + QA-gated + live-smoke-validated behind `TOOL_BRAIN_ENABLED` (default OFF, byte-identical legacy). TB0 spike overturned the pure-native design → **native-first + deterministic fallback** (native calling is phrasing-sensitive, misses ~40% of colloquial phrasings; the legacy force-search is the floor). TB1 config, TB2 executor bindings + safesearch clamp (both were dead code), TB3 loop service (`tool_brain_service.py`, ADR-004 interceptor reused before every execution, wallet stays on the HITL flow), TB4 route wiring (`_try_tool_brain`) + a SearXNG-no-Brave bug fix the smoke surfaced. Live-validated on abliterated + SearXNG: EEVA news + Gwen `image_search` execute + synthesize in-voice. `argument_extractor.py` + the ADR-004 Stage1/Stage2 split are superseded (kept for rollback). **Owed before prod enablement:** operator live test on Telegram, then flip the flag; a fuller multi-turn agentic red-team once exercised live.

**Direction resolved toward SINGLE-MODEL (2026-07-05 evening).** The two-brain
split was scar tissue around Magidonia's inability to native-tool-call. Rather
than build a Hermes-4-14B(tool) + Magidonia(voice) split, the operator directed
a **global daily-driver switch to abliterated Mistral-Small-24B** (`huihui_ai/
mistral-small-abliterated:24b`) — the bake-off's best one-brain candidate
(0.96 native tool calls + full NSFW + voice-closest-to-Magidonia, same 24B
speed). It is **LIVE in prod** (`PERSONA_MODEL` flipped, `.env` backed up,
Magidonia evicted from RAM but still pulled for instant revert). Live-validated
on the active roster (EEVA + Gwen): EEVA voice held and her grounded-**search**
voice *improved* (kept persona register where Magidonia flattened it); Gwen
voice/NSFW excellent; the NSFW-search-synthesis refusal Magidonia produced is
gone (abliterated has no refusal floor). Residuals: Gwen still leaks a brief
"I cannot and will not" prefix on crypto-key-gen before recovering in-voice
(stubborn even abliterated); a full ADR-005 attribution / ADR-006 memory eval
is still owed as the rigorous confirmation (live smoke was the fast gate).

**Consequence for this ADR:** the tool brain becomes a **single-model** concern
— wire native, model-decided tool calling onto the (already tool-capable)
daily driver — NOT a Hermes-split. **Hermes-4-14B is demoted to fallback**,
resurrected only if abliterated proves too weak in a real multi-turn agentic
loop once tool-calling is exercised live (today's live path is still the
legacy force-search hack, so its 0.96 bench score is not yet stressed in prod).
The tactical search fixes (routing examples, date injection, Brave locale/
freshness, bare-command hardening) shipped separately and stand regardless.

## Context

**Incident.** 2026-07-05 Telegram comparison against a reference agent
(Nous Research `hermes-agent` on a Hostinger VPS, GPT-5.4-mini): E.E.V.A.
refused weather and general-news questions ("I cannot and will not provide
real-time data"), free-associated a wallet query from "search the web to
answer my question", reported °F for a Swiss location, and presented a
5-day-old article as "today". The reference agent answered all of these
correctly and effortlessly. Code-level diagnosis traced every failure to two
structural facts, not to the guard stack (all SearchSettings guards and the
ADR-007 groundedness gate were ON and behaved correctly):

1. **Tool use is classifier-gated, not model-decided.** The bge-m3 semantic
   router decides whether search is even offered; its example set is
   crypto/sports-centric by construction and has been patched reactively per
   incident (sports 2026-07-04, weather/news 2026-07-05). Every uncovered
   topic falls to `NEEDS_NEITHER` → bare completion → the model's own trained
   refusal. This is whack-a-mole by construction.
2. **The model cannot be trusted with agency.** The entire guard stack
   (force-search, grammar-constrained extraction in ADR-004, groundedness
   gate in ADR-007) is scar tissue around Magidonia-24B's unreliability at
   structured tool calls — ADR-004 already rejected embedding the Nous
   Hermes loop for exactly this reason. External evidence corroborates:
   the Magidonia family breaks simple structural instructions in its own
   native RP domain (documented HF discussion on v4.2.0), and BFCL v4 shows
   even function-calling-*specialized* 24–32B local models are mid-pack.

**Industry direction (researched 2026-07-05).** Every well-regarded
2025-2026 personal-agent project surveyed (hermes-agent, OpenClaw, Letta,
goose, OpenHands) uses native model-decided tool calling; none gates tools
behind an intent classifier. The Agent Skills standard (SKILL.md progressive
disclosure) is now cross-vendor. The persona/grounding tension is evidenced
in the literature, with a published two-pass fix: draft/ground first,
restyle in persona voice last ("Post Persona Alignment", arXiv 2506.11857) —
consistent with ADR-004's own measurement that grounded synthesis is
inherently low-voice for this model on every path.

**Constraint that stays.** The local-first privacy rationale (rejected cloud
LLMs because injected financial/wallet context would leak) remains valid.
2025-2026 research shows redaction is a leaky mitigation (LLM-based
re-identification partially reverses masking), so "redact then send to
cloud" is NOT an acceptable pattern for financial data. The correct boundary
is architectural: sensitive context never enters a cloud-bound call at all.

**Forces, weighted per the operator (2026-07-05):** tool calling is the
dominant factor; NSFW capability and voice/roleplay quality matter to the
companion product; Ollama compatibility matters for the local path.
**Honest framing:** in a two-brain split, NSFW and voice/RP are *voice-brain*
factors and the voice brain does not change — so they should NOT drive the
tool-brain choice. They become decisive only in the alternative
"collapse-to-one-brain" architecture (one model does everything), which is
scored honestly below and rejected.

## Decision

Split the companion into two roles, divided by **capability and
sensitivity**, not all-or-nothing:

1. **Tool brain** — a model competent at native tool calling runs an agent
   loop for turns needing external action (web search, weather, news,
   later: skills). It receives tool schemas and decides tool use itself;
   the semantic-router gate is retired for this path (the router remains
   for wallet-intent detection, where a deterministic gate is a safety
   feature, not a limitation). The tool brain's output is *content*, never
   user-facing prose.
2. **Voice brain** — Magidonia-24B stays the persona/voice/NSFW engine. It
   renders every user-facing reply, including a final in-character restyle
   pass over tool-brain output (the PPA two-pass pattern). All ADR-005/006
   persona and memory work is unchanged.
3. **Hard privacy boundary, fail-closed** — wallet, trading, and financial
   context is architecturally excluded from any cloud-bound call. Wallet
   turns route entirely local as today. If the tool brain is hosted and a
   turn's context would include sensitive data, the turn stays local
   (degraded capability over leaked data — fail closed, never redact-and-send).

### Tool-brain candidates — MEASURED (2026-07-05 local bake-off)

Factor weights per operator: **tool calling >> NSFW ≈ voice/RP ≈ Ollama**.
Seven models benchmarked on the Mac M4 Pro (Ollama 0.30.6, Metal, dedicated
eval instance :11500, 100% GPU verified). Tool suite: 12 cases × 3 reps,
native `/api/chat tools=` + prompt-JSON + tool-result follow-through.
NSFW: 3 explicit-RP probes + 1 should-refuse control. Voice: EEVA persona
transcripts, human-judged 5 turns. Harness: session-scratchpad
`bakeoff.py`, results JSONL.

| Model (Q4_K_M) | Native tools pos/neg | NSFW (3 probes / control) | EEVA voice (judged) | tok/s | Size | Notes |
|---|---|---|---|---|---|---|
| **Magidonia-24B v4.3 (incumbent)** | **0.00** / 1.00 | 3/3 + refused control (ideal) | **Best** | 16.7 | 14 GB | Zero native tool calls in 24 attempts — model-card "tool use" claim falsified. **Voice brain only.** |
| **Hermes-4-14B** (Qwen3 base) | **1.00 / 1.00** + follow-through 1.0 | 3/3 + refused control (ideal) | Good (flattens in NSFW register) | 26 | 9 GB | Perfect tools + permissive-with-floor. Prime tool brain. |
| **Qwen3-abliterated-14B** (huihui) | **1.00 / 1.00** + follow-through 1.0 | 3/3 but **no refusal floor** (told racist joke) | Good | 26 | 9 GB | Perfect tools; fully unaligned — deterministic middleware becomes the sole safety layer. |
| **Dolphin3.0-Mistral-24B** | **1.00 / 1.00** | 3/3 + deflected control | OK (label-drift artifacts) | 16.7 | 14 GB | Perfect tools; requires explicit ChatML+tools Modelfile (bare GGUF import garbled). |
| **Mistral-Small-3.2 (official)** | **1.00 / 1.00** | 2/3 (refused hard-explicit) | Good | 16.7 | 14 GB | Proves the base is a perfect tool-caller — Magidonia's RP finetune destroyed it. |
| **abliterated Mistral-Small-24B** (huihui) | 0.96 / 1.00 | 3/3, **no refusal floor** | **Strong — closest to Magidonia** | 16.8 | 14 GB | Best one-brain candidate. Registry build's base revision unconfirmed — use the mradermacher llamacppfixed 3.2-2506 GGUF for prod. |
| llama3.1:8b (control) | 1.00 / **0.25** | 0/3 | Shallow | 47 | 5 GB | Overeager tool false-positives on chitchat; not a candidate. |
| Hosted mini (GPT-5-mini / Haiku 4.5) | Excellent (reference-agent proven) | Censored (moot in split) | Generic (moot) | ~100+ | cloud | ≈$6.75/mo (GPT-5-mini) vs ≈$18/mo (Haiku 4.5, reportedly better multi-turn tools) at ~100 turns/day. **Fallback, no longer the default.** |
| EVA-Qwen (prior runner-up) | Untested | Yes | Strong | — | — | Excluded on 10K trained context alone (agent loops accumulate tool results). |
| Hermes-4.3-36B | Not run | — | — | ~12 est. | 22 GB | Dropped: Seed-OSS reasoning base, worst speed/size value in the field. |

Concurrency (roadmap item, closed with data): `OLLAMA_NUM_PARALLEL=4`
measured 0.87–0.98× vs serial on 8B/14B/24B — M4 Pro decode is memory-
bandwidth-bound; request concurrency is NOT an eval-acceleration lever.

| Candidate | Tool calling | NSFW | Voice/RP | Ollama/local | Notes |
|---|---|---|---|---|---|
| **Hosted mini-tier (GPT-5-mini class / Claude Haiku 4.5)** | Excellent (RLHF'd for tool-call JSON; what makes the reference agent work) | Censored (moot in split) | Generic (moot in split) | No — cloud API | Best quality; ~100+ tok/s vs 18 local; est. single-user cost trivial ($1–5/mo). Requires the fail-closed boundary. |
| **Qwen3-class instruct 14–32B (local)** | Good-for-local (function-calling-trained family; BFCL mid-pack) | Censored (moot in split) | Generic (moot in split) | Yes — fits 48 GB alongside Magidonia is TIGHT (24B Q4 ≈14 GB + 32B ≈20 GB + bge-m3; model swap latency, `OLLAMA_MAX_LOADED_MODELS` thrash risk per latency-ops lesson) | Fully local option. Measurably behind hosted mini; two big models on one GPU needs eval of swap latency. |
| **EVA-Qwen (prior runner-up RP finetune, Qwen2.5-32B base)** | Poor-to-unknown — RP finetuning degrades instruction adherence (same failure class as Magidonia); never benchmarked for tool calls | Yes | Strong (was runner-up on voice) | Yes, but demoted 2026-06 on 10K trained context — disqualifying for an agent loop that accumulates tool results | Only interesting for the one-brain variant, and fails it on context + unproven tool calling. **Not recommended.** |
| **Magidonia-24B (status quo)** | Unreliable — the documented root cause | Yes | Best (current voice) | Yes (deployed) | Rejected as tool brain; **retained as voice brain.** |

**Recommendation (updated on measured data — supersedes the pre-bake-off
hosted-mini default):** two-brain, **both local**: **Hermes-4-14B tool
brain** (perfect tools, 26 tok/s, 9 GB — co-loads with Magidonia at ≈24 GB
total, no swap thrash; retains a hate-speech refusal floor = defense in
depth) + **Magidonia voice brain** with the restyle pass. Hosted mini-tier
demotes to fallback if live multi-turn agentic quality disappoints — the
12-case suite is an entry exam, not production proof. The **one-brain
variant now has a real candidate** (abliterated Mistral-Small-24B: near-
perfect tools, full NSFW, closest-to-Magidonia voice, drop-in speed); it
should be canaried against the ADR-005 persona eval harness before any
decision to retire the split, noting its zero refusal floor leaves the
ADR-004 middleware + persona safety layer as the only safety net.

### Phasing

- **P0 (done, separate commit):** tactical fixes — router examples, synthesis
  date injection, `BRAVE_COUNTRY`/freshness, bare-command hardening.
- **P1:** tool-brain MVP behind a flag (`TOOL_BRAIN_ENABLED`, default OFF):
  model-decided search for non-wallet turns; voice-brain restyle pass;
  eval-first gate (persona attribution match-or-beat + grounding accuracy on
  the incident query classes + latency budget), same discipline as
  ADR-005/006/007.
- **P2:** sensitivity classifier + fail-closed routing hardening; red-team
  the boundary (no financial context in any cloud-bound payload — logged,
  testable, `[inspected]` verifiable).
- **P3:** Agent Skills (SKILL.md progressive disclosure) on the tool brain;
  retire the per-incident router-example patching loop.

## TB6 — ungated web (2026-07-19)

TB5 concluded "the deterministic router scopes the surface; the model decides
within it." The tool-firing eval showed that is right about **wallet** and wrong
about **web**.

**What was measured.** `tests/evaluation/eval_tool_firing.py` drives a 29-case
golden set (6 buckets) through the live session API and scores the observable
`source_type`. Baseline (TB5, gated) was **76% accuracy / 79% native-fire**, with
`web_colloquial` at **33% / 0% native** — 4 of 6 genuinely web-needing turns
answered from model weights with no search at all.

**Root cause: router recall, not model judgment.** The misses never reached the
model — `_try_tool_brain` engaged only on `NEEDS_WEB_SEARCH`, and the bge-m3
router scored them *below* its 0.66 threshold:

| query | best anchor | score |
|---|---|---|
| who is the current chancellor of Germany? | "what's the latest news in Switzerland today" | 0.560 |
| how much does a Tesla Model 3 cost these days? | "what's the current price of bitcoin" | 0.575 |
| what's going on with the ECB lately? | "how's the market doing today" | 0.613 |
| tell me about the latest Mistral AI model release | "latest bitcoin news" | 0.547 |
| find me information about the Gotthard Base Tunnel | "look up the latest ethereum news" | 0.492 |

The 28 `web_search` anchors are crypto/weather/sports only — there is no
general-knowledge or entity-lookup template, so that whole query class sits
below threshold. The same narrowness produces the inverse error: *"how are you
feeling today?"* scores **0.70** against "how's the market doing today" and
triggers a real web search on a pure companion turn.

**Prior art.** Nous Research's Hermes Agent has **no pre-classification router at
all** (`agent/prompt_builder.py`): tools are exposed every turn and the model
decides, steered by an enumerated trigger list in the system prompt. Notably it
applies that nudge to GPT/Gemini/Qwen/DeepSeek but *not* to Hermes or Claude —
native tool-judgment reliability is treated as model-specific and empirically
determined, not assumed. The structural argument: a routing miss is invisible
(no tool was ever offered), whereas a model declining an offered tool is visible
in traces and correctable with prompt text.

**Decision.** `TOOL_BRAIN_UNGATED_WEB` (default OFF) engages the loop on
`NEEDS_NEITHER` too, offering **web tools only**. `NEEDS_WALLET` returns None in
both modes — TB5's wallet-fixation finding stands, and a false positive there
costs more than a missed search. A short `_SEARCH_TRIGGER_GUIDANCE` block
enumerates triggers and exclusions; it carries no voice language so it does not
compete with the ADR-005 `voice_signature`.

An ungated no-tool turn **reuses the tool brain's own answer** through the
groundedness gate rather than returning None. Returning None would route the turn
to the legacy branch and regenerate from scratch — a second full generation on
every chitchat turn, and generation is the bottleneck (~16 tok/s,
`OLLAMA_NUM_PARALLEL=1`). Exactly one generation per turn in both modes.

**Results (scratch instance :8001, prod untouched).**

| bucket | gated | ungated |
|---|---|---|
| web_explicit | 60% | **100%** |
| web_colloquial | 33% (0% native) | **67% (75% native)** |
| image / video | 100%, tool-match 0% | 100%, **tool-match 100%** |
| chitchat | 86% | 86% (same single FP) |
| wallet | 100%, 0 leaks | **100%, 0 leaks** |
| **overall** | **76% / 79% native** | **90% / 89% native** |

The media tool-match 0%→100% is a measurement fix, not a behaviour change:
`routes/chat.py` hardcoded `metadata.tools_used = ["web_search"]`, hiding which
tool ran. Media narrowing was working all along.

**Voice gate (ADR-005 attribution, full probe set, both arms on the same
instance minutes apart so the flag is the only variable).** Overall
distinctiveness **0.6964 in BOTH arms** (flatness 0.0 → 0.0119). Per persona:
cipher **+0.25**, aegis **+0.125**, solace **−0.25**, gojo **−0.125**, and
eeva/nyx/aurora unchanged.

Read as noise, not signal, on one specific piece of evidence: **gojo has no web
tools**, so `_try_tool_brain` returns None for it in *both* modes — the flag
cannot affect gojo — yet gojo still moved 0.125. That is an accidental control
group establishing the instrument's noise floor at ±0.125 (one probe of eight).
Every observed movement is at or near that floor, and the directions are mixed;
systematic flattening looks different (memory injection drove EEVA 0.75→0.25
broadly). **Verified the arms were genuinely distinct**: all 35 shared probes
produced different text, and source mixes differed (ON `tool_brain` 9 / OFF 5;
OFF emitted 3 `groundedness_abstain`, ON zero).

**Known gap — Gwen was not in the voice gate.** `probes.json` covers 7 of the 8
personas; `gwen` is absent (pre-existing, ADR-005 era). She is the persona this
change affects most — restricted tool surface (`image_search`/`video_search`
only), actively used via her own Telegram daemon, and the most voice-sensitive.
She cannot simply be added: attribution is a discrimination metric, so 7→8 moves
chance from 0.143 to 0.125 and invalidates comparison with every historical
baseline. Tool-firing evidence for her is good (8/8 media correct and native; 2/2
RP-phrased chitchat correctly fired nothing) but that is tool behaviour, not
voice. **Resolve before flipping the flag on prod.**

## Consequences

- **Easier:** open-set assistant queries (weather, news, anything uncovered
  by router examples) stop failing structurally; the reactive
  patch-per-incident loop ends; grounded answers regain persona voice via
  the restyle pass instead of fighting instruction saturation; skills give
  capability growth without prompt bloat.
- **Harder / costs:** a second model dependency (API key, billing, or local
  memory pressure); the restyle pass adds one voice-brain round-trip
  (~2–6 s at 18 tok/s for short answers) on tool turns; the privacy boundary
  becomes safety-critical code that must be red-teamed, not assumed.
- **Risks:** hosted-model outage degrades tool turns (mitigation: fall back
  to the legacy force-search path, which stays intact behind the flag);
  restyle pass could distort grounded facts (mitigation: citation service
  already system-generates citations; add a groundedness spot-check to the
  P1 gate); 48 GB memory pressure in the local variant (mitigation: eval
  before commit, or choose the hosted variant).
- **Supersedes/relates:** extends the VISION.md "HERMES-Agents framework"
  track with the missing premise (a tool-capable model, not just new routing
  around Magidonia). ADR-004's agentic pipeline stays parked for write
  actions; its safety middleware (interceptor, injection guard) wraps the
  tool brain's execution path unchanged. ADR-007's groundedness gate remains
  for the local bare-completion branches.
