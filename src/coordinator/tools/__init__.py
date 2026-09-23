# src/coordinator/tools/__init__.py
# Tools package for LLM function calling and intent classification
# This package provides modular components for:
# - Query intent classification (Brave Search, Wallet, or neither)
# - Tool definition generation (OpenAI-compatible function definitions)
# - Synthesis prompt building (anti-hallucination, persona voice preservation)
# - Tool calling utilities (parsing, formatting, routing)

from __future__ import annotations

# Keyword dictionaries for intent classification
from .keywords import (
    NO_SEARCH_KEYWORDS,
    SEARCH_KEYWORDS,
)

# Intent classifier
from .intent_classifier import (
    QueryIntent,
    classify_query_intent,
    classify_query_intent_ex,
)

# Tool generators
from .tool_generators import (
    get_brave_search_tool,
    AVAILABLE_TOOLS,
)

# Synthesis prompts
from .synthesis_prompts import (
    build_tool_system_prompt,
    build_synthesis_prompt,
)

# Utility functions
from .tool_utils import (
    ToolCall,
    should_use_keyword_filter,
    parse_tool_call,
    format_search_results_for_llm,
    get_tools_for_persona,
    get_tools_for_query,
)

__all__ = [
    # Keywords
    "NO_SEARCH_KEYWORDS",
    "SEARCH_KEYWORDS",
    # Intent
    "QueryIntent",
    "classify_query_intent",
    "classify_query_intent_ex",
    # Tool generators
    "get_brave_search_tool",
    "AVAILABLE_TOOLS",
    # Synthesis prompts
    "build_tool_system_prompt",
    "build_synthesis_prompt",
    # Utils
    "ToolCall",
    "should_use_keyword_filter",
    "parse_tool_call",
    "format_search_results_for_llm",
    "get_tools_for_persona",
    "get_tools_for_query",
]
