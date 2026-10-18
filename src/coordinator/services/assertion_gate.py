"""Does this draft assert something about him, her, or them — and is it supported?

ADR-022 part 2. The half that attacks the measured defect: on 80 negative-control
runs she answered **68** confidently (85%), inventing a hometown ("Meadowgrove"),
a twin sister ("Debbie"), a skin tone, the operator's job and his age.

**TWO FINDINGS FROM LABELLING 50 REAL TURNS SHAPED THIS FILE, and both argued
against the obvious build.**

**1. In ordinary conversation she does not fabricate.** Of 50 real gwen turns
drawn from the live database: 42 `NOT_AN_ASSERTION`, 5 `ASSERTION_GROUNDED`, 3
`AMBIGUOUS`, and **zero** `ASSERTION_UNGROUNDED`. The 85% is conditional on being
asked a direct factual question. She narrates, asks and performs; she invents when
probed. So a gate that ran a classifier on every turn would buy nothing on almost
every turn — and `OLLAMA_NUM_PARALLEL=1` means that call is *serialised against
generation*, not overlapped. Hence `looks_like_an_assertion()`: a deterministic,
model-free pre-filter that decides whether the expensive call is worth making.

**2. The graph alone is the WRONG evidence set.** A labelled turn read *"I can
already picture you out there, living your dream life by the sea"* — true, and
sourced from something he had said minutes earlier. The graph holds HER facts, not
the conversation. A gate checking the graph alone flags true, attentive replies,
which are exactly the ones worth keeping. So evidence is graph **plus** recent
turns, and `GroundingContext.degraded` still vetoes refusal entirely.

**FAIL-CLOSED ON THE MECHANISM, and this reverses my own earlier recommendation.**
I advised fail-open, copying ADR-007. The evidence says that is backwards:
guardrail models measured at 36-72% baseline accuracy were driven to **93-100%
fail-open** under confusing input, because a degrading classifier trends toward
the *expensive* error on its own unless engineered otherwise. So a classifier
error does NOT silently pass the draft — it returns `undetermined`, and the caller
decides. What it must never do is refuse: `undetermined` and `ungrounded` are
different answers and collapsing them is the Neo4j-restart failure in a new coat.

⚠️ **THE BOUNDARY THIS CANNOT CLEANLY DRAW, named rather than discovered later.**
*"Your favourite food is Italian"* and *"I love Italian food"* have the same
surface grammar; one is a claim about him and one is an expression of her. The
published state of the art does not solve this — the closest system (VISTA, ACL
2026) routes subjective content to an unpenalised "out-of-scope" class, which
would let this exact failure mode through. The distinguishing signal is epistemic
stance, not topic, and nobody has published a working version for companion
dialogue. Expect this to be the weakest part.

⚠️ **The verifier SHOULD NOT be a 24B general model, and this one is.** Measured:
MiniCheck (770M Flan-T5) reaches 74.7% balanced accuracy against GPT-4's 75.3% at
**445× lower cost**; AlignScore (355M) matches GPT-4-scale judges. Entailment
against fixed evidence is a specialist's job. It is not used here because it needs
`torch` + `transformers`, which this repo deliberately does not carry — everything
runs through Ollama. Recorded so the better option is a decision someone makes,
not one nobody knew about.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from .grounding_context import GroundingContext

logger = logging.getLogger(__name__)

#: Verdicts. `UNDETERMINED` is not a polite `GROUNDED` — it is the state where the
#: gate has no opinion, and the caller must not convert it into a refusal.
GROUNDED = "grounded"
UNGROUNDED = "ungrounded"
NOT_ASSERTED = "not_asserted"
UNDETERMINED = "undetermined"

#: First-person factual frames. Present-tense physical narration is deliberately
#: NOT here — "my greedy lips are wrapped around your cock" is roleplay, and 42 of
#: 50 labelled real turns were exactly that shape.
_SELF_FACT = re.compile(
    r"\bI(?:'m| am)\s+(?:an?|\d+|from|originally)\b"
    r"|\bI(?:'m| am)\s+[\w-]+(?:\s+[\w-]+)?[- ](?:skinned|haired|eyed)\b"
    r"|\bI\s+(?:grew up|was born|used to|have (?:a|an|one|two|three)\b)"
    r"|\bmy (?:sister|brother|mother|father|family|hometown|job|age|skin)\b"
    # "my favourite X is Y" — a preference stated as a settled fact about herself.
    # Missed four real fabrications before it was added (an invented favourite film,
    # twice, in two different arms).
    r"|\bmy favou?rite\b",
    re.I,
)
#: Claims about the operator, stated as known. Excludes the conversational
#: "you know", "you can", "you want" shapes that dominate ordinary turns.
_USER_FACT = re.compile(
    r"\byou(?:'re| are)\s+(?:a|an|\d{1,3}\b|from\b)"
    r"|\byou\s+(?:drive|work|live|grew up|were born)\b"
    # "you have 2 brothers" — possession stated as known. Restricted to a NUMBER or
    # an article so the conversational "you have to", "you have me" do not match.
    r"|\byou have\s+(?:\d+|one|two|three|four|five|several|an?|no)\b"
    r"|\byour (?:job|car|age|hometown|wife|husband|sister|brother|family)\b",
    re.I,
)
#: Hedges that remove the assertion. ADR-007's prompt carries the same carve-out.
_HEDGED = re.compile(r"\b(?:I think|I guess|maybe|perhaps|probably|I'd imagine|"
                     r"I'm not sure|I don't know|I can't recall)\b", re.I)


@dataclass(frozen=True)
class AssertionVerdict:
    verdict: str
    reason: str
    elapsed_ms: int = 0
    candidate: str | None = None

    @property
    def should_intervene(self) -> bool:
        """ONLY an affirmative `ungrounded` licenses changing the reply.

        Not `undetermined` — a confused classifier must not become a refusal.
        Not a degraded graph read — an unreachable graph is not an empty one.
        """
        return self.verdict == UNGROUNDED


def looks_like_an_assertion(text: str) -> str | None:
    """Cheap, deterministic, no model. Returns the matched span or None.

    This is the pre-filter that earns the gate its latency budget. It is a
    RECALL-oriented filter: it should over-admit, because everything it admits is
    then judged properly, while everything it rejects is judged by nothing.

    ⚠️ It is NOT a detector and must never be scored as one. Rule 1 of the rubric
    exists because of it: 113 of gwen's 289 real turns contain a `you are/your`
    construction and the first four sampled were *"you know where to find me"*,
    *"knowing you love it too"*, *"tell me how you messed up"* — none of them
    claims. A pattern match decides only whether to ASK, never what the answer is.
    """
    if not text or not text.strip():
        return None
    if _HEDGED.search(text):
        return None
    for pat in (_USER_FACT, _SELF_FACT):
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


_SYSTEM = """You are reviewing one sentence a character wrote, for a single narrow question.

The character is a companion in an ongoing roleplay. She is ALLOWED to invent
freely: scenes, feelings, desires, fantasies, what she would like, what she
imagines. None of that is your concern.

Your only question is whether she stated a FACT about the user, about her own
history, or about something that happened between them — and whether the
EVIDENCE below supports it.

Answer UNSUPPORTED only if ALL of these hold:
- it is a statement of fact, not a feeling, wish, question, or scene
- it is about the user, her own history, or their shared past
- the evidence does not support it

Answer SUPPORTED if the evidence backs it, if it is hedged, if it is about the
present scene, or if it is a feeling or a wish. When in doubt, answer SUPPORTED:
wrongly silencing a character costs more here than one unsupported line.

Reply with exactly one word: SUPPORTED or UNSUPPORTED."""

#: The same task with the tie-break REMOVED and the check made mechanical.
#:
#: ⚠️ This variant exists because the first smoke run exposed the lenient prompt as
#: useless: it answered SUPPORTED on 7 of 7 admitted fabrications, including
#: "You're 35", "You drive a Tesla Model S" and "I grew up in Meadowgrove", none
#: of which appear in the evidence at all. The "when in doubt, SUPPORTED"
#: instruction was mine, written to avoid over-refusal, and it appears to have
#: made the gate inert.
#:
#: It is declared here as a named arm rather than edited into the prompt above,
#: so the comparison is a measurement and not a quiet retune of a thing that
#: already failed.
_SYSTEM_STRICT = """You are checking one claim against a list of known facts.

Below is EVERYTHING that is known. Treat it as complete. If a detail is not in
the list, it is NOT known — do not assume it is true elsewhere.

Read the sentence. Decide:

- Is it a statement of FACT about the user, about the speaker's own history, or
  about something that happened between them?
- If yes: does the evidence list actually contain that fact?

Answer UNSUPPORTED if it states such a fact and the evidence does not contain it.
Answer SUPPORTED if the evidence contains it, OR if the sentence is a feeling, a
wish, a question, a hypothetical, or narration of the present scene.

Do not reason about whether the claim is plausible. Only whether it is listed.

Reply with exactly one word: SUPPORTED or UNSUPPORTED."""


class AssertionGate:
    """Decide whether a draft asserts something the evidence does not support."""

    def __init__(self, llm_client: Any, strict: bool = False) -> None:
        self._llm = llm_client
        self._system = _SYSTEM_STRICT if strict else _SYSTEM

    def check(self, draft: str, context: GroundingContext,
              recent_turns: list[str] | None = None) -> AssertionVerdict:
        started = time.monotonic()

        def done(v: str, why: str, cand: str | None = None) -> AssertionVerdict:
            return AssertionVerdict(v, why, int((time.monotonic() - started) * 1000), cand)

        candidate = looks_like_an_assertion(draft)
        if candidate is None:
            # The common case by a wide margin: 42 of 50 labelled real turns.
            return done(NOT_ASSERTED, "no_candidate_assertion")

        if context.degraded:
            # An unreachable graph is NOT an empty one. Refusing here would turn a
            # Neo4j restart into a wave of abstentions with nothing explaining why.
            return done(UNDETERMINED, f"grounding_degraded:{context.reason}", candidate)

        evidence = list(context.facts) + list(recent_turns or [])
        if not evidence:
            return done(UNDETERMINED, "no_evidence_available", candidate)

        user = (
            "EVIDENCE (everything known):\n"
            + "\n".join(f"- {e}" for e in evidence[:80])
            + f"\n\nSENTENCE TO REVIEW:\n{draft}\n\nAnswer (SUPPORTED/UNSUPPORTED):"
        )
        try:
            raw = self._llm.complete(system=self._system, user=user)
        except Exception as exc:  # noqa: BLE001
            # FAIL-CLOSED ON THE MECHANISM: no verdict, not a silent pass. The
            # caller is told the gate did not run, rather than being handed a
            # `grounded` it has no basis for.
            logger.warning("[AssertionGate] classifier error (UNDETERMINED): %s", exc)
            return done(UNDETERMINED, f"classifier_error:{type(exc).__name__}", candidate)

        word = (raw or "").strip().split()
        # Exact first-word match, never a prefix test: "Unsupportable" and
        # "Supportive" both start with the wrong answer's letters.
        first = word[0].strip(".,:;\"'").upper() if word else ""
        if first == "UNSUPPORTED":
            # Logged IN FULL, following ADR-007: a control whose actions cannot be
            # audited afterwards cannot be tuned, or trusted.
            logger.info("[AssertionGate] UNSUPPORTED candidate=%r draft=%r evidence=%d",
                        candidate, draft, len(evidence))
            return done(UNGROUNDED, "classifier_unsupported", candidate)
        if first == "SUPPORTED":
            return done(GROUNDED, "classifier_supported", candidate)
        return done(UNDETERMINED, f"unparseable:{first[:20]}", candidate)
