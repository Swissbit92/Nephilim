# src/coordinator/tools/registrations.py
"""Declarative tool registrations — ADR-009 Phase R (R2 migration).

Imported for side effects: every existing tool declares its ToolSpec here
(definition factory + safety policy). Policies mirror the pre-ADR-009
interceptor `_TOOL_POLICY` table EXACTLY — the migration is behavior-identical
by construction, and test_toolkit_characterization pins that.

Executors are bound separately at service-wiring time (not here) to keep this
module free of `services/` imports.

Import this module once at startup (startup.py) so the registry is populated
before any request. Registration is idempotent.
"""

from __future__ import annotations

from .registry import register_tool
from .tool_generators import get_brave_search_tool
from .image_tool_generators import get_generate_image_tool
from .web_tool_generators import (
    get_extract_tool,
    get_fetch_url_tool,
    get_image_search_tool,
    get_news_search_tool,
    get_video_search_tool,
    get_web_search_tool,
)
from .wallet_tool_generators import (
    get_solana_get_quote_tool,
    get_solana_propose_strategy_tool,
    get_solana_propose_swap_tool,
    get_solana_rsi_check_tool,
    get_solana_trade_history_tool,
    get_wallet_create_guided_tool,
    get_wallet_get_balances_tool,
)


def register_builtin_tools() -> None:
    """Register the 8 pre-ADR-009 tools. Idempotent."""
    # --- web toolset --------------------------------------------------
    # Legacy intent-gated search tool (still used by the force-search path;
    # now SearXNG-backed via W1). Kept for byte-identical legacy behavior.
    register_tool(
        "brave_web_search", "web", get_brave_search_tool,
        blast_radius="low", requires_hitl=False,
    )
    # ADR-009 W2: generic web toolset. All read-only, low blast radius, no HITL.
    # search-family tools are nsfw_modulated (safesearch clamp in the executor).
    register_tool(
        "web_search", "web", get_web_search_tool,
        blast_radius="low", requires_hitl=False, nsfw_modulated=True,
    )
    register_tool(
        "fetch_url", "web", get_fetch_url_tool,
        blast_radius="low", requires_hitl=False,
    )
    register_tool(
        "image_search", "web", get_image_search_tool,
        blast_radius="low", requires_hitl=False, nsfw_modulated=True,
    )

    # Its OWN toolset, not "web": web tools READ the internet, this one spends
    # five and a half minutes of the whole machine. Grouping them would mean a
    # persona granted web search silently also gets a GPU job.
    #
    # requires_hitl stays FALSE and that is deliberate, not an oversight:
    # routes/chat.py routes ST_HITL to the WALLET handler, so setting it here
    # would send an image request into the trade-proposal flow. The guard that
    # belongs here is the throttle, which lives in the executor.
    register_tool(
        "generate_image", "image", get_generate_image_tool,
        blast_radius="low", requires_hitl=False,
    )
    register_tool(
        "video_search", "web", get_video_search_tool,
        blast_radius="low", requires_hitl=False, nsfw_modulated=True,
    )
    register_tool(
        "news_search", "web", get_news_search_tool,
        blast_radius="low", requires_hitl=False,
    )
    register_tool(
        "extract", "web", get_extract_tool,
        blast_radius="low", requires_hitl=False,
    )

    # --- wallet toolset ----------------------------------------------
    # Registration order matches get_wallet_tools() exactly so the tool list
    # presented to the model is byte-order-identical to the legacy path.
    # (blast_radius/requires_hitl mirror the interceptor _TOOL_POLICY.)
    register_tool(
        "wallet_get_balances", "wallet", get_wallet_get_balances_tool,
        blast_radius="none", requires_hitl=False,
    )
    register_tool(
        "wallet_create_guided", "wallet", get_wallet_create_guided_tool,
        blast_radius="high", requires_hitl=True,
    )
    register_tool(
        "solana_get_quote", "wallet", get_solana_get_quote_tool,
        blast_radius="none", requires_hitl=False,
    )
    register_tool(
        "solana_rsi_check", "wallet", get_solana_rsi_check_tool,
        blast_radius="none", requires_hitl=False,
    )
    register_tool(
        "solana_propose_swap", "wallet", get_solana_propose_swap_tool,
        blast_radius="high", requires_hitl=True,
    )
    register_tool(
        "solana_propose_strategy", "wallet", get_solana_propose_strategy_tool,
        blast_radius="high", requires_hitl=True,
    )
    register_tool(
        "solana_trade_history", "wallet", get_solana_trade_history_tool,
        blast_radius="none", requires_hitl=False,
    )


# Populate on import so `from . import registrations` is enough.
register_builtin_tools()
