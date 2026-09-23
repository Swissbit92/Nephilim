# src/coordinator/services/tool_interceptor.py
"""Deterministic pre-execution tool-call interceptor (HERMES-Agents Phase 3, M2).

The single most critical safety component of Phase 3. Research finding
("Mind the GAP", arXiv 2602.16943): text-level safety does NOT transfer to
tool-call safety, and LLMs cannot self-police. Therefore every tool call — no
matter how it was produced (LLM-decided, force-search, agentic pipeline) — must
pass a deterministic, external gate before execution:

1. **Per-persona mcp_access** re-enforcement (defence-in-depth vs. routing
   bypass: code paths that call execute_search / wallet ops directly).
2. **Argument-level allowlist** — not just tool-name allowlisting. A swap with an
   out-of-set token, or a search query carrying shell metacharacters, is rejected
   even though the tool itself is permitted.
3. **Blast-radius / HITL classification** — read ops execute freely; irreversible
   write ops (swaps, wallet creation) are flagged ``requires_hitl`` so the caller
   routes them through the existing propose->confirm->execute flow.
4. **Hard block on direct execution from an agent source** — any *execution* tool
   (``solana_execute_swap`` / ``execute_swap``) called with ``source="agent"`` is
   always denied; on-chain spend is reachable only via ``source="user_confirmed"``.

Pure logic, no I/O — runs headless and fast.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Tokens permitted in any swap / quote / rsi argument. Restricting the enum is a
# primary guard against attacker-controlled token addresses in a swap proposal.
ALLOWED_TOKENS = {"SOL", "USDC", "USDT"}

# Control characters disallowed in a free-text search query. NOTE: shell
# metacharacters (; & | < > $ `) are intentionally NOT blocked — the query is
# delivered to the Brave MCP over STDIO JSON-RPC (no shell is ever invoked), and
# legitimate queries routinely contain them ("AT&T stock", "C# vs C++", "x > y").
# Blocking them would regress real searches for a vector that does not exist here.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_QUERY_LEN = 300

# Every search-family tool whose `query` argument gets the structural validation
# above: the legacy force-search name plus the live ADR-009 registry-bound names.
_SEARCH_QUERY_TOOLS = frozenset({
    "brave_web_search", "web_search", "image_search", "video_search", "news_search",
})

# MCP service identifiers as they appear in persona `mcp_access`.
_MCP_BRAVE = "brave_search"
_MCP_WALLET = "solana_wallet"

# Execution tools that must NEVER be reachable from an autonomous agent source.
ALWAYS_BLOCKED_FROM_AGENT = {"solana_execute_swap", "execute_swap"}

# blocked_category constants
CAT_NONE = ""
CAT_MCP = "persona_mcp_access"
CAT_UNKNOWN = "unknown_tool"
CAT_ARGS = "argument_schema"
CAT_DIRECT_EXEC = "direct_execute_blocked"


@dataclass
class InterceptResult:
    allowed: bool
    reason: str
    blast_radius: str = "none"  # "none" | "low" | "high"
    requires_hitl: bool = False  # True -> caller must show HITL gate before exec
    blocked_category: str = CAT_NONE


@dataclass(frozen=True)
class _ToolPolicy:
    mcp: str  # required mcp_access entry
    blast_radius: str
    requires_hitl: bool


def _lookup_policy(tool_name: str) -> Optional[_ToolPolicy]:
    """Resolve a tool's safety policy from the ADR-009 registry.

    The registry is the single source of truth (R2); this adapts a ToolSpec to
    the interceptor's `_ToolPolicy` shape, mapping the spec's toolset to the
    legacy `mcp_access` string the persona gate speaks. Returns None for any
    unregistered tool -> caller denies as unknown_tool (unchanged semantics).
    """
    # Lazy import (registry lives in tools/, ensure builtins are registered).
    from ..tools.registry import registry
    from ..tools import registrations  # noqa: F401

    spec = registry.get(tool_name)
    if spec is None:
        return None
    return _ToolPolicy(spec.mcp_alias, spec.blast_radius, spec.requires_hitl)


def _validate_arguments(tool_name: str, args: Dict[str, Any]) -> Optional[str]:
    """Return an error string if arguments violate the per-tool allowlist, else None."""
    args = args or {}

    # Search-family query validation. Covers the legacy force-search name AND
    # the live ADR-009 tool-brain names (image_search/web_search/video_search/
    # news_search) — previously ONLY brave_web_search was checked, so the active
    # tool-brain path had no length cap or control-char guard on the query.
    # Structural only (non-empty, length, control chars); content is not judged.
    if tool_name in _SEARCH_QUERY_TOOLS:
        query = args.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return "query must be a non-empty string"
        if len(query) > _MAX_QUERY_LEN:
            return f"query exceeds {_MAX_QUERY_LEN} characters"
        if _CONTROL_CHARS.search(query):
            return "query contains disallowed control characters"
        return None

    if tool_name in ("solana_get_quote", "solana_propose_swap"):
        from_token = args.get("from_token")
        to_token = args.get("to_token")
        amount = args.get("amount")
        if from_token not in ALLOWED_TOKENS or to_token not in ALLOWED_TOKENS:
            return f"from_token/to_token must be one of {sorted(ALLOWED_TOKENS)}"
        if from_token == to_token:
            return "from_token and to_token must differ"
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
            return "amount must be a positive number"
        return None

    if tool_name == "solana_rsi_check":
        token = args.get("token")
        if token not in ALLOWED_TOKENS:
            return f"token must be one of {sorted(ALLOWED_TOKENS)}"
        return None

    if tool_name == "wallet_create_guided":
        name = args.get("wallet_name", "")
        if not isinstance(name, str) or not name.strip():
            return "wallet_name must be a non-empty string"
        if len(name) > 32:
            return "wallet_name exceeds 32 characters"
        return None

    # Read tools with no required arguments (e.g. wallet_get_balances,
    # solana_trade_history): nothing to validate.
    return None


class ToolCallInterceptor:
    """Deterministic gate run before any tool execution.

    Stateless; safe to share a single instance. ``enforce_arguments`` defaults to
    the ``AGENTIC_ARGUMENT_ALLOWLIST`` flag but can be overridden for tests.
    """

    def __init__(self, enforce_arguments: Optional[bool] = None):
        self._enforce_arguments_override = enforce_arguments

    def _enforce_arguments(self) -> bool:
        if self._enforce_arguments_override is not None:
            return self._enforce_arguments_override
        # Lazy import to avoid a config import cycle at module load.
        from ..config import get_settings
        return get_settings().agent.argument_allowlist

    def validate(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]],
        persona_key: str,
        mcp_access: Optional[List[str]],
        source: str = "agent",
        persona_card: Optional[Dict[str, Any]] = None,
    ) -> InterceptResult:
        """Validate a proposed tool call. Never raises; returns an InterceptResult.

        `persona_card` enables TOOL-level enforcement. Without it this check is
        toolset-level only (`mcp_access`), which cannot see a card's `tools`
        allowlist — so a persona scoped to image/video search would still be
        allowed to execute `web_search`, because both live in the `web` toolset.

        That gap mattered: filtering what the model is OFFERED is not an
        enforcement boundary. Function-calling models demonstrably emit calls for
        tools that were never offered, on hosted APIs and on Ollama alike, so the
        offer is a hint and this is the point of complete mediation
        (OWASP LLM06, Excessive Agency).
        """
        arguments = arguments or {}
        mcp_access = mcp_access or []

        # 1. Hard block: execution tools from an autonomous source. Checked FIRST
        #    so it cannot be bypassed by an unknown-tool / mcp ordering trick.
        if tool_name in ALWAYS_BLOCKED_FROM_AGENT and source != "user_confirmed":
            return InterceptResult(
                allowed=False,
                reason=(
                    f"'{tool_name}' is an execution tool and is blocked from "
                    f"source='{source}'; on-chain execution requires an explicit "
                    "user-confirmed proposal."
                ),
                blast_radius="high",
                requires_hitl=True,
                blocked_category=CAT_DIRECT_EXEC,
            )

        # 2. Unknown tool: deny by default.
        policy = _lookup_policy(tool_name)
        if policy is None:
            return InterceptResult(
                allowed=False,
                reason=f"unknown or unsupported tool '{tool_name}'",
                blocked_category=CAT_UNKNOWN,
            )

        # 3. Per-persona mcp_access re-enforcement (always on, independent of the
        #    argument-allowlist flag).
        if policy.mcp not in mcp_access:
            return InterceptResult(
                allowed=False,
                reason=(
                    f"persona '{persona_key}' lacks mcp_access '{policy.mcp}' "
                    f"required by '{tool_name}'"
                ),
                blocked_category=CAT_MCP,
            )

        # 3b. Per-persona TOOL allowlist re-enforcement. Resolved from the SAME
        #     registry method the offering layer uses (specs_for_persona), so the
        #     two layers cannot drift into disagreeing about what a persona holds.
        if persona_card is not None:
            from ..tools.registry import registry

            granted = {s.name for s in registry.specs_for_persona(persona_card)}
            if granted and tool_name not in granted:
                return InterceptResult(
                    allowed=False,
                    reason=(
                        f"persona '{persona_key}' is not granted '{tool_name}' "
                        f"(granted: {sorted(granted)})"
                    ),
                    blast_radius=policy.blast_radius,
                    blocked_category=CAT_MCP,
                )

        # 4. Argument-level allowlist (gated by AGENTIC_ARGUMENT_ALLOWLIST).
        if self._enforce_arguments():
            err = _validate_arguments(tool_name, arguments)
            if err is not None:
                return InterceptResult(
                    allowed=False,
                    reason=f"argument validation failed for '{tool_name}': {err}",
                    blast_radius=policy.blast_radius,
                    blocked_category=CAT_ARGS,
                )

        # Allowed — surface blast radius / HITL requirement to the caller.
        return InterceptResult(
            allowed=True,
            reason="ok",
            blast_radius=policy.blast_radius,
            requires_hitl=policy.requires_hitl,
            blocked_category=CAT_NONE,
        )
