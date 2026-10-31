# src/coordinator/services/search_execution_service.py
"""
Search Execution Service - Execute Brave web search tool calls.

Extracted from llm_client.py as part of Phase 2 Service Decomposition.
Handles MCP client invocation for web search operations.
"""

from __future__ import annotations

import logging
import re
from typing import Optional, List, Any

from .. import research_trace
from ..tool_definitions import ToolCall

logger = logging.getLogger(__name__)

# Queries about the immediate present/near future benefit from Brave's
# `freshness` filter (2026-07-05 incident: a "latest news in switzerland
# today" search surfaced a 5-day-old roundup; a weather query surfaced a
# generic 10-day-forecast page). Conservative, whole-word heuristics: when in
# doubt pass NO freshness filter (full index) — a too-narrow window can
# starve legitimate queries of results.
_FRESHNESS_DAY_RE = re.compile(
    r"\b(today|tonight|right now|breaking)\b", re.IGNORECASE
)
_FRESHNESS_WEEK_RE = re.compile(
    r"\b(tomorrow|weather|forecast|latest|this week|recent|news|headlines|"
    r"currently)\b",
    re.IGNORECASE,
)


def infer_freshness(query: str) -> Optional[str]:
    """Map temporal cues in a query to a Brave freshness filter.

    Returns 'pd' (past day) for immediate/now cues, 'pw' (past week) for
    recency cues, or None (no filter) when the query carries no temporal cue.
    """
    if not query:
        return None
    if _FRESHNESS_DAY_RE.search(query):
        return "pd"
    if _FRESHNESS_WEEK_RE.search(query):
        return "pw"
    return None


# Categories Brave cannot serve: its client takes no category parameter.
MEDIA_CATEGORIES = frozenset({"images", "videos"})


class SearchExecutionService:
    """Service for executing web search operations via MCP client.

    Handles:
    - Brave web search execution
    - Query validation
    - Error handling for search operations

    This service requires an MCP client for operation.
    """

    def __init__(self, mcp_client: Optional[Any] = None):
        """Initialize the search execution service.

        Args:
            mcp_client: Brave MCP client for web search (BraveMCPClientStdio)
        """
        self.mcp_client = mcp_client

    def execute_search(self, tool_call: ToolCall) -> Optional[List[Any]]:
        """Execute a Brave web search tool call.

        Args:
            tool_call: ToolCall with brave_web_search

        Returns:
            List of SearchResult objects, or None if search failed
        """
        try:
            query = tool_call.arguments.get("query", "")
            if not query:
                logger.warning("[SearchExecution] Search query is empty")
                return None

            # ADR-009 Phase W: backend chain. safesearch comes from the tool_call
            # args when the model/executor set it (per-persona nsfw clamp, W2),
            # else the global WEB_SAFESEARCH_DEFAULT. An nsfw persona reaches this
            # with no Brave client at all, which is what keeps it off Brave.
            from ..config import get_settings

            settings = get_settings()
            web = settings.web_search
            args = tool_call.arguments or {}
            safesearch = args.get("safesearch") or web.safesearch_default
            category = args.get("category") or "general"
            media = category in MEDIA_CATEGORIES

            if web.backend == "routed" and self.mcp_client and not media:
                results = self._search_brave_or_none(query, category, safesearch, settings)
                if results:
                    return results
                logger.info("[SearchExecution] Brave gave nothing, trying SearXNG")
                return self._try_searxng(query, category, safesearch, web)

            # SearXNG needs no Brave client — try it FIRST so a SearXNG-primary
            # deployment works even with Brave unconfigured (bug surfaced by the
            # ADR-008 live smoke: the old `if not self.mcp_client: return None`
            # guard bailed before SearXNG ever ran).
            searxng_results = self._try_searxng(
                query, category, safesearch, web
            )
            if searxng_results is not None:
                logger.info(
                    f"[SearchExecution] SearXNG returned {len(searxng_results)} results"
                )
                return searxng_results

            # search_web has no category, so Brave would answer an image search
            # with plain web results that synthesis then presents as images.
            if media:
                logger.warning(
                    f"[SearchExecution] SearXNG unavailable for {category} search; "
                    "not falling back to Brave — returning no results."
                )
                return None

            # --- Brave fallback (or backend='brave') --------------------------
            if not self.mcp_client:
                logger.warning(
                    "[SearchExecution] SearXNG gave no results and no Brave client "
                    "is available — returning no results."
                )
                return None
            return self._search_brave(query, category, safesearch, settings)

        except Exception as e:
            logger.error(f"[SearchExecution] Brave search failed: {e}", exc_info=True)
            return None

    def _search_brave(self, query, category, safesearch, settings):
        brave = settings.brave
        country = brave.country or None
        search_lang = brave.search_lang or None
        freshness = infer_freshness(query)

        logger.info(
            f"[SearchExecution] Executing Brave search: '{query}' "
            f"(country={country}, lang={search_lang}, freshness={freshness}, "
            f"safesearch={safesearch})"
        )
        results = self.mcp_client.search_web(
            query,
            country=country,
            search_lang=search_lang,
            freshness=freshness,
            safesearch=safesearch,
        )
        logger.info(f"[SearchExecution] Brave search returned {len(results)} results")
        research_trace.note_search("brave", category, len(results))
        return results

    def _search_brave_or_none(self, query, category, safesearch, settings):
        try:
            return self._search_brave(query, category, safesearch, settings)
        except Exception as e:  # noqa: BLE001 - routed mode has SearXNG behind this
            logger.warning(f"[SearchExecution] Brave search failed ({e})")
            research_trace.note_search("brave", category, None)
            return None

    def _try_searxng(self, query, category, safesearch, web_settings):
        """Attempt the SearXNG backend per config; None => fall through to Brave.

        Returns a results list (possibly empty) when SearXNG is the selected
        backend and the call succeeds; returns None to signal "use Brave" —
        either because the backend selection excludes SearXNG, it is not
        configured, or its call errored (transport failure falls back rather
        than failing the turn). When backend is 'searxng' (SearXNG-only), an
        error returns [] instead of None so we do NOT silently hit Brave.
        """
        backend = web_settings.backend
        base = (web_settings.searxng_base_url or "").strip()

        if backend == "brave":
            return None
        if backend == "auto" and not base:
            return None  # legacy Brave-only path
        if not base:
            logger.warning("[SearchExecution] backend=searxng but SEARXNG_BASE_URL unset")
            return None

        from ..searxng_client import SearxngClient
        from ..models.mcp_models import MCPError

        client = SearxngClient(base, timeout=web_settings.searxng_timeout)
        try:
            results = client.search(query, category=category, safesearch=safesearch)
            research_trace.note_search("searxng", category, len(results))
            return results
        except MCPError as e:
            logger.warning(f"[SearchExecution] SearXNG failed ({e})")
            research_trace.note_search("searxng", category, None)
            # searxng-only: honour the choice, don't leak to Brave -> empty.
            # auto: fall back to Brave.
            return [] if backend == "searxng" else None
