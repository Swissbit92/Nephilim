# src/coordinator/services/groundedness_gate_service.py
"""
Groundedness Gate Service - Catch confident fabrication when routing itself
missed a search-worthy query (ADR-007).

Distinct from the SearchSettings guards (query_resolution, relevance_gate):
those live INSIDE the tool-calling path and only run when the intent router
already decided a tool call was needed. This gate covers the case where
routing decided NO tool was needed at all — routes/chat.py's `if not tools:`
branch — where none of the existing anti-hallucination guards are reachable.

2026-07-04 incident (session dcc3693d): "What was their last match?" fell
through to NEEDS_NEITHER (no force-search pattern, no semantic-router match —
see docs/decisions/007-generation-time-groundedness-gate.md), and the bare LLM
completion fabricated a detailed, confident sports result with zero grounding.

Mechanism: after a draft response is generated with no tool call this turn,
ask the same loaded persona LLM a second, cheap yes/no question — does the
draft assert a specific, falsifiable, temporally-scoped real-world claim (a
score/date/outcome/statistic) with nothing backing it? If yes, the draft is
replaced with an honest offer-to-search. Narrowly scoped to avoid the named
top risk (false-abstention on legitimate persona lore or general knowledge) —
see the classifier prompt and tests/evaluation/groundedness_eval_set.json.

Fail-open by design, same principle as SearchRelevanceService: any classifier
error returns "not flagged" so the gate can never make a response worse than
the legacy (no-gate) path — it only ever *adds* honest abstentions on clearly
fabricated real-world claims.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_FLAG_LIVE_STATE = (
    "- The USER'S OWN REAL ACCOUNT OR PORTFOLIO STATE presented as known: that "
    "a position is hedged or unhedged, a balance or holding amount, what is "
    "open right now, what a strategy did recently. Nothing was fetched this "
    "turn, so any such statement is a guess wearing the clothes of a fact. "
    "This is about real money and real accounts — a character describing "
    "themselves or a scene is not this.\n"
)

# The exclusion that stops the gate eating fiction. The pre-existing roleplay
# clause covered a persona's BACKSTORY — who it is, what its world contains —
# but not the scene it is narrating right now. A companion writing "I'm on my
# knees, my hands trembling" is authoring fiction in the first person, yet the
# surface form is a specific, present-tense, falsifiable-sounding claim about a
# current state, which is exactly the shape the flags above describe. The gate
# duly fired mid-scene and replaced the reply with an offer to search
# (observed 2026-08-23). Fiction being generated is not a claim about the world.
_NO_FLAG_ROLEPLAY_SCENE = (
    "- IN-SCENE NARRATION the character is authoring right now: its own body, "
    "posture, sensations, feelings, actions, or what is happening in the "
    "fictional scene. This is creative writing being composed, not a claim "
    "about external reality, and it is never checkable by search. Present "
    "tense and specific physical detail are how fiction is written — they are "
    "not evidence of a factual assertion.\n"
)

# The exclusion that stops the gate eating analysis. Written as "reasoning ABOUT
# numbers the user gave you", not "anything with a modal verb", because the
# measured false-abstains were interpretive sentences over user-supplied figures
# ("a correlation of 0.7 indicates...") — and two of them contained no digits at
# all, so no numeral heuristic would have saved them.
_NO_FLAG_REASONING = (
    "- Reasoning, analysis, or arithmetic over figures THE USER SUPPLIED in "
    "their own question, or about how something works in general or under a "
    "stated condition. \"A correlation of 0.7 indicates a strong relationship\" "
    "is analysis of the user's own number, not a claim about the world. "
    "Explaining a mechanism (\"fees on both legs can exceed the funding "
    "accrued\") asserts nothing lookup-able.\n"
    # 2026-08-12 holdout: the gate destroyed "you ran 16 tests, five were
    # conclusive — that's a survival rate of 31%". The arithmetic is correct and
    # every input came from the user, but "a survival rate of 31%" pattern-matches
    # the word "statistic" in the flag list. DERIVED quantities need naming
    # explicitly; the general "arithmetic over user figures" clause above was
    # losing the conflict against the more concrete-sounding trigger.
    "- A quantity the draft COMPUTED, but ONLY when EVERY figure the sum rests "
    "on already appeared in the user's own message. \"You ran 16 tests and 5 "
    "were conclusive, a rate of 31%\" is arithmetic on what the user just said "
    "— there is nowhere to look it up, so there is nothing to verify.\n"
    "  Trace each number back before you excuse it. If the draft introduces a "
    "figure the user never gave — a current rate, price, balance, or level — "
    "then FLAG it, however correct the arithmetic built on top of it is. "
    "\"Funding is running about 0.031% per 8h, so you're netting 34% "
    "annualised\" must be flagged: the 34% is honest arithmetic, but the "
    "0.031% was never supplied and is the whole problem.\n"
)


def _classifier_system(live_state: bool = True) -> str:
    """Build the classifier prompt.

    Deliberately NOT a module constant any more: the live-state clause is
    flag-gated so the pre-2026-08-12 trigger definition remains reachable for a
    clean A/B and an instant revert.
    """
    return (
        "You are reviewing a DRAFT response before it is sent. No search, tool, "
        "or live data lookup was used to produce this draft.\n\n"
        "Flag the draft if it presents any of these as established fact:\n"
        "- A SPECIFIC, FALSIFIABLE, TIME-SENSITIVE claim ABOUT THE WORLD — a "
        "score, date, price, published statistic, or outcome of an actual "
        "real-world event (sports, elections, markets, breaking news). It must "
        "be something that could be looked up somewhere. A number the draft "
        "WORKED OUT from what the user just said is not this.\n"
        + (_FLAG_LIVE_STATE if live_state else "")
        + "\nFlag it even when the claim is only a PREMISE inside otherwise sound "
        "reasoning. A fabricated figure that the rest of the answer reasons "
        "correctly from is the most dangerous case, not an excuse — judge the "
        "premise on its own, not the argument built on it.\n\n"
        "Do NOT flag:\n"
        + _NO_FLAG_REASONING
        + "- In-character fictional/persona lore or worldbuilding (this is a "
        "roleplay companion; its own backstory or the world's fiction is not a "
        "real-world claim)\n"
        + _NO_FLAG_ROLEPLAY_SCENE
        + "- General, timeless knowledge (definitions, settled history, science "
        "concepts, \"capital of France\" style facts)\n"
        "- Opinions, feelings, creative writing, or hedged/uncertain language "
        "(\"I'm not sure, but...\", \"I don't have live access...\")\n"
        "- Responses that already reference search results or cite sources\n\n"
        "Answer with exactly one word: YES if the draft presents an unverified "
        "claim as fact, or NO otherwise."
    )


# Retained for the existing unit tests and for anyone importing the old symbol.
_CLASSIFIER_SYSTEM = _classifier_system(live_state=True)

_ABSTAIN_MESSAGE = (
    "I don't actually have grounded, up-to-date information on that — I'd "
    "rather admit that than guess or make something up. Want me to search "
    "for it?"
)


@dataclass(frozen=True)
class GroundednessVerdict:
    """Result of a groundedness check."""

    should_abstain: bool
    reason: str  # "flag_off" | "grounded" | "ungrounded" | "classifier_error_fail_open"


class GroundednessGateService:
    """Post-hoc classifier gating no-tool-call responses for ungrounded claims.

    Stateless apart from the injected LLM client.
    """

    def __init__(self, llm_client: Any):
        """Args:
        llm_client: object exposing complete(system: str, user_prompt: str) -> str,
            matching create_llm_client(card)'s interface (routes/chat.py's
            _complete_or_503 uses the same shape).
        """
        self.llm_client = llm_client

    def check(self, user_turn: str, drafted_response: str) -> GroundednessVerdict:
        """Check whether `drafted_response` should be replaced with an abstention.

        Never raises: on any classifier failure, fails open (never blocks a
        response the legacy path would have returned).
        """
        from ..config import get_settings

        if not get_settings().groundedness.gate_enabled:
            return GroundednessVerdict(should_abstain=False, reason="flag_off")

        try:
            user_prompt = (
                f"User asked: {user_turn}\n\n"
                f"Draft response:\n{drafted_response}\n\n"
                "Answer (YES/NO):"
            )
            system = _classifier_system(
                live_state=get_settings().groundedness.live_state_claims_enabled
            )
            raw_verdict = self.llm_client.complete(system, user_prompt)
            flagged = self._parse_verdict(raw_verdict)
            if flagged:
                # Log the draft IN FULL. Abstaining discards the model's answer
                # irrecoverably, and until 2026-08-12 only 80 characters were
                # kept — so when the gate was found to be destroying good
                # analysis, the evidence had to be reconstructed from a separate
                # run. A safety control whose actions cannot be audited after
                # the fact cannot be tuned, or trusted.
                logger.warning(
                    "[GroundednessGate] ABSTAINED. query=%r\n--- draft destroyed ---\n%s\n---",
                    user_turn,
                    drafted_response,
                )
            return GroundednessVerdict(
                should_abstain=flagged,
                reason="ungrounded" if flagged else "grounded",
            )
        except Exception as e:  # noqa: BLE001 - gate must never break chat
            logger.warning(f"[GroundednessGate] Classifier failed ({e}); failing open")
            return GroundednessVerdict(should_abstain=False, reason="classifier_error_fail_open")

    @staticmethod
    def _parse_verdict(raw: str) -> bool:
        """True iff the classifier's first word is exactly YES (case-insensitive).

        Exact match, not a prefix check — "Yesterday..." must not be misread as
        "YES" via naive startswith("YES") substring matching.
        """
        if not raw or not raw.strip():
            return False
        first_word = raw.strip().split()[0].strip(".,:;\"'").upper()
        return first_word == "YES"

    @staticmethod
    def abstain_message() -> str:
        """Fixed, voice-neutral honest-abstention string (persona post-processing
        already applies first-person/voice adjustments downstream, same as any
        other LLM output)."""
        return _ABSTAIN_MESSAGE
