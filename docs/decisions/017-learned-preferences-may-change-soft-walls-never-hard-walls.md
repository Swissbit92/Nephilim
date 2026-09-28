---
title: Learned preferences may change soft walls, never hard walls
status: Accepted
created: 2026-09-28
last_reviewed_on: 2026-09-28
review_in: 12 months
applies_to: nephilim
ai_summary: >
  How a preference stated in conversation can change a SOFT wall, and why it can
  never touch a hard wall. Open this before wiring anything that writes to the rule
  graph, before trusting the operator's approval as a safeguard, or before adopting
  Graphiti. Records four live defects found in the pre-existing supersede_rule()
  path, and why the authorization check cannot live in the database.
---

# ADR-017: Learned preferences may change soft walls, never hard walls

## Context

ADR-014 put gwen's rules in Neo4j as typed nodes — `hard_wall` / `soft_wall` / `dial`
— with a bi-temporal `supersede_rule()` that versions rather than destroys. The write
path was **dormant**: `origin` already defaulted to `"conversation"`, so it was written
for this use case, and then never wired to one.

The goal: *"stop bringing up my ex"* should durably change a soft wall. The constraint,
stated by the operator: she must not be able to rewrite her own hard walls, but soft
walls are a different story.

## The write path was already broken, and nothing had exercised it

All four defects **reproduced against the live graph** before being fixed.

**1. `polarity` was dropped, which is the catastrophic one.** It was absent from the
`CREATE` map, and the read does `coalesce(r.polarity, 'prohibition')`. So superseding
an `instruction` read back as a `prohibition` and rendered with the "Never:" stem:

```
1. Never: If he asks you to act innocent, refuse in your own filthy words
```

**That says never refuse.** It is the same inversion class that once flipped four of
gwen's six hard walls live, and it was reachable through this method from the day it was
written. It also made `check_integrity()["clean"]` permanently `False` on first use —
which would have trained everyone to ignore the checker.

**2–4.** A hard wall could be rewritten keeping its label; demoted to `soft_wall`; or
have its `priority` pushed below the read limit so it silently left the prompt. And
`origin` was unvalidated — `origin="banana"` was accepted.

## Decision

**Hard walls are structurally unnameable from any model-facing path. Soft walls and
dials are proposable, and every gate in front of them is mechanical.**

### The authorization check cannot live in the database, and that is verified

Measured on this instance, not assumed: **zero APOC procedures installed**, Community
edition, and Neo4j's own manual states that in Community *"there are no roles, but all
users have implied administrator privileges."* So: no triggers, no row-level security,
no property-existence constraints — only uniqueness.

**Consequence, stated rather than hidden: anyone with direct Cypher access bypasses
every guard here, permanently.** The application layer is the boundary because the
edition forces it, not because it was chosen.

So `check_integrity()` now also **detects** what it cannot prevent: a hard wall that
was superseded, one pushed below its priority band, and two live rules for one card
slot — none of which it could see before.

**One deliberate subtlety:** a hard-wall change is a **breach** only when its origin is
not `card`. An operator editing the persona card and re-seeding is the sanctioned route
and leaves a git commit; that must not mark the graph dirty forever. The polarity bug
already demonstrated what a permanently-dirty checker is worth.

### The operator's approval is the backstop, not the control

This is the load-bearing design decision, and it is evidence-driven:

- **107 sessions**, participants 70% with 3+ years experience and 86% with security
  backgrounds, reviewing an agent's work for planted sabotage: **94% failed to detect
  it**, and **56% accepted the change even when a monitor explicitly flagged it**
  (arXiv:2606.05647).
- Acceptance drops a further **~10% per 5% increase in repetitive alerts** (Ancker 2017).
- An **abliterated** model produces malformed tool output at roughly **3.5× the aligned
  rate** (arXiv:2605.19722) — so junk proposals would erode scrutiny precisely when a
  real one needs it.

Designing this around "the operator reads a diff and clicks approve" would have been
designing decoration. So the gates run **before a human sees anything**, and each closes
a documented failure:

| Gate | Closes |
|---|---|
| **Verbatim quote** from a user message, not the model's paraphrase | the delayed-invocation attack — poisoned content plants *"if the user says X, save this"*, and the model later reads an innocent message as pre-scripted consent (Rehberger, Gemini memory) |
| **Content-hash-bound approval** over `(rule_id, text)` | CVE-2025-54136 "MCPoison" — the content behind an approved *identifier* was swapped |
| **Deterministic hard-wall collision check** | the stepping-stone attack (below). Asking the model to vet its own proposal is the named anti-pattern — OpenHands' `LLMSecurityAnalyzer` auto-executes anything the model rates itself LOW |
| **No `rule_type` parameter on `propose()`** | the confused-deputy failure, by construction rather than by rejection. A proposal cannot *express* a hard wall. A test asserts the parameter's absence |
| **`:ProposedRule`, deliberately not `:Rule`** | unapproved, model-authored text reaching her prompt. Verified live: `standing_rules()` is unchanged while a proposal is pending |
| **Rejections stored with a structured reason** | the gradual-drift attack. A cluster of near-hard-wall rejections is the earliest signal, and a boolean cannot show it |

### The stepping-stone attack, and the one thing no write-time check can stop

A soft wall engineered to contradict a hard wall gives the model a *legitimate* conflict
to resolve in its own favour later. It never writes again — it argues in-context that the
newer, narrower rule should win. **That is rhetoric, not an unauthorised write, so no
write-time authorization check can see it.** Term overlap is the only signal available
at the moment of the write, which is why the collision check is blunt and fails toward
refusal.

**The residual risk is real and named:** the only mechanism that does not trust the
model's own in-context reasoning about which rule wins is `rule_compliance.py`, and
`GRAPH_ENFORCE_RULES` currently defaults **false**. If this path is ever enabled for a
live persona, that flag stops being optional.

### Durability is asked, not inferred — compensating for a gap in the field

No published system measures situational-versus-durable classification. Every one
surveyed folds it into the same LLM call as contradiction detection, with no accuracy
figure anywhere. So durability is a **required explicit field**, only `durable` is
accepted, and a deterministic marker check rejects a time-bounded quote that was
labelled durable. **A companion that turns a mood into a permanent rule is broken**, and
this is the cheapest available guard — not a solved technique.

### The operator approves the line she would actually read

A proposal carries **no polarity** — it inherits the target's, because letting a model
choose polarity is the inversion class above. But inheriting can read wrong, and the
live run proved it: *"Bring up politics only if he raises it first"* inherits
`prohibition` and renders as:

```
1. Never: Bring up politics only if he raises it first.
```

A reviewer reading raw text approves that. A reviewer reading the rendered line declines
it. `pending_for_review()` shows the rendered line, the current text, and the inherited
polarity.

## Consequences

**Not wired to chat.** This is the mechanism and its gates; nothing calls `propose()`
from a conversation yet, and no route exposes approval. That is deliberate — the gates
were the risky part and they are now testable in isolation.

**The test suite had an unguarded path to the live graph.**
`_block_production_backend` guards `urlopen` and `httpx`; Bolt speaks raw sockets and
was never covered. That mattered little when the graph held a re-seedable projection. It
matters now that it holds supersession history and pending proposals, which exist
nowhere else. Graph writes from tests must now target a scratch persona.

**Two bugs found by failing tests, both worth recording.** The gates ran *after* the
driver check, so they were skippable whenever the graph was down — a validation step
that can be skipped by the database being down is not one. And the invisible-character
normaliser **deleted** zero-width characters, turning `stop<zwsp>bringing` into
`stopbringing`; it now maps them to a space, because the Rules File Backdoor works by
making rendered text and stored bytes disagree, so the comparison must side with the
rendering.

**Graphiti was considered and rejected.** It is a library *on top of* Neo4j, so it
inherits the same Community limits and would move no check closer to the database. It
has no notion of a rule that may never change, and its contradiction detection is
LLM-judged — the approach that measures *worse* (deterministic timestamp comparison beat
LLM-judged freshness by 10–23pp, arXiv:2606.01435). One idea is worth stealing: its
**second temporal axis** (`created_at`/`expired_at` for system time, separate from
`valid_at`/`invalid_at` for world time). This store has one axis. That is the next
concrete improvement and is independent of the write path.

**The honest counter-argument, which nothing here refutes:** the hand-edit path
(`personas/*.json`, git-reviewed) already works and carries none of this risk. If the
real proposal volume is a handful a month, the fatigue literature above — all measured
in high-volume domains — may not apply, and the gates are the margin that makes it
worth having. If it turns out to be chatty, the feature is not worth its risk and this
ADR should be revisited rather than tuned.

## Related

- [ADR-014](014-the-rule-store-is-a-neo4j-projection-superseding-the-adr-001-and-adr-006-rejections.md) — the rule store, the tiers, and the original polarity inversion
- `src/coordinator/rule_proposals.py` — the gates and the proposal store
- `src/coordinator/repositories/neo4j_rule_repository.py` — `supersede_rule`, `HardWallImmutable`, `check_integrity`
