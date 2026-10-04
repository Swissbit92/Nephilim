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
import urllib.error
import urllib.request
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .routes.auth import auth_router
from .routes.chat import router as chat_router
from .routes.nephilim import router as nephilim_router
from .routes.notifications import router as notifications_router
from .routes.personas import router as personas_router
from .routes.sessions import router as sessions_router
from .routes.wallet import router as wallet_router
from .services.resource_arbiter import ResourceBusyError
from .startup import get_brave_client, get_session_repo, initialize_all

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

    # Subsystem lifespans compose through an AsyncExitStack: FastAPI has no
    # multi-lifespan primitive, and a stack makes the next subsystem a
    # one-liner instead of another level of nesting. It unwinds in reverse,
    # which is the same dependency-order rule the shutdown block below argues
    # for by hand.
    from contextlib import AsyncExitStack

    from .services.image_gen.lifespan import image_gen_lifespan

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(image_gen_lifespan(app))
        try:
            yield
        finally:
            _shutdown()


def _shutdown() -> None:
    """Teardown, in DEPENDENCY ORDER, each step in its own try/finally.

    Lifted verbatim out of the lifespan body when subsystem lifespans were
    composed above; the argument below is the original one and still governs.
    Note the AsyncExitStack unwinds BEFORE this runs, so any subsystem lifespan
    has already stopped its own threads by the time the shared resources close
    — which is the same producers-before-consumers rule, applied one level up.

    ORDER IS LOAD-BEARING: producers of graph work stop BEFORE the driver
    closes. The driver is documented as concurrency-safe while `close()`
    explicitly is NOT — "make sure you are not using the driver object or any
    resources spawned from it while calling this method. Failing to do so
    results in unspecified behavior." A first draft of this block closed the
    driver first, which would be a use-after-close the moment anything
    graph-touching is added to the scheduler or the pre-warm threads.

    THE try/finally IS ALSO LOAD-BEARING, and it is why this block grew:
    without it a raising scheduler.shutdown() skips close_graph_driver()
    entirely — and in driver 6.x a leaked driver is SILENT. All that remains of
    the old __del__ behaviour is a ResourceWarning, which Python ignores by
    default, so the leak produces no error, no log and no warning. Just
    orphaned sockets and threads for the life of the process. In 5.x the GC
    quietly saved you; it no longer does.
    """
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

app = FastAPI(title="Local Coordinator (Chat-only)", version="0.8.0", lifespan=lifespan)

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
app.include_router(notifications_router)
app.include_router(wallet_router)
app.include_router(auth_router)


# ----------------- Resource-busy handling -----------------

@app.exception_handler(ResourceBusyError)
async def _resource_busy(request, exc: ResourceBusyError):
    """A generation holds the machine — answer in voice, not as a failure.

    APP-WIDE rather than per-route, deliberately. The first version of this
    caught it inside `routes/chat.py::chat`, which fixed `/chat` and left
    `POST /sessions/{id}/chat` — the endpoint the Telegram gateway actually
    uses — still returning 503 "LLM service temporarily unavailable". Measured
    live: the user asked "How are you today?" while an image they had just
    requested was rendering normally and was told twice that something had
    gone wrong.

    HTTP 200, not 503: nothing failed. A 5xx makes every client treat a
    correct, expected refusal as an outage, and the gateway renders it as
    MSG_ERROR. The body is chat-shaped so existing clients parse it with no
    change at all.
    """
    logger.info("[Busy] refused while the machine is leased: %s", exc)
    from .routes.chat import busy_drawing_body

    # The persona is on the request body, which an exception handler does not
    # get. Read it back so the line is HERS; an unreadable body degrades to
    # the voiceless fallback rather than failing the turn.
    persona = ""
    try:
        body = await request.json()
        persona = str(body.get("persona") or "")
    except Exception:  # noqa: BLE001
        pass
    if not persona:
        persona = _persona_for_session_path(request)

    return JSONResponse(status_code=200, content=busy_drawing_body(persona))


def _persona_for_session_path(request) -> str:
    """Recover the persona for POST /sessions/{id}/chat, which does not carry
    one in its body — the session row knows it."""
    try:
        parts = request.url.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "sessions":
            from .startup import get_session_repo

            row = get_session_repo().get_session(parts[1])
            return (row or {}).get("persona_key", "") or ""
    except Exception:  # noqa: BLE001
        pass
    return ""


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
