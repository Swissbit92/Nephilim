"""Evicting the chat model from Ollama, and proving it actually left.

Ollama has no admission-control API: any request loads the model, and nothing
can be told to refuse. All this module does is *evict* and *verify*; keeping
traffic away during the window is the arbiter's job (`resource_arbiter.py`).

⚠️ **The unload response is not evidence the model left.** Measured against
the live server (Ollama 0.34.2, 2026-10-02):

    POST /api/generate {"model": M, "keep_alive": 0}
      -> HTTP 200 in 0.002 s, body {"done": true, "done_reason": "unload"}

...sent while a generation was in flight. The reply claimed `unload`
immediately; the in-flight request then ran to completion, all 400 tokens, with
the model resident the whole time. It only left 0.1 s after that request
finished. So a caller that trusted the 200 would have launched a 20 GB
generation alongside 17 GB of resident model — the exact collision this exists
to prevent.

This is the house failure shape, third instance: a success response that is not
evidence of the outcome (mflux exits 0 having written nothing; a failed KuCoin
call rendered as `+0.00 funding`). So `unload()` ignores the body and polls
`/api/ps` until the model is genuinely absent.

Two upsides that fell out of the same measurement:

- **The poll IS the drain.** A deferred unload is not discarded — it fires as
  soon as the in-flight request completes. Waiting for absence therefore waits
  out in-flight work for free; no separate drain machinery is needed.
- **Eviction is quick once idle**: `/api/ps` cleared 0.125 s after the request
  and the runner process was gone at 0.169 s. Freeing ~17 GiB costs under a
  fifth of a second. The expense is all on the way back in (~5 s), not out.

Scope: the CHAT model only. `bge-m3` is also resident but is 0.63 GiB and sits
on the hot path (semantic router, lore, RAG, the relevance gate), so evicting
it would buy ~1% of the memory and cost a reload on every routing decision.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

#: Generous: the only thing we do is wait for an in-flight turn to end.
_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)

_POLL_SECONDS = 0.1


@dataclass(frozen=True)
class Resident:
    """One model as `/api/ps` reports it."""

    model: str
    size_vram: int
    expires_at: str

    @property
    def pinned(self) -> bool:
        """Pinned forever (keep_alive=-1) renders as a year-2319 expiry."""
        return self.expires_at[:4].isdigit() and int(self.expires_at[:4]) > 2100


@dataclass(frozen=True)
class UnloadResult:
    unloaded: bool
    seconds: float
    #: Set when the model was still resident at the deadline.
    still_resident_vram: int | None = None


def _get(base_url: str, path: str) -> dict:
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get(f"{base_url.rstrip('/')}{path}")
        r.raise_for_status()
        return r.json()


def _post(base_url: str, path: str, payload: dict) -> dict:
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.post(f"{base_url.rstrip('/')}{path}", json=payload)
        r.raise_for_status()
        return r.json()


def resident_models(base_url: str) -> list[Resident]:
    """Everything Ollama currently holds. Empty on any failure.

    Deliberately forgiving: this feeds a guard, and a guard that raises when
    the server is merely unreachable converts a harmless condition into a
    failed generation. An unreachable Ollama is holding nothing.
    """
    try:
        payload = _get(base_url, "/api/ps")
    except Exception as exc:  # noqa: BLE001 - a probe never propagates
        logger.warning("[OllamaAdmin] /api/ps unreachable: %s", exc)
        return []
    out = []
    for m in payload.get("models") or []:
        name = m.get("model") or m.get("name")
        if name:
            out.append(
                Resident(
                    model=name,
                    size_vram=int(m.get("size_vram") or 0),
                    expires_at=str(m.get("expires_at") or ""),
                )
            )
    return out


def is_resident(base_url: str, model: str) -> bool:
    return any(r.model == model for r in resident_models(base_url))


def resident_vram_bytes(base_url: str) -> int:
    """Total VRAM Ollama reports holding, across every model.

    The ONLY honest instrument for this. The runner process reported **8 MiB
    RSS while holding 16.40 GiB** — `ps`/`psutil` cannot see Metal buffers, so
    any check built on process memory reads "nothing loaded" on a full machine.
    """
    return sum(r.size_vram for r in resident_models(base_url))


def unload(base_url: str, model: str, *, timeout_seconds: float = 120.0) -> UnloadResult:
    """Evict `model` and wait until `/api/ps` confirms it is gone.

    Returns rather than raises on timeout: the caller (the arbiter) has to
    decide whether to abandon the generation, and it holds the context needed
    to say so usefully.

    `timeout_seconds` is sized for an in-flight chat turn to finish, not for
    the eviction itself (which takes ~0.17 s once idle). 120 s is ~6x the
    typical turn. Note the completion client's own read timeout is 600 s, so a
    pathological turn CAN outlast this — abandoning the generation is the right
    outcome there, because proceeding would mean 17 GiB plus 20 GiB on a 48 GiB
    box.
    """
    started = time.monotonic()

    # keep_alive MUST be the integer 0, never the string "0": Ollama parses it
    # as a Go duration and a bare numeric string is rejected outright
    # ("time: missing unit in duration"). Same trap OllamaSettings.wire_keep_alive
    # documents for "-1".
    try:
        _post(base_url, "/api/generate", {"model": model, "keep_alive": 0})
    except Exception as exc:  # noqa: BLE001
        # Not fatal by itself — the model may already be gone, which the poll
        # below settles. Raising here would turn "already evicted" into an error.
        logger.warning("[OllamaAdmin] unload request failed (%s); verifying anyway", exc)

    deadline = started + timeout_seconds
    while time.monotonic() < deadline:
        if not is_resident(base_url, model):
            elapsed = time.monotonic() - started
            logger.info("[OllamaAdmin] %s evicted and verified gone in %.2fs", model, elapsed)
            return UnloadResult(unloaded=True, seconds=elapsed)
        time.sleep(_POLL_SECONDS)

    held = next((r.size_vram for r in resident_models(base_url) if r.model == model), 0)
    elapsed = time.monotonic() - started
    logger.error(
        "[OllamaAdmin] %s STILL RESIDENT after %.0fs (%.2f GiB) — refusing to "
        "report an eviction that did not happen",
        model,
        elapsed,
        held / 2**30,
    )
    return UnloadResult(unloaded=False, seconds=elapsed, still_resident_vram=held)


def repin(base_url: str, model: str, keep_alive: str | int = -1) -> bool:
    """Load `model` back and pin it, so the next chatter does not pay for it.

    Best-effort by design, and cheap to lose: every completion already sends
    `keep_alive=-1`, so the first chat turn after the window re-pins by itself.
    All this buys is that the user is not the one who waits — measured at
    5.11 s (3.31 s load + 1.67 s prompt eval) for a trivial turn.

    ⚠️ That 5.11 s was measured with a WARM page cache and no intervening
    generation. A ~20 GB image job is exactly the workload that evicts those
    pages, so the real post-generation reload may be closer to a cold start.
    Not yet measured; do not quote 5.11 s for the post-generation case.
    """
    try:
        _post(
            base_url,
            "/api/generate",
            {
                "model": model,
                "prompt": "",
                "stream": False,
                "keep_alive": keep_alive,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[OllamaAdmin] re-pin of %s failed: %s", model, exc)
        return False

    resident = next((r for r in resident_models(base_url) if r.model == model), None)
    if resident is None:
        logger.warning("[OllamaAdmin] re-pin of %s returned 200 but it is not resident", model)
        return False
    logger.info(
        "[OllamaAdmin] %s re-pinned (%.2f GiB, pinned=%s)",
        model,
        resident.size_vram / 2**30,
        resident.pinned,
    )
    return True
