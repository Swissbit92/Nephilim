# src/coordinator/tool_definitions.py
# BACKWARD COMPATIBILITY WRAPPER
# This file re-exports all functions from the modular tools/ package
# to maintain compatibility with existing code that imports from tool_definitions.py

from __future__ import annotations

# Re-export keyword dictionaries
from .tools.keywords import (
    NO_SEARCH_KEYWORDS,
    SEARCH_KEYWORDS,
)

# Re-export intent classifier
from .tools.intent_classifier import (
    QueryIntent,
    classify_query_intent,
    classify_query_intent_ex,
)

# Re-export tool generators
from .tools.tool_generators import (
    get_brave_search_tool,
    AVAILABLE_TOOLS,
)

# Re-export synthesis prompts
from .tools.synthesis_prompts import (
    build_tool_system_prompt,
    build_synthesis_prompt,
)

# Re-export utility functions
from .tools.tool_utils import (
    ToolCall,
    should_use_keyword_filter,
    parse_tool_call,
    format_search_results_for_llm,
    get_tools_for_persona,
    get_tools_for_query,
)

# Explicit re-export surface (this module is a backward-compat shim).
__all__ = [
    "NO_SEARCH_KEYWORDS",
    "SEARCH_KEYWORDS",
    "QueryIntent",
    "classify_query_intent",
    "classify_query_intent_ex",
    "get_brave_search_tool",
    "AVAILABLE_TOOLS",
    "build_tool_system_prompt",
    "build_synthesis_prompt",
    "ToolCall",
    "should_use_keyword_filter",
    "parse_tool_call",
    "format_search_results_for_llm",
    "get_tools_for_persona",
    "get_tools_for_query",
]
