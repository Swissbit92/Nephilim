# src/coordinator/services/tool_brain_service.py
"""Single-model native tool-brain loop — ADR-008 P1 (TB3).

The daily-driver model (abliterated Mistral-Small-24B) decides + fills tool
calls natively via Ollama `/api/chat` with `tools=`; every proposed call passes
the deterministic ADR-004 `ToolCallInterceptor` before execution; web tools run
through the ADR-009 registry-bound executors (with the per-persona safesearch
clamp); the SAME model then continues the conversation to synthesize in-voice —
no second model, no restyle handoff.

The TB0 spike found native tool calling is phrasing-sensitive (explicit
phrasings call, colloquial ones miss ~40%). So this service returns a rich
STATUS and leaves the fallback decision to the caller (the route, TB4):

  answered        native did tool call(s) + synthesized, or answered directly
                  with no tool. `answer` is final. When the model emitted no
                  tool call this is still returned with status=silent (below).
  silent          model emitted NO native tool call at all. `answer` is the
                  model's direct content — VALID only if no tool was actually
                  needed. The caller runs the deterministic intent router: if it
                  says a tool WAS needed -> legacy force-search; else use answer.
  delegate_wallet model called a wallet tool -> hand to the existing
                  handle_wallet_query / propose-confirm flow (wallet never
                  executes inside this loop).
  hitl            a write op (requires_hitl) -> hand to propose->confirm->execute.

Wallet stays entirely on the existing path; this loop executes web reads only.
The interceptor is the primary gate; injection-guard integration is optional
here and hardened in TB4.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Spurious synthesis-refusal detector. The daily driver is abliterated, but
# abliteration drives residual refusal toward — not to — zero, so an uncensored
# persona sometimes still emits a template safety refusal in the SYNTHESIS step
# even though the tool call already SUCCEEDED (results are in hand). We scan only
# the OPENING of the synthesis output for these templated refusal signatures
# (high precision in this narrow context — we are not classifying arbitrary open
# text). Scoped to search/assist refusals so ordinary in-character negations
# ("I won't beg", "I can't believe you") are never caught.
_REFUSAL_PATTERNS = (
    r"\bi\s+cannot\s+and\s+will\s+not\b",
    r"\bi\s+(?:can\s?not|cannot|can't|won'?t|will\s+not)\s+(?:search|look|find|browse|help\s+(?:you\s+)?(?:with\s+)?(?:that|this))\b",
    r"\bi'?m\s+(?:not\s+able|unable)\s+to\s+(?:search|look|find|browse|help|assist|do\s+that)\b",
    r"\bi\s+(?:can'?t|cannot)\s+assist\s+with\s+that\b",
    r"\bas\s+an\s+ai\b",
    r"\bi\s+(?:must|have\s+to)\s+(?:decline|refuse)\b",
)
_REFUSAL_RE = re.compile("|".join(_REFUSAL_PATTERNS), re.IGNORECASE)

# How much of the opening to scan — refusals lead; a long in-voice answer that
# merely mentions "search" deep in a paragraph is not a refusal.
_REFUSAL_SCAN_CHARS = 240


def is_synthesis_refusal(text: Optional[str]) -> bool:
    """True if `text` opens with a templated search/assist refusal signature."""
    if not text:
        return False
    return bool(_REFUSAL_RE.search(text[:_REFUSAL_SCAN_CHARS]))


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Access a field on an Ollama response node that may be a dict OR a
    pydantic-style object (the ollama client returns objects; test fakes use
    plain dicts)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# Status constants
ST_ANSWERED = "answered"
ST_SILENT = "silent"
ST_DELEGATE_WALLET = "delegate_wallet"
ST_HITL = "hitl"


@dataclass
class ToolBrainResult:
    status: str
    answer: Optional[str] = None
    tool_trace: List[Dict[str, Any]] = field(default_factory=list)
    used_search: bool = False
    search_results: Optional[List[Any]] = None
    hitl_tool: Optional[str] = None
    hitl_args: Optional[Dict[str, Any]] = None
    # True when the synthesis step emitted a spurious refusal that survived the
    # one bounded prefill-steered retry — the caller must NOT staple citations
    # onto it and should fall through to the legacy honest floor.
    refused: bool = False


class ToolBrainService:
    """Native tool-calling loop. Stateless apart from injected collaborators."""

    def __init__(self, interceptor, ollama_client=None):
        self.interceptor = interceptor
        self._client = ollama_client  # injectable for tests

    # ------------------------------------------------------------------ client

    def _client_or_default(self):
        if self._client is not None:
            return self._client
        import ollama
        from ..config import get_settings
        return ollama.Client(host=get_settings().ollama.base)

    # --------------------------------------------------------- refusal retry

    # Turn-scoped anti-refusal nudge — states the tool already succeeded and the
    # results are present, so the model has no basis to refuse the synthesis.
    _ANTI_REFUSAL_NUDGE = (
        "The tool call already completed successfully and the results are in the "
        "conversation above. Do NOT refuse and do NOT claim you are unable to "
        "search — the search is already done. Simply present the results to the "
        "user in your own natural voice."
    )
    # Compliant prefill opening — seeds the assistant turn so the first-token
    # distribution steers away from a refusal (far more reliable than a nudge
    # alone on refusal-prone / abliterated models). Kept short and neutral so it
    # dents persona voice minimally; the continuation is in-voice.
    _PREFILL_OPENING = "Here's what I found for you — "

    def _synthesize_with_refusal_retry(
        self, client, model, messages, opts, content: str
    ) -> tuple[str, bool]:
        """If `content` (a completed synthesis) is a spurious refusal, run ONE
        bounded prefill-steered retry (synthesis only, no tools). Returns
        (final_answer, refused). `refused` is True only if the retry ALSO
        refused — the caller then falls through to the legacy honest floor.

        No-op (returns content, False) when content is not a refusal."""
        if not is_synthesis_refusal(content):
            return content, False

        logger.info("[ToolBrain] synthesis refusal detected; one prefill-steered retry")
        retry_messages = list(messages) + [
            {"role": "system", "content": self._ANTI_REFUSAL_NUDGE},
            {"role": "assistant", "content": self._PREFILL_OPENING},
        ]
        try:
            resp = client.chat(model=model, messages=retry_messages,
                               stream=False, options=opts)
            continuation = (_get(_get(resp, "message", {}), "content", "") or "").strip()
        except Exception as e:  # noqa: BLE001 - never break the turn
            logger.warning(f"[ToolBrain] refusal retry failed ({e}); keeping original")
            return content, True

        combined = f"{self._PREFILL_OPENING}{continuation}".strip()
        if is_synthesis_refusal(combined):
            logger.warning("[ToolBrain] retry still refused; caller should fall through")
            return content, True
        logger.info("[ToolBrain] prefill retry recovered the turn")
        return combined, False

    # -------------------------------------------------------------------- run

    def run(
        self,
        *,
        persona_card: Dict[str, Any],
        system_prompt: str,
        user_message: str,
        history: Optional[List[Dict[str, str]]] = None,
        tools: List[Dict[str, Any]],
    ) -> ToolBrainResult:
        """Run the native tool-calling loop for one turn. Never raises — any
        Ollama/executor error degrades to a silent result so the caller can fall
        back to the legacy path."""
        from ..config import get_settings
        from ..tools.registry import registry, TOOLSET_MCP_ALIASES
        from ..tools.tool_utils import format_search_results_for_llm

        st = get_settings()
        model = st.ollama.model
        max_iter = st.tool_brain.max_iterations
        opts = {"temperature": 0.4, "num_predict": st.ollama.max_output_tokens,
                "num_ctx": st.ollama.context_window}
        client = self._client_or_default()

        persona_key = persona_card.get("key", "")
        granted = registry.toolsets_for_persona(persona_card)
        mcp_access = [TOOLSET_MCP_ALIASES.get(t, t) for t in granted]

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for h in (history or []):
            hrole = (h.get("role") or "").lower()
            if hrole in ("narrator", "recalled"):
                # ADR-011: scene direction is not a tool-decision turn. Neither
                # is a semantically-recalled message — it is background, and
                # mapping it to "user" would present old text as a fresh ask.
                continue
            role = "assistant" if hrole == "assistant" else "user"
            messages.append({"role": role, "content": h.get("content", "")})
        messages.append({"role": "user", "content": user_message})

        trace: List[Dict[str, Any]] = []
        used_search = False
        search_results: Optional[List[Any]] = None

        try:
            for iteration in range(max_iter):
                resp = client.chat(model=model, messages=messages, tools=tools,
                                   stream=False, options=opts)
                msg = _get(resp, "message", {})
                tcs = _get(msg, "tool_calls", None) or []
                content = (_get(msg, "content", "") or "").strip()

                if not tcs:
                    if iteration == 0:
                        # No tool call at all — caller decides via the router.
                        return ToolBrainResult(status=ST_SILENT, answer=content, tool_trace=trace)
                    # After tool execution: this is the in-voice synthesis. Guard
                    # against a spurious refusal despite successful search.
                    refused = False
                    if used_search:
                        content, refused = self._synthesize_with_refusal_retry(
                            client, model, messages, opts, content)
                    return ToolBrainResult(
                        status=ST_ANSWERED, answer=content, tool_trace=trace,
                        used_search=used_search, search_results=search_results,
                        refused=refused)

                # Record the assistant's tool-call turn so the model has context.
                messages.append({"role": "assistant", "content": content, "tool_calls": tcs})

                for tc in tcs:
                    fn = _get(tc, "function", {})
                    name = _get(fn, "name", "")
                    args = _get(fn, "arguments", {}) or {}

                    res = self.interceptor.validate(name, args, persona_key, mcp_access, source="agent")
                    trace.append({"tool": name, "allowed": res.allowed,
                                  "requires_hitl": res.requires_hitl,
                                  "blocked": res.blocked_category})

                    if res.requires_hitl:
                        # Write op (wallet swap/creation) -> propose/confirm flow.
                        return ToolBrainResult(status=ST_HITL, hitl_tool=name,
                                               hitl_args=args, tool_trace=trace)
                    if not res.allowed:
                        messages.append({"role": "tool",
                                         "content": f"[tool '{name}' not permitted: {res.reason}]"})
                        continue

                    spec = registry.get(name)
                    toolset = spec.toolset if spec else ""
                    if toolset == "wallet":
                        # Wallet reads/ops stay on the existing handler.
                        return ToolBrainResult(status=ST_DELEGATE_WALLET, tool_trace=trace)

                    executor = spec.executor if spec else None
                    if executor is None:
                        messages.append({"role": "tool",
                                         "content": f"[tool '{name}' unavailable]"})
                        continue

                    result = executor(args, persona_card)
                    is_search = toolset == "web" and name != "fetch_url"
                    if is_search:
                        used_search = True
                        search_results = result or []
                        formatted = (format_search_results_for_llm(result)
                                     if result else "No results found.")
                    else:
                        formatted = str(result) if result is not None else "[no output]"
                    messages.append({"role": "tool", "content": formatted})

            # Iteration budget exhausted — force a final synthesis without tools.
            resp = client.chat(model=model, messages=messages, stream=False, options=opts)
            fcontent = (_get(_get(resp, "message", {}), "content", "") or "").strip()
            refused = False
            if used_search:
                fcontent, refused = self._synthesize_with_refusal_retry(
                    client, model, messages, opts, fcontent)
            return ToolBrainResult(status=ST_ANSWERED, answer=fcontent, tool_trace=trace,
                                   used_search=used_search, search_results=search_results,
                                   refused=refused)

        except Exception as e:  # noqa: BLE001 - never break the turn
            logger.warning(f"[ToolBrain] loop failed ({e}); returning silent for fallback")
            return ToolBrainResult(status=ST_SILENT, answer=None, tool_trace=trace)
