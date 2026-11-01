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

import httpcore
import httpx

logger = logging.getLogger(__name__)

_MAX_BYTES = 2_000_000       # 2 MB page cap (enforced DURING streaming download)
_MAX_OUTPUT_CHARS = 20_000   # cap returned text (local model context budget)
_TIMEOUT_S = 15
#: Carrier-grade NAT, which Tailscale uses. Python does not class it as private.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


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
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified
            or addr in _CGNAT)


class BlockedAddress(httpcore.ConnectError):
    """A connection to an internal address was refused before it was opened."""


class _PublicOnlyBackend(httpcore.SyncBackend):
    """Resolve, validate every address, then connect to the validated IP.

    Every connection the client opens comes through here, including each redirect
    hop, so one check covers them all. Connecting to the IP that was validated, not
    the name, leaves no second lookup for a rebinding DNS server to answer differently.
    TLS still verifies against the hostname, which httpcore passes separately.
    """

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ips = [info[4][0] for info in infos]
        if not ips or any(_is_private_ip(ip) for ip in ips):
            raise BlockedAddress(f"refused internal address for {host}")
        error = None
        for ip in dict.fromkeys(ips):  # a site's IPv6 address may be unreachable from here
            try:
                return super().connect_tcp(
                    ip, port, timeout=timeout, local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                error = e
        raise error


class _PublicOnlyTransport(httpx.HTTPTransport):
    """httpx has no backend option, so this swaps in a pool that has one. A test
    against a real local server fails if an httpx upgrade stops honouring it."""

    def __init__(self):
        super().__init__()
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(), network_backend=_PublicOnlyBackend(),
        )


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

    try:
        with httpx.Client(
            transport=_PublicOnlyTransport(), follow_redirects=True, timeout=_TIMEOUT_S,
            headers={"User-Agent": "Mozilla/5.0 (nephilim-coordinator)"},
        ) as client:
            # Streaming download with an early stop so _MAX_BYTES bounds actual
            # network/memory use, not just the returned string (QA finding).
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
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
