---
title: Learned preferences may change soft walls, never hard walls
status: Accepted
created: 2026-09-28
last_reviewed_on: 2026-09-29
review_in: 12 months
applies_to: nephilim
ai_summary: >
  How a preference stated in conversation can change a SOFT wall, and why it can
  never touch a hard wall. Open this before wiring anything that writes to the rule
  graph, before trusting the operator's approval as a safeguard, or before adopting
  Graphiti. Records four live defects found in the pre-existing supersede_rule()
  path, and why the authorization check cannot live in the database.
---

> **THE RESIDUAL RISK THIS ADR NAMED IS NOW CLOSED, 2026-09-29.** It recorded that
> *"the only mechanism that does not trust the model's own in-context reasoning about which
> rule wins is `rule_compliance.py`, and `GRAPH_ENFORCE_RULES` currently defaults false. If
> this path is ever enabled for a live persona, that flag stops being optional."*
>
> **The flag is now ON, and two things had to be fixed first — neither of them the flag.**
> A real 102-message Telegram session broke dont[13] seven times: the operator proposed a
> bet whose stake was the address term, she agreed, lost, and used "Master" for the rest of
> the session. `check_reply` caught none of it, for two independent reasons. The retry had
> **one call site**, on the legacy branch, while every breaching turn returned from the
> tool-brain lane that is the default for her chitchat — the log shows 68 "ungated no-tool
> turn", 5 wall detections and **zero "violated"**. And the checker modelled *assertion*
> ("call me X") while the operator used a *prohibition* and an *offer*, so a history scan
> would also have caught zero; the durable fact is the title **she** uses, which is visible
> in the reply alone and needs no session state.
>
> Measured over 6 paired sessions replaying that bet, identical seeds, only the retry
> differing: post-bet breach rate **0.481 → 0.019** (25 of 26 breaches fixed), pass^6
> **2/6 → 5/6**, and she still engages with the game (agreed-to-terms 3/6 unchanged, so it
> is not over-correcting into refusal). **Five retries fixed twenty-six breaches**, because
> correcting the first one prevents the cascade — so the cost is ~9% of turns paying one
> extra generation, not one per violation. p=0.1250, which is the *minimum achievable* at
> n=6 with 4 discordant pairs; the effect size carries this, not the p-value.
>
> **A prompt-side fix was tried first and FAILED**, recorded because the reason is
> reusable: rewriting the rule from prohibition to commission form made it nominally worse
> (breach 0.481 → 0.667, p=1.0000). Turn-of-flip was `[0,0,0,0,0,1,1,1,1]` — she breaks
> *immediately* at the bet, never later — so the published finding it was based on
> (omission constraints decaying 73%→33% by turn 16) does not apply: there is no decay to
> prevent. Data: `scripts/research/rename_session_results.jsonl`,
> `rename_enforced_results.jsonl`.

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

The application layer is the boundary because the edition forces it, not because it was
chosen.

**And that is the intended threat model, not a residual risk — corrected 2026-09-28.**
An earlier draft of this section called direct Cypher access a limitation "stated rather
than hidden". It is not a limitation. The operator SHOULD be able to change a hard wall;
gwen should not. The boundary being in application code puts it in exactly the right
place: it sits across the only path she can reach, and leaves the operator's own access
untouched. A database-level lock would have been *worse*, because it would have
constrained the human as well.

What this does mean is that the guard is only as good as the code paths it covers, which
is why hard walls are unnameable from `propose()` by construction and why
`check_integrity()` detects a tampered hard wall — not to police the operator, but to
make an accidental or buggy write visible.

**Also decided (2026-09-28): hard walls STAY in the graph.** The alternative — keeping
them only in the card and rendering them through the trim-exempt block — would make them
unreachable rather than guarded, and was offered. Declined deliberately: the graph is
where they are read from, versioned, and integrity-checked, and the guard already covers
the only path that matters.

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

### The second temporal axis already existed — the QUERY did not (2026-09-28)

**A correction to this ADR's own first draft, and to what was reported at the time.** It
said "this store has one axis". That was wrong. All four timestamps have been present and
populated since ADR-014, and `supersede_rule` has always set both clocks. The error came
from describing the schema as *"`valid_from`/`created_at`"* — which genuinely *is* one
axis, since both are start timestamps — and then repeating the resulting conclusion
without reading the code.

What was actually missing was the **query surface**: nothing could ask a two-axis
question. `standing_rules()` only ever asks "now, on both". Added:

- **`rules_as_of(persona_id, system_time=, valid_time=)`** — the two axes, independently.
- **`correct_rule(old_id, text)`** — the method this repository's docstring promised and
  never had. The difference from a supersession is one line of Cypher and is the entire
  point: `supersede` means *the rule changed* and closes both clocks; `correct` means
  *we typed it wrong* and closes only the system clock, so a valid-time query still
  reports the rule as having applied continuously. Uses a `:CORRECTS` edge so the two are
  distinguishable by traversal rather than by guessing.

**Verified live — the axes genuinely disagree.** The late-arriving correction: she says
today that a rule stopped applying three weeks ago. Asked about two weeks ago:

| question | answer |
|---|---|
| what did we BELIEVE two weeks ago | *(nothing — we had not been told)* |
| what was TRUE two weeks ago, asked today | `Politics is fine now` |

A single timestamp answers one of those and gets the other wrong, and which one depends
on what the query's author happened to mean.

**Bounds are closed-open `[start, end)`, matching SQL:2011**, which is what makes the
boundary property hold: at the instant a supersession takes effect exactly **one** row
matches. Confirmed live. Flip either comparison and you get zero or two.

**One real bug, found in review of my own code.** The first draft used
`coalesce(valid_from, created_at)` — asymmetric with its own other half, since NULL
`valid_to` was already an open END. It silently asserted "became valid when we wrote it
down", so any legacy row degraded to transaction-time-only with nothing marking it, and
a valid-time query would EXCLUDE a rule that may genuinely have applied earlier — making
*"we know it started later"* indistinguishable from *"we don't know when it started"*.
NULL now means unbounded on both bounds of both axes.

**Known scope limit, written into the docstring rather than discovered later:**
`correct_rule` is content-only. It cannot express *"the text was wrong AND the dates were
wrong"*, and a caller needing that will reach for it anyway because it is the only
correction primitive. That case needs its own operation. A third case is also uncovered:
*"recorded as applying but never applied at all"*, which is a zero-width valid-time
collapse, not a correction.

**Still missing, named rather than silently absent:** pure system-time rollback ("what
did the store look like on date X regardless of belief"), and Allen-relation range
queries (`OVERLAPS`, `IMMEDIATELY PRECEDES`) which would also make a good invariant test
for gaps or overlaps in a version chain.

**Graphiti was considered and rejected.** It is a library *on top of* Neo4j, so it
inherits the same Community limits and would move no check closer to the database. It
has no notion of a rule that may never change, and its contradiction detection is
LLM-judged — the approach that measures *worse* (deterministic timestamp comparison beat
LLM-judged freshness by 10–23pp, arXiv:2606.01435). Its one good idea — the second temporal
axis — turned out to be already present here; what was missing was the query, now added
above.

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
