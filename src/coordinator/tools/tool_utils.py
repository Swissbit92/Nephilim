# src/coordinator/tools/tool_utils.py
# Utility functions for tool calling, search result formatting, and query routing
# Supports both static persona-based and dynamic intent-based tool injection

from __future__ import annotations

import json
import re
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

from .keywords import NO_SEARCH_KEYWORDS, SEARCH_KEYWORDS
from .intent_classifier import QueryIntent, classify_query_intent


@dataclass
class ToolCall:
    """Represents a function call from the LLM."""
    name: str
    arguments: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments
        }


def should_use_keyword_filter(query: str) -> Optional[bool]:
    """
    Quick keyword-based filter to prevent obvious false positives.

    Returns:
        True: Should search (has search keywords)
        False: Should NOT search (has no-search keywords)
        None: Uncertain, let LLM decide
    """
    query_lower = query.lower()

    # Check for no-search keywords
    for keyword in NO_SEARCH_KEYWORDS:
        if keyword in query_lower:
            # Exception: if also has search keywords, let LLM decide
            has_search_keyword = any(kw in query_lower for kw in SEARCH_KEYWORDS)
            if not has_search_keyword:
                return False

    # Check for search keywords
    for keyword in SEARCH_KEYWORDS:
        if keyword in query_lower:
            return True

    # No strong signal, let LLM decide
    return None


def parse_tool_call(response: str) -> Optional[ToolCall]:
    """
    Parse LLM response for function call.

    Args:
        response: Raw LLM response text

    Returns:
        ToolCall if found, None otherwise
    """
    # Try to find JSON in response
    try:
        # Check if entire response is JSON
        if response.strip().startswith('{'):
            data = json.loads(response)
            if "function_call" in data:
                fc = data["function_call"]
                return ToolCall(
                    name=fc["name"],
                    arguments=fc.get("arguments", {})
                )
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from text
    json_pattern = r'\{[^{}]*"function_call"[^{}]*\{[^{}]*\}[^{}]*\}'
    match = re.search(json_pattern, response, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if "function_call" in data:
                fc = data["function_call"]
                return ToolCall(
                    name=fc["name"],
                    arguments=fc.get("arguments", {})
                )
        except json.JSONDecodeError:
            pass

    return None


def format_search_results_for_llm(results: List[Any], max_results: int = 5) -> str:
    """
    Format search results for LLM context.

    Args:
        results: List of SearchResult objects from mcp_client
        max_results: Maximum number of results to include

    Returns:
        Formatted string with search results
    """
    if not results:
        return "No search results found."

    formatted = "Web search results:\n\n"

    for i, result in enumerate(results[:max_results], 1):
        formatted += f"{i}. {result.title}\n"
        formatted += f"   URL: {result.url}\n"
        formatted += f"   {result.description}\n"
        if hasattr(result, 'age') and result.age:
            formatted += f"   Published: {result.age}\n"
        formatted += "\n"

    formatted += (
        "\nIMPORTANT: Use this information to answer the user's question. "
        "You MUST cite your sources using markdown links at the end of your response:\n\n"
        "🔍 Sources:\n"
        "• [Title - Source Name](URL)\n"
    )

    return formatted


def get_tools_for_persona(
    persona_key: str,
    persona_rarity: str,
    mcp_access: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Get available tools based on persona rarity or explicit mcp_access (static approach).

    NOTE: For intent-based routing, use get_tools_for_query() instead.

    Args:
        persona_key: Persona identifier (e.g., "Eeva", "Gojo")
        persona_rarity: Persona rarity level ("common", "rare", "epic", "legendary")
        mcp_access: Optional explicit list of allowed MCP services from the persona
                    JSON ``mcp_access`` field.  When provided, overrides rarity gating.

    Returns:
        List of tool definitions
    """
    # ADR-009 R2/W2: sourced from the tool registry, behavior-identical to the
    # pre-registry mcp_access/rarity logic. This LEGACY path offers only the
    # legacy-executable tools (brave_web_search + the wallet toolset) — the new
    # generic web tools (web_search/fetch_url/...) are catalog-only until the
    # ADR-008 tool brain can execute them, and are exposed via the registry's
    # persona-grant methods (introspection / tool brain), not here.
    from .registry import registry
    from . import registrations  # noqa: F401 - ensure builtins registered

    card = {"key": persona_key, "rarity": persona_rarity}
    if mcp_access is not None:
        card["mcp_access"] = mcp_access
    granted = registry.toolsets_for_persona(card)

    tools: List[Dict[str, Any]] = []
    if "web" in granted:
        tools.append(registry.get("brave_web_search").definition())
    if "wallet" in granted:
        tools.extend(registry.definitions_for_toolsets(["wallet"]))
    return tools


def get_tools_for_query(
    query: str,
    persona_key: str,
    persona_rarity: str,
    mcp_access: Optional[List[str]] = None,
    precomputed_intent: Optional[QueryIntent] = None,
    persona_card: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Layer 2: Dynamic tool injection based on query intent, filtered by what the
    persona is actually GRANTED.

    Args:
        query: User query string
        persona_key: Persona identifier
        persona_rarity: Persona rarity level
        mcp_access: Optional explicit list of allowed MCP services from the persona
                    JSON ``mcp_access`` field.  When provided, overrides rarity gating.
        precomputed_intent: If the caller already classified intent (e.g. chat.py,
                    which classifies before calling this), pass it here to skip a
                    redundant ``classify_query_intent`` call — which, under semantic
                    routing, would be a second Ollama embedding round-trip. None =
                    classify internally (backward compatible). Passing it also fixes
                    a latent bug: this internal call omitted ``last_assistant_message``,
                    so a short wallet follow-up ("yes") built an empty tool list here.
        persona_card: The FULL persona card. Required for the card's ``tools``
                    allowlist to be honoured — a card reconstructed from the three
                    scalars above has no ``tools`` key, so the allowlist is invisible
                    to it. Omitting this degrades to toolset-level gating, never to
                    no gating.

    Returns:
        Tool definitions for this query that the persona is actually granted.

    This function used to do NO persona authorization at all. It returned
    ``brave_web_search`` on any web intent and the whole wallet toolset on any
    wallet intent, trusting that its one caller had already checked the grants
    while classifying intent. Measured 2026-09-24: forced to ``NEEDS_WALLET``, it
    handed a persona whose ``mcp_access`` is only ``["brave_search"]`` all seven
    wallet tools. Not reachable in production — ``routes/chat.py`` classifies with
    the persona's ``mcp_access``, so that intent cannot arise for her — but the
    function had no defence of its own, and "one caller happens to check first" is
    a single point of failure, not an authorization model.

    It also meant a persona-card ``tools`` allowlist was honoured only on the
    ADR-008 tool-brain path: gwen is scoped to image/video search (ADR-008) and
    the legacy path handed her ``brave_web_search`` anyway.
    """
    if precomputed_intent is not None:
        intent = precomputed_intent
    else:
        intent = classify_query_intent(query, persona_rarity, mcp_access=mcp_access)

    from .capability_scope import GENERAL_LOOKUP_TOOLS
    from .registry import registry
    from . import registrations  # noqa: F401 - ensure builtins registered

    # Fall back to a card built from the scalars when the caller passes none. That
    # still resolves toolsets, so the degraded mode is *toolset-level* gating —
    # strictly more restrictive than the previous behaviour of none at all. An
    # optional authorization argument must never default to skipping the check.
    card = persona_card
    if card is None:
        if mcp_access is None:
            # Nothing to authorize against: no card AND no mcp_access. Deny rather
            # than fall through to the legacy rarity fallback, which would hand a
            # rare/epic/legendary persona `brave_web_search` on the strength of a
            # rarity string alone. `None` means "grants unknown", never "unrestricted".
            return []
        card = {"key": persona_key, "rarity": persona_rarity, "mcp_access": mcp_access}

    if intent == QueryIntent.NEEDS_WEB_SEARCH:
        # Resolve through specs_for_persona so the card's `tools` allowlist applies.
        # The offer stays narrowed to brave_web_search: it is the only web tool the
        # legacy force-search path can execute (tool_calling_service keys on that
        # literal name), so returning the persona's other web tools would route the
        # turn into chat.py's `else` branch, which cannot run them and logs nothing.
        granted = {s.name for s in registry.specs_for_persona(card)}
        if granted & GENERAL_LOOKUP_TOOLS:
            return [registry.get("brave_web_search").definition()]
        return []
    if intent == QueryIntent.NEEDS_WALLET:
        if "wallet" in registry.toolsets_for_persona(card):
            return registry.definitions_for_toolsets(["wallet"])
        return []
    # QueryIntent.NEEDS_NEITHER → empty tools list
    return []
