# src/coordinator/server.py
"""
Local Coordinator server for GraphRAG Local QA Chat with Personas.

This is the main entry point that assembles the FastAPI application
from modular components. Business logic is organized into:
- routes/ - API endpoint handlers
- services/ - Business logic services
- repositories/ - Database access layer
- schemas.py - Pydantic models

Provides endpoints for chat, greetings, persona CV summaries, and chat persistence (SQLite).
"""

from __future__ import annotations

import logging
import urllib.request
import urllib.error
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .startup import initialize_all, get_session_repo, get_brave_client
from .routes.chat import router as chat_router
from .routes.sessions import router as sessions_router
from .routes.personas import router as personas_router
from .routes.nephilim import router as nephilim_router
from .routes.wallet import router as wallet_router
from .routes.auth import auth_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ----------------- Lifespan -----------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    initialize_all()
    # Publish the composition-root snapshot for the request path (dependencies.py).
    from .startup import get_app_state
    app.state.container = get_app_state()
    try:
        yield
    finally:
        # Shutdown, in DEPENDENCY ORDER, each step in its own try/finally.
        #
        # ORDER IS LOAD-BEARING: producers of graph work stop BEFORE the driver
        # closes. The driver is documented as concurrency-safe while `close()`
        # explicitly is NOT — "make sure you are not using the driver object or
        # any resources spawned from it while calling this method. Failing to do
        # so results in unspecified behavior." A first draft of this block closed
        # the driver first, which would be a use-after-close the moment anything
        # graph-touching is added to the scheduler or the pre-warm threads.
        #
        # THE try/finally IS ALSO LOAD-BEARING, and it is why this block grew:
        # without it a raising scheduler.shutdown() skips close_graph_driver()
        # entirely — and in driver 6.x a leaked driver is SILENT. All that remains
        # of the old __del__ behaviour is a ResourceWarning, which Python ignores
        # by default, so the leak produces no error, no log and no warning. Just
        # orphaned sockets and threads for the life of the process. In 5.x the GC
        # quietly saved you; it no longer does.
        from .startup import close_graph_driver, get_strategy_scheduler
        try:
            scheduler = get_strategy_scheduler()
            if scheduler and scheduler.running:
                scheduler.shutdown(wait=False)
                logger.info("Strategy scheduler stopped")
        except Exception:
            logger.exception("Strategy scheduler shutdown failed")
        finally:
            close_graph_driver()


# ----------------- FastAPI App -----------------

app = FastAPI(title="Local Coordinator (Chat-only)", version="0.7.0", lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3001", "http://127.0.0.1:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(chat_router)
app.include_router(sessions_router)
app.include_router(personas_router)
app.include_router(nephilim_router)
app.include_router(wallet_router)
app.include_router(auth_router)


# ----------------- Health & Debug Endpoints -----------------

@app.get("/health")
def health():
    """Health check endpoint."""
    try:
        model = get_settings().ollama.model
        # DB ping
        get_session_repo().get_all_sessions()
        return {"status": "ok", "model": model, "db": "ok"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})


@app.get("/ready")
def ready():
    """Subsystem readiness check — returns status of DB, Ollama, and MCP clients."""
    checks = {}

    # DB check: lightweight SELECT 1
    try:
        repo = get_session_repo()
        repo._conn().execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # Ollama check: HTTP GET /api/version with 3s timeout
    try:
        ollama_base = get_settings().ollama.base.rstrip("/")
        req = urllib.request.Request(f"{ollama_base}/api/version", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            checks["ollama"] = "ok"
    except Exception as e:
        checks["ollama"] = f"error: {e}"

    # MCP subsystems: report enabled/disabled
    checks["brave_mcp"] = "enabled" if get_brave_client() is not None else "disabled"

    # Critical path: DB + Ollama must be ok
    critical_ok = checks["database"] == "ok" and checks["ollama"] == "ok"
    status_code = 200 if critical_ok else 503

    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if critical_ok else "degraded", "checks": checks},
    )


# Note: initialization is handled by the lifespan context manager above.
