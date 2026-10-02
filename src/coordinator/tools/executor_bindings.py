# src/coordinator/tools/executor_bindings.py
"""Bind runtime executors onto registry ToolSpecs — ADR-008 TB2.

The registry declares tool definitions + safety policy at import (registrations.py);
this module attaches the *executors* at service-wiring time (startup). Until now
`registry.bind_executor` and `clamp_safesearch` were both dead code — this wires
them.

Executor contract (used by the TB3 tool-brain loop):

    executor(arguments: dict, persona_card: dict) -> Any

`arguments` are the model's native tool-call args; `persona_card` is in scope so
the search family can apply the per-persona nsfw safesearch clamp (a non-nsfw
persona can never drop below "moderate", enforced HERE — the only place the
persona and the tool call meet). Brave client + settings are resolved lazily at
call time (not captured at bind time) so binding is init-order-independent.

Scope (MVP, reads only): the web toolset — web_search / image_search /
video_search / news_search / fetch_url. Wallet tools stay on the existing
propose->confirm->execute / handle_wallet_query flow (the loop delegates to it,
they are NOT bound here). `extract` is LLM-dependent and bound by the loop.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

from .registry import registry
from .web_tool_generators import WEB_TOOL_CATEGORY
from .web_safesearch import clamp_safesearch
from .result_filters import filter_junk_results

logger = logging.getLogger(__name__)

# Lazy module-level singleton so the bge-m3 embedder is built at most once across
# all search calls (its constructor lazily resolves the RAG embedder on first
# use). Only ever instantiated when the relevance gate is flag-enabled.
_relevance_service = None


def _get_relevance_service():
    global _relevance_service
    if _relevance_service is None:
        from ..services.search_relevance_service import SearchRelevanceService
        _relevance_service = SearchRelevanceService()
    return _relevance_service


def _apply_relevance_filter(query: str, results: Any) -> Any:
    """Per-result bge-m3 relevance floor, flag-gated by SEARCH_RELEVANCE_GATE.

    Returns `results` unchanged when the gate is off, the query is empty, the set
    is falsy, or on any embedder error (fail-open). Never empties a non-empty set
    on its own (graceful — the deterministic denylist is the authoritative layer)."""
    from ..config import get_settings

    if not results or not query:
        return results
    cfg = get_settings().search
    if not cfg.relevance_gate_enabled:
        return results
    try:
        return _get_relevance_service().filter_relevant(
            query, results, cfg.relevance_min_cosine
        )
    except Exception as e:  # noqa: BLE001 - relevance must never break search
        logger.warning(f"[executor_bindings] relevance filter failed ({e}); failing open")
        return results


def _make_search_executor(tool_name: str) -> Callable[[Dict[str, Any], Dict[str, Any]], Any]:
    """Executor for a search-family tool. Applies the nsfw safesearch clamp,
    forces the tool's category, and routes through the SearXNG->Brave chain."""
    category = WEB_TOOL_CATEGORY.get(tool_name, "general")

    def executor(arguments: Dict[str, Any], persona_card: Dict[str, Any]) -> Any:
        # Lazy imports: avoid config/startup import cycles + init-order coupling.
        from ..startup import get_brave_client
        from ..services.search_execution_service import SearchExecutionService
        from ..tool_definitions import ToolCall
        from ..config import get_settings

        nsfw = registry.persona_nsfw(persona_card)
        default = get_settings().web_search.safesearch_default
        clamped = clamp_safesearch(arguments.get("safesearch"), nsfw, default)
        eff_category = arguments.get("category") or category
        args = {
            **arguments,
            "safesearch": clamped,            # per-persona floor enforced here
            "category": eff_category,
        }
        svc = SearchExecutionService(mcp_client=get_brave_client())
        results = svc.execute_search(ToolCall(name=tool_name, arguments=args))

        # Deterministic junk denylist (always-on, images only): strip icon-CDN /
        # placeholder / favicon / badge junk that aggregated image engines surface
        # on keyword collisions, before results reach synthesis or citations. The
        # never-empty fallback keeps a niche all-icon result set rather than
        # emptying it.
        results = filter_junk_results(results, eff_category)

        # Probabilistic relevance floor (flag-gated) — drop off-topic hits a
        # static denylist can't catch (e.g. a museum artwork whose title merely
        # contains the query word). Per-result, graceful (never empties a set on
        # its own), fail-open on embedder error.
        results = _apply_relevance_filter(arguments.get("query", ""), results)
        return results

    return executor


def _fetch_url_executor(arguments: Dict[str, Any], persona_card: Dict[str, Any]) -> Any:
    """Executor for fetch_url (SSRF-guarded, size/timeout-capped in the service)."""
    from ..services.web_fetch_service import fetch_url

    url = arguments.get("url", "")
    mode = arguments.get("mode", "markdown")
    return fetch_url(url, mode)


# Tools bound here (web toolset, reads only). Wallet + extract are handled by
# the TB3 loop (HITL flow / LLM), not bound as plain executors.
_SEARCH_TOOLS = ("web_search", "image_search", "video_search", "news_search")


def bind_web_executors() -> List[str]:
    """Attach web-toolset executors onto the registry. Idempotent (safe to call
    more than once). Returns the list of tool names bound. Skips any tool not
    registered (defensive) rather than raising, so a partial registry never
    breaks startup."""
    from . import registrations  # noqa: F401 - ensure specs registered

    bound: List[str] = []
    for name in _SEARCH_TOOLS:
        if registry.get(name) is None:
            logger.warning(f"[executor_bindings] tool '{name}' not registered — skipped")
            continue
        registry.bind_executor(name, _make_search_executor(name))
        bound.append(name)
    if registry.get("fetch_url") is not None:
        registry.bind_executor("fetch_url", _fetch_url_executor)
        bound.append("fetch_url")
    if registry.get("generate_image") is not None:
        from .image_executor import generate_image_executor

        registry.bind_executor("generate_image", generate_image_executor)
        bound.append("generate_image")
    logger.info(f"[executor_bindings] bound web executors: {bound}")
    return bound
