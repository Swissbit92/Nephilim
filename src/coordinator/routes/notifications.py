# src/coordinator/routes/notifications.py
"""Out-of-band results, for a client that was not waiting on a request.

A generation takes ~331 s. The Telegram gateway's HTTP client gives up at
180 s and every coordinator route is a sync `def` in a threadpool, so the
answer cannot come back on the request that asked for it. The gateway polls
here instead.

**The response is deliberately CHAT-SHAPED.** `metadata.media` is a list of
`MediaItem`, exactly as `/chat` and the media fixture endpoint return it, so
the gateway parses it with `relay.extract_media` — the same extractor it
already uses on the chat path — and delivers it with the same
`_deliver_media`. Inventing a second shape here would mean a second parser,
and the transport would then be proven on a code path the real one does not
use. That failure has a name in this repo: agreement between two things is
not evidence when the comparison ran on a different path than the one being
judged.

**Claim-on-read, not read-then-mark.** `POST /notifications/claim` marks the
jobs it returns as notified *before* handing them over, and the gateway hands
one back with `/nack` if the send fails. The ordering is chosen on which
failure is cheaper: a crash between claiming and sending loses one
notification, while a crash between sending and marking delivers the image
twice. It is a POST and not a GET precisely because it mutates — a GET that
silently consumes work is the kind of thing that looks idempotent until
someone retries it.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import startup
from ..config import get_settings
from ..repositories.image_job_repository import JobStatus
from ..schemas import MediaItem, ResponseMetadata, SourceType
from ..services import media_storage

router = APIRouter(tags=["notifications"])
logger = logging.getLogger(__name__)

#: Plain text, never a parse_mode. These reach a user verbatim.
_FAILED_TEXT = "I tried to make that picture and it didn't work out."
_CANCELLED_TEXT = "I stopped making that picture."
_SUCCESS_TEXT = "Here, I made this for you."


def _repo():
    """Resolved through the ``startup`` module at call time, never imported.

    Tests patch ``src.coordinator.startup.get_image_job_repo``; a
    module-load `from ..startup import get_image_job_repo` would freeze the
    unpatched original and the patch would silently not apply.
    """
    return startup.get_image_job_repo()


def _media_for(job) -> list[MediaItem]:
    """Build the chat-shaped media list for a finished job.

    Returns empty rather than raising when the file is gone: the notification
    still has to go out, because "your picture is ready" followed by silence
    is worse than an honest failure message.
    """
    if job.status != JobStatus.SUCCEEDED or not job.media_path:
        return []
    path = Path(job.media_path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        logger.warning("[Notify] %s succeeded but its file is unreadable: %s", job.id, exc)
        return []

    dims = media_storage.png_dimensions(data)
    return [
        MediaItem(
            media_id=path.stem,
            path=str(path),
            filename=f"nephilim_{path.stem[:12]}.png",
            bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            width=dims[0] if dims else None,
            height=dims[1] if dims else None,
            caption=_SUCCESS_TEXT,
        )
    ]


def _render(job) -> dict:
    """One job as a chat-shaped payload the gateway can deliver as-is."""
    media = _media_for(job)

    if job.status == JobStatus.SUCCEEDED and media:
        answer = _SUCCESS_TEXT
    elif job.status == JobStatus.SUCCEEDED:
        # Succeeded but the file vanished. Say so — a success with nothing
        # attached is exactly the silent failure this feature was built to
        # avoid rendering as success.
        answer = "I made that picture but I can't find it any more."
    elif job.status == JobStatus.CANCELLED:
        answer = _CANCELLED_TEXT
    else:
        answer = _FAILED_TEXT

    metadata = ResponseMetadata(source_type=SourceType.LLM, media=media)
    return {
        "job_id": job.id,
        "session_id": job.session_id,
        "persona_key": job.persona_key,
        "status": str(job.status),
        # The operator-facing reason, for the gateway's log. Never shown to the
        # user: six distinct failure reasons collapse to one message so a
        # failure cannot describe the machine's internals in a chat.
        "error": job.error,
        "answer": answer,
        "message_flow": "single",
        "message_count": 1,
        "used_search": False,
        "metadata": metadata.model_dump(),
        "rewritten": False,
    }


@router.post("/notifications/claim")
def claim_notifications(limit: int = 10):
    """Take responsibility for delivering up to ``limit`` finished jobs.

    Marks each returned job notified BEFORE returning it. A job this call
    hands over will not be handed to anyone else unless the caller `/nack`s
    it, so a gateway that crashes mid-delivery loses that one notification
    rather than sending the image twice.
    """
    if limit < 1 or limit > 50:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 50")

    repo = _repo()
    claimed = []
    for job in repo.pending_notification(limit=limit):
        if not repo.claim_for_notify(job.id):
            # Someone else took it between the read and the claim. Not an
            # error — this is the conditional UPDATE doing its job.
            continue
        claimed.append(_render(job))

    if claimed:
        logger.info("[Notify] handed %d notification(s) to a client", len(claimed))
    return {"notifications": claimed}


@router.post("/notifications/{job_id}/nack")
def nack_notification(job_id: str):
    """Hand a claimed notification back after a failed delivery.

    Without this a transient Telegram outage would permanently swallow the
    result of a five-and-a-half-minute generation.
    """
    repo = _repo()
    job = repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    repo.unclaim_notify(job_id)
    logger.info("[Notify] %s handed back for redelivery", job_id)
    return {"ok": True, "job_id": job_id}


@router.get("/notifications/jobs/{job_id}")
def get_job(job_id: str):
    """Current state of one job, for a `/status`-style command.

    Read-only and claim-free, deliberately: a status check must never consume
    a pending notification.
    """
    job = _repo().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {
        "job_id": job.id,
        "session_id": job.session_id,
        "status": str(job.status),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error,
    }


@router.post("/sessions/{session_id}/image/generate")
def enqueue_generation(session_id: str, body: dict):
    """Queue a generation. DEVELOPMENT SURFACE — 404s unless enabled.

    Lives on this router rather than `routes/sessions.py` because it is part
    of the image-job surface, and 404 rather than 403 when disabled for the
    same reason the media fixture does: a dev endpoint should not advertise
    its own existence.

    Returns immediately with a job id. It cannot do otherwise — the work takes
    ~331 s and the gateway's client gives up at 180 s, which is the entire
    reason the notification path exists.
    """
    cfg = get_settings().image_gen
    if not (cfg.enabled and cfg.dev_endpoint_enabled):
        raise HTTPException(status_code=404, detail="Not found.")

    prompt = (body or {}).get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")
    if len(prompt) > 1000:
        raise HTTPException(status_code=400, detail="prompt is too long")

    if not startup.get_session_repo().session_exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found.")

    persona_key = (body or {}).get("persona_key") or "gwen"
    job = _repo().create(
        session_id=session_id, persona_key=persona_key, prompt=prompt.strip()
    )
    logger.info("[Notify] queued generation %s for session %s", job.id, session_id[:8])
    return {"job_id": job.id, "status": str(job.status)}
