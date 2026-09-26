# src/coordinator/services/message_processing_service.py
"""Message processing utilities for multi-message response handling."""

from __future__ import annotations

import re
import logging
from typing import Dict, Optional

from ..config.chunking import chunking_settings

logger = logging.getLogger(__name__)


def legacy_force_split(response: str, query: str) -> str:
    """The pre-ADR-015 splitter. Reachable only when ``CHUNK_IN_CODE=false``.

    Kept rather than deleted because it is the revert path, and because its four
    strategies are the only thing 24 existing tests describe. Its two defects,
    both measured and both the reason ADR-015 exists:

      * Strategy 2's sentence rule is ``(?<=[.!?])\\s+(?=[A-Z])`` — the
        uppercase-only lookahead cannot fire on a lowercase-texting persona, so
        for gwen the strategy is dead code that looks alive.
      * The 500-char floor is above the whole body of her replies (live median
        ~300 chars), so on the primary path this function returns unchanged.

    BUGFIX (Dec 28, 2025): Reduced aggressiveness to prevent unwanted splits.
    Only splits responses that are VERY long (500+ chars) with clear conversational breaks.

    Args:
        response: LLM response string (without <msg> tags)
        query: Original user query (used for context)

    Returns:
        Response with <msg> tags applied
    """
    # Don't split if already has tags
    if '<msg>' in response:
        return response

    # Don't split short responses (< 500 chars) - keep them as single message
    if len(response.strip()) < 500:
        return response

    # BUGFIX: Remove paragraph splitting - too aggressive
    # Only split VERY long responses (800+ chars) with clear conversational structure
    response_clean = response.strip()

    # Strategy 1: Only split if response is VERY long (800+ chars) AND has question at end.
    # group(1) is GREEDY so it captures all body text up to the LAST sentence break
    # before the trailing question (main_content), leaving group(3) as just the final
    # question. A non-greedy (.*?) here minimised group(1) to the first sentence, which
    # both lumped the rest into the "question" message and made the 3-message split
    # (which needs main_content > 400 chars containing a '. ') unreachable.
    question_match = re.search(r'(.*)([.!]\s+)(.+\?)\s*$', response_clean, re.DOTALL)
    if question_match and len(response_clean) > 800:
        main_content = question_match.group(1) + question_match.group(2)
        question = question_match.group(3)

        # Split main content if it's very long
        if len(main_content) > 400:
            # Split main content in half
            mid_point = len(main_content) // 2
            # Find nearest sentence break
            split_point = main_content.rfind('. ', 0, mid_point + 50)
            if split_point > 0:
                first_part = main_content[:split_point + 1].strip()
                second_part = main_content[split_point + 1:].strip()
                logger.info("[Phase2-ForceSplit] Split long response with question: 3 messages")
                return f'<msg>{first_part}</msg>\n<msg>{second_part}</msg>\n<msg>{question}</msg>'

        logger.info("[Phase2-ForceSplit] Split long response with question: 2 messages")
        return f'<msg>{main_content.strip()}</msg>\n<msg>{question}</msg>'

    # Strategy 2: Split long single paragraph by sentences
    response_clean = response.strip()

    # Split into sentences (look for period followed by space and capital letter, or question marks)
    sentence_pattern = r'(?<=[.!?])\s+(?=[A-Z])'
    sentences = re.split(sentence_pattern, response_clean)

    if len(sentences) >= 3:
        # Group sentences into 2-3 messages
        messages = []

        # First message: opening sentence(s)
        if len(sentences[0]) < 100 and len(sentences) > 1:
            messages.append(f'<msg>{sentences[0]} {sentences[1]}</msg>')
            remaining_start = 2
        else:
            messages.append(f'<msg>{sentences[0]}</msg>')
            remaining_start = 1

        # Middle messages: group remaining sentences
        remaining = sentences[remaining_start:]
        if remaining:
            # Check if last sentence is a question
            last_sentence = remaining[-1].strip()
            has_question = last_sentence.endswith('?')

            if has_question and len(remaining) > 1:
                # Middle content
                middle = ' '.join(remaining[:-1])
                if middle:
                    messages.append(f'<msg>{middle}</msg>')
                # Question as separate message
                messages.append(f'<msg>{last_sentence}</msg>')
            else:
                # All remaining as one message
                messages.append(f'<msg>{" ".join(remaining)}</msg>')

        # Cap at 4 messages
        messages = messages[:4]

        if len(messages) >= 2:
            logger.info(f"[Phase2-ForceSplit] Split by sentences: {len(messages)} messages")
            return '\n'.join(messages)

    # Strategy 3: Split long response with question at the end
    question_match = re.search(r'(.*?)([.!]\s+)(.+\?)\s*$', response_clean, re.DOTALL)
    if question_match and len(response_clean) > 150:
        main_content = question_match.group(1) + question_match.group(2)
        question = question_match.group(3)

        # Split main content if it's long
        if len(main_content) > 200:
            # Split main content in half
            mid_point = len(main_content) // 2
            # Find nearest sentence break
            split_point = main_content.rfind('. ', 0, mid_point + 50)
            if split_point > 0:
                first_part = main_content[:split_point + 1].strip()
                second_part = main_content[split_point + 1:].strip()
                logger.info("[Phase2-ForceSplit] Split with question: 3 messages")
                return f'<msg>{first_part}</msg>\n<msg>{second_part}</msg>\n<msg>{question}</msg>'

        logger.info("[Phase2-ForceSplit] Split with question: 2 messages")
        return f'<msg>{main_content.strip()}</msg>\n<msg>{question}</msg>'

    # Strategy 4: For responses 150-300 chars, split at midpoint
    if 150 <= len(response_clean) <= 300:
        # Find a good split point (period, comma, or 'and'/'but')
        mid = len(response_clean) // 2
        split_candidates = [
            response_clean.rfind('. ', mid - 50, mid + 50),
            response_clean.rfind(', and ', mid - 50, mid + 50),
            response_clean.rfind(', but ', mid - 50, mid + 50),
            response_clean.rfind('. But ', mid - 50, mid + 50),
        ]

        split_point = max(split_candidates)
        if split_point > 0:
            first = response_clean[:split_point + 1].strip()
            second = response_clean[split_point + 1:].strip()
            if first and second and len(second) > 20:
                logger.info("[Phase2-ForceSplit] Split at midpoint: 2 messages")
                return f'<msg>{first}</msg>\n<msg>{second}</msg>'

    # No good split found - return as single message
    logger.debug(f"[Phase2-ForceSplit] No split applied (length: {len(response_clean)})")
    return response



# ─────────────────────────────────────────────────────────────────────────────
# ADR-015 — bubble boundaries as a pure function of the text
# ─────────────────────────────────────────────────────────────────────────────
#
# Emoji are consumed to the LEFT of a boundary. In this register a trailing
# emoji run IS the sentence-final punctuation ("hey. 🥵 come here." — the 🥵
# belongs to "hey."), and a naive (?<=[.!?])\s+ orphans it into a bubble of its
# own, which is the single most obviously-mechanical artifact a reader can see.
_EMO = "\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"

# (?!\.) keeps "..." intact. The opener class is deliberately NOT uppercase-only
# — that is exactly the bug in the legacy Strategy 2.
_BOUNDARY_RE = re.compile(
    rf'(?<=[.!?\u2026])(?!\.)'
    rf'(?P<tail>["\'\u201d\u2019\)\]]*(?:\s*[{_EMO}]+)*)'
    rf'\s+'
    rf'(?=[A-Za-z0-9\u201c"\'\[{_EMO}])'
)
_ABBREV_RE = re.compile(r'\b(?:mr|mrs|ms|dr|vs|etc|e\.g|i\.e)\.$', re.I)
_EMOJI_ONLY_RE = re.compile(rf'^[\s{_EMO}\W_]+$')
_PARA_RE = re.compile(r'\n\s*\n+')


def _sentences(text: str) -> list[str]:
    """Split on sentence boundaries, keeping emoji and closing quotes on the left."""
    out: list[str] = []
    last = 0
    for m in _BOUNDARY_RE.finditer(text):
        chunk = text[last:m.end('tail')]
        if _ABBREV_RE.search(chunk.strip()):
            continue  # "e.g. " is not a boundary
        out.append(chunk.strip())
        last = m.end()
    if text[last:].strip():
        out.append(text[last:].strip())
    return out


def split_bubbles(
    text: str,
    max_bubbles: int | None = None,
    min_words: int | None = None,
    words_per_bubble: int | None = None,
) -> list[str]:
    """Break a reply into chat bubbles. Pure function of the text.

    Deterministic and total: always returns at least one element for non-empty
    input, and never raises. That totality is the whole argument for doing this
    in code — a prompt instruction competes with every other instruction in the
    block and is followed on SOME fraction of turns, which cannot be a UI
    contract.

    Paragraph breaks win over sentence breaks when the model emitted any,
    because a blank line is author intent about where the beat ends while a
    sentence break is only our guess.
    """
    if max_bubbles is None:
        max_bubbles = chunking_settings.max_bubbles
    if min_words is None:
        min_words = chunking_settings.min_words
    if words_per_bubble is None:
        words_per_bubble = chunking_settings.words_per_bubble

    text = (text or "").strip()
    if not text:
        return []

    paras = [p.strip() for p in _PARA_RE.split(text) if p.strip()]
    units = paras if len(paras) >= 2 else _sentences(text)
    if len(units) <= 1:
        return [text]

    # Bubble count comes from total LENGTH, not unit count: six short sentences
    # is one beat, not six bubbles. The divisor is fitted to this deployment's
    # own history (see ChunkingSettings.words_per_bubble) rather than carried
    # over from a reference implementation.
    total_w = len(text.split())
    target = max(1, min(max_bubbles, round(total_w / words_per_bubble)))
    if paras is units:
        # The model emitted blank lines. That is author intent about where the
        # beat ends, and it outranks our own length heuristic — a two-line reply
        # of six words is still two bubbles, because it was written as two.
        target = max(target, min(len(paras), max_bubbles))
    target = min(target, len(units))
    if target <= 1:
        return [text]

    budget = total_w / target
    groups: list[str] = []
    cur: list[str] = []
    cur_w = 0
    for u in units:
        uw = len(u.split())
        if cur and cur_w + uw > budget * 1.25 and len(groups) < target - 1:
            groups.append(" ".join(cur))
            cur, cur_w = [], 0
        cur.append(u)
        cur_w += uw
    if cur:
        groups.append(" ".join(cur))

    # No orphans. Merge backward, then ONE forward pass for a short head — the
    # backward-only version leaves a stub as bubble 1, which is the bug the
    # reference implementation shipped before fixing it.
    out: list[str] = []
    for g in groups:
        if out and (len(g.split()) < min_words or _EMOJI_ONLY_RE.match(g)):
            out[-1] += " " + g
        else:
            out.append(g)
    if len(out) >= 2 and (len(out[0].split()) < min_words or _EMOJI_ONLY_RE.match(out[0])):
        out[1] = out[0] + " " + out[1]
        out.pop(0)
    return out


def force_multi_message_split(response: str, query: str) -> str:
    """Apply ``<msg>`` tags to a reply that has none.

    Signature and return contract are unchanged from the pre-ADR-015 version —
    a string, tags included — so no caller moves. What changed is who decides
    the boundaries.

    Model-emitted tags are honoured on BOTH paths and short-circuit here. That
    is not a courtesy: the analytical format block still asks for them
    deliberately, so a persona whose card sets ``format_style: analytical`` is
    untouched by ``CHUNK_IN_CODE``.
    """
    if '<msg>' in response:
        return response

    if not chunking_settings.in_code:
        return legacy_force_split(response, query)

    bubbles = split_bubbles(response)
    if len(bubbles) <= 1:
        return response
    logger.info("[ADR-015] split into %d bubbles in code", len(bubbles))
    return '\n'.join(f'<msg>{b}</msg>' for b in bubbles)


# A hallucinated next turn: the compiled prompt renders history as "User: ..." /
# "Assistant: ...", so the model sometimes keeps going and writes the *next* turn
# itself. Anchored to line-start so persona prose containing the word is untouched.
_HALLUCINATED_TURN_RE = re.compile(r'^\s*(?:User|Assistant)\s*:', re.MULTILINE)
_LEADING_ROLE_RE = re.compile(r'^\s*(?:Assistant|User)\s*:\s*', re.IGNORECASE)


def strip_role_prefix_leaks(answer: str) -> str:
    """Remove role-prefix artifacts leaked from the compiled prompt format.

    Two distinct leaks, both from the "User:/Assistant:" transcript framing:
      1. a leading ``Assistant:`` / ``User:`` prefix on the reply itself;
      2. a *trailing* fabricated turn — the model writes ``\\nUser: ...`` and keeps
         going (the classic missing-stop-sequence artifact). Everything from that
         line on is cut.

    Shared by both finalize paths (``routes/chat.py:_build_llm_response`` and
    ``QueryHandlerService._finalize_response``) so they can't drift apart again.
    """
    if not answer:
        return answer

    cleaned = _LEADING_ROLE_RE.sub('', answer, count=1)

    # Cut a fabricated next turn, but only if real content precedes it.
    match = _HALLUCINATED_TURN_RE.search(cleaned)
    if match and cleaned[:match.start()].strip():
        logger.info("[RoleLeak] Cut hallucinated turn at offset %d", match.start())
        cleaned = cleaned[:match.start()]

    return cleaned.strip()


# Cap on how many substitution rules a persona may declare — a persona card is
# owner-authored, but this bounds the per-reply regex work regardless.
_MAX_WORD_SUBSTITUTIONS = 25


def _case_preserving_replacement(good: str, matched: str) -> str:
    """Return ``good`` cased to mirror the matched text (ALL-CAPS / Title / lower)."""
    if matched.isupper():
        return good.upper()
    if matched[:1].isupper():
        return good[:1].upper() + good[1:]
    return good


def apply_word_substitutions(answer: str, substitutions: Optional[Dict[str, str]]) -> str:
    r"""Replace whole-word banned terms with a persona-approved form (ADR-012).

    Data-driven from the persona card's ``word_substitutions`` map (e.g.
    ``{"shaft": "cock"}``): the ONLY reliable lever for word choice, since the lean
    prompt builder (ADR-005) doesn't include the ``do``/``dont`` arrays and negative
    instructions can't steer a local model's baseline vocabulary at temperature.

    Runs once on the finished string (a plain ``re.sub`` per rule — microseconds, no
    effect on generation speed). Guards: whole-word (``\b``) so substrings survive;
    case-insensitive match with case-preserving output; keys regex-escaped; rule
    count capped. No-op (returns ``answer`` unchanged) when the map is empty — so the
    other personas pay nothing.
    """
    if not substitutions or not answer:
        return answer
    for bad, good in list(substitutions.items())[:_MAX_WORD_SUBSTITUTIONS]:
        if not isinstance(bad, str) or not bad.strip() or not isinstance(good, str):
            continue
        answer = re.sub(
            rf"\b{re.escape(bad)}\b",
            lambda m, g=good: _case_preserving_replacement(g, m.group(0)),
            answer,
            flags=re.IGNORECASE,
        )
    return answer


def parse_multi_message_response(response: str) -> tuple[list[str], str]:
    """
    Parse LLM response for <msg> tags and split into multiple messages.

    PHASE 2: Enables natural multi-message conversational flow.

    Args:
        response: LLM response string (may contain <msg> tags)

    Returns:
        Tuple of (messages: list[str], flow_type: str)
        - messages: List of individual message strings
        - flow_type: 'single' or 'multi'
    """
    # Extract all <msg>...</msg> blocks
    msg_pattern = r'<msg>(.*?)</msg>'
    matches = re.findall(msg_pattern, response, re.DOTALL)

    if matches and len(matches) > 1:
        # Multi-message response (2+ messages)
        messages = [m.strip() for m in matches[:4]]  # Cap at 4 messages
        logger.info(f"[Phase2] Parsed {len(messages)} messages from response")
        return (messages, 'multi')
    elif matches and len(matches) == 1:
        # Single message with tags (treat as single)
        return ([matches[0].strip()], 'single')

    # No well-formed pair matched. The model sometimes opens <msg> without ever
    # closing it, which used to fall through and leak the literal tags into the
    # chat. Recover by splitting on the opening tags (only reached when the
    # well-formed path above found nothing, so that path stays byte-identical).
    if '<msg>' in response:
        pieces = [
            re.sub(r'</msg>', '', p).strip()
            for p in response.split('<msg>')
        ]
        pieces = [p for p in pieces if p]
        if len(pieces) > 1:
            logger.info(f"[Phase2] Recovered {len(pieces)} messages from unclosed <msg> tags")
            return (pieces[:4], 'multi')
        if len(pieces) == 1:
            return ([pieces[0]], 'single')

    # No tags found, return original response
    return ([response], 'single')
