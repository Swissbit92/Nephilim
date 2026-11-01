# src/coordinator/services/web_fetch_service.py
"""fetch_url executor — ADR-009 Phase W (W2).

Fetches a page over HTTP and returns its main content as clean text/markdown.
Extraction chain: trafilatura (when installed) -> stdlib HTML strip fallback,
so the tool works without the optional dependency and improves with it.

Single-user, local: one consent/age-gate redirect is followed (httpx default),
no crawling, hard size + timeout caps.
"""

from __future__ import annotations

import html
import ipaddress
import logging
import re
import socket
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_MAX_BYTES = 2_000_000       # 2 MB page cap (enforced DURING streaming download)
_MAX_OUTPUT_CHARS = 20_000   # cap returned text (local model context budget)
_TIMEOUT_S = 15


def _is_private_host(hostname: str) -> bool:
    """True if `hostname` resolves only to loopback/private/link-local addresses.

    SSRF defense-in-depth (QA finding, 2026-07-05): fetch_url takes a
    model-provided URL; once a live agent loop can act on fetched content, a
    prompt-injected fetch of e.g. http://127.0.0.1:11434 (Ollama) must not be
    possible. All resolved addresses are checked; resolution failure is treated
    as blocked (fail closed — the fetch would fail anyway).
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return True  # unresolvable -> block (fetch would fail regardless)
    if not infos:
        return True
    return any(_is_private_ip(info[4][0]) for info in infos)


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (addr.is_loopback or addr.is_private or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


def _connected_ip(resp) -> Optional[str]:
    """The address this response actually came from, or None if not exposed."""
    stream = getattr(resp, "extensions", {}).get("network_stream")
    addr = stream.get_extra_info("server_addr") if stream else None
    return addr[0] if isinstance(addr, tuple) and addr else None

_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>",
                           re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")
_BLANKLINES = re.compile(r"\n\s*\n\s*\n+")


def _stdlib_extract(raw_html: str) -> str:
    """Dependency-free fallback: strip scripts/tags, unescape, collapse space."""
    text = _SCRIPT_STYLE.sub(" ", raw_html)
    text = re.sub(r"</(p|div|br|li|h[1-6]|tr)>", "\n", text, flags=re.IGNORECASE)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = _BLANKLINES.sub("\n\n", text)
    return text.strip()


def _trafilatura_extract(raw_html: str, url: str, as_markdown: bool) -> Optional[str]:
    try:
        import trafilatura  # optional dependency
    except ImportError:
        return None
    try:
        return trafilatura.extract(
            raw_html, url=url, output_format=("markdown" if as_markdown else "txt"),
            include_comments=False, include_tables=True,
        )
    except Exception as e:  # noqa: BLE001 - never fail the turn on extraction
        logger.warning(f"[fetch_url] trafilatura extract failed: {e}")
        return None


def fetch_url(url: str, mode: str = "markdown") -> str:
    """Fetch `url` and return cleaned content. Returns an error sentinel string
    (never raises) so a tool-calling loop can react without a crash."""
    if not url or not url.strip():
        return "ERROR: fetch_url requires a non-empty url."
    url = url.strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url

    hostname = urlparse(url).hostname or ""
    if not hostname or _is_private_host(hostname):
        logger.warning(f"[fetch_url] blocked private/loopback host: {hostname!r}")
        return f"ERROR: fetching internal/private addresses is not allowed ({hostname})."

    import httpx

    try:
        with httpx.Client(
            follow_redirects=True, timeout=_TIMEOUT_S,
            headers={"User-Agent": "Mozilla/5.0 (nephilim-coordinator)"},
        ) as client:
            # Streaming download with an early stop so _MAX_BYTES bounds actual
            # network/memory use, not just the returned string (QA finding).
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                # Redirect-SSRF guard: a public host may 30x to an internal
                # address. The request has already happened (read-only GET),
                # but we refuse to RETURN internal content to the model.
                final_host = (resp.url.host or "") if hasattr(resp, "url") else hostname
                if final_host and final_host != hostname and _is_private_host(final_host):
                    logger.warning(
                        f"[fetch_url] blocked redirect to private host: {final_host!r}"
                    )
                    return (
                        f"ERROR: {url} redirected to an internal/private address; "
                        "content withheld."
                    )
                # DNS rebinding: the name can resolve publicly for the check above
                # and privately for the connection. Refuse on what was connected
                # to, before reading any body.
                peer = _connected_ip(resp)
                if peer and _is_private_ip(peer):
                    logger.warning(f"[fetch_url] blocked connection to private peer: {peer}")
                    return f"ERROR: {url} resolved to an internal address; content withheld."
                chunks: list[bytes] = []
                received = 0
                for chunk in resp.iter_bytes():
                    chunks.append(chunk)
                    received += len(chunk)
                    if received >= _MAX_BYTES:
                        logger.info(f"[fetch_url] truncated at {_MAX_BYTES} bytes: {url}")
                        break
                encoding = resp.encoding or "utf-8"
            raw = b"".join(chunks)[:_MAX_BYTES].decode(encoding, "replace")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[fetch_url] fetch failed for {url}: {e}")
        return f"ERROR: could not fetch {url}: {e}"

    if mode == "raw":
        return raw[:_MAX_OUTPUT_CHARS]

    text = _trafilatura_extract(raw, url, as_markdown=(mode != "text"))
    if not text:
        text = _stdlib_extract(raw)
    if not text:
        return f"ERROR: fetched {url} but could not extract readable content."
    return text[:_MAX_OUTPUT_CHARS]
