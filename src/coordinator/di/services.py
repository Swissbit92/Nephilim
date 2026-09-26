# src/coordinator/di/services.py
"""Service cluster: Brave MCP, memory/RAG/fact-extraction, tool interceptor.

Split out of ``startup.py`` (2026-08-22 decomposition, audit rec #7). Moved
verbatim — no behavior change; see ``startup.py`` for the re-export contract
that keeps ``startup.get_X`` / ``startup.init_X`` working unchanged.
"""

from __future__ import annotations

import logging
import os

from ..config import get_settings
from ..fact_extractor import FactExtractor
from ..mcp_client_stdio import BraveMCPClientStdio
from ..memory_manager import ConversationSummarizer, MemoryManager
from ..memory_rag import EpisodicMemoryRAG
from .repositories import get_message_repo, get_session_repo

logger = logging.getLogger(__name__)

# ----------------- Global State -----------------

# MCP Clients
_brave_client: BraveMCPClientStdio | None = None

# Memory Management (Phase 2)
_memory_manager: MemoryManager | None = None
_conversation_summarizer: ConversationSummarizer | None = None

# Phase 3: Advanced AI Memory
_episodic_memory_rag: EpisodicMemoryRAG | None = None
_fact_extractor: FactExtractor | None = None

# ADR-006 Phase 1 (M3/M4): ontology-lite fact store + async extraction worker
_memory_fact_repo = None        # Optional[MemoryFactRepository] (shared read/write)
_fact_extraction_worker = None  # Optional[FactExtractionWorker]

# HERMES-Agents Phase 3: deterministic tool-call interceptor (stateless singleton)
_tool_interceptor = None

# ADR-014: the Neo4j driver. Untyped `= None` rather than Optional["Driver"] so
# importing this module never pulls in the neo4j package — the same reason
# app_state.py keeps its repository types under TYPE_CHECKING.
_neo4j_driver = None  # Optional[neo4j.Driver], None whenever GRAPH_ENABLED is false


# ----------------- Getters -----------------

def get_brave_client() -> BraveMCPClientStdio | None:
    """Get the global Brave MCP client instance (STDIO ephemeral containers)."""
    return _brave_client


def get_memory_manager() -> MemoryManager:
    """Get the memory manager."""
    if _memory_manager is None:
        raise RuntimeError("MemoryManager not initialized — server startup incomplete")
    return _memory_manager


def get_conversation_summarizer() -> ConversationSummarizer:
    """Get the conversation summarizer."""
    if _conversation_summarizer is None:
        raise RuntimeError("ConversationSummarizer not initialized — server startup incomplete")
    return _conversation_summarizer


def get_episodic_memory_rag() -> EpisodicMemoryRAG | None:
    """Get the episodic memory RAG system."""
    return _episodic_memory_rag


def get_fact_extractor() -> FactExtractor | None:
    """Get the fact extractor."""
    return _fact_extractor


def get_fact_extraction_worker():
    """Get the async ontology-lite fact-extraction worker (None when facts disabled)."""
    return _fact_extraction_worker


def get_memory_fact_repo():
    """Get the shared ontology-lite fact store (None until initialised)."""
    return _memory_fact_repo


def get_neo4j_driver():
    """Get the graph driver, or None when GRAPH_ENABLED is false or it failed to connect.

    Returns None rather than raising, matching get_memory_fact_repo and
    get_brave_client rather than the raise-on-missing getters in di/repositories.
    The distinction is not stylistic: a raise-on-missing getter says "this cannot
    be absent and the server is broken", which is true of the database and false of
    an optional projection. ADR-014 requires a graph outage to cost capability, not
    the server.
    """
    return _neo4j_driver


def get_tool_interceptor():
    """Get the shared ToolCallInterceptor (lazy; stateless, safe to share)."""
    global _tool_interceptor
    if _tool_interceptor is None:
        from ..services.tool_interceptor import ToolCallInterceptor
        _tool_interceptor = ToolCallInterceptor()
    return _tool_interceptor


# ----------------- Initialization Functions -----------------

def init_brave_client():
    """Initialize Brave MCP client if enabled."""
    global _brave_client

    brave_cfg = get_settings().brave
    if not brave_cfg.enabled:
        logger.info("Brave MCP is disabled (no API key)")
        return

    try:
        api_key = brave_cfg.api_key
        max_results = brave_cfg.max_results
        safesearch = brave_cfg.safesearch
        timeout = brave_cfg.timeout

        _brave_client = BraveMCPClientStdio(
            image=os.getenv("BRAVE_MCP_IMAGE", "docker.io/mcp/brave-search"),
            api_key=api_key,
            max_results=max_results,
            safesearch=safesearch,
            timeout=timeout
        )

        # Verify Docker daemon is reachable (Brave MCP requires Docker)
        if not _brave_client.health_check():
            logger.error(
                "Brave MCP client created but Docker is NOT running. "
                "Web search will fail until Docker Desktop is started."
            )
            _brave_client = None
        else:
            logger.info(f"Brave MCP STDIO client initialized (image={_brave_client.image}, max_results={max_results}, timeout={timeout}s)")
    except Exception as e:
        logger.error(f"Failed to initialize Brave MCP client: {e}")
        _brave_client = None


def init_memory_manager():
    """Initialize memory management components."""
    global _memory_manager, _conversation_summarizer

    _memory_manager = MemoryManager(max_tokens=get_settings().ollama.context_window)
    _conversation_summarizer = ConversationSummarizer()
    logger.info("Memory manager initialized (Phase 2)")


def prewarm_session_indexes(rag, session_repo, message_repo, limit: int) -> int:
    """ADR-006 M1: pre-index the ``limit`` most-recently-updated sessions.

    Rebuilds each session's FAISS index from its SQLite messages (the same path
    the chat flow runs lazily on first access), so a restart doesn't impose a
    cold-start re-index latency on the user's first message. SQLite remains the
    source of truth — this loses no data and is safe to skip.

    Pure and synchronous for testability; the caller runs it in a daemon thread.
    Each session is isolated so one failure can't abort the rest. Returns the
    number of sessions successfully warmed.
    """
    if rag is None or limit <= 0:
        return 0
    warmed = 0
    try:
        sessions = session_repo.get_all_sessions()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"[SessionPrewarm] could not list sessions (non-fatal): {exc}")
        return 0
    for s in sessions[:limit]:
        if s.get("message_count", 0) <= 0:
            continue
        try:
            msgs = message_repo.get_messages_by_session(s["id"])
            if msgs:
                rag.index_session(s["id"], msgs)
                warmed += 1
        except Exception as exc:
            logger.debug(f"[SessionPrewarm] session {s.get('id')} skipped (non-fatal): {exc}")
    return warmed


def init_graph_driver():
    """Construct the Neo4j driver when GRAPH_ENABLED is true. Never aborts a boot.

    Swallow-and-continue, matching Brave, Jupiter, the scheduler and the fact
    worker. Only the model check and init_db() may abort startup in this app, and a
    rebuildable projection does not belong in that tier — that is the property in
    ADR-014 that answers ADR-001's objection to another always-on service.
    """
    global _neo4j_driver
    g = get_settings().graph
    if not g.enabled:
        logger.info("[Graph] disabled (GRAPH_ENABLED=false) — no driver, no connection attempted")
        _neo4j_driver = None
        return
    try:
        from ..graph_driver import build_driver
        _neo4j_driver = build_driver(
            g.base_url, g.username, g.password, max_pool_size=g.max_pool_size,
        )
        logger.info("[Graph] driver ready at %s (ADR-014)", g.base_url)
    except Exception as e:
        logger.warning("[Graph] init skipped (non-fatal): %s: %s", type(e).__name__, e)
        _neo4j_driver = None


def close_graph_driver():
    """Close the driver. Called from the lifespan shutdown.

    Driver 6.x no longer closes itself in __del__, so this is not belt-and-braces:
    an unclosed driver holds its pooled connections for the life of the process.
    This is the second entry in server.py's shutdown block — the scheduler was the
    only resource in this app that had one.
    """
    global _neo4j_driver
    if _neo4j_driver is not None:
        try:
            _neo4j_driver.close()
            logger.info("[Graph] driver closed")
        except Exception as e:
            logger.warning("[Graph] driver close failed (non-fatal): %s", e)
        finally:
            _neo4j_driver = None


def init_phase3_memory():
    """Initialize Phase 3 advanced memory systems (RAG + Fact Extraction)."""
    global _episodic_memory_rag, _fact_extractor, _fact_extraction_worker, _memory_fact_repo

    try:
        # Initialize RAG memory with embeddings (uses config default)
        _episodic_memory_rag = EpisodicMemoryRAG()
        logger.info("Episodic Memory RAG initialized (Phase 3)")

        # Phase-2 (HERMES): pre-warm the global lore corpus in a background thread.
        # On-demand lore retrieval is always on (LORE_ONDEMAND_ENABLED retired
        # 2026-07-04). Reuses the RAG embedder; daemon so it never blocks startup.
        try:
            import threading as _threading

            def _prewarm_lore():
                try:
                    _episodic_memory_rag.index_lore_corpus()
                    logger.info("[LoreRAG] Lore corpus pre-warm complete")
                except Exception as exc:
                    logger.debug(f"[LoreRAG] Lore corpus pre-warm failed (non-fatal): {exc}")
            _threading.Thread(target=_prewarm_lore, daemon=True, name="prewarm-lore").start()
        except Exception as e:
            logger.debug(f"[LoreRAG] Lore pre-warm thread start failed (non-fatal): {e}")

        # ADR-006 M1: pre-warm the N most-recently-updated session indexes from
        # SQLite in a background daemon thread. The per-session FAISS index is
        # otherwise rebuilt lazily on the first chat after a restart (no data is
        # lost — SQLite is the source of truth); this only removes that one-time
        # cold-start re-index latency. Daemon so it never blocks startup; each
        # session is isolated so one failure can't abort the rest. No-op when
        # MEMORY_PREWARM_SESSIONS=0.
        try:
            from ..config import get_settings
            prewarm_n = get_settings().memory.prewarm_sessions
            if prewarm_n > 0:
                import threading as _threading

                def _prewarm_sessions():
                    warmed = prewarm_session_indexes(
                        _episodic_memory_rag,
                        get_session_repo(),
                        get_message_repo(),
                        prewarm_n,
                    )
                    logger.info(f"[SessionPrewarm] pre-warmed {warmed} session index(es)")
                _threading.Thread(
                    target=_prewarm_sessions, daemon=True, name="prewarm-sessions"
                ).start()
        except Exception as e:
            logger.debug(f"[SessionPrewarm] pre-warm thread start failed (non-fatal): {e}")

        # Initialize fact extractor
        # Note: Will need LLM client, initialized later in chat flow
        # For now, mark as ready for lazy init
        _fact_extractor = None  # Will be initialized with LLM client on first use
        logger.info("Fact Extractor ready for initialization (Phase 3)")

        # ADR-006 Phase 1 (M3): start the async ontology-lite fact-extraction worker
        # ONLY when MEMORY_FACTS_ENABLED. Off (default) → no worker thread, no fact
        # store writes. The extractor's LLM client is built lazily on first job (off
        # the request path), so startup stays cheap.
        try:
            from ..config import get_settings
            if get_settings().memory.facts_enabled:
                from ..fact_extraction_worker import FactExtractionWorker
                from ..repositories.memory_fact_repository import MemoryFactRepository

                _memory_fact_repo = MemoryFactRepository()  # shared read (M4) + write (M3)

                def _make_extractor():
                    from ..llm_client import create_llm_client
                    from ..triplet_extractor import TripletExtractor
                    llm = create_llm_client(
                        {}, temperature=get_settings().ollama.temp_fact_extraction
                    )
                    return TripletExtractor(llm)

                _fact_extraction_worker = FactExtractionWorker(
                    _make_extractor, _memory_fact_repo
                )
                _fact_extraction_worker.start()
                logger.info("[FactWorker] ontology-lite fact store ENABLED (ADR-006 M3/M4)")
        except Exception as e:
            logger.warning(f"[FactWorker] init skipped (non-fatal): {e}")
            _fact_extraction_worker = None

    except Exception as e:
        logger.error(f"Failed to initialize Phase 3 memory systems: {e}")
        logger.warning("Phase 3 features (RAG, cross-session memory) will be disabled")
        _episodic_memory_rag = None
        _fact_extractor = None
