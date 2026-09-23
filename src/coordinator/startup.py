# src/coordinator/startup.py
"""Application startup and initialization logic — the composition root.

The 25 ``get_*``/8 ``init_*`` functions that used to live directly in this
file (752 lines, flagged as the #1 structural fix in the 2026-07-04 and
2026-08-22 repo audits) are now split by cluster into ``di/``:

- ``di.repositories`` — the 12 SQLite repositories + DB/Alembic init + orphan
  cleanup.
- ``di.services`` — Brave MCP, memory manager, Phase 3 RAG/fact-extraction,
  tool interceptor.
- ``di.jupiter`` — Jupiter MCP, wallet execution/strategy services, scheduler.

This module re-exports every one of those names via plain
``from .di.X import name`` — a *name binding*, not a copy — so
``src.coordinator.startup.get_X`` / ``startup.init_X`` keep resolving exactly
as before. That is deliberate, not a shim to be "cleaned up" later:

- ``dependencies.py``'s FastAPI providers resolve through
  ``startup.get_X()`` (module-attribute lookup, not ``from .startup import
  get_X``) specifically so tests patching ``src.coordinator.startup.get_X``
  still intercept. Moving the *definitions* to ``di/*.py`` does not disturb
  this: patching the ``startup`` module's attribute still overrides what
  every caller sees, because every caller (routes, services, tests) resolves
  through this module's namespace, either via ``from .. import startup`` +
  ``startup.get_X()``, or via ``from ..startup import get_X`` (which binds
  the same function object at import time — unaffected by *where* that
  object's code lives).
- What stays here, and only here: ``initialize_all()`` (the ordered startup
  sequence), ``build_app_state()``/``get_app_state()`` (the AppState
  snapshot), and the three background pre-warm threads that don't belong to
  any one cluster (semantic router centroids, persona system-prompt cache).

See ``app_state.py`` and ``dependencies.py`` for the typed-DI layer this
composition root feeds (added 2026-07 M1-M3, ADR referenced in CLAUDE.md).
"""

from __future__ import annotations

import logging

from .app_state import AppState
from .config import get_settings
from .di.jupiter import (  # noqa: F401 - re-exported for startup.get_X()/init_X()
    get_jupiter_client,
    get_jupiter_ops,
    get_strategy_scheduler,
    get_strategy_service,
    get_trade_proposal_repo,
    get_wallet_execution_service,
    get_wallet_repo,
    init_jupiter,
    init_strategy_scheduler,
)
from .di.repositories import (  # noqa: F401 - re-exported for startup.get_X()/init_X()
    _DB_PATH,
    cleanup_orphaned_sessions,
    get_emotional_state_repo,
    get_message_repo,
    get_seeker_progression_repo,
    get_session_note_repo,
    get_session_repo,
    get_summary_repo,
    get_trade_history_repo,
    get_user_profile_repo,
    get_user_repo,
    get_wallet_flow_repo,
    get_wallet_registry_repo,
    get_wallet_summary_repo,
    init_db,
    init_repositories,
)
from .di.services import (  # noqa: F401 - re-exported for startup.get_X()/init_X()
    get_brave_client,
    get_conversation_summarizer,
    get_episodic_memory_rag,
    get_fact_extraction_worker,
    get_fact_extractor,
    get_memory_fact_repo,
    get_memory_manager,
    get_tool_interceptor,
    init_brave_client,
    init_memory_manager,
    init_phase3_memory,
    prewarm_session_indexes,
)
from .ollama_utils import assert_model_available, require_model_configured
from .persona_memory import _load_all_cards_cached, ensure_all_summaries_serialized

logger = logging.getLogger(__name__)

# Composition root: an AppState snapshot of the di/* singletons, built at the
# tail of initialize_all() and mirrored onto app.state.container (see server.py).
_app_state: AppState | None = None


def _safe(getter):
    """Call a raise-on-missing di getter, collapsing "not initialized" to None.

    build_app_state() wants the raw nullable value (a disabled/failed
    subsystem is a legitimate None in the snapshot), while several di
    getters raise RuntimeError instead of returning None so route/service
    callers get a clean 503 via dependencies.py. This reconciles the two: the
    result is identical to reading the underlying global directly, whether
    or not it happens to be initialized yet.
    """
    try:
        return getter()
    except RuntimeError:
        return None


def build_app_state() -> AppState:
    """Snapshot the initialized di/* singletons into an :class:`AppState`.

    Read-only view of what ``initialize_all`` has wired up so far — the request
    path reaches these through ``dependencies.py``/``app.state.container`` while
    the ``get_X()`` getters stay the patchable seam. Safe to call at any time;
    fields not yet initialized are simply ``None``.
    """
    return AppState(
        session_repo=_safe(get_session_repo),
        message_repo=_safe(get_message_repo),
        summary_repo=_safe(get_summary_repo),
        emotional_state_repo=_safe(get_emotional_state_repo),
        user_profile_repo=_safe(get_user_profile_repo),
        seeker_progression_repo=_safe(get_seeker_progression_repo),
        user_repo=_safe(get_user_repo),
        memory_manager=_safe(get_memory_manager),
        conversation_summarizer=_safe(get_conversation_summarizer),
        episodic_memory_rag=get_episodic_memory_rag(),
        fact_extractor=get_fact_extractor(),
        fact_extraction_worker=get_fact_extraction_worker(),
        memory_fact_repo=get_memory_fact_repo(),
        wallet_registry_repo=get_wallet_registry_repo(),
        wallet_summary_repo=get_wallet_summary_repo(),
        wallet_flow_repo=get_wallet_flow_repo(),
        trade_history_repo=get_trade_history_repo(),
        brave_client=get_brave_client(),
        jupiter_client=get_jupiter_client(),
        jupiter_ops=get_jupiter_ops(),
        wallet_execution_service=get_wallet_execution_service(),
        strategy_service=get_strategy_service(),
        wallet_repo=_safe(get_wallet_repo),
        trade_proposal_repo=_safe(get_trade_proposal_repo),
        strategy_scheduler=get_strategy_scheduler(),
    )


def get_app_state() -> AppState | None:
    """Return the composition-root snapshot built at the end of startup."""
    return _app_state


def initialize_all():
    """Run all initialization routines."""
    logger.info("Initializing FastAPI server...")

    # Check Ollama. require_model_configured runs FIRST: assert_model_available
    # would pass happily on a fallback model that happens to be pulled locally,
    # which is precisely the silent-wrong-model case we refuse to boot into.
    try:
        _model = require_model_configured(get_settings().ollama.model)
        assert_model_available(get_settings().ollama.base, _model)
        logger.info(f"Model check passed: {_model}")
    except Exception as e:
        logger.error(f"Model check failed: {e}")
        raise

    # Initialize database
    try:
        init_db()
        logger.info("Database initialized.")
    except Exception as e:
        logger.error(f"Database init failed: {e}")
        raise

    # Initialize repositories
    init_repositories()

    # Initialize memory manager
    init_memory_manager()

    # Initialize Phase 3 advanced memory (RAG + fact extraction)
    try:
        init_phase3_memory()
        if get_episodic_memory_rag():
            logger.info("Phase 3: RAG memory enabled (semantic search)")
        else:
            logger.info("Phase 3: RAG memory disabled")
    except Exception as e:
        logger.warning(f"Phase 3 initialization warning: {e}")

    # Initialize Brave MCP
    try:
        init_brave_client()
        if get_brave_client():
            logger.info("Brave MCP enabled (web search available)")
        else:
            logger.info("Brave MCP disabled (web search not available)")
    except Exception as e:
        logger.warning(f"Brave MCP initialization warning: {e}")

    # Bind web-toolset executors onto the tool registry (ADR-008 TB2). Attaches
    # the runtime search/fetch executors + the per-persona safesearch clamp;
    # harmless when the tool brain is off (nothing calls them). Lazy client
    # resolution inside the executors makes this init-order-independent.
    try:
        from .tools.executor_bindings import bind_web_executors
        bind_web_executors()
    except Exception as e:
        logger.warning(f"Tool-executor binding warning: {e}")

    # Initialize Jupiter MCP
    try:
        init_jupiter()
    except Exception as e:
        logger.warning(f"Jupiter MCP initialization warning: {e}")

    # Initialize Strategy Scheduler (must be after Jupiter)
    try:
        init_strategy_scheduler()
    except Exception as e:
        logger.warning(f"Strategy scheduler initialization warning: {e}")

    # Remove sessions for personas that no longer exist
    cleanup_orphaned_sessions()

    # Refresh persona summaries
    try:
        result = ensure_all_summaries_serialized(timeout_sec=0.01, poll_sec=0.01)
        logger.info(f"Summaries check completed: {result}")
    except Exception as e:
        logger.warning(f"Summary check failed: {e}")

    # R4: Pre-warm semantic router centroids in background (reuses the RAG embedding model already pulled)
    try:
        import threading as _threading
        def _prewarm_semantic():
            try:
                from .tools.semantic_router import warm_centroids
                # Semantic router is always primary (ROUTING_SEMANTIC_PRIMARY
                # retired 2026-07-04) → always warm the primary centroid set.
                ok = warm_centroids(include_primary=True)
                logger.info(
                    f"[SemanticRouter] Centroid pre-warm {'succeeded' if ok else 'skipped (no embedding model)'}"
                    " (incl. primary set)"
                )
            except Exception as exc:
                logger.debug(f"[SemanticRouter] Centroid pre-warm failed (non-fatal): {exc}")
        _threading.Thread(target=_prewarm_semantic, daemon=True, name="prewarm-semantic").start()
    except Exception as e:
        logger.debug(f"[SemanticRouter] Pre-warm thread start failed (non-fatal): {e}")

    # R6: Pre-warm system prompt LRU cache for all personas in a background thread.
    # Eliminates blocking CV-summary LLM call on the first user request per persona.
    try:
        import threading

        from .persona_memory import build_system_prompt as _build_sp

        def _prewarm_prompts():
            cards = _load_all_cards_cached()
            for card in cards:
                key = card.get("key")
                if not key:
                    continue
                try:
                    _build_sp(key)
                    logger.debug(f"[Prewarm] System prompt cached for '{key}'")
                except Exception as exc:
                    logger.debug(f"[Prewarm] Skipped '{key}': {exc}")
            logger.info(f"[Prewarm] System prompt cache warmed for {len(cards)} persona(s)")

        threading.Thread(target=_prewarm_prompts, daemon=True, name="prewarm-prompts").start()
        logger.info("[Prewarm] System prompt pre-warming started in background")
    except Exception as e:
        logger.warning(f"[Prewarm] Pre-warm thread failed to start: {e}")

    # Composition root: snapshot the now-initialized globals so the request path
    # can reach them via app.state.container / dependencies.py.
    global _app_state
    _app_state = build_app_state()

    logger.info("FastAPI server initialization complete.")
