# src/coordinator/app_state.py
"""Typed container for the application's initialized singletons.

`AppState` is the composition root: one place that names every long-lived
service/repository the app wires up at startup, instead of ~25 loose module
globals in ``startup.py``. It is built by :func:`startup.build_app_state` once
``initialize_all`` has populated those globals, and stored on
``app.state.container`` for the request path to reach via ``dependencies.py``.

Design notes
------------
- Every field is ``Optional`` and defaults to ``None``. Startup is best-effort:
  Brave/Jupiter/RAG can each fail to initialize (or be disabled) and the app
  still serves. A ``None`` field means "not initialized / disabled", exactly as
  the corresponding ``startup`` global would be.
- Type hints are deferred (``from __future__ import annotations`` + a
  ``TYPE_CHECKING`` block), so importing this module pulls in no repository or
  service code — it stays cycle-free and cheap.
- The ``startup.get_X()`` getters remain the seam tests patch. FastAPI providers
  in ``dependencies.py`` resolve through those getters, not through this
  container's fields directly, so this snapshot never has to be the test seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .mcp_client_stdio import BraveMCPClientStdio
    from .memory_manager import ConversationSummarizer, MemoryManager
    from .memory_rag import EpisodicMemoryRAG
    from .fact_extractor import FactExtractor
    from .repositories.session_repository import SessionRepository
    from .repositories.media_repository import MediaRepository
    from .repositories.message_repository import MessageRepository
    from .repositories.summary_repository import SummaryRepository
    from .repositories.emotional_state_repository import EmotionalStateRepository
    from .repositories.user_profile_repository import UserProfileRepository
    from .repositories.seeker_progression_repository import SeekerProgressionRepository
    from .repositories.user_repository import UserRepository
    from .repositories.wallet_registry_repository import WalletRegistryRepository
    from .repositories.wallet_summary_repository import WalletSummaryRepository
    from .repositories.wallet_flow_repository import WalletFlowRepository
    from .repositories.trade_history_repository import TradeHistoryRepository


@dataclass
class AppState:
    """Snapshot of every initialized singleton — the app's composition root."""

    # Core repositories (raise-on-missing at the getter layer)
    session_repo: Optional["SessionRepository"] = None
    message_repo: Optional["MessageRepository"] = None
    summary_repo: Optional["SummaryRepository"] = None
    emotional_state_repo: Optional["EmotionalStateRepository"] = None
    user_profile_repo: Optional["UserProfileRepository"] = None
    seeker_progression_repo: Optional["SeekerProgressionRepository"] = None
    user_repo: Optional["UserRepository"] = None
    media_repo: Optional["MediaRepository"] = None

    # Memory management (Phase 2)
    memory_manager: Optional["MemoryManager"] = None
    conversation_summarizer: Optional["ConversationSummarizer"] = None

    # Phase 3 advanced memory (RAG + facts)
    episodic_memory_rag: Optional["EpisodicMemoryRAG"] = None
    fact_extractor: Optional["FactExtractor"] = None
    fact_extraction_worker: object = None  # FactExtractionWorker (facts-gated)
    memory_fact_repo: object = None         # MemoryFactRepository (facts-gated)

    # Wallet metadata layer
    wallet_registry_repo: Optional["WalletRegistryRepository"] = None
    wallet_summary_repo: Optional["WalletSummaryRepository"] = None
    wallet_flow_repo: Optional["WalletFlowRepository"] = None
    trade_history_repo: Optional["TradeHistoryRepository"] = None

    # MCP clients
    brave_client: Optional["BraveMCPClientStdio"] = None

    # Jupiter MCP + strategy execution (all lazily/optionally initialized)
    jupiter_client: object = None
    jupiter_ops: object = None
    wallet_execution_service: object = None
    strategy_service: object = None
    wallet_repo: object = None
    trade_proposal_repo: object = None
    strategy_scheduler: object = None
