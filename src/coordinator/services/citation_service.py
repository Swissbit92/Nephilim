# src/coordinator/services/citation_service.py
"""
Citation Service - Generate and validate citations from search results.

Extracted from llm_client.py as part of Phase 2 Core Refactoring.
Prevents LLM hallucination of sources by auto-generating verified citations.
"""

from __future__ import annotations

import logging
from typing import List, Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class CitationService:
    """Service for managing citations from web search results.

    Handles:
    - Auto-generation of formatted citations from verified search results
    - Stripping hallucinated citations from LLM responses
    - Citation validation and formatting

    Anti-Hallucination Strategy:
    - LLM generates content only (no URLs)
    - System auto-generates citations with verified URLs
    - Removes any hallucinated citations from responses
    """

    @staticmethod
    def compact_citations(search_results: List[Any]) -> str:
        """One quiet line of links, for a reply that should stay in voice."""
        links = []
        for result in search_results[:3]:
            host = urlparse(getattr(result, "url", "") or "").netloc.removeprefix("www.")
            if host:
                links.append(f"[{host}]({result.url})")
        return f"\n\n🔍 {' · '.join(links)}" if links else ""

    @staticmethod
    def auto_generate_citations(search_results: List[Any]) -> str:
        """Auto-generate formatted citations from search results.

        This ensures 100% accurate URLs with no hallucination risk.
        The LLM is NOT responsible for formatting citations - the system
        generates them automatically from actual search results.

        Args:
            search_results: List of SearchResult objects from Brave

        Returns:
            Formatted citations string with 🔍 emoji and markdown links
        """
        if not search_results:
            return ""

        citations = "\n\n🔍 Sources:\n"

        # Use top 5 search results for citations
        for result in search_results[:5]:
            # Use actual title and URL from search result (cannot be hallucinated)
            title = result.title if result.title else "Untitled"
            url = result.url if result.url else "#"

            citations += f"• [{title}]({url})\n"

        logger.info(f"[Auto-Citations] Generated {min(len(search_results), 5)} citations with verified URLs")

        return citations

    @staticmethod
    def strip_hallucinated_citations(response: str) -> str:
        """Strip any hallucinated citations from LLM response.

        If the LLM hallucinates citations (when search wasn't used),
        we remove them entirely. This prevents showing fake sources to users.

        Args:
            response: LLM response that may contain hallucinated citations

        Returns:
            Response with citations removed (if they exist)
        """
        # Check if response contains citation markers
        citation_markers = ["🔍 Sources:", "Sources:", "**Sources:**"]

        for marker in citation_markers:
            if marker in response:
                # Remove everything from the marker onwards
                response = response.split(marker)[0].strip()
                logger.warning("[Anti-Hallucination] Stripped hallucinated citations from response")
                break

        return response

    @staticmethod
    def validate_citation_urls(text: str, expected_urls: List[str]) -> bool:
        """Validate that citations in text match expected URLs.

        Args:
            text: Text containing citations
            expected_urls: List of expected URLs

        Returns:
            True if all citations are valid, False otherwise
        """
        # Simple validation: check if expected URLs appear in text
        if not expected_urls:
            return True

        for url in expected_urls:
            if url not in text:
                logger.warning(f"[Citation Validation] Missing expected URL: {url}")
                return False

        return True


def validate_citations(answer: str, used_search: bool, search_results_count: int) -> tuple[str, bool, dict]:
    """Validate and process citations in answer based on search usage.

    Args:
        answer: The LLM-generated answer
        used_search: Whether web search was used
        search_results_count: Number of search results returned

    Returns:
        Tuple of (processed_answer, has_valid_citations, citation_details)
    """
    citation_markers = ["🔍 Sources:", "Sources:", "**Sources:**"]
    has_citations = any(marker in answer for marker in citation_markers)

    if used_search:
        # Search was used - citations should be present
        if has_citations:
            logger.info(f"[Citation Validation] Citations present (search used, {search_results_count} results)")
            return answer, True, {
                "has_citations": True,
                "search_results_count": search_results_count,
                "status": "valid"
            }
        else:
            logger.warning(f"[Citation Validation] Missing citations despite search ({search_results_count} results)")
            return answer, False, {
                "has_citations": False,
                "search_results_count": search_results_count,
                "status": "missing"
            }
    else:
        # Search was NOT used
        if has_citations:
            # Strip hallucinated citations
            logger.warning("[Citation Validation] Removing hallucinated citations (no search)")
            answer = CitationService.strip_hallucinated_citations(answer)
            return answer, False, {
                "has_citations": False,
                "search_results_count": 0,
                "status": "hallucinated_removed"
            }
        else:
            # No citations, as expected
            return answer, True, {
                "has_citations": False,
                "search_results_count": 0,
                "status": "valid_no_search"
            }
