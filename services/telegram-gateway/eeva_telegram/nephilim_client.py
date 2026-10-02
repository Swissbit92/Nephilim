"""Async HTTP client for the nephilim FastAPI backend.

Wraps the session-API endpoints the gateway needs: create/greet/chat/clear,
the read-only toolkit introspection, and the ADR-011 conversation-control verbs
(regenerate/continue/undo/narrate/impersonate, session metadata, and note CRUD).

Errors are normalised into a small typed hierarchy so the handler layer can map
them to user-facing messages WITHOUT ever leaking URLs, stack traces, or JSON
internals into a Telegram chat.
"""

from __future__ import annotations

from typing import Any

import httpx


class NephilimError(Exception):
    """Base class for all nephilim client failures."""


class NephilimUnavailableError(NephilimError):
    """Backend unreachable or timed out (connection error / read timeout)."""


class NephilimSessionNotFoundError(NephilimError):
    """The session id is unknown to the backend (HTTP 404).

    Signals the caller to drop its stale mapping and recreate a session.
    """


class NephilimServerError(NephilimError):
    """Backend returned an unexpected non-2xx status, or a malformed body."""


class NephilimBadRequestError(NephilimError):
    """Backend rejected the request as invalid (HTTP 400).

    For conversation-control verbs this means "nothing to act on" (e.g. /regen
    with no prior reply) — a normal, user-facing state, not a server fault.
    """


class NephilimClient:
    """Thin async wrapper around the nephilim session API.

    One long-lived httpx.AsyncClient is reused for keep-alive. Call aclose()
    on shutdown (or use as an async context manager).
    """

    def __init__(self, base_url: str, timeout_seconds: float = 180.0) -> None:
        self._base_url = base_url.rstrip("/")
        # Connect quickly (backend is local); allow a long read for slow LLM gen.
        timeout = httpx.Timeout(timeout_seconds, connect=5.0)
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)

    async def __aenter__(self) -> NephilimClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=json)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as exc:
            raise NephilimUnavailableError(str(exc)) from exc
        except httpx.HTTPError as exc:  # catch-all for any other transport error
            raise NephilimServerError(str(exc)) from exc

        if response.status_code == 404:
            raise NephilimSessionNotFoundError(path)
        if response.status_code == 400:
            raise NephilimBadRequestError(f"HTTP 400 for {path}")
        if response.status_code >= 400:
            raise NephilimServerError(f"HTTP {response.status_code} for {path}")

        # DELETE returns {"ok": true}; tolerate empty/non-JSON gracefully.
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise NephilimServerError(f"malformed JSON from {path}") from exc

    async def create_session(self, persona_key: str, title: str = "Telegram") -> str:
        """Create a session and return its session_id."""
        data = await self._request("POST", "/sessions", json={"persona_key": persona_key, "title": title})
        session_id = data.get("session_id") or data.get("id")
        if not session_id:
            raise NephilimServerError("create_session response missing session_id")
        return str(session_id)

    async def greet(self, session_id: str) -> dict[str, Any]:
        """Generate and store an in-character greeting for the session."""
        return await self._request("POST", f"/sessions/{session_id}/greet", json={})

    async def chat(self, session_id: str, message: str) -> dict[str, Any]:
        """Send one chat turn. Returns the raw nephilim response dict."""
        return await self._request("POST", f"/sessions/{session_id}/chat", json={"message": message})

    async def clear_messages(self, session_id: str) -> dict[str, Any]:
        """Delete all messages + emotional state for the session (true reset).

        Returns the body so the caller can read ``cleared.images`` — the count
        the coordinator actually quarantined. Previously the body was discarded,
        which is why nothing could report what a reset had done.
        """
        return await self._request("DELETE", f"/sessions/{session_id}/messages")

    async def request_fixture_image(self, session_id: str) -> dict[str, Any]:
        """POST /sessions/{id}/media/fixture — dev probe, 404s unless enabled."""
        return await self._request("POST", f"/sessions/{session_id}/media/fixture", json={})

    async def claim_notifications(self, limit: int = 10) -> dict[str, Any]:
        """POST /notifications/claim — take delivery of finished image jobs.

        MUTATING despite reading like a fetch: the coordinator marks each
        returned job notified before handing it over, so anything this returns
        is ours to deliver and nobody else's. Hand one back with
        ``nack_notification`` if the send fails, or it is lost.
        """
        return await self._request(
            "POST", f"/notifications/claim?limit={int(limit)}", json={}
        )

    async def nack_notification(self, job_id: str) -> dict[str, Any]:
        """POST /notifications/{id}/nack — return an undelivered notification."""
        return await self._request("POST", f"/notifications/{job_id}/nack", json={})

    async def enqueue_generation(
        self, session_id: str, prompt: str, persona_key: str
    ) -> dict[str, Any]:
        """POST /sessions/{id}/image/generate — queue a real generation.

        Returns a job id immediately; the image arrives later via the
        notification poller. 404s unless the coordinator has both
        IMAGE_GEN_ENABLED and IMAGE_GEN_DEV_ENDPOINT on.
        """
        return await self._request(
            "POST",
            f"/sessions/{session_id}/image/generate",
            json={"prompt": prompt, "persona_key": persona_key},
        )

    async def get_toolkit(self, persona_key: str) -> dict[str, Any]:
        """Fetch the registry-driven toolkit summary for a persona (ADR-009 W3)."""
        return await self._request("GET", f"/personas/{persona_key}/toolkit")

    # ── ADR-011 conversation-control verbs (thin, no logic) ──────────────────

    async def regenerate(self, session_id: str) -> dict[str, Any]:
        """Reroll the last assistant reply (/regen)."""
        return await self._request("POST", f"/sessions/{session_id}/regenerate", json={})

    async def continue_reply(self, session_id: str) -> dict[str, Any]:
        """Extend the last assistant reply (/continue)."""
        return await self._request("POST", f"/sessions/{session_id}/continue", json={})

    async def undo(self, session_id: str) -> dict[str, Any]:
        """Delete the last exchange (/undo)."""
        return await self._request("POST", f"/sessions/{session_id}/undo", json={})

    async def narrate(self, session_id: str, text: str) -> dict[str, Any]:
        """Inject a narrator/scene beat and return the persona's reaction (/sys)."""
        return await self._request("POST", f"/sessions/{session_id}/narrate", json={"text": text})

    async def impersonate(self, session_id: str, hint: str | None = None) -> dict[str, Any]:
        """Draft the user's next line (/impersonate). Returns {"draft": ...}."""
        return await self._request("POST", f"/sessions/{session_id}/impersonate", json={"hint": hint} if hint else {})

    async def get_session_meta(self, session_id: str) -> dict[str, Any]:
        """Lean session metadata for /whoami (persona identity + counts)."""
        return await self._request("GET", f"/sessions/{session_id}/meta")

    async def set_note(self, session_id: str, note: str) -> dict[str, Any]:
        """Set the per-session author's note (/note <text>)."""
        return await self._request("PUT", f"/sessions/{session_id}/note", json={"note": note})

    async def get_note(self, session_id: str) -> dict[str, Any]:
        """Get the per-session author's note (/note)."""
        return await self._request("GET", f"/sessions/{session_id}/note")

    async def clear_note(self, session_id: str) -> dict[str, Any]:
        """Clear the per-session author's note (/note clear)."""
        return await self._request("DELETE", f"/sessions/{session_id}/note")
