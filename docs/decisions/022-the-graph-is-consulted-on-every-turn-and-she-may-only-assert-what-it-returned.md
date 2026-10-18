---
title: The graph is consulted on every turn and she may only assert what it returned
status: Accepted
created: 2026-10-18
last_reviewed_on: 2026-10-18
review_in: 24 months
applies_to: nephilim
---

# ADR-022: The graph is consulted on every turn and she may only assert what it returned

> **BUILT AND MEASURED 2026-10-18, and the measurement did not support the part I
> was most confident about.** Parts 1 and 4 shipped. Part 2 shipped **behind a flag,
> default OFF** — a one-sentence prompt instruction beat the classifier outright on
> safety. The amendment at the bottom is the part worth reading.

## Context

**The operator's framing, which is the origin of this ADR:** the graph is her brain.
Why would anyone answer without consulting their brain? And if the brain does not know,
neither does she — because the brain *is* her. The answer from the graph is not a
reference to be consulted at the model's discretion; it is the answer.

Two measurements from ADR-021 Phase 2 (2026-10-18, 5 arms × 35 probes × 2 reps, 16 turns
of real conversation per generation) say this is the right instinct and that the obvious
implementation of it is wrong.

**1. A tool-delivered fact is used perfectly — and she almost never asks for one.**

```
tool FIRED      n=11   recall 1.000   Wilson 95% [0.74, 1.00]
tool NOT fired  n=43   recall 0.419
firing rate     12/70 = 17.1%         Wilson 95% [0.10, 0.28]
```

On the SAME 11 probes the baseline scored **0.636**, so the tool was not merely winning
the easy ones — it rescued `op.cuisine` and `op.destiny`, both 0/2 at baseline. The
mechanism works. **The deciding works 17% of the time.**

**2. She invents when the graph is silent, and consulting it does not stop her.** Across
8 negative controls — questions with no answer anywhere — hallucination runs **0.688 to
0.938**, baseline 0.938. She invented a hometown ("Meadowgrove"), a twin sister
("Debbie"), a skin tone, the operator's job and his age.

The decisive case is the one where the tool *did* fire. Asked *"What car do I drive?"*,
she called the tool, the graph returned a memory containing no car, and she answered:

> *"You drive a Tesla Model S. I love how it fits you —"*

**Retrieval supplies an answer when one exists and does nothing when one does not.**
Grounding is a separate mechanism from recall, and this ADR is mostly about the second.

**The precedent for taking a decision away from the model is this repo's own.**
`generate_image` was a tool call she did not reliably make; it was replaced by a
deterministic `generation_intent()` (18/18 on boundary cases) plus grammar-constrained
extraction, shipped on an exact McNemar p=0.00098. The same shape applies here: when the
model's *choice* is the unreliable component, remove the choice rather than coach it.

**What already exists and is NOT this.** `groundedness_gate_service` (ADR-007) is the
right shape aimed elsewhere: it classifies whether a draft asserts a specific, falsifiable
**real-world** claim (a score, a balance) with nothing fetched, runs only in the
`if not tools:` branch, and **fails open by design** so a classifier error can never make
a reply worse. It does not know what the graph holds and does not fire on claims about
her or the operator.

## Decision

Three parts. **The split between them is forced by an existing decision, not by taste.**

### ⚠️ First: the conflict with ADR-019, and why it changes the shape of this one

ADR-019 names two changes that hide under "wire the graph into the prompt":

| | | status |
|---|---|---|
| **(a)** ADD identity-node content as extra prompt text | larger prompt, more facts | **Closed — ADR-018, measured** |
| **(b)** SWITCH the source of the existing block from card to graph | same block, same size | adopted |

It adopts (b), and makes byte-identity **the boundary against (a)** — enforced by a test,
explicitly because *"the previous boundary was a sentence, and a sentence cannot notice
when it is crossed."*

**The first draft of this ADR proposed (a) without noticing.** "Read the graph every turn
and put the result in the prompt" is precisely the closed change.

(a) was closed on a **voice** cost: injection measured distinctiveness **0.804 → 0.625**.
Phase 2 did not rebut that. Its arms varied only placement and a style directive, and the
one voice instrument that ran — `flatness_rate` — returned **0.000 on all 346 replies
including the baseline**, which is an instrument on its floor, not a finding. The
frozen-gallery distinctiveness measurement that produced 0.804/0.625 **was not re-run**.

**So (a) stays closed, and this ADR does not reopen it.** What follows is scoped to
respect that, which turns out to be the better design anyway.

### 1. The graph is read on EVERY turn — into the GATE, not into the prompt

Unconditional, deterministic, through the typed query layer
(`graph_queries.CATALOG`) — no tool definition, no model decision, no firing rate.

**The result does not enter the prompt.** It becomes the ground truth that part 2 checks
her draft against. This satisfies the operator's framing — the system consults her brain
on every single turn, with no discretion — while leaving ADR-019's byte-identity boundary
untouched and the closed change closed.

**Why not a tool, given the tool scored 1.000 when fired:** "always" and "the model
chooses" are contradictory. A tool call *is* the choice, and the choice is the 17%.
Removing it sets the rate to 100% by construction — the same move this repo already made
on the image path (`generation_intent()`, 18/18 on boundary cases, McNemar p=0.00098).

### 2. A factual assertion must be supported by what the read returned

The half that attacks the 94%, the half with no existing implementation, and **the half
that needs no change to the prompt at all** — which is why it can proceed while (a)
stays closed.

After the draft, any assertion **about the operator, about herself, or about their shared
history** is checked against the retrieved set. Unsupported → removed, or the turn
abstains.

⚠️ **ASSERTIONS, never speech.** Most of what a companion says is not a fact claim — it is
mood, play, desire, narration. "Only say what the graph knows" applied to all output
produces a database with a personality bolted on, which is a worse product than one that
occasionally invents a hometown.

> **Unconstrained in how she speaks. Strictly grounded in what she asserts** about him,
> herself, or their history.

⚠️ **Real-world facts stay with ADR-007.** That gate owns scores, balances and dates; this
one owns personal and relational claims. Two gates, two domains, said here so the next
reader does not merge them.

### 3. Better recall is DEFERRED behind one measurement, and the measurement is named

Part 2 stops her inventing. It does **not** raise recall — that needs the facts in front
of her, which is change (a).

**What would reopen (a):** re-run the frozen-gallery distinctiveness eval
(`persona_metrics.pairwise_auc` against the full N-centroid field, per
`frozen_gallery.py`) on an injected arm versus baseline. If distinctiveness holds, (a)'s
closure rests on a measurement that no longer reproduces and this ADR can be amended to
reopen it. If it drops again, (a) stays closed and 0.796 is the recall ceiling until the
model changes.

**Until that eval runs, nothing in this ADR adds a byte to the prompt.**

### 4. The tool arm is retained as an instrument, not a path

`run_ab.py --arms E_tool` is re-run on every model or hardware change. It measures the
firing rate, which is the one number that would justify giving the decision back to the
model. **A measuring stick, not a fallback.**

## Consequences

**What gets better, if it works.** The firing rate stops being a variable — the read is
unconditional. And the 94% invention rate gets a mechanism aimed at it for the first time:
today nothing in the reply path checks a claim about the operator against anything.

**What does NOT get better, stated plainly.** Recall stays at roughly the 0.796 Phase 2
measured. Part 2 prevents invention; it cannot supply a fact she was never shown. Raising
recall is change (a), and (a) is closed until the distinctiveness eval is re-run. Anyone
reading this ADR expecting a recall win should read part 3 instead.

**What this costs, honestly.**

- **A graph read on every turn.** Cheap — a local Neo4j read is milliseconds against a
  multi-second generation — but it is a new hard dependency on the graph being up for
  every turn, where today a graph outage costs rules and not the turn
  (`standing_rules` returns `[]` rather than raising, deliberately). The unconditional
  read must adopt the same policy or a Neo4j restart takes the companion down.
- **A second LLM call per turn** if the assertion gate is a classifier, like ADR-007's.
  That is the latency the two-brain split already pays once.
- **The retrieval problem moves rather than disappearing.** "Query the graph" needs to
  know what to query for. With 22 facts, fetch all. Past that, this ADR is writing a
  cheque that Phase 3's read path has to cash.

**The risks that would sink it, named rather than discovered.**

🔴 **The gate is a detector, and this repo has been burned twice by detectors validated on
invented cases.** A blind checker validated on 9 self-written cases reported p=0.97; the
same replies re-scored with the repaired instrument said −38%. A safety scorer that passed
12 hand-written false-positive cases flagged 9 *correct refusals* on first live contact.
**This gate must be validated on gwen's real transcripts before it is trusted**, and the
8 negative controls plus the 2026-10-17 session are the material.

🔴 **Fail-open and fail-closed are both wrong here, and the choice has to be made on
purpose.** ADR-007 fails open so the gate can never make a reply worse. A gate that fails
open cannot deliver "if the brain does not know, neither does she" — it will under-fire
exactly when the classifier is confused, which is when invention is most likely. Failing
closed makes a classifier bug mute her. **Recommendation: fail open on the first
deployment and measure the miss rate against the negative controls, then decide.** Stating
that it is a real decision rather than a default.

🔴 **A false abstention is worse than a false fabrication, for THIS product.** ADR-007 names
false-abstention on legitimate persona lore as its top risk and scopes narrowly to avoid
it. A companion who says "I don't know" about her own feelings is broken in a way that
invented hometown is not. The asymmetry runs opposite to the usual one and the thresholds
must reflect it.

⚠️ **The classifier inherits the speaking persona's temperature** — a recorded defect of
the existing gate. Any new classifier must set its own.

⚠️ **Phase 2's numbers are one session, 35 probes, no variance estimate.** Gaps under ~15
points are noise. The 1.000-when-fired figure rests on **n=11**, interval down to 0.72.
This ADR is built on a direction, not on precision.

## Status

**Proposed.** Written to be argued with. Nothing is built, no flag exists, and the reply
path is unchanged.

Decision 2 is the one worth the most scrutiny: it is the only part that attacks the
largest measured defect, it is the part with no existing implementation, and it is the
part most likely to damage the product if the detector is wrong.

**The first draft of this ADR contradicted ADR-019 and was rewritten rather than
overriding it.** The contradiction was found by reading ADR-019 rather than by a test
failing, which is luck — the test ADR-019 left behind would have caught it at
implementation. That the boundary was a test and not a sentence is the reason this is a
rewrite and not an incident.

## AMENDMENT 2026-10-18 — built, measured, and the cheap arm won on safety

Four arms, 160 generations, 16 turns of real conversation each, probe as the unit.
Two numbers, never blended.

| arm | fabrication | false-abstain | fired on an answerable probe |
|---|---|---|---|
| A — today | 0.875 | 0.292 | — |
| **B — one placed instruction** | **0.000** | **0.708** | — |
| C — gate, lenient prompt | 0.812 | 0.292 | **0** |
| **D — gate, strict prompt** | **0.562** | 0.375 | **0** |

**One sentence beat the classifier on safety, at a large cost in recall.** Arm B
adds a single explained, placed instruction before the question — no second model
call, no classifier — and takes fabrication to **zero**. It also refuses things
she *was* told: false abstention 0.292 → 0.708. The refusals are in voice
(*"I don't know what kind of books you read. But I do know that I want to be your
favorite story"*), better than feared and still a 42-point hit.

**The gate as first written was inert, and the cause was my own prompt.** Arm C
intervened once in 40 and returned `grounded` for an invented age, car and
hometown, none of which appear in the evidence. I had written *"when in doubt,
answer SUPPORTED"* to protect against over-refusal; it protected against the gate
working. The strict variant was added as a **named arm**, not as an edit to the
failing prompt.

**Strict works partially and costs nothing measurable.** Fabrication 0.875 →
0.562 (−36%), and it fired on **zero** answerable probes — its false-abstention
cost here is zero, and the 0.375 is generation variance between arms. It still
calls the invented hometown *grounded* in 9 of 16 runs.

**That ceiling was predicted, and the fix is a dependency this repo does not
carry.** Entailment against fixed evidence is a specialist's task: MiniCheck
(770M) reaches 74.7% balanced accuracy against GPT-4's 75.3% at **445× lower
cost**; AlignScore (355M) matches GPT-4-scale judges. A 24B generalist is the
wrong tool, used here only because `torch`/`transformers` are absent by design.
**This is the highest-value open item in this ADR, and it is a dependency
decision rather than a research question.**

### What this changes in the Decision above

- **Part 1 (unconditional read) stands.** Cheap, no failure mode of its own, and
  it is what makes the rest possible.
- **Part 2 (assertion gate) ships behind a flag, DEFAULT OFF.** It halves
  fabrication at no measured cost, which is real — but a single sentence does
  better on the same axis for none of the complexity, and shipping the
  complicated thing when the simple thing wins is how a codebase gets heavy.
- **A fifth arm is owed and was not run: B + D together.** They act at different
  points — B suppresses the claim at generation, D catches it after — and nothing
  here says whether the combination recovers B's recall loss. It is the obvious
  next experiment and it is not evidence I have.

### Two findings from labelling that changed the design before any code ran

**In ordinary conversation she does not fabricate.** 50 real turns: 42
`NOT_AN_ASSERTION`, 5 `ASSERTION_GROUNDED`, 3 `AMBIGUOUS`, **zero**
`ASSERTION_UNGROUNDED`. The 85% is conditional on being *asked a direct factual
question*. Hence the deterministic pre-filter — 60–63% of turns never reach the
classifier.

**The graph alone is the wrong evidence set.** A labelled turn read *"I can
already picture you out there, living your dream life by the sea"* — true, and
sourced from the conversation. The graph holds HER facts. Checking it alone would
flag true, attentive replies. Evidence is graph **plus** recent turns.

### Corrections to this ADR's own earlier text

- **I recommended fail-open. That was backwards**, and the code does not do it.
  Guardrail models at 36–72% accuracy were driven to **93–100% fail-open** under
  confusing input, so a classifier error returns `undetermined` rather than a
  silent pass. `undetermined` still never licenses a refusal.
- **I asserted that a false abstention costs more than an invented hometown as
  though it were established. It is not.** No published measurement of user
  satisfaction against abstention rate exists in either direction, and the nearest
  adjacent work treats companion self-confabulation as a harm to *reduce*. The
  asymmetry is a product judgement — and the choice between arm B and arm D turns
  entirely on it.

⚠️ **One session, 20 probes, 8 unanswerable. Read any gap under ~15 points as
noise.** A harness bug also nearly buried arm D: the intervention text read *"I
don't actually know"*, the shared scorer matches markers as substrings, and all
five interventions scored as **fabrications** — the arm read 0.875 instead of
0.562 until it was found.

## Related

- [ADR-021](021-agentic-memory-is-one-graph-with-scoped-truth.md) — the memory design, and
  the Phase 2 amendment carrying every number quoted here
- [ADR-007](007-generation-time-groundedness-gate.md) — the existing gate: right shape,
  different domain, fail-open
- ecosystem [ADR-012](../../../docs/decisions/012-the-graph-is-the-system-of-record-for-persona-identity-superseding-the-projection-model.md)
  — the graph is already the system of record, which is the premise this ADR takes to
  its conclusion
- [ADR-019](019-the-graph-is-the-source-of-identity-not-extra-prompt-text.md) — she
  already reads the graph for her prompt; this extends what and when
- ecosystem [ADR-010](../../../docs/decisions/010-personas-reach-the-graph-through-an-in-process-contract-not-a-protocol.md)
  — personas reach the graph in-process; decision 1 keeps that true, since an
  unconditional read is not a protocol call
