from __future__ import annotations

import logging

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class BraveSettings(BaseSettings):
    """Brave Search MCP configuration."""

    api_key: str = Field(
        default="",
        description="Brave Search API key",
        alias="BRAVE_API_KEY"
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum search results to return",
        alias="BRAVE_MAX_RESULTS"
    )
    safesearch: str = Field(
        default="moderate",
        description="Safe search level: off|moderate|strict",
        alias="BRAVE_SAFESEARCH"
    )
    country: str = Field(
        default="",
        description=(
            "2-letter country code (e.g. 'CH') passed to Brave for locale-aware "
            "ranking. Empty (default) = not passed, Brave decides — which "
            "US-biases results (2026-07-05 incident: a Swiss weather query "
            "returned a US-oriented aggregator in °F). Set BRAVE_COUNTRY=CH."
        ),
        alias="BRAVE_COUNTRY"
    )
    search_lang: str = Field(
        default="",
        description=(
            "Language code (e.g. 'en', 'de') passed to Brave. Empty (default) = "
            "not passed."
        ),
        alias="BRAVE_SEARCH_LANG"
    )
    timeout: int = Field(
        default=20,
        ge=1,
        le=60,
        description=(
            "Search timeout in seconds. Covers the ephemeral `docker run` container "
            "cold-start + Brave API call. 10s was too tight on a cold image pull "
            "(silently returned no results); 20s gives margin once the image is cached."
        ),
        alias="BRAVE_SEARCH_TIMEOUT"
    )

    @property
    def enabled(self) -> bool:
        """Check if Brave search is enabled (API key is set)."""
        return bool(self.api_key.strip())

    @field_validator('country')
    @classmethod
    def validate_country(cls, v: str) -> str:
        """Normalize/validate the country code; drop invalid values."""
        v = v.strip().upper()
        if v and (len(v) != 2 or not v.isalpha()):
            logger.warning(f"Invalid BRAVE_COUNTRY '{v}' (need 2-letter code), ignoring")
            return ""
        return v

    @field_validator('safesearch')
    @classmethod
    def validate_safesearch(cls, v: str) -> str:
        """Validate safesearch value."""
        valid_values = {"off", "moderate", "strict"}
        if v.lower() not in valid_values:
            logger.warning(f"Invalid BRAVE_SAFESEARCH '{v}', defaulting to 'moderate'")
            return "moderate"
        return v.lower()

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }

class WebSearchSettings(BaseSettings):
    """Generic web-search backend configuration (ADR-009 Phase W).

    Selects the search backend and the global safesearch default. SearXNG
    (self-hosted Docker metasearch) is preferred when configured — the query
    never leaves the machine and its safesearch pass-through is the most
    permissive option for an uncensored companion — with the Brave API as
    fallback. When ``searxng_base_url`` is empty (default) the chain degrades
    to Brave-only, i.e. byte-identical to the pre-ADR-009 behavior.
    """

    backend: str = Field(
        default="auto",
        description=(
            "Search backend selection: 'auto' (SearXNG if searxng_base_url is "
            "set, else Brave), 'searxng' (SearXNG only), 'brave' (Brave only), or "
            "'routed' (text search Brave first with SearXNG behind it; images and "
            "videos SearXNG only). 'auto' with no SearXNG URL == legacy Brave-only "
            "behavior. On every value an nsfw persona never reaches Brave, and "
            "neither does an image or video search."
        ),
        alias="WEB_SEARCH_BACKEND",
    )
    searxng_base_url: str = Field(
        default="",
        description=(
            "Base URL of a self-hosted SearXNG instance with JSON format enabled "
            "(e.g. http://127.0.0.1:8888). Empty (default) = SearXNG disabled, "
            "chain falls back to Brave. Set SEARXNG_BASE_URL to enable."
        ),
        alias="SEARXNG_BASE_URL",
    )
    research_trace_path: str = Field(
        default="",
        description=(
            "JSONL file for per-turn research telemetry (backend, tools, pages, "
            "latency; never query text). Empty (default) = off."
        ),
        alias="RESEARCH_TRACE_PATH",
    )
    searxng_timeout: int = Field(
        default=10, ge=1, le=60,
        description="Per-request timeout (s) for the SearXNG HTTP call.",
        alias="SEARXNG_TIMEOUT",
    )
    safesearch_default: str = Field(
        default="off",
        description=(
            "Global default safesearch level (off|moderate|strict). Default 'off' "
            "for an uncensored companion (the pre-ADR-009 hardwired 'moderate' "
            "filtered adult content). Per-persona nsfw flag clamps this UP for "
            "non-nsfw personas; the model may also tighten per-call. Maps to "
            "SearXNG 0/1/2 and Brave off/moderate/strict."
        ),
        alias="WEB_SAFESEARCH_DEFAULT",
    )

    @field_validator("safesearch_default")
    @classmethod
    def _validate_safesearch(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"off", "moderate", "strict"}:
            logger.warning(f"Invalid WEB_SAFESEARCH_DEFAULT '{v}', using 'off'")
            return "off"
        return v

    @field_validator("backend")
    @classmethod
    def _validate_backend(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"auto", "searxng", "brave", "routed"}:
            logger.warning(f"Invalid WEB_SEARCH_BACKEND '{v}', using 'auto'")
            return "auto"
        return v

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }


class SearchSettings(BaseSettings):
    """Web-search grounding configuration (follow-up query resolution + gate).

    Two independent, default-OFF guards for the Brave force-search path. Both
    off (default) = byte-identical to the legacy behavior, so revert is a single
    env flip and no eval baseline shifts.

    ``query_resolution_enabled`` fixes the "search the web for it" bug: a deictic
    follow-up turn was sent to Brave verbatim (topic lost), returning junk
    meta-results. When on, the latest turn is resolved against prior conversation
    before the search — with a hard fallback to the raw latest turn on any
    failure, so it can never be worse than the legacy path.

    ``relevance_gate_enabled`` is defense-in-depth: even a resolved query can
    return off-topic results, and non-empty junk currently bypasses the
    "no results -> I don't know" guard. When on, results whose max bge-m3 cosine
    to the query falls below ``relevance_min_cosine`` are treated as no-result
    (honest abstention) instead of fed to synthesis.
    """

    query_resolution_enabled: bool = Field(
        default=False,
        description=(
            "Resolve deictic/follow-up search turns against prior conversation "
            "before hitting Brave. False (default) = legacy verbatim behavior. "
            "Set SEARCH_QUERY_RESOLUTION_ENABLED=true to enable."
        ),
        alias="SEARCH_QUERY_RESOLUTION_ENABLED",
    )
    relevance_gate_enabled: bool = Field(
        default=False,
        description=(
            "Abstain when search results are off-topic (max bge-m3 cosine to the "
            "query < relevance_min_cosine) instead of synthesizing over junk. "
            "False (default) = legacy behavior. Independent of query resolution."
        ),
        alias="SEARCH_RELEVANCE_GATE_ENABLED",
    )
    relevance_min_cosine: float = Field(
        default=0.36,
        ge=0.0,
        le=1.0,
        description=(
            "Cosine floor (bge-m3, exact 1 - D/2) below which the best search "
            "result is treated as off-topic and the relevance gate abstains. "
            "Tuned 2026-07-04 (ADR-007, tests/evaluation/tune_relevance_threshold.py). "
            "First pass used relevance_gate_eval_set.json at n=8 (mostly hand-"
            "written descriptions) and landed on 0.28 — conservative, only "
            "caught 1 of 2 junk samples. Extended the same day to n=25 with 17 "
            "REAL Brave query/result pairs (14 relevant across sports/crypto/"
            "weather/knowledge/product domains, 3 real junk-mismatch pairs "
            "reproducing the actual incident failure mode with genuine data on "
            "both sides). With real data the separation is clean: every real "
            "relevant result scores >= 0.561, every junk sample (including all "
            "3 new real ones) scores <= 0.347. 0.36 catches 100% of junk "
            "(junk_catch_recall=1.0) with only a 5% false-abstention rate — and "
            "that single false-abstention is the original n=8 pass's own "
            "deliberately-adversarial SYNTHETIC sample (a hand-written "
            "lexically-distant-but-relevant description, cosine 0.358), not a "
            "real result. 0.40 (the original untuned placeholder) was "
            "conservative-by-guess, not conservative-by-data."
        ),
        alias="SEARCH_RELEVANCE_MIN_COSINE",
    )
    synthesis_trust_results: bool = Field(
        default=False,
        description=(
            "Add a synthesis-prompt directive telling the model the search "
            "results below are fresh/just-retrieved/verified and must be used to "
            "answer, overriding any earlier in-conversation self-doubt/refusal "
            "about searching (context-poisoning fix). Scoped: does NOT override "
            "honest abstention when results are genuinely empty. False (default) "
            "= byte-identical synthesis prompt. Set "
            "SEARCH_SYNTHESIS_TRUST_RESULTS=true to enable."
        ),
        alias="SEARCH_SYNTHESIS_TRUST_RESULTS",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }
