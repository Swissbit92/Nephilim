---
title: "Layered toolkit: registry, generic web toolset, inner wisdom, skills"
status: Accepted
created: 2026-07-05
last_reviewed_on: 2026-10-31
review_in: 12 months
applies_to: nephilim
---

# ADR-009: Layered toolkit — registry, generic web toolset, inner wisdom, skills

## Status

Accepted 2026-07-05 (operator approval). Sequenced BEFORE the ADR-008 P1
tool-brain build at the operator's direction (2026-07-05): decide what the
tool brain will call before deciding which model calls it.

## Context

The 2026-07-05 Telegram comparison against a `hermes-agent` reference
exposed not just a routing/model problem (ADR-008) but a **toolkit
poverty** problem. Verified state of the codebase:

- The general-purpose toolkit is **one tool** (`brave_web_search`),
  hardwired to `safesearch=moderate` — actively filtering for an
  explicitly uncensored adult companion. The other 7 tools are a
  domain-locked wallet/Solana cluster.
- There is **no tool registry**. Selection is hardcoded `if/elif` on
  `QueryIntent` (`tools/tool_utils.py:get_tools_for_query`); execution is
  two bespoke paths (Brave via `SearchExecutionService`, wallet via the
  `handle_wallet_query` cascade). Adding one tool today touches ≥4 files
  plus the interceptor policy and extractor schema tables. The only
  extension point built for pluggability was
  `AgenticPipeline.tool_executors` (ADR-004), gated behind
  `AGENTIC_ENABLED`. *(Historical: that pipeline was deleted 2026-07-19 —
  the registry's `executor_bindings` now fills this role. See ADR-004,
  Superseded.)*
- The lore wiki's `entity_type: capability` entries are **diegetic
  flavor**, not a skills mechanism — a real skill loader is a new
  subsystem and must use different vocabulary.
- The companion's "inner" faculties (semantic memory, facts, lore,
  emotional state) are **prefilled** into the prompt, never model-invoked.

Reference design (operator-requested): **hermes-agent** (NousResearch,
MIT). Its qualitative architecture — a self-registering tool registry,
toolset-level enable/disable, platform adapters as toolsets, and a crisp
tools/skills split ("memory stores small durable facts that should always
be in context, while skills store longer procedures that should load only
when relevant") — is the model worth copying. (Its "70+ tools / 28
toolsets" marketing figure is internally inconsistent across its own docs;
treat as order-of-magnitude.)

Research findings that bound the design (4 threads, 2026-07-05):

1. **Web**: SearXNG (Dockerized metasearch, 130+ engines, JSON API,
   categories, `safesearch=0..2` pass-through) is the most permissive and
   most private backend — the query never leaves the machine. No
   mainstream API guarantees `safesearch=off` truly disables filtering
   (Google filters regardless; Bing's API is retired); Brave with
   `safesearch=off` is the most reliably permissive commercial API. No
   dedicated adult-search API exists anywhere — loosened general engines
   are the only retrieval path. Snippets alone are insufficient grounding;
   `fetch_url` with a trafilatura→readability→jusText extraction chain is
   the single highest-leverage addition. Deep research = a **capped**
   search→fetch→gap-check loop (2–3 rounds, top 2–4 fetches) — a 14–24B
   local model cannot productively reason over 20-source sweeps.
2. **Sandbox**: for a single-user local deployment the threat is
   prompt-injection-induced misuse of legitimate tools, not sandbox
   escape. Hardened ephemeral Docker (existing pattern + `--network none`
   or allowlist proxy, read-only rootfs, `--cap-drop=ALL`, pids/mem/cpu
   caps, timeout) is the right isolation depth; Firecracker/gVisor are
   Linux-only and solve a multi-tenant problem we don't have. Structural
   capability minimization beats prompt-level restraint (arXiv 2606.13884;
   the $150K Grok wallet-drain via prompt injection is the cautionary
   precedent): **signing/write paths must never be reachable through a
   generic terminal or skills layer.**
3. **Skills**: SKILL.md progressive disclosure is an open, multi-vendor
   standard and runnable against Ollama today (OpenCode, open-skills,
   local-skills-agent), but the one field data point puts the reliability
   floor at **20B+ non-reasoning models**, and no published benchmark
   measures the tier-1→tier-2 "load this skill?" decision by model size.
   Eval-first, not assumed.
4. **Inner wisdom**: Letta/MemGPT (production precedent) is a **hybrid** —
   core memory blocks always prefilled, only *editing* and
   *archival/episodic recall* are tools (`archival_memory_search`,
   `conversation_search`, `core_memory_append/replace`). The hypothesis
   that tool-invoked recall reduces voice homogenization is mechanistically
   consistent with our own Gate-0/M1 findings but **not directly
   benchmarked anywhere** — it is a well-motivated bet requiring an
   eval-first gate. Generative-Agents-style reflection is
   harness-scheduled, not model-invoked (sidesteps "model never calls it").

## Decision

Adopt a **layered toolkit architecture** — few powerful generic
primitives in a real registry, skills later on top, structural gating for
anything irreversible. Five components, phased:

### 1. Tool registry (foundation — Phase R)

A new `tools/registry.py` modeled on hermes-agent: each tool registers a
**definition** (existing OpenAI-function dict convention), an **executor**
callable, a **policy** entry (`mcp_access` key, `blast_radius`,
`requires_hitl` — feeding the ADR-004 interceptor instead of its private
`_TOOL_POLICY` table), and an optional **result formatter**. Tools group
into **toolsets** (`web`, `wallet`, `memory`, later `terminal`) that
enable/disable as units and gate per-persona via the existing `mcp_access`
mechanism. The registry feeds: (a) the legacy paths, (b) the ADR-004
agentic pipeline's `tool_executors`, and (c) the future ADR-008 tool
brain. Existing brave + wallet tools migrate into it; `get_tools_for_query`
becomes a registry lookup. This dissolves the 4-file-per-tool tax.

**Per-persona toolkit subsets (operator decision 2026-07-05).** Each
persona declares its own toolset grants — a first-class generalization of
the existing `mcp_access` mechanism, gated at the **toolset** level (not
per-tool: per-tool grants across 7 personas is unmaintainable config
sprawl). Persona JSON grows a `toolsets` field (e.g. E.E.V.A.:
`["web", "memory", "wallet"]`; Aegis: `["web", "memory"]`; Nyx:
`["memory"]`; Gojo: `[]`), with `mcp_access` kept as a deprecated alias
during migration. The interceptor re-checks grants independently
(defence-in-depth, unchanged from ADR-004).

**NSFW as a cross-cutting persona capability flag — not a separate
toolset.** A per-persona `nsfw: true|false` modulates granted toolsets
rather than duplicating them:

- **Web**: sets the persona's safesearch *floor* — an `nsfw: true`
  persona defaults to `off`; an `nsfw: false` persona is clamped to
  `moderate`+ regardless of the model's per-call argument (the clamp is
  enforced in the executor, not the prompt).
- **Inner wisdom**: gates access to the **intimate memory partition**
  (below).

Honest scoping note: on a single-operator machine where every persona runs
the same uncensored model, per-persona NSFW gating is **character
integrity and data hygiene, not a security boundary** — it keeps a
productivity persona from surfacing bedroom content mid-worksession and
keeps intimate data out of contexts where it homogenizes voice; it does
not (and need not) stop the operator from anything.

### 2. Generic web toolset (Phase W — first user-visible payoff)

Backend: **SearXNG in Docker as primary** (JSON format enabled, limiter
tuned for single-user; reuses the established `docker run` MCP pattern),
**Brave API as fallback** engine. `BRAVE_SAFESEARCH` and a new
`WEB_SAFESEARCH_DEFAULT` become real config (**default `off`** — this is
a private adult companion; the current hardwired `moderate` is a bug for
this product), with `safesearch` also exposed as a per-call tool argument
(`off|moderate|strict`) so the model can tighten per query.

Tools (all generic — no bespoke weather/finance/wikipedia tools; search +
fetch reaches them all):

| Tool | Params | Notes |
|---|---|---|
| `web_search` | query, category (general/images/videos/news/science/files), safesearch, time_range, max_results | SearXNG primary → Brave fallback |
| `fetch_url` | url, mode (markdown/text/raw) | trafilatura → readability-lxml → jusText chain; one consent/age-gate redirect retry |
| `image_search` / `video_search` / `news_search` | query, safesearch, time_range | thin category wrappers over `web_search` |
| `extract` | url_or_text, instruction | LLM summarize/extract, separate from raw fetch |

Plus a **capped deep-research orchestration** (not a tool): ≤2–3 rounds of
search→fetch(top 2–4)→gap-check→synthesize with citation tracking through
the existing CitationService. Explicitly NOT built: crawler, maps, finance
tools (the trading repos' MongoDB/CCXT stack owns that data — a second
source of truth is a bug), multi-agent research supervisors.

### 3. Inner-wisdom toolset (Phase I — partial conversion, eval-gated)

Follow the Letta hybrid, not a wholesale tools conversion:

- **Convert to model-invoked tools**: episodic/archival recall —
  `memory_search` (semantic RAG over past conversations; wraps the
  existing bge-m3/FAISS memory), `conversation_search` (FTS over session
  history), `lore_search` (wraps existing hybrid lore retrieval as an
  invocable alternative to per-turn injection where sensible). These
  attack the always-present context bulk that ADR-006 showed dilutes
  voice.
- **Keep prefilled** (per-persona `<remembered>` framing per ADR-006 M1):
  core profile facts, emotional/PAD state (no evidence anywhere supports
  affect-as-a-queryable-tool), the persona identity itself.
- **Reflection**: harness-scheduled (Generative-Agents pattern), never a
  model-invoked "reflect_now" tool a small model would simply not call.
- **Intimate memory partition (operator decision 2026-07-05 — NSFW inner
  wisdom).** Adult-relationship continuity is inner-wisdom data: intimate
  RP history, expressed preferences, boundaries/limits, relationship-arc
  facts. It lives in the SAME stores (memory rows / `memory_facts`) with a
  **`sensitivity` tag written at ingestion** (`standard | intimate`;
  tagged by the existing async extraction worker, with the write-time
  classifier erring toward `intimate` on uncertainty) and is **filtered at
  the data layer** — the retrieval queries behind `memory_search` /
  `conversation_search` and the prefill assemblers exclude `intimate` rows
  for personas without the `nsfw` flag. Enforcement lives in the
  repository/query layer, never as a prompt instruction (a prompt-level
  "don't mention" is theater a small model will eventually violate).
  Boundaries/limits facts are the priority content class: a companion
  that forgets stated limits is a safety bug, not a memory bug.
- **Gate**: the voice-homogenization benefit is an unbenchmarked
  hypothesis — ship behind a flag with the ADR-005/006 attribution eval
  (pre/post, full 7 personas) before any default-ON. The sensitivity
  partition additionally gets characterization tests pinning that
  `intimate` rows never appear in a non-nsfw persona's retrieval or
  prefill.

### 4. Terminal/code-exec tool (Phase T — later, structurally gated)

One `run_code` tool executing in **hardened ephemeral Docker**:
`--network none` by default (per-call allowlist proxy when a skill
genuinely needs egress), read-only rootfs + tmpfs scratch,
`--cap-drop=ALL`, `--pids-limit`, memory/CPU caps, hard timeout, no
volume mounts, **no env/secret propagation ever**. Registry policy:
`blast_radius=high`, `requires_hitl=True` initially (propose→confirm→
execute card, the existing wallet-write pattern), member of
`ALWAYS_BLOCKED_FROM_AGENT` for `source="agent"` until a dedicated
red-team eval (ADR-004 M6 pattern) passes. **Wallet signing remains
structurally unreachable from this tool and from skills, permanently** —
capability minimization, not prompt-level restraint.

### 5. Skills — SKILL.md (Phase S — last)

Adopt the Agent Skills open standard for procedures (deep-research
playbook, site-specific research etiquette, later trading-report
formatting): `skills/<category>/<name>/SKILL.md`, tier-1 metadata always
in context, tier-2 body loaded on demand. New vocabulary "skills" kept
strictly distinct from lore "capabilities" (diegetic). **Skills may
orchestrate tools but never grant capabilities** — the registry policy
layer, not skill text, decides what can execute. Gate: an eval of the
tier-1→tier-2 loading decision on the chosen local model (the 20B+
reliability-floor caveat), before any skill can influence a
`requires_hitl` action.

### Sequencing (supersedes the ADR-008 P1 ordering)

**R → W → (re-enter ADR-008 P1: split-vs-single model eval against the
real web toolset) → I → T → S.** Rationale: the model eval is only
meaningful against a genuine multi-tool surface (operator decision
2026-07-05, "toolkit design first"); inner-wisdom and terminal each carry
their own eval/red-team gates; skills last because they presuppose both a
rich toolset and a validated loop driver.

## Consequences

- **Positive**: adding a tool becomes one registration instead of a
  4-file scatter; the companion gains a genuinely generic, uncensored,
  privacy-preserving research capability (queries never leave the machine
  on the SearXNG path); grounding improves (fetch + citations vs
  snippet-only); ADR-004's middleware gains a single policy source of
  truth; ADR-008's eval becomes realistic; a principled path to skills
  without betting safety on them.
- **Negative / costs**: a new always-on Docker service (SearXNG) to
  operate (engine bans/CAPTCHA churn are normal — expect to tolerate
  degraded engines); registry migration touches the interceptor/extractor
  tables (must stay behavior-identical for existing tools — regression
  suite required); fetch_url adds real latency per fetched page on local
  inference; more model-invoked tools = more silent-skip failure surface
  on small models.
- **Risks / premortem**: *This could fail if* (a) the registry migration
  silently changes interceptor policy for an existing wallet tool —
  mitigated by characterization tests pinning current allow/deny behavior
  before migration; (b) SearXNG's per-engine safesearch inconsistency
  frustrates the NSFW use case — mitigated by Brave-`off` fallback and
  engine curation; (c) memory-as-tools ships on vibes — blocked by the
  eval-first gate; (d) the terminal tool becomes an injection vector —
  blocked structurally (network-none, HITL, no secrets, signing
  unreachable).

### Addendum 2026-10-31: the Brave fallback no longer applies to nsfw or media search

This ADR mitigated SearXNG's safesearch inconsistency with a "Brave-`off`
fallback". That is withdrawn for nsfw personas: they never reach Brave, so they get
no results while SearXNG is down. Image and video searches never reach Brave
either, because its client takes no category and returned plain web results.

SearXNG had also stopped running at some point with nothing noticing, so every
search was silently going to Brave. The container now has a compose file and a
health alert, and `WEB_SEARCH_BACKEND=routed` keeps general text search on Brave
because SearXNG's results were worse on this IP.

## Alternatives considered

- **Path A — many bespoke function tools** (get_weather, get_news, …):
  rejected. Doesn't scale (per-tool schema context cost, 4-file tax),
  never composes, and contradicts the measured lesson that search+fetch
  reaches everything a bespoke read-only tool would.
- **Path B pure — terminal + skills only, minimal tools**: rejected for
  now. The token-efficiency argument is real (Anthropic: −78.5%), but it
  buys efficiency by pushing free-text judgment onto exactly the model
  size where reliability is unproven, and it concentrates blast radius in
  the single most dangerous tool. Terminal arrives Phase T, gated.
- **Wholesale memory-as-tools (pure MemGPT-2023 paging)**: rejected —
  even Letta abandoned it for the hybrid; prefilled core memory is
  load-bearing for persona voice.
- **Commercial deep-research APIs / hosted sandboxes (E2B, Firecrawl
  cloud)**: rejected — sends queries/code off-machine, violating the
  local-first privacy stance that motivated local models in the first
  place.
