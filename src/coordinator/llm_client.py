# src/coordinator/llm_client.py
"""
DEPRECATED: This module is being phased out in favor of individual services.

Phase 2 Service Decomposition Complete:
- Use LLMCompletionService for basic completions
- Use ToolCallingService for tool calling
- Use CitationService for citation management
- Use QueryExtractionService for query extraction
- Use ForceSearchService for force-search detection
- Use SearchExecutionService for search execution

This file now serves as a backward-compatible facade that delegates to the new services.
New code should use the services directly instead of LC_OllamaClient.
"""

import logging
import warnings
from typing import List, Dict, Any, Optional, Tuple

# Utility imports (still needed)

# Import the new services
from .services import (
    LLMCompletionService,
    ToolCallingService,
    CitationService,
    QueryExtractionService,
    ForceSearchService,
    SearchExecutionService
)
from .mcp_client_stdio import BraveMCPClientStdio
from .tool_definitions import ToolCall
from .models.sampling_presets import SamplingConfig

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """Estimate token count (4 chars ≈ 1 token).

    Args:
        text: Input text to estimate tokens for

    Returns:
        Estimated token count
    """
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except (ImportError, Exception):
        # Fallback: character-based approximation
        return max(1, len(text) // 4)


def log_context_stats(system_prompt: str, history: List[Any], query: str, model_context_window: int = 4096) -> dict:
    """Log token usage statistics for monitoring.

    Args:
        system_prompt: System prompt text
        history: List of ChatTurn objects
        query: User query text
        model_context_window: Model's context window size (default: 4096)

    Returns:
        Dictionary with token usage statistics
    """
    system_tokens = estimate_tokens(system_prompt)
    history_tokens = sum(estimate_tokens(turn.content) for turn in history)
    query_tokens = estimate_tokens(query)
    total_tokens = system_tokens + history_tokens + query_tokens

    stats = {
        "system_tokens": system_tokens,
        "history_tokens": history_tokens,
        "history_messages": len(history),
        "query_tokens": query_tokens,
        "total_input_tokens": total_tokens,
        "estimated_budget_remaining": model_context_window - total_tokens,
        "budget_usage_percent": round((total_tokens / model_context_window) * 100, 1)
    }

    # Log with color coding based on usage
    usage_pct = stats["budget_usage_percent"]
    if usage_pct > 90:
        logger.warning(
            f"[Tokens] ⚠️ HIGH USAGE: {total_tokens}/{model_context_window} tokens ({usage_pct}%) | "
            f"History: {len(history)} msgs ({history_tokens} tokens) | Remaining: {stats['estimated_budget_remaining']}"
        )
    elif usage_pct > 70:
        logger.info(
            f"[Tokens] Input: {total_tokens}/{model_context_window} tokens ({usage_pct}%) | "
            f"History: {len(history)} msgs ({history_tokens} tokens) | Remaining: {stats['estimated_budget_remaining']}"
        )
    else:
        logger.debug(
            f"[Tokens] Input: {total_tokens}/{model_context_window} tokens ({usage_pct}%) | "
            f"History: {len(history)} msgs ({history_tokens} tokens)"
        )

    return stats


def create_llm_client(
    persona_card: dict,
    *,
    mcp_client: Optional[BraveMCPClientStdio] = None,
    temperature: Optional[float] = None,
) -> "LC_OllamaClient":
    """Factory for LC_OllamaClient with standard settings.

    Centralises the 3-line construction pattern used across routes and services.
    Reads full sampling overrides (temperature, min_p, repeat_penalty,
    repeat_last_n, top_k, top_p) from the persona card's model_preferences field.

    Args:
        persona_card: Persona JSON dict (used for per-persona sampling overrides).
        mcp_client: Optional Brave MCP client for web search.
        temperature: Explicit temperature override; if None, uses persona override
                     or global default.
    """
    from .config import get_settings, get_persona_sampling_overrides  # noqa: PLC0415

    cfg = get_settings()
    overrides = get_persona_sampling_overrides(persona_card)
    temp = temperature if temperature is not None else overrides.get("temperature", cfg.ollama.temperature)
    return LC_OllamaClient(
        base=cfg.ollama.base,
        model=cfg.ollama.model,
        temperature=temp,
        mcp_client=mcp_client,
        repeat_penalty=overrides.get("repeat_penalty"),
        repeat_last_n=overrides.get("repeat_last_n"),
        min_p=overrides.get("min_p"),
        top_k=overrides.get("top_k"),
        top_p=overrides.get("top_p"),
    )


class LC_OllamaClient:
    """DEPRECATED: Backward compatibility facade for legacy code.

    This class delegates to the new service layer architecture.
    New code should use the services directly:
    - LLMCompletionService for basic completions
    - ToolCallingService for tool calling with autonomous search

    This facade will be removed in a future version.
    """

    def __init__(
        self,
        base: str,
        model: str,
        temperature: float = 0.1,
        mcp_client: Optional[BraveMCPClientStdio] = None,
        sampling_config: Optional[SamplingConfig] = None,
        repeat_penalty: Optional[float] = None,
        repeat_last_n: Optional[int] = None,
        top_k: Optional[int] = None,
        top_p: Optional[float] = None,
        min_p: Optional[float] = None,
    ):
        """Initialize the facade client (delegates to services).

        DEPRECATED: Use LLMCompletionService and ToolCallingService directly.

        Args:
            base: Ollama base URL
            model: Model name
            temperature: Sampling temperature
            mcp_client: Optional Brave MCP client for web search
            sampling_config: Optional sampling configuration
            repeat_penalty: Optional repetition penalty
            repeat_last_n: Optional repetition-penalty lookback window
            top_k: Optional Top-K sampling
            top_p: Optional nucleus sampling
            min_p: Optional Min-P dynamic threshold
        """
        # Issue deprecation warning (only once per session)
        if not hasattr(LC_OllamaClient, '_deprecation_warned'):
            warnings.warn(
                "LC_OllamaClient is deprecated and will be removed in a future version. "
                "Use LLMCompletionService and ToolCallingService instead.",
                DeprecationWarning,
                stacklevel=2
            )
            LC_OllamaClient._deprecation_warned = True

        # Create the new services
        self._llm_service = LLMCompletionService(
            base=base,
            model=model,
            temperature=temperature,
            sampling_config=sampling_config,
            repeat_penalty=repeat_penalty,
            repeat_last_n=repeat_last_n,
            top_k=top_k,
            top_p=top_p,
            min_p=min_p,
        )

        # Create tool calling service (if MCP client provided)
        if mcp_client:
            self._tool_service = ToolCallingService(
                llm_service=self._llm_service,
                citation_service=CitationService(),
                query_extractor=QueryExtractionService(),
                force_search=ForceSearchService(),
                search_executor=SearchExecutionService(mcp_client)
            )
        else:
            self._tool_service = None

        # Store original params for backward compatibility
        self.llm = self._llm_service.llm  # Expose LLM for compatibility
        self.mcp_client = mcp_client
        self.sampling_config = sampling_config

        logger.info(
            f"[DEPRECATED] Initialized LC_OllamaClient facade "
            f"(delegating to services): model={model}, tools_enabled={mcp_client is not None}"
        )

    def get_sampling_info(self) -> Dict[str, Any]:
        """Get current sampling configuration (delegates to service)."""
        return self._llm_service.get_sampling_info()

    def _invoke(self, prompt: str) -> str:
        """Low-level invocation (delegates to service)."""
        return self._llm_service.invoke(prompt)

    def complete(self, system: str, user_prompt: str) -> str:
        """Complete a prompt without tool support (delegates to service).

        Args:
            system: System prompt
            user_prompt: User message

        Returns:
            LLM response string
        """
        return self._llm_service.complete(system, user_prompt)

    def complete_with_tools(
        self,
        persona_system: str,
        user_prompt: str,
        tools: List[Dict[str, Any]],
        max_iterations: int = 2
    ) -> Tuple[str, Optional[ToolCall], Optional[List[Any]]]:
        """Complete with tool calling support (delegates to service).

        Args:
            persona_system: Original persona system prompt
            user_prompt: User message
            tools: List of tool definitions (OpenAI format)
            max_iterations: Max tool calls allowed (default 2)

        Returns:
            Tuple of (final_response, tool_call_used, search_results)
        """
        if not self._tool_service:
            # No tools configured, fall back to regular completion
            logger.warning("[Facade] Tool calling requested but no MCP client configured")
            response = self.complete(persona_system, user_prompt)
            return (response, None, None)

        return self._tool_service.complete_with_tools(
            persona_system=persona_system,
            user_prompt=user_prompt,
            tools=tools,
            max_iterations=max_iterations
        )
