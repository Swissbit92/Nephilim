# src/coordinator/tools/intent_classifier.py
"""Intent classification for query routing to appropriate MCP services."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from .keywords import (
    EXPLICIT_SEARCH_COMMANDS,
    WALLET_FASTPATH,
)

logger = logging.getLogger(__name__)


# Negation detection: action verbs prefixed with "not"/"don't" reverse intent.
# Prevents "not buying Bitcoin" or "I'm not going to trade SOL" from triggering NEEDS_WALLET.
# Handles: infinitives (buy), gerunds (buying), and multi-word bridges (going to, want to).
_NEGATED_ACTION = re.compile(
    r"\b(?:not|don't|dont|never)\s+"
    r"(?:(?:going|want(?:ing)?|planning)\s+to\s+)?"
    r"(?:buy(?:ing)?|sell(?:ing)?|swap(?:ping)?|trad(?:e|ing)|exchang(?:e|ing)|purchas(?:e|ing))\b",
    re.IGNORECASE,
)

# Media-search fast-path (2026-07-05): colloquial "find me images / find me a
# video / show me pics" must route to web search so the tool brain's
# image_search/video_search fire. REQUIRES a media noun near a fetch verb — so
# it catches those but NOT bare "find me" RP ("come find me when you're ready",
# "I hope you find me pretty") which has no media noun. More precise than a
# semantic example (which over-routes "find me" RP; measured 2026-07-05).
_MEDIA_VERB = r"\b(?:find|show|get|give|pull\s+up|search(?:\s+for)?|look\s+(?:up|for))\b[^.?!]{0,25}?\b"
_IMAGE_NOUN = r"(?:image|images|pic|pics|picture|pictures|photo|photos|gif|gifs)\b"
_VIDEO_NOUN = r"(?:video|videos|vid|vids|clip|clips|movie|movies|footage)\b"
_MEDIA_IMAGE = re.compile(_MEDIA_VERB + _IMAGE_NOUN, re.IGNORECASE)
_MEDIA_VIDEO = re.compile(_MEDIA_VERB + _VIDEO_NOUN, re.IGNORECASE)
# Combined matcher (kept for the fast-path route decision).
_MEDIA_SEARCH = re.compile(
    _MEDIA_VERB + r"(?:" + _IMAGE_NOUN + r"|" + _VIDEO_NOUN + r")",
    re.IGNORECASE,
)


def media_search_type(query: str) -> Optional[str]:
    """Return "video", "image", or None for a colloquial media-find query.

    Lets the tool-brain route DETERMINISTICALLY narrow the offered surface to the
    single matching media tool (video_search/image_search) — native calling is
    unreliable at picking video_search among 4 web tools, but reliably calls the
    one tool it's given (2026-07-05: "find me a video" fix). Video checked first
    so "video clip" etc. never mis-tag as image."""
    if _MEDIA_VIDEO.search(query):
        return "video"
    if _MEDIA_IMAGE.search(query):
        return "image"
    return None


class QueryIntent(Enum):
    """Query intent classification for MCP routing."""
    NEEDS_WEB_SEARCH = "web"      # Brave MCP
    NEEDS_NEITHER = "llm"          # Pure LLM
    NEEDS_WALLET = "wallet"        # Jupiter wallet / Solana trading


def _brave_accessible(mcp_access: Optional[List[str]], persona_rarity: str) -> bool:
    """Whether Brave web search is available — per-persona mcp_access takes priority,
    else rarity-based fallback. Mirrors the legacy inline logic."""
    if mcp_access is not None:
        return "brave_search" in mcp_access
    return persona_rarity.lower() in {"rare", "epic", "legendary"}


@dataclass(frozen=True)
class IntentDecision:
    """An intent, plus whether the classifier was actually able to decide it.

    `NEEDS_NEITHER` means "no confident route — answer conversationally". It was
    ALSO what came back when the embedding service could not be reached at all,
    and downstream had no way to tell those apart. `capability_scope` reads
    `NEEDS_NEITHER` as "no tool is required, nothing is missing", so an outage
    silently satisfied the out-of-surface guard.

    `classifier_available=False` says: this intent is a DEFAULT, not a finding.
    """

    intent: QueryIntent
    classifier_available: bool = True


def _classify_semantic_primary(
    query: str,
    query_lower: str,
    can_use_brave: bool,
    can_use_wallet: bool,
    routing,
) -> IntentDecision:
    """Semantic-PRIMARY intent classification (flag-ON path).

    Order: high-precision keyword fast-path → bge-m3 semantic router → NEEDS_NEITHER.
    Returns an `IntentDecision` so a caller can tell a routing DECISION from a
    routing OUTAGE — see that class.
    Follow-up detection is handled by the caller before this runs. Deliberately does
    NOT fall back to the fuzzy SEARCH_KEYWORDS/WALLET_KEYWORDS lists — the whole point
    is to route ambiguous queries by intent similarity, not keyword presence. A miss
    falls through to NEEDS_NEITHER (pure LLM), the safe default for a companion.
    """
    # 1. Keyword fast-path — high-precision, zero-latency, no embed round-trip.
    if can_use_wallet and any(kw in query_lower for kw in WALLET_FASTPATH):
        if not _NEGATED_ACTION.search(query_lower):
            return IntentDecision(QueryIntent.NEEDS_WALLET)
    if can_use_brave and any(kw in query_lower for kw in EXPLICIT_SEARCH_COMMANDS):
        return IntentDecision(QueryIntent.NEEDS_WEB_SEARCH)
    # Colloquial media-find ("find me images", "show me a video") — precise
    # verb+media-noun rule, so bare "find me" RP never matches (see _MEDIA_SEARCH).
    if can_use_brave and _MEDIA_SEARCH.search(query_lower):
        return IntentDecision(QueryIntent.NEEDS_WEB_SEARCH)

    # 2. Semantic router — the primary decision.
    #
    # `raise_on_unavailable=True` is the whole point: the router used to answer
    # "could not run" and "ran, found nothing" with the same `None`, so an outage
    # arrived here indistinguishable from a verdict and fell through to the safe
    # default. The default is only safe when it was actually CHOSEN.
    available = True
    try:
        from .semantic_router import EmbeddingsUnavailable, route_by_embedding
        semantic_intent = route_by_embedding(
            raise_on_unavailable=True,
            query=query,
            can_use_brave=can_use_brave,
            can_use_mongodb=False,
            can_use_wallet=can_use_wallet,
            threshold=routing.semantic_threshold,
            margin=routing.semantic_margin,
            drop_llm_only_centroid=True,
        )
        if semantic_intent == "wallet":
            # Negation guard applies to semantic wallet results too.
            if not _NEGATED_ACTION.search(query_lower):
                return IntentDecision(QueryIntent.NEEDS_WALLET)
        elif semantic_intent == "web_search":
            return IntentDecision(QueryIntent.NEEDS_WEB_SEARCH)
    except EmbeddingsUnavailable as exc:
        # The router could not run. Still non-fatal — the turn gets NEEDS_NEITHER
        # and answers conversationally, exactly as before — but it is now MARKED,
        # so the out-of-surface guard is not handed a default dressed as a finding.
        logger.warning(
            "[IntentClassifier] semantic router unavailable (%s) — "
            "intent defaults to NEEDS_NEITHER and is marked unclassified", exc,
        )
        available = False
    except Exception as exc:
        # SIBLING PATH — the same defect, one branch over, and it survived the fix
        # that named it. An ImportError, a TypeError inside the router, a config
        # error raised mid-call: none of them are `EmbeddingsUnavailable`, and all
        # of them mean the turn was NOT classified. Marking only the tidy failure
        # left the untidy ones reporting a default as a finding, which is the exact
        # conflation this module was just changed to remove.
        #
        # Still non-fatal — the turn answers conversationally — but it is marked,
        # so the tool gate cannot mistake it for a decision.
        logger.warning(
            "[IntentClassifier] semantic router failed (%s: %s) — intent defaults "
            "to NEEDS_NEITHER and is marked unclassified",
            type(exc).__name__, exc,
        )
        available = False

    # 3. No confident route → pure LLM.
    return IntentDecision(QueryIntent.NEEDS_NEITHER, classifier_available=available)


def classify_query_intent_ex(
    query: str,
    persona_rarity: str,
    mcp_access: Optional[List[str]] = None,
    last_assistant_message: Optional[str] = None,
) -> QueryIntent:
    """
    Layer 1: Fast keyword-based intent classification for MCP routing.

    Args:
        query: User query string
        persona_rarity: Persona rarity level (common, rare, epic, legendary)
        mcp_access: Optional explicit list of allowed MCP services from the persona
                    JSON ``mcp_access`` field (e.g. ``["brave_search"]``).
                    When provided this takes priority over ``persona_rarity``-based
                    gating entirely.
        last_assistant_message: Optional last assistant message for follow-up detection.
                    When the last message was wallet-related and the user gives a short
                    affirmative, we route to NEEDS_WALLET for continuity.

    Returns:
        IntentDecision — the intent, plus whether the classifier could actually
        run. See `IntentDecision`; `classify_query_intent` returns just the intent
        for the callers that do not need the distinction.
    """
    query_lower = query.lower()

    # Determine wallet access
    can_use_wallet = "solana_wallet" in (mcp_access or [])

    # Follow-up detection: short affirmative after wallet-related assistant message
    if can_use_wallet and last_assistant_message:
        _AFFIRMATIVES = [
            "yes", "yeah", "yep", "sure", "ok", "okay", "please",
            "show me", "do it", "go ahead", "let's go", "lets go",
            "absolutely", "definitely", "of course", "y",
        ]
        _WALLET_CONTEXT_KEYWORDS = [
            "wallet", "balance", "swap", "trade", "sol", "usdc",
            "address", "create", "strategy", "rsi", "jupiter",
        ]
        query_stripped = query_lower.strip().rstrip("!.?")
        last_lower = last_assistant_message.lower()
        is_short_affirmative = any(query_stripped == a or query_stripped.startswith(a + " ") for a in _AFFIRMATIVES)
        last_was_wallet = any(kw in last_lower for kw in _WALLET_CONTEXT_KEYWORDS)
        if is_short_affirmative and last_was_wallet:
            # A deterministic, embedding-free decision — the classifier was not
            # needed, so it counts as available.
            return IntentDecision(QueryIntent.NEEDS_WALLET)

    # ------------------------------------------------------------------
    # Semantic router is the primary (and only) intent classifier.
    # ROUTING_SEMANTIC_PRIMARY was retired 2026-07-04 (audit cleanup step 5):
    # the bge-m3 semantic path had graduated to the prod default, so the legacy
    # keyword-first body was removed. Follow-up detection above is shared. A
    # persona with no MCP capability has nothing to route to → pure LLM.
    # ------------------------------------------------------------------
    # `_routing is None` has TWO causes and they are not the same fact: settings
    # failed to load (an outage), or this persona has no routable capability (a
    # decision). Collapsing them is how the second sibling fail-open got here.
    _settings_loaded = True
    try:
        from ..config import get_settings
        _routing = get_settings().routing
    except Exception as exc:
        logger.warning(
            "[IntentClassifier] routing settings unavailable (%s: %s) — "
            "intent defaults to NEEDS_NEITHER and is marked unclassified",
            type(exc).__name__, exc,
        )
        _routing = None
        _settings_loaded = False
    _can_use_brave = _brave_accessible(mcp_access, persona_rarity)
    if _routing is not None and (can_use_wallet or _can_use_brave):
        return _classify_semantic_primary(
            query, query_lower, _can_use_brave, can_use_wallet, _routing
        )
    # A persona with no routable capability is a genuine DECISION — nothing to
    # route to, so NEEDS_NEITHER is the right answer and the classifier is not
    # "unavailable". But arriving here because settings would not load is an
    # OUTAGE wearing the same return value.
    return IntentDecision(
        QueryIntent.NEEDS_NEITHER, classifier_available=_settings_loaded
    )


def classify_query_intent(
    query: str,
    persona_rarity: str,
    mcp_access: Optional[List[str]] = None,
    last_assistant_message: Optional[str] = None,
) -> QueryIntent:
    """Back-compat wrapper: the intent alone.

    Every pre-existing caller keeps its exact contract. Only the out-of-surface
    guard needs to know whether the classifier could run, and it calls
    `classify_query_intent_ex`. Adding the field to this return type instead would
    have touched every call site to fix one.
    """
    return classify_query_intent_ex(
        query, persona_rarity, mcp_access, last_assistant_message
    ).intent
