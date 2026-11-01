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
from .query_resolution_service import QueryResolutionService, query_origin_ok
from .research_service import asks_for_fresh_facts, mentions_someone

#: Her reply carries this when it was built from research.
SOURCES_MARK = "🔍"

_CONSEQUENTIAL = re.compile(
    r"\b(invest|investing|investment|tax|taxes|loan|mortgage|pension|insurance|dose|dosage"
    r"|medication|medicine|symptom|symptoms|diagnosis|treatment|side effects?|pregnan\w*"
    r"|password|seed phrase|private key|vulnerability|malware|legal|lawsuit|contract)\b",
    re.IGNORECASE,
)
_CAPITALIZED = re.compile(r"\b[A-ZÄÖÜ][\w'-]{2,}\b")


@dataclass(frozen=True)
class ResearchPolicy:
    proposers: frozenset
    #: How many earlier user turns a query may draw on. 0 means no limit.
    query_turns: int = 0
    #: User turns shown to synthesis, so she does not lose the thread.
    context_turns: int = 0
    compact_sources: bool = False
    constraint_reminder: bool = False
    #: A reply to a personal message stays in the conversation.
    personal_turns: bool = True


_ORDINARY = ResearchPolicy(proposers=frozenset({"router", "freshness", "followup", "model"}))
_NSFW = ResearchPolicy(
    # A query the model writes is composed under her persona and the scene.
    proposers=frozenset({"router", "freshness", "followup"}),
    query_turns=2,
    context_turns=2,
    compact_sources=True,
    constraint_reminder=True,
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
    last = history[-1]
    return (
        last.role == "assistant"
        and SOURCES_MARK in (last.content or "")
        and "\n" not in message.strip()
        and QueryResolutionService._looks_like_followup(message)
    )


def is_consequential(query: str) -> bool:
    return bool(_CONSEQUENTIAL.search(query or ""))


def private_terms(card: dict[str, Any], recalled: str | None) -> set:
    """Names and proper nouns from what she remembers, and her own names."""
    names = [card.get("key", ""), card.get("display_name", ""), "daddy"]
    found = _CAPITALIZED.findall(recalled or "") + _CAPITALIZED.findall(" ".join(names))
    return {t.casefold() for t in found} | {n.casefold() for n in names if n}


def query_is_clean(query: str, message: str, turns: Sequence[str], private: set) -> bool:
    """The rule for a query built from more than the current message.

    Words must come from the allowed turns, and nothing she remembers may be added.
    What the operator typed in this message is theirs to send.
    """
    if not query_origin_ok(query, turns):
        return False
    typed = {w.casefold() for w in re.findall(r"\w+", message)}
    added = {w.casefold() for w in re.findall(r"\w+", query)} - typed
    return not added & private


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
