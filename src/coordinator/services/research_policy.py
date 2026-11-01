# src/coordinator/services/research_policy.py
"""What research each persona may do. Read by the coordinator, never by the model.

Which provider answers is not here: an nsfw persona's executor has no Brave client at
all, so that guarantee cannot be configured away by a policy value.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..config import get_settings
from ..schemas import SOURCES_LINE
from .query_resolution_service import QueryResolutionService
from .research_service import asks_for_fresh_facts, mentions_someone

_ASKS = re.compile(
    r"\?\s*$|^(what|which|who|when|where|why|how|is|are|does|do|can|could|and)\b.*\b\w+\b.*\b\w+\b",
    re.IGNORECASE,
)
_MIN_FOLLOWUP_WORDS = 3

_CONSEQUENTIAL = re.compile(
    r"\b(invest|investing|investment|tax|taxes|loan|mortgage|pension|insurance|dose|dosage"
    r"|medication|medicine|symptom|symptoms|diagnosis|treatment|side effects?|pregnan\w*"
    r"|password|seed phrase|private key|vulnerability|malware|legal|lawsuit|contract)\b",
    re.IGNORECASE,
)
_CAPITALIZED = re.compile(r"\b[A-ZÄÖÜ][\w'-]{2,}\b")
_PROPER_NOUN = re.compile(r"(?<=[a-zäöü,] )[A-ZÄÖÜ][\w'-]{2,}\b")
_REMEMBERED = re.compile(r"<remembered>(.*?)</remembered>", re.DOTALL)


@dataclass(frozen=True)
class ResearchPolicy:
    proposers: frozenset
    #: How many earlier user turns a query may draw on. 0 means no limit.
    query_turns: int = 0
    compact_sources: bool = False
    constraint_reminder: bool = False
    #: A strong persona can talk past the question, so check that it was answered.
    check_answered: bool = False
    #: A reply to a personal message stays in the conversation.
    personal_turns: bool = True


_ORDINARY = ResearchPolicy(proposers=frozenset({"router", "freshness", "followup", "model"}))
_NSFW = ResearchPolicy(
    # A query the model writes is composed under her persona and the scene.
    proposers=frozenset({"router", "freshness", "followup"}),
    query_turns=2,
    compact_sources=True,
    constraint_reminder=True,
    check_answered=True,
    personal_turns=False,
)


def policy_for(card: dict[str, Any], surface: Sequence[str]) -> ResearchPolicy | None:
    search = get_settings().search
    if not search.research_service_enabled:
        return None
    if card.get("nsfw"):
        return _NSFW if card.get("research") and search.research_nsfw_enabled else None
    return _ORDINARY if "web_search" in surface else None


def proposal(
    policy: ResearchPolicy, needs_web: bool, message: str, history: Sequence[Any]
) -> str | None:
    """Which proposer fires for this turn, if any. One answer, so one run."""
    if needs_web and "router" in policy.proposers:
        if policy.personal_turns or not mentions_someone(message):
            return "router"
    if "freshness" in policy.proposers and asks_for_fresh_facts(message):
        return "freshness"
    if "followup" in policy.proposers and history and _follows_research(message, history):
        return "followup"
    return None


def _follows_research(message: str, history: Sequence[Any]) -> bool:
    return (
        _last_reply_cited(message, history)
        and "\n" not in message.strip()
        # An acknowledgement is not a question: "thanks", "ok lol", "why?".
        and len(message.split()) >= _MIN_FOLLOWUP_WORDS
        and bool(_ASKS.search(message.strip()))
        and QueryResolutionService._looks_like_followup(message)
    )


def _last_reply_cited(message: str, history: Sequence[Any]) -> bool:
    """Her previous reply, which may be several bubbles, carried a citation.

    A session's history can end on the current user turn, so that one is skipped.
    """
    for t in reversed(history):
        if t.role == "user":
            if t.content.strip() == message.strip():
                continue
            return False
        if t.role == "assistant" and SOURCES_LINE.search(t.content or ""):
            return True
    return False


def is_consequential(query: str) -> bool:
    return bool(_CONSEQUENTIAL.search(query or ""))


def private_terms(card: dict[str, Any], recalled: str | None) -> set:
    """Names and proper nouns from what she remembers, and her own names."""
    names = [card.get("key", ""), card.get("display_name", ""), "daddy"]
    # Only what she remembers, not her whole context, which is full of ordinary words.
    # Mid-sentence, because a sentence's first word is capitalized whatever it is.
    remembered = " ".join(_REMEMBERED.findall(recalled or ""))
    found = _PROPER_NOUN.findall(remembered) + _CAPITALIZED.findall(" ".join(names))
    return {t.casefold() for t in found} | {n.casefold() for n in names if n}


def query_is_clean(query: str, message: str, private: set) -> bool:
    """Nothing she remembers was added beyond what the operator typed in this message.

    The rewriter sees a few user turns and nothing else, so that input is the boundary.
    A word check on its output rejected good rewrites over "place" and "german". This
    only guards what those turns can still carry: a name from earlier in the scene.
    """
    typed = {w.casefold() for w in re.findall(r"\w+", message)}
    added = {w.casefold() for w in re.findall(r"\w+", query)} - typed
    return not added & private


def carries_private_term(message: str, private: set) -> bool:
    return bool({w.casefold() for w in re.findall(r"\w+", message)} & private)


class HourlyCap:
    """A soft limit per session, so a long scene cannot get this IP blocked."""

    def __init__(self):
        self._times: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, session: str, limit: int, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        times = self._times[session]
        while times and now - times[0] > 3600:
            times.popleft()
        if len(times) >= limit:
            return False
        times.append(now)
        return True


research_cap = HourlyCap()


def recent_user_turns(history: Sequence[Any], limit: int) -> list[str]:
    turns = [t.content for t in history if t.role == "user"]
    return turns[-limit:] if limit else turns
