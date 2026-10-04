# src/coordinator/dependencies.py
"""FastAPI dependency providers — the request-path seam onto the app singletons.

Each provider here is meant to be used with ``fastapi.Depends`` in a route
signature. Two families:

- ``require_*`` — for singletons that MUST exist to serve the request. They
  resolve the underlying ``startup`` getter and translate its
  ``RuntimeError("… not initialized")`` into an HTTP 503, so a request that
  arrives before startup finished (or in a degraded process) gets a clean,
  retryable error instead of a 500. This generalizes the pre-existing
  ``routes/nephilim.py::_require_progression_repo`` pattern.
- ``optional_*`` — for singletons that are legitimately absent when disabled or
  failed to initialize (Brave, Jupiter, RAG, wallet-metadata). They return the
  value as-is (possibly ``None``); the handler decides what a ``None`` means.

Resolution goes THROUGH the ``startup`` module object (``startup.get_X()``), not
via ``from .startup import get_X``. That is deliberate: tests patch
``src.coordinator.startup.get_X``, and a call-time attribute lookup on the module
honors the patch, whereas a name imported at module load would freeze the
original and defeat the patch. Do not "optimize" these into direct imports.
"""

from __future__ import annotations

from fastapi import HTTPException

from . import startup


def _require(getter, label: str):
    """Call a raise-on-missing startup getter, mapping non-init to a 503."""
    try:
        return getter()
    except RuntimeError:
        raise HTTPException(status_code=503, detail=f"{label} not initialized")


# ----------------- Required singletons (503 when missing) -----------------

def require_session_repo():
    return _require(startup.get_session_repo, "SessionRepository")


def require_media_repo():
    return _require(startup.get_media_repo, "MediaRepository")


def require_image_job_repo():
    return _require(startup.get_image_job_repo, "ImageJobRepository")


def require_message_repo():
    return _require(startup.get_message_repo, "MessageRepository")


def require_summary_repo():
    return _require(startup.get_summary_repo, "SummaryRepository")


def require_emotional_state_repo():
    return _require(startup.get_emotional_state_repo, "EmotionalStateRepository")


def require_user_profile_repo():
    return _require(startup.get_user_profile_repo, "UserProfileRepository")


def require_seeker_progression_repo():
    return _require(startup.get_seeker_progression_repo, "Progression system")


def require_user_repo():
    return _require(startup.get_user_repo, "UserRepository")


def require_memory_manager():
    return _require(startup.get_memory_manager, "MemoryManager")


def require_conversation_summarizer():
    return _require(startup.get_conversation_summarizer, "ConversationSummarizer")


def require_wallet_repo():
    return _require(startup.get_wallet_repo, "WalletRepository")


def require_trade_proposal_repo():
    return _require(startup.get_trade_proposal_repo, "TradeProposalRepository")


# ----------------- Optional singletons (may return None) -----------------

def optional_brave_client():
    return startup.get_brave_client()


def optional_episodic_memory_rag():
    return startup.get_episodic_memory_rag()


def optional_fact_extractor():
    return startup.get_fact_extractor()


def optional_fact_extraction_worker():
    return startup.get_fact_extraction_worker()


def optional_memory_fact_repo():
    return startup.get_memory_fact_repo()


def optional_wallet_registry_repo():
    return startup.get_wallet_registry_repo()


def optional_wallet_summary_repo():
    return startup.get_wallet_summary_repo()


def optional_wallet_flow_repo():
    return startup.get_wallet_flow_repo()


def optional_trade_history_repo():
    return startup.get_trade_history_repo()


def optional_jupiter_client():
    return startup.get_jupiter_client()


def optional_jupiter_ops():
    return startup.get_jupiter_ops()


def optional_wallet_execution_service():
    return startup.get_wallet_execution_service()


def optional_strategy_service():
    return startup.get_strategy_service()


def optional_strategy_scheduler():
    return startup.get_strategy_scheduler()
