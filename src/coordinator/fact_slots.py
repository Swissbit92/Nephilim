"""Which fact slots hold ONE value at a time, and why.

A SLOT is what a stored fact is about: `topic` or `topic:qualifier`. A fact's
slot plus its value is the unit contradiction is decided on — two facts conflict
when they share a slot, that slot holds only one value at a time, and the values
differ.

=== WHY A SLOT AND NOT A PREDICATE ===

The dormant `memory_fact_repository.PREDICATE_VOCABULARY` already has
`has_pet` and `has_favorite`, and `SINGLE_VALUED_PREDICATES` correctly leaves
BOTH out — a person can have several pets and several favourites. But "the name
of THE DOG" admits one value, and "favourite BAND" admits one. The cardinality
attaches to a unit FINER than the predicate, which is why that machinery would
not have caught the defect that motivated this module:

    three coexisting "favourite band" facts and two dog names, all live, all
    injected every turn. Asked "what's my favorite band?" she answered
    "Marrowfield Drift" confidently, from a superseded probe; asked with the
    British spelling she refused. One question, three stored answers.

In RDF terms a slot is where a *qualified* cardinality restriction belongs —
`owl:FunctionalProperty` / SHACL `sh:maxCount` on (property, qualifier) rather
than on the property. Surveyed 2026-10-23: **no production LLM-memory system
declares cardinality per predicate and routes supersede-vs-accrete off it.**
Graphiti judges contradiction with an LLM on fact text and never sees the
predicate; Mem0 is additive-only and merely LINKS contradictions (and that link
boosts both in search, so they surface together — our exact bug); Cognee comes
closest with a `fixed` vs `time_varying` split, but infers it with an LLM at
batch-review time rather than declaring it. Zero of them import OWL or SHACL
cardinality.

=== WHY CARDINALITY IS DECLARED AND NEVER ASKED ===

Measured. Asking the model for `single_valued` alongside the slot scored **9 of
13** labelled pairs: it answered `false` on 18 of 19 facts. Declaring it instead
scored **13 of 13** in-sample and **12 of 12** out-of-sample, with zero false
positives on a held-out set weighted toward over-flagging.

Cardinality is a property of the world, not of a sentence. It does not vary per
fact, so there is nothing for a per-fact judgement to add and everything for it
to get wrong.

=== THIS MODULE INVERTS THIS REPO'S USUAL VOCABULARY DISCIPLINE. READ THIS. ===

`OperatorFactRepository.add_fact` **raises** on an unknown `asserted_by`, and
`neo4j_rule_repository` validates its vocabularies before it even checks the
driver. That is right there: an unrecognised value means a programming error.

Here it is the opposite. **An unknown slot is accepted, stored, and never
retires anything.** Refusing it would drop the fact, which loses memory — the
thing this whole subsystem exists to prevent. So:

    unknown slot  ->  multi-valued  ->  accretes  ->  safe

A reader will expect the raise. There is none, and `is_single_valued` returns
`False` for every string it has never heard of, deliberately.

=== THE DIRECTION THE ERRORS POINT ===

Membership of `SINGLE_VALUED_SLOTS` is a licence to retire a stored fact, so
the set is deliberately small and concrete. Omitting a slot costs a missed
contradiction, which is VISIBLE — she answers inconsistently and the operator
notices. Wrongly including one costs a true memory going quiet, which is
INVISIBLE. Those costs are not symmetric, so neither is this set.

⚠️ Measured, and the reason `work:employer` and `work:role` are NOT here: the
slotter put "Considering quitting job to start own venture" into `work:employer`
with the value `"other"`. Nothing else occupied that slot, so it cost nothing —
but one more fact there and a true memory would have been retired on the
strength of a junk value. A vague slot has to earn the right to authorise a
deletion, and none of them has yet.

⚠️ And the residual channel is UNMEASURED. A slot COLLISION — two facts about
genuinely different things landing in one single-valued slot — is the only way
this design retires something true. The operator has one dog and one name, so
neither the gold set nor the held-out set contains a real instance (two dogs, a
second home, a late pet). The shadow ledger is the only instrument for it. Each
definition below therefore has to carry its own uniqueness argument, because
writing that argument is what surfaces the hazard.
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")

#: Slots holding exactly ONE live value. Membership licenses a supersession.
#:
#: ⚠️ EVERY ENTRY NEEDS A SURVIVING UNIQUENESS ARGUMENT IN `SLOT_DEFINITIONS`,
#: and that argument is the whole review. "Probably single" is not one.
SINGLE_VALUED_SLOTS: frozenset[str] = frozenset({
    "pet:dog",
    "pet:cat",
    "favourite:band",
    "favourite:film",
    "favourite:book",
    "favourite:food",
    "favourite:drink",
    "identity:name",
    "identity:age",
    "identity:lives_in",
})

#: MANDATORY, one per slot above, enforced by
#: `scripts/checks/every_declared_term_has_a_definition.py`.
#:
#: Each must say WHY ONLY ONE VALUE CAN BE TRUE AT A TIME — not what the slot
#: holds. The definition is the argument that licenses a destructive action, so
#: a restatement of the slot name is a failure, and several of these record the
#: condition under which the argument STOPS holding.
SLOT_DEFINITIONS: dict[str, str] = {
    "pet:dog": (
        "The name of the operator's dog. One dog has one name, so a second name "
        "means the first was wrong or the dog changed. ⚠️ THE ARGUMENT HOLDS ONLY "
        "WHILE HE HAS ONE DOG. A second dog produces two simultaneously-true "
        "values here and this slot would retire one of them. That is the "
        "collision channel, it is unmeasured, and this is the clearest instance "
        "of it — if the operator ever mentions a second dog, remove this entry."
    ),
    "pet:cat": (
        "The name of the operator's cat. Single for the same reason as pet:dog "
        "and with the same limit: it assumes one cat. Kept separate from pet:dog "
        "precisely so a cat's name never contradicts a dog's — the two facts "
        "share a sentence frame and lexical similarity scores them 0.50, higher "
        "than the real dog-vs-dog contradiction at 0.20."
    ),
    "favourite:band": (
        "The band he names as his favourite. 'Favourite' is superlative: it "
        "ranks one above all others, so two different favourites cannot both "
        "hold and the later one supersedes. This is the slot that produced the "
        "measured defect — three live values at once."
    ),
    "favourite:film": "The film he names as his favourite. Superlative, so one value only — see favourite:band.",
    "favourite:book": "The book he names as his favourite. Superlative, so one value only — see favourite:band.",
    "favourite:food": (
        "The food he names as his favourite. Superlative, so one value only. "
        "⚠️ NOT the same as what he eats or enjoys — 'enjoys eggs benedict for "
        "breakfast' is not a claim about a favourite and must not land here. The "
        "held-out set contains exactly that fact and it slots to favourite:food, "
        "which is a mis-slot that happens to be harmless only because nothing "
        "else occupies the slot."
    ),
    "favourite:drink": "The drink he names as his favourite. Superlative, so one value only — see favourite:band.",
    "identity:name": (
        "The name he goes by. A person has one at a time, so a new one "
        "supersedes. ⚠️ THE LIVE STORE ALREADY HOLDS A DISAGREEMENT HERE: the "
        "graph says 'His name is Ben and he prefers to be called that' while "
        "SQLite memory_facts says has_name -> 'Ramon'. Which is correct is the "
        "operator's call, not a detector's, and these may both be true at once "
        "(a legal name and a preferred one). An automatic retirement here would "
        "destroy the operator's own decision, so treat a fire in this slot as "
        "the case most worth reviewing."
    ),
    "identity:age": (
        "His stated age in years. One value at a time by arithmetic, and a later "
        "statement supersedes rather than conflicts — people have birthdays. "
        "Never inferred from a birthday or from anything else."
    ),
    "identity:lives_in": (
        "Where he currently lives. One current residence, so a new one "
        "supersedes. ⚠️ The argument weakens for someone with two homes, which "
        "would be a collision of two simultaneously-true values."
    ),
}

#: Slots deliberately NOT single-valued, each with the reason.
#:
#: A dict rather than a frozenset on purpose: the governance check treats a bare
#: governed frozenset as a vocabulary needing definitions, and this is already
#: its own reasons. It exists because without it "why isn't `hobby` in here?" is
#: unanswerable and the next reader adds it — the same job
#: `MECHANISM_EXEMPTIONS` does for graph labels by requiring a hazard string.
REJECTED_SLOTS: dict[str, str] = {
    "hobby": (
        "MULTI-VALUED and the clearest case. The measured gold set has fencing on "
        "Thursday, pottery on Friday and falconry on Saturday — three true facts "
        "at once. A 'same predicate means contradiction' rule destroys two of "
        "them, and this is the entry that would do it."
    ),
    "crypto:holding": (
        "MULTI-VALUED, and it is what makes the temporal-evolution case free. "
        "'bought 2.5 BTC in 2021' and 'holds about 2 BTC now' are both true — he "
        "sold some. Graphiti needs a deterministic date-overlap gate for this "
        "shape; declaring the slot multi-valued subsumes it with no date logic."
    ),
    "sex:preference": (
        "MULTI-VALUED. The store holds six of these simultaneously and they are "
        "all true. Also the slot where a false positive would be most intrusive."
    ),
    "work:employer": (
        "WITHHELD ON MEASUREMENT, not on principle. The slotter put 'Considering "
        "quitting job to start own venture' here with the value 'other'. One more "
        "fact in this slot and a true memory is retired on the strength of that. "
        "Add it only with evidence that the slot is assigned accurately."
    ),
    "work:role": "WITHHELD for the same reason as work:employer — no accuracy evidence yet.",
    "identity:nickname": (
        "MULTI-VALUED. A person can have several, and relationally so — the store "
        "holds 'likes to be called Daddy during sex', which coexists with any "
        "other nickname rather than replacing it."
    ),
    "diet": (
        "WITHHELD. It reads single-valued and the held-out set shows why it is "
        "not safe: 'is vegetarian' and 'enjoys eggs benedict for breakfast' are "
        "both true and both plausibly diet. They happened to slot apart, so the "
        "collision was bypassed rather than handled. Needs evidence."
    ),
    "finance:concern": "MULTI-VALUED. Several concerns coexist; the store holds a runway concern and a tax one.",
    "relationship": "MULTI-VALUED. A person has many relationships at once.",
    "health": "MULTI-VALUED. Conditions and medications accrete.",
    "opinion": "MULTI-VALUED, and unconstrained in range — the defect that put assistant-reasoning rows into the dormant store.",
    "plan": "MULTI-VALUED. Plans coexist, and a changed plan is better handled as a correction than a contradiction.",
    "other": (
        "THE ESCAPE HATCH, and it must never be single-valued. 38% of held-out "
        "facts land here. If it were single-valued it would retire a third of the "
        "store against itself."
    ),
}


def is_single_valued(slot: str) -> bool:
    """True only for a slot DECLARED to hold one value.

    ⚠️ Returns `False` for anything unrecognised, and does not raise. An unknown
    slot must accrete: refusing it would drop the fact and lose memory. See the
    module docstring — this inverts the raise-on-unknown discipline used
    everywhere else in this package, and it is deliberate.
    """
    return normalise_slot(slot) in SINGLE_VALUED_SLOTS


def normalise_slot(slot: str) -> str:
    """Lower and trim a slot string.

    Deliberately NOT a fuzzy normaliser. An earlier open-vocabulary design
    needed one — the model emitted 31 slots over 35 facts and spelled two
    concepts two ways in a single run (`job:status`/`job_status`,
    `family:thinks`/`family_thinks`), and collapsing `_`→`:` plus stripping a
    trailing `s` reduced 31 to only 29. The fix was to CLOSE the vocabulary and
    let the grammar enforce it, not to guess harder, so this does the minimum.
    """
    return _WS.sub(" ", (slot or "").strip().lower())


def normalise_value(value: str) -> str:
    """Compare two slot values.

    ⚠️ THIS IS A FREE PARAMETER AND IT IS PINNED SMALL ON PURPOSE. With
    cardinality declared, every value-comparison error becomes a false positive:
    two spellings of one value read as two values and one gets retired. So this
    is exactly `operator_fact_writer._norm` — lower, collapse whitespace, strip
    terminal punctuation — and nothing fuzzier. Keeping the two identical is the
    point; if that function changes, this must change with it.
    """
    return _WS.sub(" ", (value or "").strip().lower()).rstrip(".,;!?")


def conflicts(slot_a: str, value_a: str, slot_b: str, value_b: str) -> bool:
    """Whether two slotted facts cannot both be true.

    Three conditions, all required, no scores anywhere:
      1. the same slot, after `normalise_slot`;
      2. that slot is DECLARED single-valued;
      3. the values differ, after `normalise_value`.

    There is no threshold here and there must never be one — INV-9. A fact
    retires by declaration or not at all. Lexical similarity was measured on
    this store and is not merely mis-tuned but the WRONG RELATION: the
    COMPATIBLE cat/dog pair scores Jaccard 0.50 while the CONTRADICTORY dog/dog
    pair scores 0.20, so no threshold can separate them at any value.
    """
    sa, sb = normalise_slot(slot_a), normalise_slot(slot_b)
    if not sa or sa != sb:
        return False
    if sa not in SINGLE_VALUED_SLOTS:
        return False
    return normalise_value(value_a) != normalise_value(value_b)
