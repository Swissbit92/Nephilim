# src/coordinator/searxng_client.py
"""SearXNG metasearch client — ADR-009 Phase W.

Queries a self-hosted SearXNG instance's JSON API. SearXNG fans one query out to
upstream engines from this machine's IP, so the query does leave the machine,
but with no account or API key attached. Its safesearch pass-through is the most
permissive option available for an uncensored companion. Returns the same ``SearchResult`` dataclass the Brave
client produces, so downstream synthesis/citation is backend-agnostic.

Requires the SearXNG instance to have JSON output enabled in settings.yml:

    search:
      formats: [html, json]

Stdlib-only (urllib) — no new dependency.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import List, Optional

from .models.mcp_models import SearchResult, MCPConnectionError, MCPTimeoutError

logger = logging.getLogger(__name__)

# safesearch string -> SearXNG integer (0=off, 1=moderate, 2=strict).
_SAFESEARCH_MAP = {"off": 0, "moderate": 1, "strict": 2}

# Valid SearXNG categories (subset we expose as tool params).
VALID_CATEGORIES = {
    "general", "images", "videos", "news", "science", "files", "music",
    "it", "social_media", "map",
}


def safesearch_to_int(level: Optional[str]) -> int:
    """Map an off|moderate|strict string to SearXNG's 0|1|2 (default 0=off)."""
    return _SAFESEARCH_MAP.get((level or "off").strip().lower(), 0)


class SearxngClient:
    """Thin JSON-API client for a self-hosted SearXNG instance."""

    def __init__(self, base_url: str, timeout: int = 10, max_results: int = 5):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_results = max_results

    def search(
        self,
        query: str,
        *,
        category: str = "general",
        safesearch: str = "off",
        time_range: Optional[str] = None,
        count: Optional[int] = None,
    ) -> List[SearchResult]:
        """Run a SearXNG search and return SearchResult objects.

        Raises MCPConnectionError / MCPTimeoutError on transport failure so the
        caller can fall back to another backend; returns [] on a valid-but-empty
        response.
        """
        if not query or not query.strip():
            raise ValueError("Search query cannot be empty")
        if not self.base_url:
            raise MCPConnectionError("SearXNG base_url not configured")

        cat = category if category in VALID_CATEGORIES else "general"
        params = {
            "q": query.strip(),
            "format": "json",
            "categories": cat,
            "safesearch": str(safesearch_to_int(safesearch)),
        }
        if time_range in {"day", "week", "month", "year"}:
            params["time_range"] = time_range

        url = f"{self.base_url}/search?{urllib.parse.urlencode(params)}"
        limit = count or self.max_results
        logger.info(
            f"[SearXNG] query='{query.strip()}' category={cat} "
            f"safesearch={params['safesearch']} time_range={time_range}"
        )

        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "nephilim-coordinator/1.0"}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError as e:  # pragma: no cover - network dependent
            raise MCPTimeoutError(f"SearXNG timed out after {self.timeout}s") from e
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            raise MCPConnectionError(f"SearXNG request failed: {e}") from e
        except (json.JSONDecodeError, ValueError) as e:
            raise MCPConnectionError(f"SearXNG returned invalid JSON: {e}") from e

        return self._parse(payload, limit)

    @staticmethod
    def _parse(payload: dict, limit: int) -> List[SearchResult]:
        raw = payload.get("results") or []
        results: List[SearchResult] = []
        for item in raw:
            url = item.get("url") or ""
            title = item.get("title") or "Untitled"
            if not url:
                continue
            # SearXNG uses 'content' for the snippet; 'publishedDate' for age.
            desc = item.get("content") or ""
            age = item.get("publishedDate") or None
            results.append(SearchResult(title=title, url=url, description=desc, age=age))
            if len(results) >= limit:
                break
        return results
