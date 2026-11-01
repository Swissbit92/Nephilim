# src/coordinator/services/research_service.py
"""Deterministic evidence retrieval, shared by every persona with web research.

Gemma decides how to say something, never whether evidence gets retrieved: all
three live Eeva traces on 2026-11-01 reached search by the legacy path, because
the model either made no tool call or called a tool with no executor.

Permissions stay outside this module. `search_fn` is the persona's own executor,
which already carries its safesearch floor and provider routing.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any

from .. import research_trace

logger = logging.getLogger(__name__)

MAX_PAGES = 3
FETCH_DEADLINE_S = 9.0
MIN_PASSAGE_CHARS = 60
MAX_PASSAGES_PER_SOURCE = 8
THIN_MIN_SOURCES = 2
THIN_MIN_CHARS = 300
MAX_FRESH_CHARS = 200
MIN_CITED_TERMS = 4
MAX_FRESH_WORDS = 25

_NEEDS_PAGES = re.compile(
    r"\b(today|tonight|tomorrow|yesterday|now|current|currently|latest|recent|recently"
    r"|this (week|month|year|season|weekend)|news|headlines?|price|rates?|forecast"
    r"|compare|comparison|vs\.?|versus|why|how|difference|differences|best|cheaper"
    r"|changed?|tradeoffs?|should)\b",
    re.IGNORECASE,
)
_FRESH = re.compile(
    r"\b(latest|newest|most recent|recently|this (week|month|year)|right now|current"
    r"|look (it |that )?up|search (for|the web))\b",
    re.IGNORECASE,
)
_PERSONAL = re.compile(
    r"\b(you|your|yours|we|us|our|i|my|me|mine|it|that|he|she|him|her|his|hers|they|them|their)\b",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    "a an and are as at be but by can did do does for from has have how i in is it its"
    " me my of on or our so that the their them then there these this to was we were"
    " what when where which who why will with would you your".split()
)
_WORD = re.compile(r"[^\W_]{2,}", re.UNICODE)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_EVIDENCE_TAG = re.compile(r"<\s*/?\s*evidence\b[^>\n]*>?", re.IGNORECASE)
# Whole lines only: a page that merely discusses prompt injection keeps its prose,
# and one that addresses the model loses the line.
_INSTRUCTION_LINE = re.compile(
    r"^\W*(ignore|disregard|forget|override)\b.{0,50}\b(previous|prior|above|earlier|all|system)\b"
    r"|^\W*(system|assistant|developer)\s*(prompt|message|note|instruction)?\s*:"
    r"|^\W*(you are now|you must now|new instructions?\b|from now on\b)"
    r"|\b(ai|llm|language model|assistant|chatbot)s?\b.{0,40}\b(must|should|shall|are required to)\b"
    r"|^\W*(remember|store|save|note) (that|this)\b.{0,40}\b(user|operator)\b"
    r"|^\W*the (user|operator)\b.{0,30}\b(is|has|lives|likes|wants|owns|asked)\b",
    re.IGNORECASE,
)


@dataclass
class Source:
    title: str
    url: str
    age: str | None = None
    passages: list[str] = field(default_factory=list)
    fetched: bool = False


@dataclass
class EvidencePackage:
    query: str
    sources: list[Source]
    results: list[Any]
    searches: int = 1

    @property
    def chars(self) -> int:
        return sum(len(p) for s in self.sources for p in s.passages)

    @property
    def usable(self) -> bool:
        return any(s.passages for s in self.sources)

    def render(self) -> str:
        """Evidence as data, inside a fence the sanitizer keeps pages out of."""
        blocks = []
        for i, s in enumerate((s for s in self.sources if s.passages), 1):
            dated = f" ({s.age})" if s.age else ""
            body = "\n".join(s.passages)
            blocks.append(f"[{i}] {s.title}{dated}\n{s.url}\n{body}")
        return "<evidence>\n" + "\n\n".join(blocks) + "\n</evidence>"


def asks_for_fresh_facts(message: str) -> bool:
    """An explicit freshness word in a factual question, about the world.

    A third proposer, for what both others miss: on 2026-11-01 "what is the latest
    macOS release?" and "what did Solana do this month?" were never searched, because
    the router scored them low and the model answered from its weights. A word about
    the persona or the operator is left to the conversation, and a pronoun may point
    back into it. The caller sends this exact message, never a rewrite from history,
    so what passes this check is what leaves the machine.
    """
    text = (message or "").strip()
    if "\n" in text or len(text) > MAX_FRESH_CHARS or len(text.split()) > MAX_FRESH_WORDS:
        return False  # a pasted block would leave whole; only a short question may
    return bool(_FRESH.search(text)) and not _PERSONAL.search(text)


def needs_pages(query: str) -> bool:
    """Snippets answer a stable fact. Anything current or comparative needs pages."""
    return bool(_NEEDS_PAGES.search(query or ""))


def sanitize(text: str) -> str:
    """Remove what a page could use to address the model rather than the reader."""
    text = _HTML_COMMENT.sub(" ", text or "")
    text = "".join(
        c for c in text if c in "\n\t" or unicodedata.category(c) not in ("Cf", "Cc", "Co")
    )
    text = _EVIDENCE_TAG.sub(" ", text)
    return "\n".join(
        line for line in text.splitlines() if not _INSTRUCTION_LINE.search(line.strip())
    )


def _inline(text: str | None) -> str:
    """One line with no angle brackets: titles, URLs and dates also sit in the fence."""
    return re.sub(r"[<>\s]+", " ", sanitize(text or "")).strip()


def _terms(text: str) -> set:
    return {w.lower() for w in _WORD.findall(text or "")} - _STOPWORDS


def select_passages(query: str, text: str, limit: int) -> list[str]:
    """Paragraphs that share the most terms with the question, in page order."""
    wanted = _terms(query)
    scored = []
    for position, para in enumerate(re.split(r"\n\s*\n|\n", text or "")):
        para = para.strip()
        if len(para) < MIN_PASSAGE_CHARS:
            continue
        overlap = len(wanted & _terms(para))
        if overlap:
            scored.append((overlap, position, para))
    best = sorted(scored, key=lambda s: (-s[0], s[1]))[:limit]
    return [para for _, _, para in sorted(best, key=lambda s: s[1])]


def _numbers(text: str) -> set:
    return {
        re.sub(r"[,'’]", "", n).rstrip(".")
        for n in re.findall(r"\d[\d,.'’]*\d|\d", text or "")
    }


def unsupported_figures(answer: str, evidence: str, question: str = "") -> list[str]:
    """Numbers in the answer that appear nowhere in the evidence or question.

    Matched as whole numbers, so "12" is not excused by "2012". A temperature is
    exempt: the synthesis prompt tells her to convert units. This is one narrow,
    checkable kind of unsupported claim; entailment is scored in the eval.
    """
    allowed = _numbers(evidence) | _numbers(question)
    converted = _numbers(" ".join(re.findall(r"-?\d[\d.]*\s*°", answer or "")))
    return sorted(_numbers(answer) - allowed - converted)


def drop_unsupported(answer: str, figures: Sequence[str]) -> str:
    """Remove the sentences stating those figures, leaving the rest of the reply."""
    bad = set(figures)
    sentences = re.split(r"(?<=[.!?])\s+(?=\S)", answer)
    return " ".join(s for s in sentences if not (_numbers(s) & bad)).strip()


def sources_used(answer: str, sources: Sequence[Source]) -> set:
    """URLs of sources the answer shares real content with. A source that was
    searched but not used earns no citation."""
    wanted = _terms(answer)
    return {
        s.url for s in sources
        if len(wanted & _terms(" ".join(s.passages))) >= MIN_CITED_TERMS
    }


def _second_query(query: str) -> str:
    """A rule-based rewrite: drop the question's grammar, keep its terms."""
    return " ".join(w for w in _WORD.findall(query) if w.lower() not in _STOPWORDS)


class ResearchService:
    def __init__(
        self,
        search_fn: Callable[[dict[str, Any]], list[Any] | None],
        fetch_fn: Callable[[str], str],
        *,
        budget_chars: int = 14000,
        always_fetch: bool = False,
    ):
        self._search = search_fn
        self._fetch = fetch_fn
        self._budget = budget_chars
        self._always_fetch = always_fetch

    def _pages(self, urls: Sequence[str]) -> dict[str, str]:
        """Fetch in parallel. A page past the deadline is left out, not waited for."""
        if not urls:
            return {}
        pool = ThreadPoolExecutor(max_workers=len(urls))
        futures = {pool.submit(self._fetch, u): u for u in urls}
        done, pending = wait(futures, timeout=FETCH_DEADLINE_S)
        pool.shutdown(wait=False, cancel_futures=True)
        pages: dict[str, str] = {}
        for future in done:
            content = future.exception() is None and future.result()
            ok = bool(content) and not content.startswith("ERROR")
            research_trace.note_fetch(ok)
            if ok:
                pages[futures[future]] = content
        for _ in pending:
            research_trace.note_fetch(False)
        return pages

    def _sources(self, query: str, results: list[Any], pages: dict[str, str]) -> list[Source]:
        sources = []
        per_source = max(1, self._budget // max(1, len(results)))
        for r in results:
            url = getattr(r, "url", "")
            snippet = sanitize(getattr(r, "description", "") or "").strip()
            passages = [snippet] if snippet else []
            page = pages.get(url)
            if page:
                picked, used = [], len(snippet)
                for para in select_passages(query, sanitize(page), MAX_PASSAGES_PER_SOURCE):
                    if used + len(para) > per_source:
                        break
                    picked.append(para)
                    used += len(para)
                passages += picked
            sources.append(
                Source(
                    title=_inline(getattr(r, "title", "")) or _inline(url),
                    url=_inline(url),
                    age=_inline(getattr(r, "age", None)) or None,
                    passages=passages,
                    fetched=bool(page),
                )
            )
        return sources

    def _gather(self, query: str) -> tuple:
        results = self._search({"query": query}) or []
        fetch = self._always_fetch or needs_pages(query)
        urls = [getattr(r, "url", "") for r in results[:MAX_PAGES]] if fetch else []
        return results, self._sources(query, results, self._pages([u for u in urls if u]))

    def run(self, query: str) -> EvidencePackage:
        results, sources = self._gather(query)
        package = EvidencePackage(query, sources, results)
        thin = (
            sum(1 for s in sources if s.passages) < THIN_MIN_SOURCES
            or package.chars < THIN_MIN_CHARS
        )
        rewrite = _second_query(query)
        if thin and rewrite and rewrite.lower() != query.lower():
            logger.info("[Research] thin evidence, searching once more")
            more_results, more = self._gather(rewrite)
            seen = {s.url for s in sources}
            package = EvidencePackage(
                query,
                sources + [s for s in more if s.url not in seen],
                results + [r for r in more_results if getattr(r, "url", "") not in seen],
                searches=2,
            )
        return package
