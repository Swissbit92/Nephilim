# src/coordinator/services/llm_completion_service.py
"""
LLM Completion Service - Basic completion without tool calling.

Extracted from llm_client.py as part of Phase 2 Core Refactoring.
Handles basic LLM invocations with advanced sampling support.
"""

from __future__ import annotations

import logging
from typing import Dict, Any, Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama.llms import OllamaLLM
from ollama._types import ResponseError

from ..models.sampling_presets import SamplingConfig
from ..config import get_settings

logger = logging.getLogger(__name__)


class LLMCompletionService:
    """Service for basic LLM completion without tool calling.

    Handles:
    - Ollama client initialization with advanced sampling
    - Basic prompt completion
    - Error handling for Ollama connectivity
    - Sampling configuration management

    This service is stateless and thread-safe.
    """

    def __init__(
        self,
        base: str,
        model: str,
        temperature: float = 0.1,
        sampling_config: Optional[SamplingConfig] = None,
        # Individual sampling params (override sampling_config if provided)
        repeat_penalty: Optional[float] = None,
        repeat_last_n: Optional[int] = None,
        top_k: Optional[int] = None,
        top_p: Optional[float] = None,
        min_p: Optional[float] = None,
    ):
        """Initialize the LLM completion service.

        Args:
            base: Ollama base URL
            model: Model name (e.g., 'llama3.1:latest')
            temperature: Sampling temperature (0.0-2.0)
            sampling_config: Optional SamplingConfig for preset-based configuration
            repeat_penalty: Optional repetition penalty (1.0-2.0)
            repeat_last_n: Optional repetition-penalty lookback in tokens.
                Ollama defaults to 64, which cannot see a repeated paragraph;
                -1 scales the window to the full context.
            top_k: Optional Top-K sampling (0-100)
            top_p: Optional nucleus sampling threshold (0.0-1.0)
            min_p: Optional Min-P dynamic threshold (0.0-1.0)
        """
        # Build Ollama params
        ollama_params = {
            "base_url": base,
            "model": model,
            "temperature": temperature,
            # num_ctx controls Ollama's runtime context window (KV-cache size).
            # Without this, Ollama falls back to its own default (32K), ignoring
            # MODEL_CONTEXT_WINDOW. See config.OllamaSettings.context_window.
            "num_ctx": get_settings().ollama.context_window,
            # keep_alive defaults to "-1" — the model stays loaded INDEFINITELY
            # (always-warm) so a chat never pays a cold ~17GB reload (Ollama's own
            # default is 5min idle). Deliberate for this always-on station: a resident
            # model holds VRAM but computes nothing, so it costs no heat or CPU.
            # Now configurable via OLLAMA_KEEP_ALIVE — the summarisation utilities use
            # the SEPARATE, shorter OLLAMA_UTILITY_KEEP_ALIVE, because they run on the
            # default model and would otherwise pin a SECOND model for the process
            # lifetime (7.46GB of gemma2 alongside this 17.54GB, observed 2026-08-22).
            "keep_alive": get_settings().ollama.keep_alive,
            # num_predict caps generated tokens per turn. Turn latency is ~linear in
            # output tokens at ~16 tok/s, so an unbounded reply can run 30s+. This is a
            # generous backstop against runaway verbosity; typical brevity is driven by
            # the persona response-format guidance, not this cap.
            "num_predict": get_settings().ollama.max_output_tokens,
        }

        # Reasoning ("think") control — only added when explicitly configured, so an
        # unset OLLAMA_REASONING leaves this constructor byte-identical to legacy.
        # A thinking model spends num_predict on its reasoning stream and returns an
        # empty string here; reasoning=False is what makes such a model usable at all.
        _reasoning = get_settings().ollama.reasoning
        if _reasoning is not None:
            ollama_params["reasoning"] = _reasoning

        # Apply sampling config if provided
        if sampling_config:
            config_params = sampling_config.to_ollama_params()
            # Temperature from sampling_config unless explicitly overridden
            if temperature == 0.1:  # default value
                ollama_params["temperature"] = config_params.get("temperature", 0.1)
            if "repeat_penalty" in config_params:
                ollama_params["repeat_penalty"] = config_params["repeat_penalty"]
            if "top_k" in config_params:
                ollama_params["top_k"] = config_params["top_k"]
            if "top_p" in config_params:
                ollama_params["top_p"] = config_params["top_p"]
            if "min_p" in config_params:
                ollama_params["min_p"] = config_params["min_p"]

        # Individual params override sampling_config
        if repeat_penalty is not None:
            ollama_params["repeat_penalty"] = repeat_penalty
        if repeat_last_n is not None:
            ollama_params["repeat_last_n"] = repeat_last_n
        if top_k is not None:
            ollama_params["top_k"] = top_k
        if top_p is not None:
            ollama_params["top_p"] = top_p
        if min_p is not None:
            ollama_params["min_p"] = min_p

        # min_p cannot survive the langchain transport (ollama.Options has no
        # such field), so a persona that sets it needs the direct HTTP client.
        # Flag-gated; 'langchain' keeps today's behaviour exactly.
        if get_settings().ollama.completion_backend == "http":
            from .ollama_http import OllamaHTTPClient  # noqa: PLC0415

            options = {
                k: v for k, v in ollama_params.items()
                if k not in ("base_url", "model", "keep_alive")
            }
            self.llm = OllamaHTTPClient(
                base_url=ollama_params["base_url"],
                model=ollama_params["model"],
                options=options,
                keep_alive=ollama_params.get("keep_alive"),
            )
        else:
            if "min_p" in ollama_params:
                logger.warning(
                    "[Sampling] min_p=%s requested but the langchain transport drops it "
                    "silently (ollama.Options has no min_p field). Set "
                    "OLLAMA_COMPLETION_BACKEND=http to actually apply it.",
                    ollama_params["min_p"],
                )
            self.llm = OllamaLLM(**ollama_params)
        self.sampling_config = sampling_config

        # Log sampling parameters
        sampling_info = f"temp={ollama_params['temperature']}"
        if "repeat_penalty" in ollama_params:
            sampling_info += f", repeat_penalty={ollama_params['repeat_penalty']}"
        if "top_k" in ollama_params:
            sampling_info += f", top_k={ollama_params['top_k']}"
        if "top_p" in ollama_params:
            sampling_info += f", top_p={ollama_params['top_p']}"
        if "min_p" in ollama_params:
            sampling_info += f", min_p={ollama_params['min_p']}"

        preset_name = sampling_config.name if sampling_config else "custom"
        logger.info(
            f"Initialized LLMCompletionService: model={model}, "
            f"sampling=[{sampling_info}], preset={preset_name}"
        )

    def get_sampling_info(self) -> Dict[str, Any]:
        """Get current sampling configuration as dict for response metadata.

        Returns:
            Dictionary with sampling parameters
        """
        info = {
            "temperature": self.llm.temperature,
            "model": self.llm.model,
        }
        if hasattr(self.llm, "repeat_penalty") and self.llm.repeat_penalty:
            info["repeat_penalty"] = self.llm.repeat_penalty
        if hasattr(self.llm, "top_k") and self.llm.top_k:
            info["top_k"] = self.llm.top_k
        if hasattr(self.llm, "top_p") and self.llm.top_p:
            info["top_p"] = self.llm.top_p
        if self.sampling_config:
            info["preset"] = self.sampling_config.name
        return info

    def invoke(self, prompt: str) -> str:
        """Low-level Ollama invocation with error handling.

        Args:
            prompt: Formatted prompt string

        Returns:
            LLM response string

        Raises:
            RuntimeError: If Ollama model not found or other errors
        """
        try:
            return self.llm.invoke(prompt).strip()
        except ResponseError as e:
            msg = str(e)
            if "not found" in msg.lower():
                raise RuntimeError(
                    "Ollama model not found.\n"
                    f"Pull it:\n  ollama pull {self.llm.model}\n"
                    f"base_url={self.llm.base_url}\n"
                )
            raise

    def _generate_with_stats(self, prompt: str) -> tuple[str, dict]:
        """invoke() variant that also returns Ollama's generation_info (token stats).

        Used by complete() for M2 token observability. Mirrors invoke()'s
        not-found error handling. Returns (stripped_text, generation_info_dict).
        """
        try:
            result = self.llm.generate([prompt])
            gen = result.generations[0][0]
            return gen.text.strip(), (gen.generation_info or {})
        except ResponseError as e:
            msg = str(e)
            if "not found" in msg.lower():
                raise RuntimeError(
                    "Ollama model not found.\n"
                    f"Pull it:\n  ollama pull {self.llm.model}\n"
                    f"base_url={self.llm.base_url}\n"
                )
            raise

    def complete(self, system: str, user_prompt: str) -> str:
        """Complete a prompt with system and user messages.

        Args:
            system: System prompt
            user_prompt: User message

        Returns:
            LLM response string
        """
        template = ChatPromptTemplate.from_messages([
            ("system", "{system}"),
            ("user", "{user}")
        ])
        rendered = template.format_prompt(system=system, user=user_prompt).to_string()

        # M2 (ADR-006 Phase 0): observe the FINAL assembled prompt size. This is
        # the only place the fully-rendered string (system + role labels + user
        # block) exists before it reaches Ollama. The pre-send estimate drives a
        # proactive budget warning; Ollama's prompt_eval_count (when present) is
        # the ground-truth count. Lazy import of estimate_tokens avoids the
        # llm_client <-> services circular import.
        from ..llm_client import estimate_tokens

        settings = get_settings()
        window = settings.ollama.context_window
        est = estimate_tokens(rendered)
        pct = round((est / window) * 100, 1) if window else 0.0
        logger.info(
            f"[Tokens-assembled] ~{est}/{window} tokens ({pct}%) est | chars={len(rendered)}"
        )
        if window and pct > 85:
            logger.warning(
                f"[Tokens-assembled] BUDGET WARNING: assembled prompt ~{pct}% of context "
                f"window before generation (+{settings.ollama.max_output_tokens}-token output cap)"
            )

        text, info = self._generate_with_stats(rendered)
        # prompt_eval_count is omitted/zero on KV-cache hits — guard for None.
        actual = info.get("prompt_eval_count")
        if actual:
            logger.info(f"[Tokens-assembled] actual prompt_eval_count={actual} (Ollama)")
        return text
