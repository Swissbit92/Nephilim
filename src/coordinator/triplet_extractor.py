"""ADR-006 Phase 1 (M3) — triplet fact extraction for the ontology-lite store.

Distinct from the legacy ``FactExtractor`` (which fills the flat user_profile JSON
blob): this produces subject-predicate-object triples mapped into the closed
``PREDICATE_VOCABULARY`` for the normalized ``memory_facts`` table. Runs on the
async worker (M3), never on the interactive path.

Reliability levers for a ~24B local model (research-backed, see ADR-006 P1):
- **Closed predicate vocabulary** handed to the model; anything outside it is
  dropped at parse time (fail-safe, not fail-loud — a stray predicate must not
  poison the store).
- **Few-shot with a deliberate empty-output example** to teach abstention and curb
  over-extraction ("Hi." → no facts).
- **Verbatim quote-span validation**: every accepted triple must carry a `quote`
  that actually appears in the transcript, string-matched before acceptance — a
  cheap guard against fabricated facts.
- **Batched** at the summarization cadence, not per turn (the caller's job).

The extractor is pure w.r.t. storage: it returns validated triples; the write
policy (recency-wins supersede) lives in ``fact_write_policy``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from .repositories.memory_fact_repository import PREDICATE_VOCABULARY

logger = logging.getLogger(__name__)

# One definition, shared with the legacy extractor — two spellings of this
# marker would let one path drift open again while the other stayed closed.
from .fact_extractor import _REDACTED_TURN  # noqa: E402

# Predicates whose object is another person/entity (not a literal value).
_ENTITY_OBJECT_PREDICATES = frozenset({"has_relationship", "has_pet"})

_EXTRACTION_SYSTEM = (
    "You extract durable facts about the USER from a conversation, as JSON triples. "
    "Return ONLY a JSON object, no prose, no markdown."
)

_FEWSHOT = """Extract durable facts about the user as triples. Use ONLY these predicates:
{vocab}

Rules:
- subject is "self" for facts about the user; use a person's name for facts about
  someone in their life (e.g. a sibling, partner, pet).
- object is a short literal value, or a person/thing name for relationship predicates.
- quote MUST be copied verbatim from the transcript — the exact span that supports the fact.
- Extract only durable facts (identity, relationships, preferences, goals, health,
  habits, ongoing concerns). Ignore small talk and one-off chatter.
- If there is nothing durable, return {{"facts": []}}.

Output JSON: {{"facts": [{{"subject": "...", "predicate": "...", "object": "...", "quote": "..."}}]}}

Example 1
Transcript:
User: Hey, I'm Raphael. I moved to Geneva last month and I've been learning Rust.
Assistant: Welcome, Raphael.
Output: {{"facts": [
  {{"subject": "self", "predicate": "has_name", "object": "Raphael", "quote": "I'm Raphael"}},
  {{"subject": "self", "predicate": "lives_in", "object": "Geneva", "quote": "I moved to Geneva last month"}},
  {{"subject": "self", "predicate": "is_learning", "object": "Rust", "quote": "I've been learning Rust"}}
]}}

Example 2
Transcript:
User: Hi there.
Assistant: Hello!
Output: {{"facts": []}}

Now extract from this transcript.
Transcript:
{transcript}
Output:"""


class TripletExtractor:
    def __init__(self, llm_client):
        self.llm = llm_client

    def extract_triples(
        self, messages: List[Dict[str, Any]], max_messages: int = 20
    ) -> List[Dict[str, Any]]:
        """Return validated triples from the recent transcript (may be empty)."""
        if not messages:
            return []
        transcript = self._format(messages, max_messages)
        prompt = _FEWSHOT.format(
            vocab=", ".join(sorted(PREDICATE_VOCABULARY)), transcript=transcript
        )
        try:
            raw = self.llm.complete(system=_EXTRACTION_SYSTEM, user_prompt=prompt)
        except Exception as e:  # extraction is best-effort; never break the turn
            logger.warning(f"[TripletExtract] LLM call failed: {e}")
            return []
        parsed = self._parse(raw)
        return self._validate(parsed, transcript)

    # -- internals --------------------------------------------------------

    def _format(self, messages: List[Dict[str, Any]], max_messages: int) -> str:
        recent = messages[-max_messages:] if len(messages) > max_messages else messages
        lines = []
        for m in recent:
            if m.get("role") == "narrator":
                continue  # ADR-011: scene direction, not a speaker's facts
            if m.get("role") != "user":
                # 🔴 Same hard rule as `fact_extractor._format_messages`, and
                # here it also REPAIRS the quote-span guard rather than
                # duplicating it.
                #
                # `_validate` rejects a triple whose `quote` is absent from the
                # transcript — but `norm_transcript` was built from the WHOLE
                # transcript including her turns, so a quote lifted verbatim
                # from her own reply passed with full "verbatim-supported"
                # credentials. Worse, `subject` defaults to "self" and
                # `fact_write_policy` maps "self" to the USER's entity, so an
                # assistant-sourced claim was stored as a user fact. The
                # few-shot examples even show an `Assistant:` line in the
                # transcript, teaching the model her text is in bounds.
                #
                # With her content absent, a lifted quote now fails the guard
                # that already exists.
                lines.append(_REDACTED_TURN)
                continue
            lines.append(f"User: {str(m.get('content', ''))[:500]}")
        return "\n".join(lines)

    def _parse(self, response: str) -> List[Dict[str, Any]]:
        """Robustly pull the facts array out of the model output."""
        obj = self._loads_lenient(response)
        if isinstance(obj, dict) and isinstance(obj.get("facts"), list):
            return [f for f in obj["facts"] if isinstance(f, dict)]
        if isinstance(obj, list):  # tolerate a bare array
            return [f for f in obj if isinstance(f, dict)]
        return []

    @staticmethod
    def _loads_lenient(response: str) -> Any:
        if not response:
            return None
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        m = re.search(r"\{.*\}", response, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        logger.warning("[TripletExtract] unparseable extraction output")
        return None

    def _validate(
        self, facts: List[Dict[str, Any]], transcript: str
    ) -> List[Dict[str, Any]]:
        """Keep only well-formed triples with an in-vocabulary predicate and a
        verbatim quote-span present in the transcript."""
        norm_transcript = self._norm(transcript)
        out: List[Dict[str, Any]] = []
        for f in facts:
            predicate = str(f.get("predicate", "")).strip()
            subject = str(f.get("subject", "")).strip() or "self"
            obj = str(f.get("object", "")).strip()
            quote = str(f.get("quote", "")).strip()
            if predicate not in PREDICATE_VOCABULARY:
                logger.debug(f"[TripletExtract] drop out-of-vocab predicate {predicate!r}")
                continue
            if not obj:
                continue
            if not quote or self._norm(quote) not in norm_transcript:
                logger.debug(f"[TripletExtract] drop unsupported fact (bad quote): {predicate} {obj!r}")
                continue
            out.append({
                "subject": subject,
                "predicate": predicate,
                "object": obj,
                "object_type": "entity" if predicate in _ENTITY_OBJECT_PREDICATES else "literal",
                "quote": quote,
            })
        return out

    @staticmethod
    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").lower()).strip()
