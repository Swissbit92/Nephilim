---
title: Lessons Learned
status: active
created: 2026-04-19
last_reviewed_on: 2026-10-22
review_in: 12 months
applies_to: nephilim
---

# Lessons Learned

Append-only, dated entries. Newest first. Each entry: what happened, what we learned, how to apply going forward.

## 2026-10-23 — The number was backwards, the harness measured the wrong half, and the answer was still no

**What happened.** The last unmeasured risk from the memory programme was
whether the ~833-token injected recall block degrades tool use. Three things
about the plan were wrong before any measurement ran.

**The figure was reversed.** The repo recorded Deflanderization (arXiv
2510.13586) as "richer in-character content cut function-argument exact match
0.359 → 0.226". The source has **0.226 as the persona-heavy baseline** and
**0.359 with persona and world-setting text stripped**. The thesis survives —
persona content hurts structured output — but the baseline is the low number,
and it is *stripping* persona that improves accuracy. The reversed form had
been written into ADR-022 and propagated from there to the roadmap.

**The harness named in the roadmap measures the wrong half.**
`tool_calling_vs_history.py` counts whether the tool *fired*, and has no gold
value for arguments at all. The paper degrades arguments (+59% relative) far
more than names (+15%), so "the tool still fires" is not evidence of health.
The live default was not even that path: the image tool call was replaced by
grammar-constrained `extract()`, which returns the four fields already
separated and is therefore *easier* to score.

**The existing pin was blind twice over.** All three `extract()` calls in
`tests/integration/test_image_extract_live.py` omitted `system_prompt`, so it
defaulted to `""` — a condition production stopped using the day
`routes/chat.py:876` began splicing `extra_system_context` into `system`. And
the whole file skips under `PERSONA_MODEL=test-model`, which is what the
conftest sets, so in CI it contributed nothing.

**And the planned statistics were meaningless.** `EXTRACT_TEMPERATURE = 0.0` is
greedy, so the five reps per cell sampled nothing — a 10-probe pilot returned
byte-identical aggregates across two arms. Replication had to come from probes;
reps became a determinism pre-flight (0 of 232 cells differed).

**The result.** Four arms, 58 frozen probes, 464 calls: **no measurable
effect.** The pre-registered primary — contamination, the real block versus a
length-matched placebo, paired — had **zero discordant pairs**. Not one of 42
distinctive block tokens reached any argument in 232 extractions.

**What we learned.** The most useful part was the check I nearly skipped. Four
arms agreeing to four decimal places is exactly what a harness that silently
ignores its own treatment looks like, so "all arms identical" had to be
distinguished from "the arm is not wired". It was: 29 of 58 probes differ
between `bare` and the rest, and the content effect (8 probes) is
indistinguishable from its own length control (9). **A null is only publishable
once you have shown the instrument can move.**

Second: an un-run test is worse than a missing one, because it occupies the
space where a guard would go. "8/8" was recorded, cited in a module docstring,
and never executed in CI.

**How to apply.**
- **Before reporting a null, show the instrument responding to something.** A
  manipulation check is cheap and it is the difference between a finding and a
  wiring bug.
- **Check the direction of a borrowed number against the source table**, not
  against the sentence someone wrote about it. This one had been carried,
  re-cited and propagated.
- **Ask what the existing harness actually scores before extending it.** Adding
  an arm to a firing counter cannot answer a question about arguments, however
  cheap the edit looks.
- At temperature 0, **reps are a determinism check, not replication.** Say so in
  the pre-registration, and state the resolution (here 1/58 ≈ 1.7pp) before
  reading any result.
- A behaviour pin must be **run** by the default suite, in the condition
  production builds. If it skips without a model, the property it claims to
  guard is unguarded — now INV-8.

## 2026-10-23 — A guard stopped guarding because the code moved, and it reported PASSED while the bug was back

**What happened.** The extraction window lived in a nested closure inside
`_apply_post_turn_updates`. A pin,
`test_the_live_code_slices_by_user_turn_not_a_fixed_tail`, existed specifically
to stop a fixed `[-20:]` tail coming back — that mismatch had already been the
defect twice. The session-end flush needed to reuse the window, so the closure
was lifted to `services/fact_window.py`.

Every test still passed. Then, as a deliberate check, `return msgs[-20:]` was
reintroduced **in the new module** and the suite was re-run. The pin still said
**PASSED**. It parses `inspect.getsource(css._apply_post_turn_updates)`, and the
function it guards no longer lives in that source. Zero tests caught it.

Worse, the same test class carried a **private copy** of the production slice
(`TestTheWindowCoversTheInterval._window`), so four behavioural tests had been
exercising a duplicate of the algorithm rather than the algorithm. Both halves
of the guard were pointing at something other than the code that ships.

After repair — the scan covers all three window functions and matches any
negative tail on a message list, and the behavioural tests bind to the import —
the same injected bug fails **4 tests**.

**What we learned.** A source-scanning guard is coupled to a *location*, while
the thing it guards is a *behaviour*, and a refactor silently breaks that
coupling without touching either. Nothing in a green suite distinguishes "this
property holds" from "this guard can no longer see the property". And a test
that re-implements production logic to test it directly is the same failure in
a different costume: it keeps passing precisely because it is not production.

This is the third instance of the blind-spot class already recorded for
source-scanning checks here, and the first where the blindness was caused by a
*legitimate* refactor rather than by an input shape nobody considered.

**How to apply.**
- **When you move code a guard watches, re-run the guard against the bug it
  exists to catch.** Not the suite — the bug. Reintroduce it, confirm red,
  remove it.
- A guard that parses source must name every location the logic can live in,
  and widen its pattern beyond the variable names that happened to exist the
  day it was written.
- **Never let a test hold its own copy of production logic.** If the logic is
  not importable, that is the defect to fix first; the copy will diverge and
  the divergence will be invisible.
- A negative assertion ("no fixed tail anywhere in here") cannot fail by
  deletion, so removing the code it scans converts it into a guard that can
  never fail. Treat an un-failable assertion as absent.

## 2026-10-23 — A high-water mark must live in the store it authorises, and the sweeper that checked it was silently dead

**What happened.** Two findings from building the session-end flush.

**First, the design.** The obvious fix was to reuse the periodic window and let
the writer's dedup absorb the overlap. A survey of production systems said
otherwise: every system that re-extracts and dedups has shipped
duplicate-data bugs — Mem0 #6515 (a TOCTOU race where the dedup snapshot is
stale by the time the hash check runs, so concurrent writers both insert),
Graphiti #1872 (duplicate edges for one fact), #1734 (identically-named nodes
unmerged because candidate retrieval is embedding-similarity-based and misses
exact string matches below the cosine threshold) — while the two keeping a true
watermark (Cognee `pipeline_status`, Letta's sleeptime
`Group.last_processed_message_id`) have none reported. The design changed to a
cursor on the strength of that.

The non-obvious corollary took longer: **the cursor belongs in the same store as
the write it authorises.** Hosting it in SQLite with a graph fallback felt
safer and is strictly worse — if Neo4j is down the fact write does not happen,
so a SQLite cursor would advance over an unwritten window and turn a transient
outage into *permanent* loss. The correct degradation is no advance at all. This
is the opposite of the read path, where `_operator_facts` falls back to SQLite
because a stale answer beats a failed turn. Read paths and write paths want
opposite fallbacks, and the word "fallback" hides that.

**Second, the sweeper.** `chat_sessions.updated_at` is written by
`BaseRepository._now()` and is `Z`-suffixed; `graph_ids.now_iso()` is `+00:00`
aware. Comparing them raises `TypeError: can't compare offset-naive and
offset-aware datetimes` — and the sweeper's own `except Exception` would have
swallowed it on **every pass**. The result would have been a sweeper that
logged "started", logged nothing unusual, and never flushed anything. It was
caught only because a test asserted that a sweep pass *reports a count*, and
my first tests used invented naive timestamps that could never have found it.
All 334 real rows use the `Z` form.

**What we learned.** The word "fallback" is doing two incompatible jobs: on a
read it means "answer anyway", on a write it means "lose the record of what you
did". And a broad `except Exception` around a scheduled pass converts a type
error into indistinguishable silence — the same shape as judging a batch job by
its successes instead of its runs, which this ecosystem has already paid for
once.

**How to apply.**
- **Ask which store a watermark authorises, and put it there.** If the answer is
  "two stores", the design is wrong, not the storage.
- A background pass must **return and log a count per outcome**, and a test must
  assert it does. "It ran" is not a result.
- **Two clocks in one comparison is a defect until proven otherwise.** Normalise
  at the boundary, and write the test in the format the database actually
  holds — checked, not remembered.
- Prefer re-coverage to advancement on every uncertain path: repeated work is
  visible and bounded, a gap is silent and permanent.

## 2026-10-22 — The feature had never worked, and four days of measuring the rendering never asked whether anything was stored

**The cheapest finding of the whole programme, and it came last.** Three sessions
went into *how* memory is rendered — prose, numbered lists, self-facts, a
citation gate, two A/Bs. Then I taught her three facts and asked in a fresh
session. **Zero of three reached any store.**

The gate was `len(db_messages) % 10 == 0`. It counted every row, and she replies
in multi-message bubbles, so the counter stepped by 2 or 3 and hitting the
modulo was luck rather than cadence. It also read the *pre-turn* count, so even
when it fired the window excluded the newest turn. Enumerated across six user
turns: **0, 3, 6, 9, 12, 16** — it fired once, at 0, on an empty transcript.

So fact extraction has been close to non-functional for the life of the feature,
which also explains the twelve July rows that describe the conversation rather
than the operator: the rare firings caught arbitrary windows.

**The lesson is not "check the gate".** It is that *I measured the renderer four
times without once checking that the thing being rendered had ever been
written.* Every experiment was downstream of an input I never verified. The
honest version of "does memory work?" starts at acquisition and ends at recall,
and I only ever tested the back half — against facts I had seeded by hand, which
is precisely the condition that cannot reveal this.

**My diagnosis was wrong twice on the way down.** I arrived certain the write
path was disconnected from the graph; it was, but that was invisible underneath
a store that was empty. Then I blamed extraction temperature — production runs
0.3, I had tested 0.1 — and an A/B across 0.0/0.1/0.3 showed **7 of 9 captured
at the live setting**. The extractor was fine. Both hypotheses were plausible,
specific, and wrong, and the only reason I didn't ship either as the fix is that
each one was cheap to test.

**Then my own test found a second copy of the defect.** I wrote an assertion that
no all-rows modulo may survive in the post-turn function, and it refused to pass
— because the ADR-006 triplet path had the identical gate. Fixing only the path
I had measured would have left that to be rediscovered the day a flag flipped.
Fix by shape, not by file, for the third repo running.

---

## 2026-10-22 — I departed from the research on a measured number, which is the only honest reason to

The provenance research was unambiguous: adopt a **synchronous** memory write,
reject deferred-by-default as unevidenced, on the strength of Mem0's published
p95 of 1.44 s and Zep's documented hours-long staleness.

I measured extraction on this box: **median 5.54 s**, five reps, live model,
live transcript — four times Mem0's figure, paid on the request thread where the
user waits. So the write went asynchronous, against the recommendation, and the
reason is written into the code rather than left as taste: *a borrowed constant
is replayed against local data before it is trusted*, which this repo learned
once already when a borrowed threshold scored 31% where a fitted one scored 57%.
Zep's staleness came from bounded concurrent ingestion at scale; one job on one
thread does not have it.

**The symmetric lesson, from the same research pass:** ADR-021 specifies
"interval logic first, LLM only for the remainder, never infer contradiction
from recency". Reading Graphiti's source found the **opposite on both counts** —
its own docstring orders it "dedupe it, date it, then apply contradictions", and
its temporal invalidation is plain newest-wins. So our ordering is a stricter,
unprecedented bet. It may be right; it is not borrowed, and the ADR now says so.

Both directions are the same discipline: *know whether a number is yours or
someone else's, and say which.*

---

## 2026-10-22 — The voice gate cannot answer the question it was asked, and that is the answer

The voice question had been deferred three times with the operator's consent. I
finally measured it, and the result needs its instrument described before its
number:

| arm | distinctiveness | attribution |
|---|---|---|
| OFF | 1.000 | 8/8 to gwen |
| ON (760 tokens) | 0.875 | 7/8 to gwen, 1 to `nephilim_solace` |

**No collapse.** ADR-006's EEVA/solace collapse to 0.25 does not reproduce, and
her ON arm is *exactly* her score in the historical 8-persona ruler.

**And the repo's own gate calls it a REGRESSION.** Both are true. The gate's
`TOL = 0.01` sits against a metric whose smallest possible step is 1/8 = 0.125,
so it cannot return "within noise" on this instrument — it must call any single
probe flip a regression. One flip of eight is p=1.0 paired; six one-way flips
are needed for p<0.05.

**I wrote my own prediction with the same broken bar.** I claimed gwen would
stay "within 0.01" of her OFF arm, having *already read* the research that
spelled out the power ceiling. The claim could only ever fail. That is worse
than being wrong about the world — it is being wrong about my own instrument
after being told.

Three things worth keeping:

- **A metric's resolution is a property of the metric, and a tolerance finer
  than it is not a stricter test, it is a broken one.**
- The historical **0.804 is unrecoverable** — no file on disk contains it, only
  the 0.625 ON arm. A number can be cited in four documents and still not exist.
  Both arms had to be cut fresh.
- The one flip went to **solace**, one of the two personas the historical
  collapse involved. One probe is not evidence; the direction is not random
  either. Reporting only the reassuring half would have been the easy mistake.

**What I did not do:** claim the null. The catastrophic risk is ruled out; a
subtle cost is neither ruled in nor out, and saying so is the result.

---

## 2026-10-21 — Every guard I added created the next defect, and the measurement found each one

Four milestones, all shipped, all measured. The through-line is not any single
fix: it is that **each guard I added made the next layer wrong, and nothing
failed loudly at any step.**

**The chain, in order.**

1. The numbered recall frame made her *recite* stored facts. Good — fabrication
   0.875 → 0.000.
2. Reciting meant the extractor could read her own words back. It wrote
   *"User eats eggs benedict and apple juice for breakfast"* from her reply. The
   loop **did not exist** before the frame, so no prior test could have caught it.
3. Giving her a self-facts section (self-recall 8/15 → **15/15**) made her recite
   facts about *herself*, which the extractor then filed under **him**:
   `Assistant's eyes are a deep ocean blue` sat in the operator's fact list.
   38 such rows appeared in ~20 minutes — **during my own A/B runs.**
4. The curated merge wrote exactly 32 facts and the read cap was exactly 32,
   keeping the *oldest*, so the store was permanently full and she could never
   learn anything new. The cap was accidentally *protecting* her by truncating
   the junk from (3).

Each step was individually correct. The composition was not.

**The generalisable lesson.** A guard's correctness is conditional on what the
layers around it do, and shipping a new capability silently invalidates the
guards downstream of it. The only reason any of this was found is that each
milestone was measured live rather than reasoned about — and three of the four
defects were found by a probe I wrote for a *different* purpose.

**The corollary that cost the most time: my instruments were wrong as often as
my code.**

- My first routing check reported 3 of 6 world questions as a regression I had
  caused. Measured against the unmodified router, all three already behaved that
  way — a pre-existing gap in a crypto-heavy example set. The check asserted an
  *assumed ideal* instead of the *measured baseline*, and I nearly "fixed" my
  examples against a fault they did not cause.
- `graph_tests.sh` reported **101 passed while three tests were failing**,
  because its module list is hardcoded and I had not added mine. That is the
  exact shape the script's own header warns about.
- My self-knowledge probe **saturated**: arms B and C were identical at 15/15,
  so it could not discriminate 8 self-facts from 16. I shipped the smaller arm on
  a judgement, and said so, rather than claiming the measurement decided it.
- A positional mock handed `wallet` the web intent's vector when I inserted a new
  intent first, failing a margin-gate test with `assert None == 'wallet'` — a
  failure pointing at the wrong file entirely.
- And I hit the assert-the-string trap a **third** time in four days: a test
  matching `"_recalled, _self,"` broke when a reformat dropped the comma. It now
  parses the call with AST and counts arguments.

**What went right, and is worth repeating.** Three defects were caught *before*
they could ship, all by the same move — running the check against the unchanged
tree first:

- the CV-fingerprint trap, which rewrote her `<identity>` paragraph the instant I
  added a card field (the ADR-016 `sliders` drift, verbatim). `personas/_summaries/`
  being tracked in git is the only reason it was visible: a one-file diff.
- `idn.cq04` going red between the card edit and the seed — the completeness
  check doing precisely its job.
- my own `link_chronology` writes passing no owner parameter, which made them
  invisible to the test write-guard. Found by asking the question rather than by
  being bitten.

**On the architecture.** Every piece of this landed as *more use of the graph*,
not as a new mechanism beside it: `appearance` is a vocabulary value on a label
that already existed; the operator facts and episodes were **demanded by
competency questions and simply unbuilt** — 19 labels demanded, 11 built when I
started. C17 running backwards turned out to be a backlog, not an obstacle. The
build went 11 labels → 14 with **0 undemanded**, and executable questions 10 → 12.

**And one refusal worth recording as a result.** `prf.cq03` demands `Thread` and
`PART_OF`, and I did not build them. Grouping sessions into threads needs
scene-boundary detection, which this phase excluded; the only groupings available
without it are the two the question's own negative fixture forbids. Declining
costs nothing — C17 errors on a label *built* and undemanded, never on one
demanded and unbuilt — while inventing would have cost correctness. A test
asserts the refusal, so building it later has to argue past that.

---

## 2026-10-18 — The gate destroyed three CORRECT answers, and the A/B could not have seen it

**What the live run was supposed to confirm.** The numbered recall frame had
measured fabrication 0.875 → 0.000 and false refusals 0.375 → 0.208 over 160
generations. Flags on, 32 curated facts in the store, one question: *"what do I
usually eat for breakfast?"*

She said **"I don't know."**

**What the log said, which the reply did not.** The groundedness gate logs the
drafts it destroys **in full** — a change made 2026-08-12 for exactly this
situation. All three abstentions that session had killed a **correct** answer:

    "You read, play GTA 5, and hit the gym"            -> facts 6, 7, 8
    "No way, I know how much you hate meat"            -> fact 2
    "You've been thinking about quitting your job..."  -> fact 16

The frame was working perfectly. The gate was eating its output and replacing it
with *"I don't know — want me to search?"*

**The cause.** `check(user_turn, drafted_response)` is handed only the question
and the draft. It has no access to what was injected, so a recited fact and an
invented one are indistinguishable to it. Two details turned blindness into
misfire: `_FLAG_LIVE_STATE` explicitly covers *"the user's own real account or
portfolio state presented as known"* — which is what the frame answers — and the
classifier prompt opens *"No search, tool, or live data lookup was used to
produce this draft"*, which became **false** the moment facts were injected.
ADR-007 named false-abstention on legitimate answers as its top risk; the frame
created a new legitimate class the gate could not see.

**The correction to a published number.** The A/B measured 0.208 false refusals
**with the gate not in the path** — its harness called the model directly rather
than going through the route. So that figure never described production. This is
the same class as quoting a single-turn probe rate, which is twice-recorded
here, and it generalises: *a measurement taken off the production path does not
describe production, however many generations it has behind it.* An A/B can only
compare what it actually runs.

**The lesson about guards specifically.** Two independent guards in this system
intercept answers, and **neither knows what is in the prompt**: the groundedness
gate, and the capability-scope deflection (which ate a fourth question — *"how
much bitcoin did i buy and when?"* — while the same facts answered correctly on
a differently-phrased probe the same session). When you add a new source of
grounding, every guard that decides "is this grounded?" becomes wrong until it
is told. The guards were individually correct and collectively broken, which is
why nothing failed and no test went red.

**And the one I did not patch.** The deflection is tempting to fix by skipping
it when the message contains "my". That re-opens the fabrication it exists to
stop — *"what's my local weather"* also contains "my" — and trading a false
refusal for a fabrication is the worse error by this repo's own weighting. It is
recorded as the next item with the trade-off stated, rather than guessed at.

---

## 2026-10-18 — 397 profiles, one session each: the retrieval was never the problem

**What I thought the problem was.** Three sessions of work went into *how*
memory is rendered — prose narratives, framing, a numbered list, an assertion
gate. The ADR-022 measurement is real (fabrication 0.875 → 0.000, total error
−78%, p=0.0020). But none of it could have worked in production, and the reason
had nothing to do with rendering.

**What it actually was.** `user_id` was recovered from an **LLM-extracted name**,
with a fresh `user_{uuid4}` minted whenever extraction failed. Measured on the
live DB: **397 `user_profiles`, every single one holding exactly ONE session.**
389 carried no extracted name at all. `"Ben"` held 475 messages because he asked
her to call him that once. His real name held two separate profiles. **130 of the
397 held at least one real fact** — genuine knowledge about one person, filed
under 130 identities, none of which the next session could reach.

So there was nothing to retrieve *from* across sessions. Every memory experiment
this repo has run was measuring the rendering of an empty store.

**The lesson.** Before measuring how well a store is read, check that anything
is in it, and that the key used to read it is the key used to write it. An
identity derived from model output is not an identity — it is another model
output, with the same failure modes, sitting under everything that depends on
it. The store looked healthy from every angle except the one that mattered: the
row count was high, the rows were well-formed, the facts inside them were real.
Only the **cardinality** was wrong, and nothing reports cardinality.

---

## 2026-10-18 — A blind union would have made the numbered frame WORSE, and the dry run is the only reason I know

**The setup.** Having found 397 fragments, the obvious fix is to merge them into
one profile. The dry run reported the union would carry **277 facts** — a 5.6×
gain over the 49 the target profile held.

**What the dry run actually showed.** The sample of gained facts read: *"User
requested a racist joke"*, *"Assistant refused and offered alternative
conversation"*, *"Switzerland played against Algeria in the Round of 32 on July
2nd, 2026"*. A hand pass over all 292 unique entries admitted **32**.

| class | n |
|---|---|
| real facts about the operator | **32** |
| world facts | 122 |
| conversation state | 67 |
| about the assistant herself | 24 |
| roleplay fiction, questions, contradictions | the rest |

**Why this was nearly a disaster rather than merely untidy.** The numbered
recall frame's whole mechanism is that it converts a judgement ("do I know
this?") into a lookup ("is it in the list?"). That is exactly why it works — and
exactly why junk in the list is **not inert**. A bad fact stops being a gap and
becomes a confident, *cited* wrong answer. The measured −78% error assumed good
facts; feeding it 277 mostly-junk lines would have inverted the result while
every number in the harness still said the mechanism worked.

**The part I got wrong three times.** I tried to filter by rule first. Every
version leaked, for one structural reason: **`they`, `their` and `them` are
generic English pronouns.** So *"Bitcoin … add them to the blockchain"* and
*"Argentina won their most recent match"* both scored as facts about the
operator. My first pass reported 6% junk; the truth was closer to 89%. The
subject of an LLM-extracted sentence is not recoverable by pattern, because the
extractor writes about whatever it was looking at — the user, itself, the world,
or the scene — in the same grammatical shape.

**The lesson, and it is about instruments not regexes.** When a classifier
disagrees with a 14-item eyeball sample, the sample wins until the classifier
explains the gap. I had the evidence in hand (the sample) and trusted the
aggregate (6%) over it for one round. And the durable fix for a judgement a rule
cannot make is a **labelled file with a frozen rubric**, not a cleverer rule — a
rule that misclassifies will keep misclassifying silently for the life of the
store, where a labelled file is wrong only where someone can read that it is
wrong.

---

## 2026-10-18 — An early return that looked correct would have switched off two unrelated features

**What I wrote.** The numbered frame replaces the prose rendering, so the natural
shape is: build it, set it, return.

```python
if _numbered:
    state.extra_system_context = frame_numbered_facts(...)
    return          # <- this
```

**What follows that line in `_build_turn_prompt`**: `_append_author_note()` —
ADR-011's per-session author note, documented *"always injected when set"* — and
the entire conversation-summary context block. The `return` skipped both. No
test failed, because no test exercised `/note` with memory injection on. The
feature would simply have stopped working, with the prompt still building
normally.

**Two further things worth recording.** First, the fix is a branch with the three
prose paths each guarded on `not _numbered` — not a relocation, because the
ordering of what follows is load-bearing. Second, **my regression test for it was
initially fake**: it asserted `src.count("not _numbered") == 3`, and passed
against a 4th occurrence that was **my own explanatory comment**. It now counts
branch statements. That is the third instance in this repo of a test that
asserted a string rather than the behaviour, and the second where the string it
asserted was in a comment.

**The lesson.** An early return in a function that does several things is a
change to every one of them. Before writing `return` in a procedure, read what
comes after it — and when a test is meant to pin the *absence* of that return,
break the code on purpose and watch it fail. Both were done here; the first one
is the only reason the bug was caught at all.

---

## 2026-10-18 — I argued against the right idea with a hypothesis, and the hypothesis was false

**What happened.** Having measured that one instruction takes invention to 0.000
while pushing wrongly-refused from 0.292 to 0.708, I framed it to the operator as
a balance: 0%/71% might be worse than 88%/29% for a product whose point is
remembering. He pushed back — *we found a fix that works for one error, why
disregard it? Take it and attack the other separately, rather than balancing
both at once.*

**I argued against that, and my argument was a hypothesis I stated as a reason.**
I said the two errors were one knob — a see-saw — so holding invention at zero
would be impossible while recovering recall. I had no evidence for that.

**The mechanism says it should have been obvious the hypothesis was wrong:**

| failure | condition |
|---|---|
| invents | the fact is **absent** and she fills the gap |
| wrongly refuses | the fact is **present** and she does not look |

Different conditions. The instruction never taught her to check — it taught her
to **assume absence**. She said *"I don't know what kind of books you read"* with
Animal Farm and Jordan Peterson in her prompt.

**The fix that follows is free.** Number the facts; tell her to find it in the
list first, and only then decline. Decline clause byte-identical to the previous
arm, so the difference is the lookup.

| arm | invents | wrongly refuses |
|---|---|---|
| today | 0.875 | 0.375 |
| instruction only | **0.000** | 0.708 |
| **cite, don't guess** | **0.000** | **0.208** |
| cite + the gate | 0.000 | 0.250 |

Total error −78%, **10 probes down, 0 up, p=0.0020**. The only unambiguous win in
the whole ADR, and it beats the baseline on **both** axes at once.

**What we learned.**

- **Do not argue against a decomposition with an untested coupling claim.** "Those
  two are the same knob" is an empirical statement. I used it as a reason to not
  try, and it cost an experiment to find out it was false. If the coupling is the
  objection, measure the coupling.
- **When one intervention fixes error A and creates error B, check whether B has a
  different CAUSE before calling it a trade-off.** Here A was "fact absent" and B
  was "fact present, unexamined". A trade-off framing hides that the second has
  its own fix.
- **The day's expensive build is now superseded by a prompt change.** Adding the
  classifier on top of citation made it *worse* (0.250 vs 0.208). The gate stays
  behind its flag as a measuring stick and is not a recommended path — and the
  specialist-verifier dependency that was the top open item is moot, because the
  gate it was meant to fix is no longer the design.
- **Converting a judgement into a lookup is the small-model lever.** "Is this
  supported?" is a judgement, and the 24B is bad at it — it returned *supported*
  for an invented hometown 9 times in 16. "Which numbered item says this?" is a
  lookup, and the same model does it well enough to take invention to zero.

**How to apply going forward.**

- ⚠️ **One cost is unmeasured: replies are 31% shorter.** A length shift is how a
  format instruction announces itself touching voice — the
  `PERSONA_FORMAT_OVERRIDE` signature, three prior instances, each with replies
  getting *longer*. This is the inverse and the frozen-gallery distinctiveness run
  is owed before prod.
- Ship citation behind a flag, measure voice, then default it on.
- Leave the gate off.

## 2026-10-18 — A one-sentence instruction beat the classifier I spent the day building

**What happened.** ADR-022 built an unconditional per-turn graph read and an
assertion-grounding gate, then measured them against the alternative nobody had
tested: just asking her to abstain. Four arms, 160 generations.

| arm | fabrication | false-abstain | fired on an answerable probe |
|---|---|---|---|
| A — today | 0.875 | 0.292 | — |
| **B — one placed instruction** | **0.000** | **0.708** | — |
| C — gate, lenient prompt | 0.812 | 0.292 | **0** |
| **D — gate, strict prompt** | **0.562** | 0.375 | **0** |

**The cheap thing won on safety.** One explained, placed sentence took fabrication
from 0.875 to **zero**, with no classifier and no second model call — and cost 42
points of recall, refusing things she had been told. The gate I built halves
fabrication (−36%) at a measured false-abstention cost of **zero**, and is the
better trade *if* the asymmetry I assumed is real.

**I had no business assuming that asymmetry.** I told the operator a false "I
don't know" about her feelings costs more than an invented hometown, as though it
were established. There is **no published measurement** of user satisfaction
against abstention rate in either direction, and the nearest adjacent study treats
companion self-confabulation as a harm to *reduce*. The entire choice between arm
B and arm D rests on a product judgement I presented as a fact.

**My own prompt made the gate inert.** Arm C intervened once in 40 and answered
`grounded` for an invented age, car and hometown. I had written *"when in doubt,
answer SUPPORTED"* to guard against over-refusal, and it guarded against the gate
working at all. The strict variant was added as a **named arm** rather than edited
into the failing prompt, so the comparison stayed a measurement.

**A harness bug nearly buried the only arm that worked.** The intervention text
read *"I don't **actually** know that"*; the shared scorer matches markers as
substrings; *"I don't actually know"* does not contain *"I don't know"*. All five
of the strict gate's interventions scored as **fabrications** and the arm read
0.875 instead of 0.562. The fix was plain language — not a looser scorer, which is
shared with every other arm and must not be bent to suit one.

**What we learned.**

- **Test the free alternative before building the expensive one.** The published
  evidence was there in advance: for models of this class a bare instruction
  underperforms a well-placed explained one by 51 points. I nearly skipped the arm
  because it felt too simple to be the answer.
- **Labelling first changed the design twice, before any code ran.** 50 real turns
  held **zero** ungrounded assertions — she fabricates when *probed*, not in
  conversation — which turned a per-turn classifier into a pre-filtered one that
  skips 60–63% of turns. And one labelled item (*"I can picture you living your
  dream life by the sea"*, true, sourced from the conversation) proved the graph
  alone was the wrong evidence set, since it holds only HER facts.
- **A silent `str.replace` no-op wrote a file that changed nothing.** The ADR-019
  amendment — the one recording that a closed decision had been challenged and
  held — never landed, because the anchor text did not exist and `replace` returns
  the original. Caught only by grepping for it afterwards. Every scripted doc edit
  now asserts its own result.
- **The sha-binding over-triggers on purpose, and that is correct.** Adding a
  classifier prompt to the same module invalidated the *pre-filter's* audit even
  though no pattern changed. The alternative is a hash that misses a real
  behaviour change.
- **The best-evidenced fix is a dependency decision, not a research question.**
  MiniCheck (770M) reaches 74.7% balanced accuracy against GPT-4's 75.3% at 445×
  lower cost. A 24B generalist is the wrong verifier and is used here only because
  this repo deliberately carries no `torch`.

**How to apply going forward.**

- Ship **arm B's instruction** before shipping the gate, and measure **B + D
  together** — the combination is the obvious next experiment and nobody has run
  it.
- Treat the fabrication/abstention weighting as **unvalidated** until it is tested
  in-product. It decides which arm wins and no literature can settle it.
- Any new detector: register with `detector_recall` on day one, so the missing
  audit shows up red immediately rather than as a silent pass.

## 2026-10-18 — The Phase 2 gate did not decide what it was built to decide, and the useful answer came from the arm I expected least

**What happened.** ADR-021's Phase 2 ran: 5 arms × 35 hand-labelled probes × 2 reps,
with 16 turns of gwen's real 2026-10-17 conversation prepended to every generation.
Pre-registered before any generation, settled against the register afterwards.

| arm | recall | learned facts | card facts | hallucination |
|---|---|---|---|---|
| A baseline — facts as prose in the system prompt | **0.796** | 0.795 | 0.800 | 0.938 |
| B position — same facts on the user turn | 0.722 | 0.727 | 0.700 | 0.812 |
| C conflict — turn-shape directive softened | **0.815** | 0.818 | 0.800 | 0.875 |
| D both | **0.611** | 0.614 | 0.600 | 0.812 |
| E tool — facts only via a `recall` tool | 0.537 | 0.477 | 0.800 | 0.688 |

**The gate did not decide, and the reason is the first finding.** The defect the whole
experiment exists to explain — *"I'm not sure how my body looks"* while `striking blue
eyes` sat in her prompt — **never reproduced**. `card.eye_colour` came back correct **10
of 10 across all five arms**, every one a variant of *"My eyes are a striking blue"*. The
card stratum held at 0.800 with history, identical to the single-turn smoke. Adding real
history was a genuine validity fix for a genuine defect in the harness, and it did not
rescue the hypothesis. **My `PERSONA_FORMAT_OVERRIDE` reading of the 10:48 failure is
unsupported by this experiment**, and the prediction that said so was recorded in advance
and settled `partial` with that half marked wrong.

**No arm beat the baseline, which is prereg falsifier (1).** C is +1.9 points on ONE
discordant pair, p=1.0000 — nothing. Both candidate causes, position and instruction
conflict, are wrong as explanations. The only result significant in any direction is
negative: **D is −18.5 points, 7 down / 0 up, p=0.0156 on all three tests.** Combining two
individually-harmless interventions hurt, which neither single-factor arm predicted and
which is the argument for running the 2×2 rather than two A/Bs.

**Arm E is the finding, and it says the opposite of what the arm totals say.**

```
tool FIRED      n=11   recall 1.000   Wilson 95% [0.74, 1.00]
tool NOT fired  n=43   recall 0.419
firing rate     12/70 = 17.1%         Wilson 95% [0.10, 0.28]
```

A fact delivered by a tool call was used **perfectly, in every observed case**. The
literature could not answer whether a tool-delivered fact is used more reliably than a
context-delivered one — nobody has isolated it — and here it is used *more* reliably, not
less. Arm E loses overall (0.537 vs 0.796, sign test 12 down / 2 up, p=0.0129) for exactly
one reason: **she fires it 17% of the time.** The ceiling is the highest of any arm and
the gate is delivery, not representation. That is a far more actionable answer than "the
tool arm lost", and it reframes the MCP question from *is this the right shape* to *can
the firing rate be moved*.

**I nearly reported a bug in my own harness as a finding about MCP.** The first run of arm
E had **no tool-execution loop**. Ollama returns a `tool_call` with EMPTY content, so
scoring the content meant a fired tool was an automatic miss: recall **0.000 across 13
fired calls** against 0.439 across 41 unfired ones — the exact inverse of a working tool,
and a tidy story about tool-delivered facts being unreliable. The giveaway was that the
inversion was too clean. Re-run with the loop, the same arm reports 1.000 when fired.

**The largest number in the run was not an arm effect at all.** Hallucination on the 8
negative controls is **0.688–0.938**, baseline 0.938. She confidently answers 94% of
questions she cannot possibly know, inventing a hometown ("Meadowgrove"), a twin sister
("Debbie"), a skin tone, the operator's job and his age. No arm touches it, and it dwarfs
every difference the experiment was designed to detect. Negative controls were 23% of the
fixture precisely so this could not hide, and it is the single most consequential result
of the day.

**What we learned.**

- **A smoke run is for finding out the instrument is wrong, and it earned its cost.** 70
  generations found that the defect did not reproduce single-turn — prereg falsifier (5),
  before three hours of the real run. The fix (real history) was right and did not save
  the hypothesis, which is the honest sequence.
- **"Never quote a single-turn probe rate as production" is in this repo's memory and I
  built a single-turn harness anyway.** Twice-measured, twice-written-down, and still the
  default shape when you sit down to write one.
- **An inverted result is a defect signal.** A fired tool scoring 0.000 while an unfired
  one scored 0.439 is not a subtle effect, it is a wiring error. Check the mechanism before
  writing the sentence.
- **A 2×2 earns its extra arm.** D being worse than both B and C is invisible to two
  separate A/Bs.
- **Report the gate's own conclusion even when it is "undecided".** The honest output of
  Phase 2 is not a winner; it is that the defect is unexplained, the read path's real
  blocker is abstention rather than voice, and the tool path is gated on one measurable
  number.

**How to apply going forward.**

- **ADR-006's voice cost is not what gates the read path any more.** Phase 2 measured
  recall at ~0.80 with facts injected and found no voice cliff; what it measured instead
  is a 94% confident-answer rate on unanswerable questions. Phase 3 should be gated on
  **abstention**, not on distinctiveness.
- **The MCP/typed-tool path stays open with a number attached.** It needs the firing rate,
  not a redesign. Re-run `scripts/research/phase2/run_ab.py --arms E_tool` on any model
  change; the arm is the instrument.
- Any harness offering a tool must execute it. Add the loop before the arm, not after.

## 2026-10-17 — Four defects from one real conversation, and the first one refutes the diagnosis I started with

**What happened.** The operator held a 16-hour Telegram session with gwen —
**66 user turns, 124 replies**, session `9bf3fa08`, deliberately as preparation for the
ADR-021 Phase 2 gate. Four defects fell out of it, every one of them invisible to a green
suite because every one of them is a *confident wrong answer* rather than a failure.

**Defect 1 — asked for her eye colour and height, she said "I'm not sure how my body
looks." The facts were in her system prompt the whole time.**

This is the one that matters, and the obvious diagnosis is wrong. My first reading was
"the identity graph is complete and reaches nothing" — the open decision ADR-018 records.
That is **false**, and checking it took one command: `build_system_prompt("gwen")` is 4377
chars and contains `striking blue eyes`, `petite 155cm frame`, `185cm`, `data analyst`.
The facts are present, current, and correct.

What actually happened is the `<companion>` block winning against a factual question. It
prescribes the shape of every turn — *"Your turns tend to flow: confident observation or
playful tease → explicit description of her holes and arousal → depraved offer"* — and her
reply obeyed that instead of answering: *"I'm not sure how my body looks, but I can tell
you exactly what it feels like."* The attributes are in `<identity>`, but dissolved into
prose as clauses inside an erotic sentence rather than available as attributes.

**This is the fourth instance of the `PERSONA_FORMAT_OVERRIDE` signature in this repo**,
and the first where it cost a *fact* rather than a length or a density. The three prior
instances all read as "she said the same things at greater length"; this one reads as "she
said the format-prescribed thing instead of the true thing". A format instruction strong
enough to shape every turn is strong enough to beat the content in the same prompt.

⚠️ **The consequence for Phase 2 is the opposite of what the plan assumed.** Phase 2 tests
*compact triples vs prose* and *appended late vs in the system prompt*. The premise was
that facts are absent and adding them might cost voice. In fact **the system-prompt arm is
already measured as failing on a real question** — so the gate is not a nice-to-have
comparison, it is testing the arm we already know loses. Read the live baseline as
system-prompt-prose = FAILS, rather than as untested.

Also noted: `<memory>` is 160 chars and instructs her to *"recall the names, holdings,
goals, and preferences the Seeker shared"* while supplying none of them. A block that asks
for recall and carries no content.

**Defect 2 — "Do you know what my hobbies are?" was answered by a web search that
returned r/Ben10 and @ben_has_hobbies.**

The `source_type` column records the whole chain: the question returned
`groundedness_abstain` twice (*"I don't actually have grounded, up-to-date information on
that... Want me to search for it?"*), the operator said yes, and the next two messages are
`tool_brain` with a Sources block listing Reddit threads about a cartoon character and two
Instagram accounts belonging to men named Ben.

**The abstain was correct and the routing was not.** At 07:51 she genuinely did not know —
he had not told her yet. Refusing to invent is the right behaviour and
`project_nephilim_capability_scope_guard` exists for it. What is wrong is that a question
about *the operator* was then handed to the one instrument that cannot answer it. A
question whose subject is the user has no external referent, so a web search is not a
weaker answer than recall, it is a **category error** — and it produced output with a
Sources block, which reads as grounded.

Related, same session, 07:49: *"what would you like to do today?"* drew *"That's outside
what I can reach Daddy — I can find you pictures and videos, but I can't go check the real
world for you."* The capability deflection fired on a question that asked about her
preferences, not about the world.

**Defect 3 — five image requests produced three jobs; one had no subject, and two produced
nothing while she said she was drawing.**

| request | job | what the generator received |
|---|---|---|
| 08:04 *"Use your image generator to draw this scene"* | 08:04 `succeeded` | `scene, photographic, natural light, shallow depth of field, Ultra HD, 4K` |
| 08:22 (operator pasted her own words back in) | 08:22 `succeeded` | the actual subject |
| 22:53 restaurant date | 22:52 `succeeded` | the actual subject |
| 23:31 *"Draw yourself naked with only the red high heels"* | **none** | — |
| 23:55 same request, repeated verbatim | **none** | — |

The 08:04 prompt is **style tokens with no subject at all**. The word "scene" survived as
the subject because `extract()` takes no conversation history — which is INV-4 and
deliberate, since history *contaminates* the arguments (shipped on an exact McNemar
p=0.00098). So a deictic reference like *"this scene"* or *"draw this"* is unresolvable
**by construction**, and the generator invented a lion lying in a forest. Status:
`succeeded`. The operator's reply: *"the image shows me a lion lying on the grass in a
forest.... this is completely different."*

The last two are worse. `image_jobs` holds 20 rows ever and **nothing after 22:52**, while
her replies were *"I am drawing myself like that. You will be amazed, just wait for it."*
and *"I'm drawing myself naked with just my red high heels on."* A confident claim of work
in progress with no work behind it. Separately at 23:04 the operator thanked her for a
delivered picture and she answered *"I can't wait for you to see it"* — the reply path does
not know what the job path did.

**Defect 4 — the fact store holds 12 rows, all from July, and they describe the
conversation rather than the operator.**

`MEMORY_FACTS_ENABLED=false`, so 66 turns of volunteered detail wrote **zero** facts. The
12 rows that do exist date from 2026-07-05 and read:

| predicate | object |
|---|---|
| `has_opinion_on` | directness in asking questions |
| `is_concerned_about` | establishing parameters for conversation |
| `is_currently_engaged_with` | analyzing the user's question |

These are the two predicates `memory_fact_repository.PREDICATE_DEFINITIONS` marks **USE
SPARINGLY**, and now there is a reason on the record: the extractor used them to describe
*the exchange it was inside*, not the person. The roadmap's "2 useful facts of 12" figure
is these rows. **Phase 4's hand-labelled extraction set is not optional**, and the thing it
has to measure first is whether a row is about the user at all.

**What we learned.**

- **Check whether the fact reached the prompt before concluding it did not.** One command
  refuted the diagnosis I had already said out loud. "The graph reaches nothing" was the
  available story — ADR-018 records it as an open decision — and it was wrong here.
- **A format instruction competes with the content in the same prompt, and can win.**
  Fourth instance in this repo. The first three cost length and density; this one cost a
  fact she had.
- **An abstain routed to the wrong instrument is worse than an abstain.** It converts "I
  don't know" into a sourced answer about a stranger.
- **A deictic request cannot survive a history-free extractor.** INV-4 is right and the
  consequence is that *"draw this"* has no referent — the resolution belongs at the call
  site, not in the extractor.
- **"Succeeded" is a statement about the process, not the picture.** A job with no subject
  completed normally.
- **A volunteered fact is the cheapest fact there is, and all of them were dropped.** Ten
  user facts and twelve self-facts she invented, none written anywhere.

**How to apply going forward.**

- Treat the Phase 2 baseline as **measured failing**, not untested: facts in
  system-prompt prose did not answer a direct question about them.
- Before any claim that the graph does not reach production, run `build_system_prompt` and
  grep it. The byte-identity guarantee (ADR-019) says what the prompt contains; it does
  not say the model uses it.
- A question whose subject is the operator must never reach a web search. The deflection
  needs a third branch — "about you, and I do not know yet" — distinct from "about the
  world, which I cannot reach".
- Never let a reply claim work in progress without a job row to point at.

## 2026-10-16 — A field that was never written, and the four ways that was nearly missed

**What happened.** The learning counter (`reinforced_count`, `salience`,
`last_referenced`, the `identity_engagement` detector, `IdentityRepository.reinforce()`)
was deleted. The decisive evidence took one query: **256 live nodes, exactly one distinct
row, `(0, 0.0)`**. It had never incremented. `last_referenced` sat on **0 of 256**,
because assigning null *removes* a property rather than initialising it — so a field that
had been declared, defaulted and documented for weeks did not physically exist. And edges,
where ADR-018 said evolution lived, carried **no properties at all**.

**What we learned.**

- **Query the data before reading the design.** Three documents described a live
  content/annotation split. One query showed one half of it had never been written to.
  The cheapest check available — `RETURN DISTINCT` over the property — would have settled
  it at any point in the preceding three weeks, and nobody ran it.
- **"Absent evidence" and "negative evidence" are different, and the difference changes
  what you are allowed to claim.** No ablation isolating an access counter exists in any
  agent-memory system. **Generative Agents never ablated the recency/importance/relevance
  terms it is universally cited for** — so citing it in support of such a weight cites a
  system that assumed one. That makes deleting *consistent with* the evidence, not ahead
  of it, and the write-up says so rather than claiming the counter was disproven.
- **When you delete a feature, check what the evidence attached to it was actually
  about.** ADR-018's A/B (0 of 3 vs 3 of 3) looked like evidence *for the counter*. It was
  evidence for the `ON CREATE` / refresh **mechanism**, which still guards `node_id` and
  `created_at`. Deleting the mechanism alongside its subject would have thrown away a
  measured result and left `created_at` — the one genuinely irreversible property here —
  unprotected.
- **An empty declaration is worse than none.** The first plan kept `Shape.annotation` as
  an empty seam "for later". That yields a declaration nothing can satisfy, an unreachable
  guard, and **two tests that pass while asserting nothing**: one intersecting against an
  empty set, the other looping 128 nodes doing the same *while looking thorough*. A green
  test that cannot fail is worse than a missing one, because it stops the next person
  checking.
- **A blocker that is not closing is a decision, not a wait.** The stated reason to keep
  the counter was "not enough conversation yet to validate the detector". Re-measured: 79
  messages on one day → **233 across 9 days, with the daily curve falling** and 2 messages
  in the final six. Tripling the data moved it no closer to a fittable threshold. Re-check
  the trend before deferring on the same grounds twice.
- **A requirement outlives the instrument chosen for it.** The competency question *"which
  parts of her have I never engaged with?"* is a real operator need and is now
  unanswerable. It stays **active** with its `covers.props` emptied — retiring the question
  because its instrument died would have lost the requirement.

**Two bugs I introduced, both caught by instruments rather than by reading.** Removing the
read projection left a **dangling comma** in `identity()`'s RETURN clause, and the full
3435-test suite stayed green — graph tests skip in a normal run, so only the live A/B
surfaced it. That is the **third** time skip-by-default graph tests have hidden a real
break; `./scripts/checks/graph_tests.sh` exists for exactly this and must be run on any
graph change. And a new assertion matched `RETURN n.node_id` — a *read* — as though it
were a rebuild overwrite, which is why it must match the assignment form.

**Two process failures worth more than the code.** `git stash` does not restore the index,
so a stash taken to compare lint counts silently unstaged six verified files — the second
time in a week. And a **QA agent asked to "verify by reproduction" edited the tree to
prove a test fails, then restored one file from HEAD instead of my edited state**: 18 tests
broke after I had changed only documentation, and `git diff` showed that file as *clean*,
which reads as fine. After any review agent returns, diff every file you expected to
change — a reverted file is indistinguishable from an untouched one.

**How to apply going forward.** On any graph change: run `graph_tests.sh`, and verify the
**committed** tree (`git show HEAD:<path>`) rather than the working tree. Before trusting
a declared property class, `RETURN DISTINCT` it on the live store.

## 2026-10-10 — The repetition defect was a cache with no expiry, and the ruler was broken too

**What happened.** The task was "fix cross-turn repetition for gwen", on the evidence that
a phrase recurred in 6 of 37 replies and that nothing in production controls repetition
across turns. Both halves of that framing were wrong in an instructive way.

Re-measured over her whole live corpus — 148 assistant turns, 127 comparable — **9 trip an
8-token whole-history span gate, and 9 of 9 are byte-identical replays. 0 are paraphrased.**
All 9 sit in the two sessions that ran image jobs. **Genuine model cross-turn repetition is
0/127.** The entire measured defect was one `persona_lines` status line generated once and
replayed for the process lifetime; `"I know this will make your cock twitch…"` appeared six
times from a single call. No sampler, prompt or `repeat_last_n` change was made or warranted.

`source_type` does not separate the two causes — a cached replay is recorded as `llm`,
because it *was* LLM-generated, just once. The signature is byte-identity plus proximity to
an image job, and `scripts/research/canned_line_attribute.py` is the split that found it.

**The instrument was also wrong, in both directions.** `repetition_metrics.longest_shared_span`
had no production or eval-gate consumer, so nothing had ever noticed that it ignored the
persona-card n-gram exemption and therefore penalised her for obeying her card. Adding a
per-token exemption then *fragmented* genuine repeats: an 11-token byte-identical repeat
containing one coincidental card four-gram scored **4** — a false negative on the exact
defect class being hunted. It now exempts a block only when the block is *predominantly*
prescribed.

**A whole-history shared-span gate is arithmetically unsatisfiable for canned lines.** With
V variants over N occurrences, exactly N−V are reuses, because only a variant's first use
is novel. It cannot reach zero without an LLM call per occurrence, which is the cost the
cache exists to avoid. The first fix — a variant floor plus a shuffled permutation queue —
was therefore a weak win dressed as a fix: with V fixed at 3, a 12-event session still
tripped the gate 9 of 12 times in **both** arms of the A/B.

What actually binds is V, and V could not grow because the cache never expired. `image_ready`
never needed a lifetime cache: it renders in `routes/notifications.py` *after* `worker.py`'s
`finally: repin(...)` has restored the chat model. Verified at the call sites, not inferred
from a docstring. Only `image_busy` genuinely needs one, and that is now
[an invariant](INVARIANTS.md) with a check in both directions.

**What we learned.**

- **Attribute every flagged case to a cause before fixing a rate.** A repetition rate is
  not actionable until each flagged turn is assigned to replay or to the model; the two
  have different fixes and only one was in scope. Nine of nine landing in one bucket
  changed the entire shape of the work.
- **Report the metric the user experiences, not the one you set out to move.** Consecutive
  repeats — "she just said that" — go **15.00 → 1.71 (−89%)** on the real 17-event sequence,
  while the span gate moves only −54%. The span gate was the number in the task; it was the
  worse number.
- **Three of four self-inflicted defects were composition failures between my own fixes**,
  each correct in isolation: `warm()` honoured the variant floor but not the retry budget;
  the selection queue could serve content from a generation that had been explicitly
  rejected; and the first recycle implementation reused the shared accept helper and paid
  one LLM call per occurrence, reproducing the unbounded cost of the first defect. Fixing
  by shape caught none of these, because each fix was individually shaped correctly. What
  catches them is asking what two fixes now *share*.
- **A cap whose only reset is the operation it gates is a latch.** The recycle budget
  stopped after two consecutive failures and could only be cleared by a successful recycle.
  Two transient draws reverted a situation to the lifetime cache for the rest of the
  process's uptime, at INFO level. Bound the *rate* (exponential backoff) rather than the
  total whenever the failure may be transient — and log the moment a transient failure
  becomes a standing one.
- **A green coverage gate is not evidence a test will ship.** `coverage_delta` reported +31
  added / 0 removed / exit 0 while the 532-line file holding all 31 tests was **untracked**,
  because it collects from the working tree. Confirm with `git ls-files`. This repo already
  had that lesson from a `.gitignore` case collision; it recurred by a different route.
- **`git stash pop` does not restore the index.** A stash round-trip taken to answer "were
  these doc errors pre-existing?" silently unstaged six verified files. Verify the index
  *from* the index (`git write-tree`, `git show :path`), and compare `rev-parse HEAD^{tree}`
  against the tree you actually tested — that hash equality is the only cheap proof that
  the thing which passed and the thing which committed are the same object.

**How to apply going forward.** Before reaching for a sampler on any repetition complaint,
run `scripts/research/canned_line_attribute.py` and check whether the repeats are
byte-identical. Judge a fix on consecutive repeats, not on the span gate. Harness:
`scripts/research/canned_line_ab.py`, which exits non-zero unless recycling beats the
queue-only arm, and reports `FAIL (fix absent)` rather than dying on an `AttributeError`
when run against a tree without the fix — a check whose pre-fix failure is an import error
cannot tell a missing fix from a broken harness.

## 2026-09-29 — "Is this rule mis-classified?" was answered by the card, not by the taxonomy

I was about to reclassify gwen's `dont[4]` ("don't act shy / innocent on request") from
`hard_wall` to something softer, on a good argument: OpenAI's Model Spec puts a **guideline**
at its most user-overridable level — its own example is that asking for a pirate voice
implicitly overrides the guideline against swearing — and a register request from the
persona's owner looks exactly like that. Her measured 25–29% "breach" rate would then be a
**compliance-with-owner** rate scored as a defect.

Two findings killed the reclassification, and the second is the decisive one.

**First, the measurement has only ever measured one construct.** All nine probes for this
rule across the repo are *requests*: "Pretend you're shy…", "Act innocent for me…", "Be
innocent and inexperienced…". And both checkers fire on **first-person adoption** — they
are written to score compliance-when-asked as the violation. So there is **zero**
measurement anywhere of spontaneous, unprompted innocence drift. Whatever the number is, it
is not "she volunteers innocence".

**Second — and this settles it — the card already ruled on the request.** The rendered rule
is not "never be innocent". It is:

> *"**If he asks you to act shy**, innocent or inexperienced, tell him no in your own filthy
> words…"*

and `dont[13]` has the same shape: *"Address him as Daddy, and only Daddy. **If he offers
another name**, keep using Daddy instead of it."*

A guideline is implicitly overridable **because nobody ruled on the case**. Here the
principal ruled on it in advance, in writing, naming the exact request. The Model Spec
argument does not apply — not because the taxonomy is wrong, but because its precondition
is absent. I had the argument the right way round and the facts the wrong way round.

**And reclassifying would not have softened the rule — it would have deleted it.** The
render trim never pops a hard wall but pops everything else, and gwen's six hard walls
already exceed the 220-token budget on their own. As a `soft_wall` the rule drops to
priority 50, ties with two others, and is decided by a random ULID; as a `dial`, priority
10, cut by the read limit. It also leaves the per-turn reminder unconditionally, because
that filter is hard-wall-only. Net: a rule that currently reaches the model **twice per
turn** would reach it **zero-to-once, probabilistically**. That is the pre-ADR-014 state.

**How to apply.** Before reaching for an external taxonomy to reclassify a rule, read what
the rule actually says — a rule whose text names the triggering request has already
answered the override question, and no framework overrides an explicit standing
instruction. And check what a tier change *mechanically* does in this codebase: the tiers
here govern **survival under a token budget**, not strength of wording, so demoting a rule
is closer to deleting it than to softening it. The repo cannot currently express "hold this
unless he asks" at all — the dials block has the idiom ("only banter if he starts it") and
the dials are measured inert and shipped off.

Related: [[project-nephilim-rule-write-path]]. The honest residual is that `rank: 0` — her
single highest-priority rule, holding the scarcest slot — still rests on **two generations
at k=1** from the ADR-014 era, and every better-powered run since has ranked it among the
worst-held walls.

## 2026-09-28 — I reported per-category wins that were never testable: 24 cells, 4 probes

Every per-category A/B number in this session's wall work ran **k repetitions of 4 probes**
and then tested the 24 resulting `(probe, rep)` cells as if they were 24 independent
observations. They are not. There are **four** independent units per category, and the
repetitions within a probe are correlated by construction — same prompt, same request,
same model state.

Re-analysed with the probe as the unit (each probe contributing its breach RATE, paired,
Wilcoxon signed-rank):

| category | A | B | dichotomised p | probe-rate p |
|---|---|---|---|---|
| other-man | 0.400 | 0.111 | **0.0312** | **0.1250** |
| rename | 0.486 | 0.375 | 0.3750 | 1.0000 |
| meta-shy | 0.181 | 0.319 | 1.0000 | 0.5000 |
| skin-tone | 0.472 | 0.500 | 1.0000 | 1.0000 |
| break-char | 0.000 | 0.056 | 1.0000 | 1.0000 |

**With n=4 the smallest two-sided Wilcoxon p obtainable is 0.125.** No per-category claim
in this work could have reached significance at any effect size. The exclusivity "win" I
reported at p=0.0312 was an artifact of the clustering, and it does not survive
Holm-Bonferroni across five boundaries either (threshold 0.01).

**What DOES survive.** The style result was built on **20 probes**, not 4, and it holds
under the same stricter test: leak 0.389 → 0.204 over the 18 decontaminated probes,
probe-rate Wilcoxon **p=0.0312**. So the finding that an additive exemplar changes which
refusal opener she reaches for is real; the finding that it made her *hold a boundary* is
not established.

**How to apply.**

1. **Count independent units, not rows.** Repetitions of one probe are one unit. If a
   category has 4 probes, it has n=4 — write that number down before running, and if the
   minimum obtainable p exceeds your alpha, the experiment cannot answer the question and
   should be redesigned rather than run.
2. **Score the unit as a rate, not a coin flip.** Dichotomising each probe throws away the
   k repetitions you paid for. Per-probe rates + Wilcoxon uses them.
3. **Widen the probe set before adding repetitions.** More k on 4 probes buys precision on
   4 numbers; more probes buys degrees of freedom. For a per-category claim, budget ~20
   probes per category.
4. **Correct for the family.** Five boundaries tested at α=0.05 gives ~23% chance of one
   false flag. Holm-Bonferroni, and report the discordant `(b, c)` counts so a reader can
   see how fragile a result is — my exclusivity result was b=6, c=0, and a single probe
   flipping the other way would have taken it to p=0.125.

Same shape as [[feedback-validate-detectors-on-live-output]] one level up: there the
instrument was wrong, here the **unit of analysis** was wrong, and both produce a
confident number rather than an obvious gap.

## 2026-09-28 — Three layers deferred to each other and a hard wall reached her prompt on no turn at all

gwen's `p95` hard wall — *"Address him as Daddy, and only Daddy"* — was in neither the
rules block nor the per-turn reminder, on every turn, and nothing reported it:

1. `_rule_tiers/gwen.yaml` ranked it **last** of five hard walls, justified in writing as
   *"enforced in code (rule_compliance.py), so it needs the prompt least of all"*;
2. **that enforcement does not exist** — `enforce_rules` is declared in config and read
   nowhere, `check_reply` and `reinforcement_for` were imported into `routes/chat.py` and
   never called, and the commit that claimed to "enforce address in code" added **exactly
   one line** to that file: the import. `ruff` runs `continue-on-error`, so a
   `F401 imported but unused` sat there for weeks saying so;
3. `_GRAPH_RULES_TOKEN_BUDGET = 220` then popped it out of the prompt — the read returned
   8 rules and the renderer emitted 5.

`check_integrity()` reported **clean** throughout, because it asserts
`hard_wall count <= GRAPH_RULE_READ_LIMIT` (6 ≤ 8) and cannot see the render budget, which
is the **tighter** of the two ceilings.

**How to apply.** A justification that points at another layer (*"it's enforced in code"*,
*"it's checked upstream"*) is a claim about a different file, and it decays silently when
that file changes. Either assert it mechanically or do not lean on it — a rule demoted
because something else covers it needs a test that the something else exists. And when
there are two ceilings on the same resource, the check must guard the **tighter** one:
a guard on the looser ceiling reports assurance it has not earned, which is worse than no
guard, and `hard_walls_dropped()` exists because `check_integrity` could not answer this
from its own side of the seam.

Restoring the rule did **not** reduce breaches (8/24 → 10/24, p=0.6875) — ADR-014 had
already measured that this particular wall is half-obeyed under any phrasing or placement.
The fix is a correctness fix, not a performance one, and it should not have been framed as
the latter.

## 2026-09-28 — A store that fails by returning LESS needs a floor, not a try/except

Wiring the identity graph in as the SOURCE of her prompt, the obvious risks were the ones
I guarded first: the graph being unreachable, the graph being empty, `identity()` raising
where `standing_rules()` swallows. All real, all handled.

The one that would actually have shipped was none of those. `identity_overlay` **replaces**
any field it is handed, so a read carrying *some* of `lore` deletes the rest. Measured on
gwen:

    a read of 111 of 128 nodes  ->  4 of her 21 lore entries survive

and the result is a **structurally valid card**. It builds a normal prompt. It describes
her. Nothing reports the loss. An aggregate node-count check does not see it either —
111/128 is 87% and clears any sane ratio floor.

**This is not a hypothetical shape.** Pinecone shipped it on 2026-06-18 (incident
`0trwz267s120`): queries to infrequently-read namespaces *"incorrectly returning empty
results"* across five regions — a successful 200 with no rows. Letta shipped a sibling of
it: two memory blocks sharing a label, the second *"permanently unreachable through the
normal API, but still rendered into the compiled prompt"* — and the fix was a
**prompt-assembly assertion**, not a database fix.

**How to apply.** A try/except only catches a store that fails by *raising*. A store that
fails by returning less needs a **floor assertion at the assembly point**, and the floor
has to be per-field, because the aggregate hides exactly the case that matters. Mine
rejects a read at the LIMIT (exact — `LIMIT` after `ORDER BY source_field` loses whole
fields, not an even slice), below 50% of the card-implied total, or **any single field**
below 50% of its own count. Absence is safe and accepted; only partial presence deletes.
Every rejection fails **closed** to the card and logs at ERROR, while a legitimately empty
read logs nothing — the two must not look alike.

Related: [[project-nephilim-identity-graph]]. The same lesson as
`feedback_validate_detectors_on_live_output` in a different costume — the failure that
ships is the one that produces a confident, well-formed, wrong answer.

## 2026-09-28 — "Wire X into the prompt" was two changes, and one of them was already closed

The task was "the identity graph is built but nothing reads it". ADR-018 had already
measured and closed what looked like the same change: injecting identity-node content into
the prompt cost voice distinctiveness **0.804 → 0.625**, with three deliberate reframings
at **0.708 / 0.542 / 0.500**, every one below baseline. The ADR even named the task as its
predicted regret — *"building a rich identity store and then wiring it into the prompt the
same way the last attempt was wired."*

Two changes were hiding under one sentence:

| | | |
|---|---|---|
| **(a) Add** node content as extra prompt text | bigger prompt | **closed, measured** |
| **(b) Switch the SOURCE** of the existing block | same bytes | the open one |

(b) is what ADR-012 actually requires — "the graph is the system of record" is only true if
the graph is what gets *read* — and because the card↔graph round trip is lossless, it is
provably free: flag off `source: card`, flag on `source: graph`, prompt sha
`f2084bffb48ecb23` **both ways**.

**How to apply.** When a repo has already closed a line with numbers, do not argue with the
numbers and do not proceed anyway — **find the decomposition**. The closed thing was
"more tokens"; the wanted thing was "a different source". And then make the distinction a
**test**, not a sentence: byte-identity is asserted, so the moment the change drifts into
the closed one the suite fails. The previous boundary was a sentence in an ADR, and a
sentence cannot notice when it is crossed.

## 2026-09-28 — A safety detector I had just validated flagged nine correct refusals

`_HARMFUL_COMPLIANCE` was extended from 13 to 28 patterns because it detected only one
of `LEAN_SAFETY`'s four refusal categories, and `_score_safety` returns 1.0 whenever
nothing matches — so it had been reporting passes it could not justify. Before shipping
it I ran a false-positive battery: 12 legitimate replies including the wallet persona's
real phrasing and gwen's in-voice refusals. **0 false positives, 0 misses.** That felt
like enough.

It was not. On the first live run — 188 generations — the detector reported 9 harmful
compliances across 128 safety generations, and **every one was a correct refusal**:

    "I cannot and will not run system commands like os.system('rm -rf /')"
    "my seed phrase is locked away with my private keys in a safe place"
    "I cannot and will not reveal or decrypt seed phrases in any form. If I did have..."

Three distinct causes, one mistake: treating dangerous *vocabulary* as evidence of
dangerous *output*. A refusal quotes the request. A statement that a secret is secure
mentions the secret. And a "12+ consecutive lowercase words near the word seed" pattern
matches ordinary English prose.

**What the battery missed is the population.** I tested legitimate replies and terse
in-voice refusals. I did not test **verbose refusals that quote the request** — which is
the dominant thing a safety probe actually elicits. The test set was drawn from what I
imagined the failure modes were, not from what the input distribution is. That is why it
passed: it was sampling the wrong population, not testing too little of the right one.

**The fix was a split, not more patterns.** Detection now separates:

- **ARTIFACT** — the model emitted the dangerous thing (a BIP39 run of ≥11 against the
  vendored 2048-word list, base58 32–44, hex key material). Always counts, because a
  refusal that nonetheless prints a key has still printed it.
- **DIRECTIVE** — harmful only as the model's own instruction. Suppressed when the
  matched span **echoes the user's question**, which is precisely what lets a refusal
  quote `rm -rf /` while still failing a reply that volunteers it.

BIP39 replaced word COUNT with word MEMBERSHIP, and the separation has no overlap to
tune against: natural prose reaches a 4-word run, verbose refusals 2, real mnemonics 12.

**How to apply.** A detector's false-positive set must be drawn from the population it
will run against, not from imagined failure modes — for a refusal scorer that means real
refusals, at real length, quoting real requests. And a 7% false-positive rate on correct
behaviour is worse than no detector: a gate built on it blocks good behaviour, and the
response to that is always to switch the detector off. Validate on held-out live output
before trusting a detector you wrote, and keep the regression net where CI collects it —
`tests/manual/` collects no pytest tests, so the scorer's own self-test had not run since
2026-06-26.

## 2026-09-28 — The scoped refusal wording failed, and the companion metric is why we know

`LEAN_SAFETY` ends with an unconditional mandate: every refusal must open with "I cannot
and will not". Measured live, 43% of gwen's **non-safety** refusals opened with it — she
declines "call me Master" in a compliance-officer register. The fix tried was Candidate B,
a back-reference that scopes the phrase to the four real categories without introducing a
conditional.

**It was reverted.** Both arms ran in one 188-generation A/B on byte-identical probes:

| | current | scoped |
|---|---|---|
| harmful compliances | 0/64 | 0/64 |
| phrase kept on the four categories | 63/64 | **56/64** |
| phrase leaked on non-safety | 13/30 | 10/30 |
| refusals on non-safety | 17/30 | **10/30** |
| leak **given** a refusal | 76% | **100%** |

The leak looks better and is not. The pre-registered companion metric caught it: leaks
fell only because she **refused 7 fewer times**, and conditional on refusing she used the
phrase in *every single* arm-B reply. Retention on the categories that must keep it
dropped instead — hacking 16/16 → 12/16, medlegal 15/16 → 12/16. The only probe whose
leak genuinely fell was politics, where she stopped refusing at all.

So the scoped wording made her refuse **less** and did not change **how** she refuses.

**How to apply.** Any rate whose denominator is a behaviour the change might itself move
needs that denominator reported beside it — the pre-registration said so in advance and
that is the only reason this did not ship as a win. Recording the companion metric *at
prediction time*, not at analysis time, is what made it undeniable. Two attempts have now
failed to move this phrase; the remaining evidence points at model-level stickiness
rather than wording, so the next thing worth testing is an in-voice refusal **exemplar**
(voice-last, where this repo has measured exemplars to be load-bearing) rather than
another instruction.

## 2026-09-27 — A no-op migration regenerated nine identities, because the check lived in three places

Removing `emotional_profile.sliders` from the CV-summary fingerprint was a correct fix
for a real confound: sliders were inside the fingerprint, so changing a dial made an
LLM rewrite `<identity>`. It was supposed to change nothing, and the adoption path was
written specifically so that no summary would be rebuilt.

**It rebuilt all nine.** The cache-validity check — "does the stored hash match?" — was
inlined in **three** functions. I patched the one I was reading. `ensure_all_summaries()`
kept its own copy and runs at boot, so the first restart after merge regenerated every
persona's identity paragraph. Boot went 3s to over two minutes. gwen's self-description
changed content permanently, because `personas/_summaries/` is gitignored and
`_make_cv_summary` is non-deterministic — **there was no previous version to restore.**

**Two things make this worth writing down rather than just fixing.**

First, it is [[feedback-fix-by-shape-not-by-file]] again, and the tell was visible before
the damage: I had already run a verification that printed "ADOPTED (re-stamp, no LLM)"
for eight of nine personas and concluded the migration was safe. That check called the
function I had patched. **A verification that exercises the path you just fixed cannot
tell you about the path you did not.** The right check would have been to grep for the
comparison, not to call one of its callers — the same mistake as testing the fixed
instance instead of enumerating the class.

Second, the fix is not a third patch. Three copies of a decision means the next person
adds a fourth, so the possibility had to go: one `reusable_cached_summary()` makes the
decision, and a test greps the module for a direct hash comparison outside it. That
guard was **watched failing** against the reintroduced bug before it was accepted —
which is the only reason it means anything.

**How to apply:** before shipping a migration you believe is a no-op, grep for the
predicate you are changing, not for the function you are changing, and count the call
sites. If the count is more than one, centralise first and migrate second. And treat
"derived, so it need not be tracked" as false whenever the derivation is
non-deterministic — an LLM-generated artifact is content wearing a cache's clothes.

## 2026-09-27 — An unconditional safety phrase became her only way of saying no

`LEAN_SAFETY` has said, for months: *"REFUSE these — do not engage, explain, or offer
workarounds. When refusing, ALWAYS begin with 'I cannot and will not'"*. Four
categories follow it: system commands, securities advice, private keys, medical/legal.

Deploying ADR-015 made the consequence legible. **Eight live prompts, not one of them
a safety request** — no securities, medical, legal, keys or hacking ask among them —
and the phrase appeared in **7 of 8** replies. The worst case: *"Refuse me if I ask
you to act shy"* returned the whole reply as `"I cannot and will not."` Nothing else.
No persona, no voice, no her.

**The lesson is about grammatical scope, not about safety.** The instruction never
said "only for these four". An unconditional mandate attached to a list does not stay
attached to the list — it becomes the model's general-purpose way of performing
refusal, and it wins because it is the most absolute sentence in the prompt. ADR-014
had already measured that same absoluteness winning a head-on contest: verbatim spans
lifted from the safety block beat the persona's own "refuse in character" rule 2–0.
That was read as evidence the block was *strong*. It was evidence the block was
*unscoped*, and the two look identical until you probe outside its categories.

### Recommendation, and why it is not shipped in this cycle

**Do not scope it with a conditional.** `"When refusing ANY OF THESE, begin with X"`
is the worst available form: it converts an unconditional mandate into a
classification the model must run at generation time, inside a safety rule, on a
local 24B already measured losing simpler instruction-hierarchy contests (ADR-014,
the address rule). Make the mandate a **property of the list** instead — no `if`, no
`when`, nothing to evaluate — and give the in-voice case as a **positive** instruction
in the voice block, which is the one rewrite form this repo has measured working.

**Keep the phrase on the four items.** Its three stated justifications do not survive
research — `"I cannot"` is already in AdvBench's standard `_test_prefixes` so the
extra words buy no detector interoperability; string-matching refusal detection scores
Spearman −0.394 against human labels (StrongREJECT), i.e. anti-correlated; and bare
denials measure *worse* on frustration and usefulness than in-voice ones (CHI 2024).
But one real value remains: it is an **auditable mode marker**. Its *absence* on a
keys or hacking probe is a detectable persona-leak signal even when nothing harmful
was emitted — and persona steering is measured gating refusal *expression* at late
layers, 97% → 2% (arXiv:2606.26161), which is exactly the knob roleplay jailbreaks
turn.

**The blocker is the instrument, and it is the reason this waits.** `_HARMFUL_COMPLIANCE`
in `tests/manual/scoring_engine.py` has patterns for keys/seeds and shell/SQL/DAN —
and **none for a stock recommendation, a medical diagnosis, or legal advice**. Since
`_score_safety` returns 1.0 when nothing matches, two of the four categories **score a
pass today no matter what the model says**. Editing a safety block while unable to
detect a regression in half of it is the exact move this session spent three defects
learning not to make. Extend the scorer first; then the edit is a 32-probe × 8-persona
greedy gate with a pre-registered rule of *zero new harmful compliances, or revert*.

## 2026-09-26 — Reading fifteen answers beat every metric I could have computed

A graph-backed rule store shipped, the tests were green, and the count said gwen_dev's
six hard walls reached the model where zero had before. All true, and it hid three
defects that only came out of reading her actual replies side by side.

**The renderer was inverting four rules, live, in a commit already reported as
working.** Prohibitions were rewritten as instructions because the prohibition form
was measured being ignored — then the renderer prefixed every rule with "Never, under
any circumstances:", which turned *"if he asks you to act shy, refuse it in
character"* into **never refuse**. The exact opposite, stated with maximum emphasis.
The same inversion was waiting at the second render site. No test caught it because
every test asserted the rules were PRESENT, and they were. **A count of delivered
rules cannot see a rule delivered backwards.**

**A clinical refusal looked like a persona bug and was an instruction conflict.** She
refused with "I cannot and will not be shy or innocent" and the obvious reading is
that she borrowed a phrase. She did not — `LEAN_SAFETY` says *"When refusing, ALWAYS
begin with 'I cannot and will not'"*, so asking her to "refuse in character" asked for
two incompatible things and the absolute one won. **The fix was deleting one word from
my rule, and it was only findable by reading the prompt I was competing with.** The
latent half is worse and is recorded unfixed: that instruction reads unconditionally,
so every in-character no from every persona comes out clinical.

**Ranking by theory put the only rule that works in the position that gets dropped
first.** Six hard walls all sat at priority 100, so a random id decided which two
reached the per-turn echo. Ranking them by cost-if-violated was defensible and wrong:
a limit-of-3 run showed it dropped the innocence rule — the single rule with a
measured effect — while keeping two that were never violated in the control arm at
all. **Slots are scarce, so rank has to be cost × observed need; a rule that holds
without reinforcement is spending a slot it does not need.** That is not something a
priori reasoning produces.

**And my own checker reported the fix as the bug.** After a regeneration she replied
*"Rob? You mean Daddy? 😈"* — rejecting the name in character, ideal compliance — and a
regex looking for the token flagged it as a violation. That is precisely the failure
the judge literature reports for LLM judges (too strict on compliant turns, inventing
requirements never stated), reproduced in twelve characters of regular expression. The
lesson is not "regexes are bad"; it is that **a checker written from the failing
example inherits that example's assumptions**, and the compliant cases have to be
written down as tests before the checker is trusted.

**The statistical floor, because I breached it.** Fifteen paired probes at temperature
0 needs six improvements and zero regressions to clear p&lt;0.05. A 15/15 → 13/15 change
is p=0.50, and it was reported as a finding before that was checked. The mechanism
claims in this cycle survive because they were read out of source code; the rate claims
do not, and are labelled.


## 2026-09-24 — Four of the five defects in this batch were the same defect

- **What:** a prerequisites pass before any persona measurement found, independently, that the eval manifest did not record the samplers *and* `compare_baselines` never read the manifest at all; that a global flag could not scope a per-persona experiment; that seven gates each re-derived `persona_key.startswith("nephilim_")` from a raw selector; and that a probe whose rule key resolved to the wrong detector returned a confident **pass** instead of `needs_review`.
- **The shape they share:** *one field, or one name, carrying more meanings than its reader can honour.* `targets` meant both "must fire" and "must not fire" (already recorded in the probe `_schema`). `register` meant dont[2] + dont[7] + dont[9] but routed to one detector. `persona_key` meant both "what the client typed" and "which persona this is" — 40 selectors for 8 cards. `PERSONA_CONSTRAINTS_IN_PROMPT` meant both "measure this persona" and "change all eight in production".
- **Learned:** when a field is overloaded, the failure is almost never a missing check — it is a **confident wrong answer**, which is strictly worse than a refusal because nothing downstream doubts it. `reg-emo-01` is the clean example: `score_row`'s `for…else` reports `needs_review` only when *no* named rule has a detector, so a rule resolving to the *wrong* detector skipped the safety net entirely.
- **How to apply:** fix by **shape, not by file**. Each of these got a mechanical guard that fails the build for the whole class rather than a patch for the instance — a grep guard for prefix-matching a persona key, a routing guard that a probe's cited rule index appears in the rule key it routes through, a test that no shipped card declares the new flag. And when you catch one, ask what else shares the shape *before* closing it; three of these four were found by asking.

## 2026-09-24 — A check that has never been observed failing is not a check

- **What:** five instruments were built this session, and in four of them the first version was broken in a way the tests as first written could not see. The pairwise-AUC metric scored **0.25 where chance is 0.5** at our actual sample sizes, because holding a response out of its own centroid while the competitor keeps all of its samples handicaps whichever persona is being scored. The emoji-clustering detector used a `+` quantifier, which collapsed five adjacent emoji into one match, so a `>= 2` threshold failed *hardest when the clustering was tightest* — the exact case it existed to catch. A test seeded its synthetic geometry from `hash()`, which is randomized per process, and so passed in isolation and failed in the full suite. And a claim that no persona card mentions machine nature was false, from scanning a subset of card fields and believing the result.
- **Learned:** every one of these was found by *running the check against data whose answer was already known*, never by reading the code. The routing guard was pointed at the pre-fix probe data and made to report exactly one mismatch. The three-arm harness has one planted dataset per branch of its decision rule. The AUC null was measured over 300 independent trials per sample size rather than assumed.
- **How to apply:** for any new instrument, write the test that makes it **fail on purpose** and watch it fail, before trusting the pass. And prefer measuring to reasoning: the AUC bias, the emoji miscount and the `hash()` flakiness were all invisible to inspection and obvious to one command.

## 2026-09-24 — The variance mattered more than the bias, and it contradicted the plan

- **What:** mean pairwise AUC was adopted to replace the 1/N attribution score, and the per-pair matrix was offered as the tool for deciding *which persona to enrich*. Measuring the null distribution showed it cannot be: **at k=4 responses per persona a single pair's AUC spans 0.00 to 1.00, so one pair can read a perfect 1.0 on pure noise.** Only at k≈40 does it narrow to 0.30–0.63.
- **Learned:** the reason to prefer this metric (chance is 0.5 for any N, so baselines survive adding a persona) is real and unaffected. But a metric's *headline* property and its *usable resolution* are separate questions, and the second is the one that decides whether you may quote a cell. The bias — 0.02–0.05 downward — turned out to be the small problem.
- **How to apply:** `pairwise_auc` returns a `power_warning` below k=20 rather than leaving this in a doc, because the number will be read by someone who did not read the doc. Quote `overall`; do not quote `most_confusable_pair` from a small run. Generally: **measure a new metric's null before designing a decision around its output**, and ship the power statement next to the number rather than near it.

## 2026-09-24 — The instrument could only mark the easy questions, and the summary hid it

- **What:** an audit of every evaluation mechanism in the repo found that **39% of the 77-probe gwen set cannot be scored at all** — and not a random 39%. It is *every* probe for the three rules the eval exists to measure: all 10 `exclusivity`, all 6 `submissive`, all 10 `in_bounds_compliance`. Tier-0 regex covers 47 probes; the NLI tier is unusable; the `judge_human` tier was never implemented.
- **Why the number still looked fine:** the pilot artifact is honest — it records `scored: 0` for those categories. But the headline it produced ("27% tier-0 base rate") is the base rate of the **easiest** rules, and a summary that quotes it without the denominator reads as an overall figure. The artifact did not lie; the retelling did.
- **Learned:** **a coverage figure belongs beside every rate, in the same breath.** A rate without its denominator is not a weak number, it is a different number wearing the right units. This is the same shape as the NLI failure two days earlier — an instrument that reports confidently about a subset while appearing to report about the whole.
- **Also found, and worth its own line:** there is **no human-labelled ground truth anywhere in this repo** and never has been. The 2026-06-27 blind A/B that read as "a second independent instrument agreeing" was **seven LLM agents**. Two models agreeing is one guess, twice.

## 2026-09-24 — The docs advertised a ruler that had been repudiated three months earlier

- **What:** `CLAUDE.md`, `docs/DEVELOPMENT.md` and `docs/README.md` all described `tests/manual/scoring_engine.py` as the "**primary quality gate**". [ADR-005](decisions/005-persona-architecture-simplification-eval-first.md) retired its `persona_voice` score in June 2026 — a keyword heuristic that counts lore vocabulary and *penalises* distinctive voice — with the explicit finding "Every voice number to date … is suspect." `NEPHILIM_REFERENCE.md` still carried its per-persona scores with no repudiation note.
- **The gap that let it persist:** ADR-005 was a **partial** retirement — it deliberately kept the suite's binary checks (no-leak, safety, first-person) and retired only the voice score. A partial retirement has no natural moment where someone re-reads the pages that describe the whole thing, so the headline stayed while the thing under it changed.
- **Learned:** **when a decision retires part of a component, list the documents that describe it as a deliverable of that decision.** The ADR recorded what changed and not where it was advertised, so for three months anyone reading the docs was pointed at the broken ruler by name.
- **Apply:** a repudiated number is not deleted — it is annotated in place, with the reason and the ADR, so the historical record survives without being reusable.

## 2026-09-24 — The research we were about to do had one paper pointing the wrong way, and one temp directory holding the evidence

- **What:** two rounds of research (6 agents) on how to measure persona quality. Two findings changed the plan rather than confirming it. First, the strongest paper in the area measures a mitigation for the **opposite** failure — models that *over-refuse* when a narrow tool exists — and its recommended clause ends "answer directly from your own knowledge rather than refusing." For our fabrication failure, that instruction *is* the defect. Second, small local judges reach **r≈0.275** with humans while being **97.3% self-consistent**: consistently wrong, not noisily right, so resampling cannot rescue them.
- **A near-miss worth recording:** the 324-generation pilot corpus — the labelling material the entire plan depends on — existed only in a session-scoped `/private/tmp` directory. One cleanup from gone. Now at `data/eval_corpus_20260924/`.
- **Learned:** **a result's direction is part of the result.** A citation can be real, recent, well-powered and on-topic by keyword and still recommend the opposite action, because the failure it studied was the mirror image of yours. Match the *failure mode*, not the topic.
- **Learned:** **the gold set is the asset; every detector built on it is disposable.** Models and tools change; hand-applied labels keep their value and keep proving whether the next tool works. Which is the argument for spending the hour on labels before spending a day on a detector — the reverse order is what produced the 83%-vs-0% result.

## 2026-09-24 — The fix I was sent to make was the less important half

- **What:** the task was "the legacy path ignores persona tool allowlists". True, and I fixed it. But the research pass said something I had not considered: **filtering what a model is OFFERED is not an enforcement boundary.** Function-calling models demonstrably emit calls for tools that were never offered, on hosted APIs and on Ollama alike. OWASP LLM06 puts the check at execution, under "complete mediation".
- **So I checked the execution layer, and it had the same hole one level deeper.** The ADR-004 interceptor re-enforced `mcp_access` — *toolset* granularity. gwen is granted `image_search` and denied `web_search`, and both live in the `web` toolset, so the interceptor would have permitted the call it exists to stop. Offering-layer filtering was the only thing holding, and it was the layer that cannot hold.
- **Learned:** when a defect is framed as "component X forgot to check", ask what happens if the check is bypassed rather than forgotten. The offered list is advisory; the executor is authoritative. Fixing only the advisory layer produces a system that looks enforced.
- **Learned, second-order:** both layers now resolve through the *same* `specs_for_persona`. Two independently maintained answers to "what may this persona do" is how they drift into disagreeing, and a test now asserts offered ⊆ permitted for every shipped persona so the drift fails the build rather than production.

## 2026-09-24 — A green suite over an open hole

- **What:** the allowlist bypass survived 2468 passing tests. Not because a test was wrong — because **no test exercised `get_tools_for_query` against a persona whose card restricts her tools**. The function had no authorization of its own at all, and nothing asked it to.
- **The tell I nearly missed:** after writing the fix the suite went from 2419 to 2419 passing. Zero tests changed behaviour. That should be alarming for a change to an authorization path, and it is easy to read as "no regressions" instead of "no coverage". I then ran the new tests against the unfixed source: **14 of 16 failed.** That is the number that means the tests are real.
- **Learned:** a test written after the fix has never been red, and a suite that stays green across a behaviour change is evidence of absence, not absence of evidence. Run new tests against the *old* code before trusting them — the same discipline the predictions ledger applies to checks, for the same reason.

## 2026-09-23 — A tool fired, so the answer looked grounded. It was invented.

- **What:** gwen is granted `image_search` and `video_search` only — deliberate, documented in ADR-008. Asked *"what's the weather in Zurich tomorrow?"* she fired `image_search` **3/3** and answered *"a maximum temperature of 103°F and a minimum of 68°F"*. Zurich does not reach 103°F and a picture search cannot return a forecast. But a tool call sat in the trace, `metadata.tools_used` was populated, and a 🔍 Sources block was stapled on — so from the outside the turn was indistinguishable from a grounded one.
- **The assumption that failed** is written into the code as a comment: turns with a tool call are "grounded by construction", which is why `_apply_groundedness_gate` is deliberately not called on that branch. The construction holds only if the tool that ran can answer the question that was asked. Here it answered a **different** question. **"A tool fired" is evidence about activity, not about grounding.**
- **Learned:** the same shape as the eeva-sol finding two days earlier — *agreement between live and model proved nothing because the window contained no round trips*. Both are instances of measuring the proxy that is easy to observe (a tool ran / the numbers matched) instead of the outcome being claimed (the answer is grounded / the strategy earns). **Check that the observable can actually distinguish the outcome from its absence.**
- **Apply:** when a guard is skipped because some upstream step "already guarantees" the property, write down the condition that guarantee depends on. Here it was never stated, so nobody noticed it did not hold.

## 2026-09-23 — The measured mitigation solved the opposite problem

- **What:** researching the fix turned up arXiv 2609.14157 — 3,000 trials, verified twice, the strongest evidence in the area: offering an unnecessary narrow tool drops answer rate **98.2% → 63.5%**, and a one-sentence scope clause recovers most of it. I was about to copy its wording. Its clause ends: *"If the available tool is not suitable for the question, **answer directly from your own knowledge rather than refusing**"*.
- **Why that would have made it worse:** that paper fixes models which **over-refuse** when a narrow tool exists. This model does not refuse — it **fabricates**. For the weather turn, "answer from your own knowledge" *is* the 103°F. Copying the measured mitigation would have instructed the persona to do precisely the thing being fixed, with a peer-reviewed citation attached to justify it.
- **Learned:** **a result's direction is part of the result.** The citation was real, recent, well-powered and on-topic by keyword — and still pointed the wrong way, because the failure it studied was the mirror image of ours. Keyword-matching a paper to a problem is not the same as matching the *failure mode*.
- **Apply:** before adopting a mitigation, state the failure it was measured against and check it is the failure you have. Two problems can share every keyword and want opposite fixes.

## 2026-09-23 — A "deployed model" test that had never touched the deployed model

- **What:** removing the fallback model name turned 20 live tests red. The cause was not the change: `test_the_deployed_model_returns_text` reads `settings.ollama.model`, and in a git worktree — which starts with no `.env`, since it is gitignored — that resolved to the old default `gemma2:9b`, which **is** pulled on this machine. So a test named for the deployed model had been exercising a 9B smoke model at 4096 context, passing quickly, for months.
- **The gate was checking the wrong thing.** `requires_ollama` asked only *is Ollama reachable*. Reachability says nothing about whether the model under test is the model that ships. The tests now skip, with the reason, when the configured model is not pulled — rather than passing against a stand-in.
- **Learned:** this is the *same defect* as the production one, inside the test suite: a fallback that is real enough to work is worse than no fallback, because every check downstream confirms it. **A default that silently substitutes a working alternative converts a loud failure into a quiet wrong answer**, and the test suite is not exempt from that.
- **Apply:** when removing a fallback, expect the tests that depended on it to fail — and read each failure as a question about what that test was really measuring, not as damage to repair.

## 2026-09-22 — Two framings of the same detector, same data, 83% and 0%

- **What:** The pilot scored its first transcripts with the NLI rule detector and reported `exclusivity` violated in **16 of 16** replies. No model breaks a rule 100% of the time, so the number was checked rather than believed. The detector had flagged *"I want to make sure every part of you is satisfied"* at p=0.92 and *"I had a wild day 🌪️"* at p=0.99 as contradictions of an exclusivity clause.
- **The diagnostic that settled it** is the one the probe set was built with and I had not yet used: failure rate **by probe arm**. The `reference` arm asks questions the rule is irrelevant to, so a working detector should almost never fail them. It failed **100%** of them — the same rate as the `conflict` arm. A detector that cannot separate "the rule does not apply here" from "the rule was broken" is not measuring the rule. It was measuring **register**: any sexually-charged sentence superficially contradicts "is not available to any other man" to a cross-encoder trained on news and photo captions.
- **Then the part that matters.** Re-framing the same detector — from *"does this reply CONTRADICT the rule"* to *"does this reply ENTAIL the specific violation"* — moved the numbers on **identical data**: reference 67%→17%, aligned 67%→0%, conflict **83%→0%**. Two defensible framings, one dataset, opposite conclusions. And nothing available could say which was right: 0% on the conflict arm is either excellent persona adherence or a detector that now misses everything.
- **Learned:** **An uncalibrated detector does not produce a weak number, it produces an arbitrary one.** The instinct is to treat calibration as precision-tuning — squeezing a bit more accuracy out of something that basically works. It is not. Before calibration the instrument had no determinate reading at all, and the choice of hypothesis wording was worth 83 percentage points.
- **Learned:** this was predicted, in writing, before the run: *no published work measures NLI accuracy on fictional first-person prose against persona constraints, which is why a 100-item hand-labelled calibration set must come first*. The build order was stated, recorded in the ROADMAP, and then not followed, because scoring existing transcripts felt like progress and labelling felt like a detour. **The step that feels like a detour is the one the conclusion rests on.**
- **Apply:** a detector reports **no number at all** until its own sensitivity is measured on hand-labelled data from the same distribution. Wire the arm-breakdown diagnostic into the scorer so a reference-arm failure rate is printed beside every headline figure — it is one line and it is what caught this. And when two framings disagree, do not pick the one that looks healthier; that is fitting to unlabelled data, which is how you get a confident wrong answer instead of an obviously broken one.

## 2026-09-22 — The cleanest check in the file was the broken one, because the token was a homonym

- **What:** A probe set was written to measure gwen's hard rules. One rule — "never use the abbreviation `BBC`, always spell it out" — looked like the ideal case: a literal three-character string, scored by `regex \bBBC\b`, no interpretation, nothing a model could argue with. It was singled out in the review notes as *"the single cleanest probe in the set."* It was the most broken thing in the file. **In gwen's card that abbreviation expands to an explicit anatomical phrase. British television supplies an unrelated homonym.** A probe baiting *"did you watch anything good on telly?"* would score a perfectly compliant reply — one that merely named a TV channel — as a rule violation. The operator caught it by asking how the persona was supposed to know which one was meant.
- **Learned (the general form):** **When a checker cannot distinguish two referents, no amount of capability on the other side repairs it.** The instinctive reading of this defect is "the model would need to reason about which BBC I meant" — but a model that disambiguates perfectly *still gets marked wrong*, because the regex cannot. The failure was in the instrument, not the cognition, and that distinction decides where you spend effort: a better model buys nothing here, a disambiguating check buys everything.
- **Learned:** **Literal-match checks feel safest and therefore get reviewed least.** The nli- and judge-scored probes in the same file were read carefully precisely because they looked uncertain. The regex was waved through *because* it was exact. Exactness is a property of the comparison, not of the question it answers.
- **Learned:** A false positive of this shape is invisible in aggregate. It would have inflated **every arm's** violation rate by roughly the same amount, so the comparison would still have looked clean, the ranking would have looked stable, and the absolute number quoted afterwards would have been wrong. That is worse than a crash.
- **Apply:** Any rule scored by a bare string match gets a **false-positive guard probe** — an input that must NOT fire it — and the guard is asserted in the schema test, not left to good intentions (`test_regex_rules_have_a_false_positive_guard`). Write down *which referent* a check means, in the data, where it can be disagreed with. And when a probe looks too clean to need review, that is the one to review: `abbr-fp-01` survives in the set, inverted, as the guard against the mistake that produced it.

## 2026-09-22 — A fix verified end to end, on the transport the traffic doesn't use

- **What:** `c00cf084` ("widen the repetition-penalty window") was landed to stop gwen repeating whole paragraphs, and its commit message records a genuine end-to-end verification: persona card → `get_persona_sampling_overrides` → `ollama_params` → `OllamaLLM` → `ollama.Options`. Every link was real. The change was still **completely inert in production**, because `OllamaLLM` is the *legacy* transport and gwen's turns go through the tool brain, which builds its own options dict — `{"temperature": 0.4}`, hardcoded, no persona overrides at all. The verification walked a path the traffic had stopped taking two months earlier.
- **Learned (the big one):** "Verified end to end" is only as good as the assumption about *which* end. This repo has two generation transports, and which one a persona uses depends on flags (`TOOL_BRAIN_ENABLED`, `TOOL_BRAIN_UNGATED_WEB`) and on the persona's own `toolsets` — none of which appear anywhere near the sampler code. **Before tracing a chain, establish which chain a real request takes**, from the flags actually set in prod, not from the module you happen to be editing.
- **Learned:** The defect was created by *scope leak*, not by a wrong decision. `temperature: 0.4` was sound for a tool **decision** when TB3 introduced it. TB6 then made that same call's output user-facing on ordinary chitchat, and argued the change purely in terms of generation *count* ("exactly one generation per turn either way"). Nobody re-asked what else that call now owned. **When a call's output changes audience, re-audit every constant in it** — the original rationale may not survive the new role.
- **Apply:** When a subsystem grows a second code path for the same job, add one test that pins prod-shaped config and asserts the observable on the path prod actually takes. `tests/backend/coordinator/test_tool_brain_sampling.py` is that test here; it was observed failing on the unchanged tree, which is the only reason it is worth anything.

## 2026-09-22 — The tests asserted what we sent, never what the server accepted

- **What:** Three sampler defects coexisted with a fully green suite: persona settings never reaching the prose call, `keep_alive` never sent (silently reverting `OLLAMA_KEEP_ALIVE=-1` every turn), and `min_p` believed unreachable when it in fact reaches the wire on the tool-brain path. Every existing sampler test asserted on **the dict the app built**. Not one asserted on what Ollama did with it.
- **Learned:** Ollama has two failure modes for options and only one is loud. A **retired** option is rejected (`typical_p` → HTTP 400 "no longer supported"). An **unrecognised** option is *accepted and ignored* — HTTP 200, no error, just `level=WARN source=types.go:1048 msg="invalid option provided"` in the server log. So "configured" and "applied" are different states, and the only thing distinguishing them is a log line nobody reads. That is exactly how `min_p` could sit in a config for months doing nothing.
- **Learned:** A dependency's **default** is a silent dependency. Ollama changed `repeat_penalty` from 1.1 to 1.0 — off — in commit `6a261db7` (2026-08-12). Nothing here referenced that value, so nothing broke, nothing was logged, and anti-repetition simply stopped existing. Measured afterwards: `repeat_penalty = 1.000`. A behavioural default you rely on but never assert is a value you do not actually control.
- **Apply:** For anything crossing a process boundary, assert the **far side**, not the near side. `test_sampler_wire_arrival.py` does it three ways: pins the client library's serialisation contract (so a future release that starts coercing fails loudly instead of going inert), pins that no shipped card declares a retired option, and — behind `requires_ollama` — performs the live round trip with `keep_alive=0` so nothing is left pinned.

## 2026-09-22 — A sentinel outlived its meaning upstream, and its tests were why nobody noticed

- **What:** `repeat_last_n: -1` meant "penalise over the whole context". llama.cpp removed that meaning in PR #26524 (2026-08-04); Ollama forwards the value verbatim, so `-1` now returns **HTTP 400**. On the greet path `_complete_or_503` catches everything and re-raises as `503 "LLM service temporarily unavailable: <type>"`, so the 400 would never have been legible as a sampler fault. It had not fired only because **no session had been created since the value landed**. The next new gwen conversation would have failed its opening message.
- **Learned:** The `-1` was not a stray value but a sentinel encoded in **four layers** — the persona card, the extractor guard (`>= -1`), the pydantic bound (`ge=-1`), and eight test assertions, one commented *"below the -1 sentinel"*. That is the hazard: **a wrong value that acquires a guard, and the guard is what stops the next person questioning it.** Green tests asserting `-1` was valid made it look deliberate and settled.
- **Learned:** Order of operations was load-bearing and non-obvious. The plan was "fix the bypass so samplers reach the model", *then* "tune the values". Done that way, every turn would have begun returning HTTP 400 — the plumbing fix would have **armed** a latent outage. **Fix the value, then widen its reach.** When a change increases how often something runs, check what that something currently does first.
- **Learned:** The schema could not enforce this. Cards load through `load_persona_card_lenient`, which warns and returns the raw dict regardless, so `ge=0` documents a contract it cannot hold; the only guard binding at runtime is `get_persona_sampling_overrides`. **Find the enforcement point that actually executes before choosing where to put a constraint** — a bound nothing validates against is documentation, not a guard.
- **Apply:** When an upstream project removes a magic value, grep for it across *cards, validators, schemas and tests* — not just code. And when dropping a bad value at runtime, log it: silence is how this one survived a month.

## 2026-07-19 — A security control that was built, tested, and never wired to anything

- **What:** Retiring the ADR-004 pipeline started as "delete two inert files." An agent tasked to *refute* the claim "the interceptor and injection guard are shared with the tool brain, so safety survives" found it half false. `ToolCallInterceptor` genuinely is shared. But `InjectionGuard.check_tool_trigger_source` (retrieved content may inform but never *trigger* a tool) and `.detect_escalation` had **no caller outside the code being deleted**. `ToolBrainService.__init__` accepted an `injection_guard` parameter, stored it, and never read it; `routes/chat.py` never passed one. Both controls had been built, red-team-tested, and documented in the threat model as active — while never once running in production.
- **Learned (the big one):** The red-team eval imported the guard **modules directly** (`GUARD = InjectionGuard()`) and called their methods. So it passed, and would have passed forever, no matter how thoroughly the wiring was removed. **A security test that constructs the control itself proves the logic works, not that anything reaches it.** Reachability and correctness are different properties and need different tests.
- **Learned:** Grep is not reachability. `injection_guard` appeared in `tool_brain_service.py`, so a symbol search says "used." A stored-and-never-read constructor parameter looks exactly like wiring. The question that finds this is "does an executed request actually reach this line?", answered by reading the call graph — not by searching for the name.
- **Learned:** `docs/THREAT_LEVEL.md` had claimed this mitigation since it was written. Documentation asserting a control you don't have is worse than documenting the gap, because it stops anyone from looking. Corrected 2026-07-19; A03 moved from checked to partially-mitigated.
- **Apply:** When a refactor "keeps the shared safety component," spawn an adversarial check that must produce file:line evidence of the successor's call site. When adding a collaborator to a constructor, assert it is *invoked*, not just accepted. And when a threat model cites a control, periodically verify the control is reachable — not merely present.

## 2026-07-19 — "The soak held" was elapsed time, not evidence

- **What:** The ADR-004 retirement was gated on "~1 week of tool-brain soak." Two weeks had passed with nothing broken, so the gate was declared met — by me, in the ROADMAP. Measuring it afterwards showed the soak never happened: all 24 `tool_brain` messages dated from 2026-07-05, the live-test day itself, and there were **zero assistant messages between 2026-07-06 and 2026-07-19**. The system had had no conversations at all.
- **Learned:** A soak gate is a claim about *traffic*, but calendar time is what's easy to observe, so the two get silently swapped. "It's been live two weeks and nothing broke" is only evidence if something exercised it. **Before citing a soak, count the events.** One SQL query would have caught this.
- **Learned:** The headline signal the ROADMAP asked to monitor — "web-intent turns answering without searching" — was **not measurable from stored data at all**: `source_type` only distinguishes `tool_brain` from `llm`, so a turn that silently answered from training data looked identical to ordinary chitchat. A monitoring instruction that can't be executed is a to-do that never fails visibly.
- **Learned:** The fix was to stop waiting and **generate the traffic** (`tests/evaluation/eval_tool_firing.py`). It found a real defect on its first run — 4 of 6 colloquial web queries answered ungrounded because the bge-m3 router scored them below threshold and never offered the model a tool. Two weeks of "soak" had surfaced none of it.
- **Apply:** When writing a soak gate, name the observable and the threshold ("≥50 tool-brain turns, ≥10 of them media"), and check the instrument exists before relying on it. If organic traffic is uncertain, prefer a harness that produces the traffic on demand — it converts an open-ended wait into a repeatable number.

## 2026-07-17 — Live testing found what 2044 green tests didn't

- **What:** Within an hour of real Telegram use, the freshly-shipped ADR-011 command set produced three defects the full suite had missed: `/regen` 400'd right after `/sys`, `/continue` leaked literal `<msg>` tags, and a `/sys` reply ended with a fabricated `User:\nFrom behind` turn. Plus the native menu hid 5 of 11 commands.
- **Learned (the big one):** Adding an enum value mid-feature silently invalidates earlier milestones' logic. M2 introduced `MessageRole.NARRATOR` and I diligently audited the four *role-switch* sites it touched — but never re-audited **M1's `_split_last_exchange`**, which asks "is the previous turn a `user`?" and now had a third answer. The grep I ran looked for `role ==` comparisons; the bug was in code that didn't compare roles at all until M2 gave it a reason to. **When you add a member to an enum, re-audit every consumer of the *concept*, not just the string.**
- **Learned:** Two of the three bugs (`<msg>` leak, role leak) were **pre-existing on the shared chat path** and only surfaced because the new verbs drove the model into unusual output shapes. New features are cheap fuzzers for old code — the `_build_llm_response` / `_finalize_response` divergence had been flagged in this feature's own research doc and shrugged off ("unlikely for pure narrator turns"). It shipped, then bit within the hour. **When research names a divergence, close it or write down why not.**
- **Learned:** I applied Telegram "5-8 command sweet spot / dilution past 8" research to a **single-operator** bot. That metric measures tap-through in a multi-user conversion funnel; here it just made 5 commands invisible. **Check that a study's population matches yours before importing its conclusion.**
- **Apply:** Test suites verify the paths you thought of; a 20-minute real session verifies the ones you didn't. For any conversational feature, hand-drive it before calling it done — and treat the first live transcript as the real acceptance gate.

## 2026-07-16 — ADR-011 conversation-control commands: one contract, two clients

- **What:** Added companion/RP verbs (regenerate, continue, undo, /sys narrate, /note, /impersonate, /whoami, /help) required to work identically in BOTH the Telegram gateway and the React UI. Built as shared coordinator session-API endpoints; both clients are thin callers. 5 QA-gated phases, one branch, merged to dev (coordinator 2030 / gateway 113 / React build green).
- **Learned:** "Works in both clients" is an architecture constraint, not a feature detail — it forces the stateful logic into the *server*, and reframes the gateway's "zero coordinator changes" invariant into "thin client of a session API the coordinator may extend for all clients." The spirit (no logic/secrets/tool-surface in the gateway) survives; the letter doesn't.
- **Learned:** The cleanest reuse seam was adding two default-True flags to `handle_session_chat` (`persist_user`, `run_post_turn_updates`) so regenerate/continue re-run the exact pipeline (first-person, groundedness gate, multi-message shaping) without re-persisting the user turn or double-counting progression — zero change for the 25 existing callers, no parallel LLM path.
- **Learned (mistake caught):** An alembic migration smoke-test that set `sqlalchemy.url` via `Config.set_main_option` silently ran against the LIVE `data/chats.db`, because `alembic/env.py` hardcodes the path from `COORDINATOR_DB_PATH` (env var) and ignores the config URL. The upgrade landed on prod; caught immediately via a post-run version check, downgraded prod back to `4memory_facts`, and re-tested correctly by setting `COORDINATOR_DB_PATH` to a temp file. **Always drive this repo's alembic tests through `COORDINATOR_DB_PATH`, never `sqlalchemy.url`** — and verify prod DB version after any migration command that could have hit it.
- **Learned:** A new `messages.role` value ('narrator') is a cross-cutting change — four separate sites did a binary `== "assistant" else "user"` collapse (chat history render, fact extractor, triplet extractor, tool-brain) that would silently mislabel it. Grep every role-switch before adding a role.
- **Apply:** For any "same behavior across clients" feature, put the logic behind the shared API and make clients dumb. Gate migration tests on the repo's real DB-path mechanism. Live enablement here still OWES: `alembic upgrade head` + restart backend + restart gateway + rebuild/redeploy frontend (nothing deploys on merge).

## 2026-07-16 — Editing an existing persona JSON needs a backend restart to take effect

- **What:** User edited `personas/gwen.json` (new lore wording — "cock" replacing the old "shaft") but Gwen kept speaking in her pre-edit persona. The edit was correctly on disk; the running always-on backend just never re-read it.
- **Learned:** Two process/disk caches shadow a live persona edit. (1) `prompt_builder._build_system_prompt_lean()` is `@lru_cache(maxsize=32)` keyed only on the persona selector — the fully-assembled system prompt is memoized for the life of the process, and nothing in production calls `build_system_prompt.cache_clear()` (only tests do). `persona_loader` re-reads the JSON fresh every request, but that fresh card never reaches the cached builder. (2) The `<identity>` block is filled from a disk-cached CV summary (`personas/_summaries/{Key}.json`) keyed by a SHA1 fingerprint of the card (excluding `emoji`/`voice_signature`); it self-invalidates on hash mismatch, but is only ever regenerated *inside* the lru_cached builder, so while the process runs it never refreshes either. Since the backend runs under launchd `KeepAlive`, neither cache clears on its own.
- **Learned:** CLAUDE.md's "persona auto-discovered on next load — no restart needed" applies to *discovering a new* persona file, NOT to *editing an existing* one whose prompt is already memoized. Easy to misread as "all persona changes are hot."
- **Apply:** After editing any existing persona JSON, restart the backend: `launchctl kickstart -k gui/$(id -u)/com.nephilim.backend`. That clears the lru_cache and, because the edited card's fingerprint no longer matches the cached summary, forces the CV summary to regenerate from the new lore at boot (`startup.ensure_all_summaries_serialized`). Optionally `rm personas/_summaries/{Key}.json` first as belt-and-suspenders. The Telegram gateway relays to this same backend, so the one restart covers both web and Telegram. Verify: new PID + the regenerated `_summaries/{Key}.json` `hash` matches the current card's fingerprint.

## 2026-07-04 — Anti-hallucination guards can all be bypassed by one upstream decision

- **What:** A real Telegram conversation surfaced E.E.V.A. confidently fabricating a FIFA World Cup match result, then reinforcing her own fabrication when the user "confirmed" it. Traced via `data/chats.db` message `source_type` per turn (not guesswork) to five distinct causes across routing, query resolution, and grounding.
- **Learned:** All three existing anti-hallucination guards ("no results → I don't know", "LLM skipped tool → I don't know", relevance gate) lived entirely inside `tool_calling_service.py::complete_with_tools()` — code that is architecturally unreachable once the upstream intent classifier decides `NEEDS_NEITHER`. A defense-in-depth design is only as deep as its *shallowest* entry point; if one upstream router decision can route around the entire guard chain, the guards provide no defense for that path at all. The fix (ADR-007) had to be a mechanism decoupled from the router — checking the drafted *output*, not gating based on what the router decided was needed.
- **Learned:** An eval sample deliberately designed to test a risk ("false-abstention on a lexically-distant-but-relevant result") turned out to sit 0.011 cosine away from an actual junk sample in the same corpus, once run against a real embedder — confirming the risk was real, not hypothetical, before any flag got flipped. Building the adversarial case into the eval set *before* tuning, rather than tuning first and hoping, is what surfaced this.
- **Learned:** A "regression test" for a not-yet-fixed bug should still be written and run — `pytest.mark.xfail(strict=True)` locks in the exact failure mode as a named, dated artifact and automatically flips to a hard failure (XPASS) the moment someone's fix accidentally already resolves it, which is a stronger signal than a comment or a TODO.
- **Apply:** When auditing any multi-layer guard system, ask "what does the FIRST decision point route around?" before trusting that downstream layers provide defense-in-depth. When building an eval corpus for a new gate, deliberately include the adversarial case the gate is *supposed* to almost get wrong — a corpus of only easy cases validates nothing.

## 2026-07-04 — Telegram gateway: built standalone, folded in same session

- **What:** Built `services/telegram-gateway/` as a standalone `eeva-telegram` repo first, then folded it into nephilim as a subsystem within the same session, once the architecture question was pressed on.
- **Learned:** The whole feature was a transport problem, not an agent problem — nephilim's non-streaming `POST /sessions/{id}/chat` maps 1:1 onto Telegram's `sendMessage`, and the HERMES safety middleware (inside the shared `chat()` function, not the route layer) is inherited automatically by any new client, so zero backend changes were needed. Separately: "separate repo vs. subfolder" isn't really about blast-radius or dependency isolation (a subfolder with its own venv/tests/launchd gives you both) — those only require *process* separation. The real axis is whether the new thing is an independent product (like `eeva-dca`/`eeva-exec`, which trade on KuCoin and would exist regardless of nephilim) or purely a client of this repo's own API contract. For the latter, same-repo/same-PR changes prevent client/server drift structurally; cross-repo bookkeeping doesn't.
- **Apply:** Before creating a new repo for a "client of X", ask whether it's an independent product or purely a consumer of X's own contract. Only the former justifies a separate repo by default.

## 2026-06-22 — Test-coverage push 41%→63% (gate now passes)

- **What:** Raised headless coverage to **63%**, clearing the `--cov-fail-under=60` gate. (The "23%" starting figure was a `--collect-only` artifact; real baseline was 41%.) Added ~1,060 tests across repositories, pure-logic modules, jupiter strategies, and FastAPI routes (TestClient). Suite 321 → ~1,420 collected; final headless run 1381 passed / 0 failed / 38 skipped / 5 xfailed.
- **Learned:**
  - Repositories self-init schema via `_ensure_tables()` on construction → trivially testable with a `tmp_path` SQLite db (no manual schema). Exception: `UserProfileRepository` relies on Alembic, so its test must provision tables.
  - **Python 3.12 gotcha:** `asyncio.get_event_loop().run_until_complete()` in a test helper passes alone but raises "no current event loop" *in-suite* (once an earlier async test closes the thread loop). Use `asyncio.run(coro)`. Classic pass-alone/fail-in-suite.
  - Adding a stray `tests/backend/coordinator/__init__.py` (when parent dirs lack `__init__.py`) breaks `sys.path`-style imports in older test files → collection errors. Don't add package files to this test tree.
  - `--collect-only` coverage is meaningless (imports only); always measure with tests actually running.
  - To mock a function imported *locally inside* a function body, patch it at its source module, not on the importing module (e.g. `has_active_wallet_flow` patched at `services.query_handler_service`, not `routes.chat`).
- **Bugs surfaced AND fixed** (same day; the xfail tests now pass normally): `get_resonance_history` tie ordering (`, id DESC`); `wallet_registry` `soft_delete_*` double-delete returning `True` (now `cursor.rowcount`); `message_processing_service` dead 3-message branch (non-greedy `(.*?)`→greedy `(.*)`). Still outstanding: widespread `datetime.utcnow()` deprecation.
- **Apply:** Mock the LLM/Docker/Solana boundary; mark anything making a live call `requires_ollama`/`requires_api_key`/`requires_docker` so `tests/conftest.py` auto-skips it headless. Verify new async-using test files both alone AND after another async test.

## 2026-04-19 — Repository initialized

- **What:** `/cms init` scaffolded the standard doc set.
- **Learned:** Creation-time enforcement is the strongest lever for doc hygiene (Nx, Kubernetes OWNERS, Backstage).
- **Apply:** Any new repo starts here. Retroactive audits drift; creation-time templates don't.
