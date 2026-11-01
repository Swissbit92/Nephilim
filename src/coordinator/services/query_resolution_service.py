# src/coordinator/services/query_resolution_service.py
"""
Query Resolution Service - Resolve deictic follow-up turns into standalone
web-search queries before hitting Brave.

Fixes the "search the web for it" bug: a follow-up turn like "search the web
for it" / "look it up" / "and Geneva?" was sent to Brave verbatim, losing the
topic established in prior turns. Brave then returned junk meta-results (e.g.
"how to search the web" help pages) which, being non-empty, bypassed the
"no results -> I don't know" guard and let the LLM confabulate.

Design (flag-gated `SEARCH_QUERY_RESOLUTION_ENABLED`, default OFF):
  - flag OFF  -> byte-identical to legacy: return the raw latest user message.
  - flag ON   -> if the latest turn looks self-contained, pass it through; if it
                 looks like a follow-up (deictic / very short / bare search
                 command) AND prior turns exist, ask the LLM to rewrite it into a
                 standalone query, then sanitize. ANY failure (empty, over-long,
                 exception) falls back to the raw latest message, so resolution
                 can never be worse than the legacy path.

The rewrite reuses the loaded persona LLM (no new model); it only fires on the
cheap heuristic trigger, so self-contained queries pay no extra round-trip.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Optional, Sequence

from .llm_completion_service import LLMCompletionService
from .query_extraction_service import QueryExtractionService
from ..tools.keywords import EXPLICIT_SEARCH_COMMANDS

logger = logging.getLogger(__name__)

# Whole-word deictic / referential tokens whose presence means the turn likely
# depends on prior context ("search for it", "what about them", "is that true").
_DEICTIC_TOKENS = frozenset(
    {
        "it", "its", "it's", "this", "that", "these", "those",
        "them", "they", "their", "there", "he", "she", "him",
        "her", "his", "one", "ones",
    }
)

# A turn at or below this many words is treated as a follow-up candidate even
# without an explicit pronoun (e.g. "and Geneva?"). Biased to over-trigger: a
# false positive only costs one cheap rewrite that falls back to the original.
_SHORT_TURN_MAX_WORDS = 5

# Explicit search commands that carry no topic on their own may appear in a
# slightly longer turn ("could you search the web for it please"); allow the
# trigger up to this many words when such a command is present.
_COMMAND_TURN_MAX_WORDS = 9

# Sanitization guards on the rewritten query.
_MAX_QUERY_WORDS = 25
_MAX_QUERY_CHARS = 200

# Leading labels a small model tends to prepend despite instructions.
_LABEL_PREFIX_RE = re.compile(
    r"^\s*(rewritten|standalone(?:\s+question)?|query|search\s+query|answer)\s*[:\-]\s*",
    re.IGNORECASE,
)

_WORD_RE = re.compile(r"[a-z0-9']+")

# Content-free tokens that, together with an EXPLICIT_SEARCH_COMMAND phrase, make
# a turn a "bare" search command (no topic of its own): "search the web (for it)",
# "look it up online". If only these remain after stripping the command phrases,
# there is nothing for the model to search but the command itself.
#
# 2026-07-04 incident (session dcc3693d): includes context-dependent-but-non-
# pronoun placeholder phrases ("next match", "last match", "the game") — these
# don't specify WHICH match/game without prior context, so on their own they
# carry no more topic than a bare "search the web" does. Without this, "search
# the web for the next match" was NOT classified as bare (the words "next"/
# "match" survived as "non-filler"), so it skipped the prior-substantive-turn
# fallback and a near-verbatim LLM echo of the same phrase reached Brave
# unchanged, reproducing the incident's "how to search a webpage" junk result.
_COMMAND_FILLER_TOKENS = frozenset(
    {
        "the", "a", "an", "web", "internet", "online", "for", "it", "this",
        "that", "these", "those", "them", "please", "can", "could", "would",
        "you", "to", "and", "up", "me", "us", "now", "about", "on", "of",
        "real", "some", "more", "info", "information", "quick", "just",
        "next", "last", "match", "game", "fixture",
        # 2026-07-05 incident (Telegram): "search the web to answer my question"
        # left `answer`/`my`/`question` as "non-filler", so the turn was NOT
        # classified bare — it skipped the prior-substantive-turn fallback and
        # went to an LLM rewrite of a topic-free phrase, which free-associated
        # an unrelated wallet query. These words reference the act of asking,
        # not a topic.
        "answer", "my", "question", "questions", "query", "request", "asked",
        "i",
    }
)

# A leading correction preamble ("no, I meant...") carries no topic content of
# its own but inflates the word count used by _looks_like_followup's trigger
# heuristic. 2026-07-04 incident: "no, I meant search the web for the next
# match" is 10 words — one over _COMMAND_TURN_MAX_WORDS — so it never
# triggered resolution at all and the raw turn (correction prefix and all)
# passed straight through to Brave untouched. Stripped ONLY for the trigger
# word-count check below, not from what's sent to the LLM rewrite step.
_CORRECTION_PREFIX_RE = re.compile(
    r"^\s*(no,?\s+)?(i\s+meant|sorry,?\s+i\s+meant|actually,?\s+i\s+meant|wait,?\s+i\s+meant)\s*[,:]?\s*",
    re.IGNORECASE,
)


#: Words a query may gain without the operator having typed them: search vocabulary
#: and public aliases. Keep this generic. A word here can leave the machine on any turn.
_PUBLIC_QUERY_WORDS = frozenset(
    "a an and at by for from in is of on or the to vs versus with about "
    "latest current recent new newest today now update updates news release releases "
    "version versions price prices cost rate rates review reviews comparison compare "
    "difference differences forecast weather schedule result results score scores "
    "benchmark benchmarks specs performance status standings information overview guide "
    "january february march april may june july august september october november december "
    "btc bitcoin eth ethereum sol solana usd eur chf".split()
)
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def _query_words(text: str) -> set:
    normal = unicodedata.normalize("NFKD", (text or "").casefold())
    return set(_WORD_RE.findall("".join(c for c in normal if not unicodedata.combining(c))))


def query_origin_ok(query: str, user_turns: Sequence[str]) -> bool:
    """Every word comes from the operator's own turns or the public list.

    For a query written with private context in view, which is the tool loop's. The
    rewriter does not use this: it is only ever given user turns, so it holds nothing
    private to leak, and on 24 real follow-ups this check rejected 13 good rewrites
    over general words such as "security". It limits where words come from, not what
    the operator typed: a private detail in their own message leaves with that message.
    """
    allowed = _query_words(" ".join(user_turns)) | _PUBLIC_QUERY_WORDS
    return all(w in allowed or _YEAR_RE.match(w) for w in _query_words(query))


class QueryResolutionService:
    """Resolve a (possibly deictic) latest user turn into a standalone search query.

    Stateless apart from the injected LLM/extractor dependencies.
    """

    def __init__(
        self,
        llm_service: LLMCompletionService,
        query_extractor: Optional[QueryExtractionService] = None,
    ):
        self.llm_service = llm_service
        self.query_extractor = query_extractor or QueryExtractionService()

    # ------------------------------------------------------------------ public

    def resolve(self, user_prompt: str) -> str:
        """Return the query to send to Brave for this compiled conversation.

        Never raises: on any failure it degrades to the raw latest user message
        (legacy behavior).
        """
        latest = self.query_extractor.extract_latest_user_message(user_prompt)

        # Flag read at call-time so an env flip / cache_clear takes effect without
        # re-instantiating the service. Lazy import avoids a config import cycle.
        from ..config import get_settings

        if not get_settings().search.query_resolution_enabled:
            return latest

        try:
            if not self._has_prior_user_turn(user_prompt, latest):
                # First turn / no context to resolve against.
                return latest
            if not self._looks_like_followup(latest):
                return latest

            # A "bare" search command ("search the web", "look it up") carries no
            # topic of its own, so the LLM must infer it with no explicit referent
            # — the least reliable rewrite. Change the fallback from the useless
            # bare command to the most recent substantive prior user turn, which
            # already carries the topic. Worst case then becomes the prior real
            # question hitting Brave (real results) instead of "search the web"
            # (junk meta-pages).
            fallback = latest
            if self._is_bare_search_command(latest):
                prior = self._prior_substantive_user_turn(user_prompt, latest)
                if prior:
                    fallback = prior

            rewritten = self._llm_rewrite(user_prompt, latest)
            resolved = self._sanitize(rewritten, fallback=fallback)
            # Guard: a rewrite that is ITSELF still a bare command (the model
            # echoed "search the web") must not reach Brave — use the fallback.
            if self._is_bare_search_command(resolved):
                resolved = fallback
            if resolved != latest:
                logger.info(
                    f"[QueryResolution] Resolved follow-up '{latest[:60]}' -> "
                    f"'{resolved[:60]}'"
                )
            return resolved
        except Exception as e:  # noqa: BLE001 - resolution must never break search
            logger.warning(
                f"[QueryResolution] Resolution failed ({e}); using latest turn verbatim"
            )
            return latest

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _has_prior_user_turn(user_prompt: str, latest: str) -> bool:
        """True if the compiled conversation contains a user turn before `latest`."""
        user_turns = [
            line.strip()[6:].strip()
            for line in user_prompt.split("\n")
            if line.strip().startswith("User: ")
        ]
        # More than one user turn, or a single user turn that isn't the latest,
        # means there's prior context worth resolving against.
        return len(user_turns) >= 2 or (
            len(user_turns) == 1 and user_turns[0] != latest
        )

    @staticmethod
    def _looks_like_followup(latest: str) -> bool:
        """Cheap heuristic: does this turn likely depend on prior context?"""
        # Strip a leading correction preamble ("no, I meant...") before counting
        # words — it carries no topic content and would otherwise inflate the
        # word count past the short/command-turn thresholds below (see
        # _CORRECTION_PREFIX_RE docstring for the incident this fixes).
        stripped = _CORRECTION_PREFIX_RE.sub("", latest).strip()
        text = (stripped or latest).lower()
        words = _WORD_RE.findall(text)
        n = len(words)
        if n == 0:
            return False

        if n <= _SHORT_TURN_MAX_WORDS:
            return True

        word_set = set(words)
        if word_set & _DEICTIC_TOKENS:
            return True

        if n <= _COMMAND_TURN_MAX_WORDS and any(
            cmd in text for cmd in EXPLICIT_SEARCH_COMMANDS
        ):
            return True

        return False

    @staticmethod
    def _is_bare_search_command(text: str) -> bool:
        """True if `text` is only a search command + filler (no topic).

        e.g. "search the web", "search the web for it", "look it up online",
        "google it" — after removing the command phrase(s) and content-free
        filler, nothing substantive remains.
        """
        if not text or not text.strip():
            return False
        low = text.lower()
        # Remove every explicit-command phrase (longest first, so multiword
        # phrases are consumed before their sub-phrases).
        for cmd in sorted(EXPLICIT_SEARCH_COMMANDS, key=len, reverse=True):
            low = low.replace(cmd, " ")
        remaining = [w for w in _WORD_RE.findall(low) if w not in _COMMAND_FILLER_TOKENS]
        # If no command phrase was present at all, it's not a *command* — only
        # treat as bare when at least one command phrase was actually stripped.
        had_command = any(cmd in text.lower() for cmd in EXPLICIT_SEARCH_COMMANDS)
        return had_command and not remaining

    @classmethod
    def _prior_substantive_user_turn(cls, user_prompt: str, latest: str) -> Optional[str]:
        """Most recent prior user turn that carries a topic (not a bare command)."""
        user_turns = [
            line.strip()[6:].strip()
            for line in user_prompt.split("\n")
            if line.strip().startswith("User: ")
        ]
        # Drop the trailing latest turn, then walk backward for the first turn
        # that has real content and isn't itself a bare search command.
        if user_turns and user_turns[-1] == latest:
            user_turns = user_turns[:-1]
        for turn in reversed(user_turns):
            if turn and not cls._is_bare_search_command(turn):
                if _WORD_RE.findall(turn.lower()):
                    return turn
        return None

    def _llm_rewrite(self, user_prompt: str, latest: str) -> str:
        """Ask the LLM to rewrite `latest` into a standalone query using context."""
        context = self._recent_context(user_prompt, latest)
        system = (
            "You rewrite a user's latest message into a short, self-contained "
            "(standalone) web search query. Use the conversation ONLY to resolve "
            'references like "it", "that", "there", or "them" to the topic they '
            "point at.\n"
            "Rules:\n"
            "- Output ONLY the rewritten search query. No preamble, no quotes, no "
            "explanation.\n"
            "- Keep it short: 3 to 12 words.\n"
            "- Do NOT answer the question. Do NOT invent facts not present in the "
            "conversation.\n"
            "- If the latest message is already a complete standalone query, "
            "return it unchanged.\n\n"
            "Example:\n"
            "Conversation:\n"
            "User: tell me about the James Webb telescope's new exoplanet finding\n"
            "Latest: search the web for it\n"
            "Rewritten: James Webb telescope new exoplanet finding"
        )
        user = (
            f"Conversation:\n{context}\n"
            f"Latest: {latest}\n"
            "Rewritten:"
        )
        return self.llm_service.complete(system, user)

    @staticmethod
    def _recent_context(user_prompt: str, latest: str, max_turns: int = 6) -> str:
        """Trailing conversation turns (excluding the latest), for rewrite context."""
        turn_lines = [
            line.strip()
            for line in user_prompt.split("\n")
            # The privacy boundary for rewrites. This model call sees no persona,
            # memory, graph fact or assistant text, so it cannot put one in a query.
            if line.strip().startswith("User: ")
        ]
        # Drop the final "User: <latest>" line so it isn't duplicated below.
        if turn_lines and turn_lines[-1] == f"User: {latest}":
            turn_lines = turn_lines[:-1]
        return "\n".join(turn_lines[-max_turns:])

    @staticmethod
    def needs_clarifying(query: str) -> bool:
        """True when nothing searchable is left: a bare command or only pronouns."""
        words = {w for w in _WORD_RE.findall((query or "").lower())}
        content = words - _DEICTIC_TOKENS - _PUBLIC_QUERY_WORDS - {"what", "how", "why", "who"}
        return not content or QueryResolutionService._is_bare_search_command(query)

    @staticmethod
    def _sanitize(raw: str, *, fallback: str) -> str:
        """Clean an LLM rewrite; fall back to the original on anything suspect."""
        if not raw or not raw.strip():
            return fallback

        candidate = raw.strip()

        # Small models sometimes wrap the query in JSON: {"query": "..."}.
        if candidate.startswith("{"):
            try:
                data = json.loads(candidate)
                if isinstance(data, dict) and data.get("query"):
                    candidate = str(data["query"])
            except (json.JSONDecodeError, ValueError):
                pass

        # First non-empty line only.
        candidate = next(
            (ln.strip() for ln in candidate.splitlines() if ln.strip()), ""
        )
        # Strip a leading label ("Rewritten:", "Standalone question:", ...).
        candidate = _LABEL_PREFIX_RE.sub("", candidate).strip()
        # Strip surrounding quotes / backticks.
        candidate = candidate.strip("\"'`").strip()

        if not candidate:
            return fallback
        if len(candidate) > _MAX_QUERY_CHARS:
            return fallback
        if len(_WORD_RE.findall(candidate.lower())) > _MAX_QUERY_WORDS:
            return fallback

        return candidate
