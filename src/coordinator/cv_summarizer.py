# src/coordinator/cv_summarizer.py
# CV-style summary generation and caching with inter-process file locking.
# Part of modular refactor from persona_memory.py.

from __future__ import annotations

import os
import json
import hashlib
import datetime
import time
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama.llms import OllamaLLM
from ollama._types import ResponseError

from .config import get_settings
from .ollama_utils import assert_model_available, require_model_configured
from .persona_loader import _load_all_cards_cached, resolve_persona_to_card

# Setup logger
logger = logging.getLogger(__name__)


# ---------------- LLM client ----------------

def _llm() -> OllamaLLM:
    """Create Ollama LLM client for CV summary generation."""
    cfg = get_settings().ollama
    model = require_model_configured(cfg.model)
    assert_model_available(cfg.base, model)
    return OllamaLLM(base_url=cfg.base, model=model, temperature=cfg.temperature, num_ctx=cfg.context_window, keep_alive=cfg.utility_keep_alive)


# ---------------- Token counting and truncation ----------------

def _count_tokens(text: str) -> int:
    """
    Count tokens in text. Attempts to use tiktoken for accuracy, falls back to approximation.
    Approximation: ~4 chars per token for English text (conservative estimate).
    """
    # Try using tiktoken for better accuracy (optional dependency)
    try:
        import tiktoken
        # Use cl100k_base encoding (GPT-4/ChatGPT tokenizer, good general approximation)
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        # Fallback to character-based approximation
        # ~4 chars per token is conservative and works well
        return max(1, len(text) // 4)
    except Exception:
        # Any other error, use fallback
        return max(1, len(text) // 4)


def _truncate_word_to_tokens(word: str, max_tokens: int) -> str:
    """Truncate a single (space-less) word to fit within max_tokens.

    Starts from a char-based estimate (~4 chars/token) then trims one char at a
    time until the actual token count fits. The estimate alone can overshoot —
    BPE tokenizers (tiktoken) split unusual words into more tokens than the
    heuristic predicts — so this verifies against _count_tokens to honor the
    max_tokens contract regardless of which tokenizer backs it.
    """
    candidate = word[:max(1, max_tokens * 4)]
    while _count_tokens(candidate) > max_tokens and len(candidate) > 1:
        candidate = candidate[:-1]
    return candidate


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to fit within max_tokens, preserving word boundaries."""
    if _count_tokens(text) <= max_tokens:
        return text

    # Binary search to find the right length
    text_words = text.split()

    # Edge case: single word that's too long
    if len(text_words) == 1:
        return _truncate_word_to_tokens(text_words[0], max_tokens)

    low, high = 0, len(text_words)

    while low < high:
        mid = (low + high + 1) // 2
        truncated = ' '.join(text_words[:mid])
        if _count_tokens(truncated) <= max_tokens:
            low = mid
        else:
            high = mid - 1

    result = ' '.join(text_words[:low])

    # Edge case: if no words fit, truncate first word to honor the token limit
    if not result and text_words:
        result = _truncate_word_to_tokens(text_words[0], max_tokens)

    # Ensure we don't exceed the limit
    while _count_tokens(result) > max_tokens and len(result.split()) > 1:
        result = ' '.join(result.split()[:-1])

    return result


def _truncate_to_sentence(text: str, max_tokens: int) -> str:
    """
    Truncate text to fit within max_tokens, preserving sentence boundaries.
    Falls back to word-boundary truncation if no complete sentence fits.

    This ensures summaries don't end mid-sentence, improving readability.
    """
    import re

    # First check if we're within limit
    if _count_tokens(text) <= max_tokens:
        return text

    # Find all sentence endings (. ! ? followed by space/EOF)
    # Using positive lookbehind to keep the punctuation with the sentence
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())

    # Build up text sentence by sentence until we exceed limit
    result = ""
    for sentence in sentences:
        candidate = (result + " " + sentence).strip() if result else sentence
        if _count_tokens(candidate) <= max_tokens:
            result = candidate
        else:
            break

    # If we got at least one complete sentence, return it
    if result and result[-1] in '.!?':
        return result

    # Fall back to word-boundary truncation (edge case: first sentence too long)
    logger.warning(f"First sentence exceeds {max_tokens} tokens, using word-boundary truncation")
    return _truncate_to_tokens(text, max_tokens)


# ---------------- Summary storage ----------------

def _summary_dir() -> Path:
    """Get directory for cached summaries."""
    return Path(get_settings().persona_dir) / "_summaries"


def _normalize_for_fingerprint(card: Dict) -> Dict:
    """Normalize card for fingerprinting (exclude fields that don't affect the summary).

    ``voice_signature`` (ADR-005 Phase B) is lean-prompt-only and never feeds the
    CV identity summary — excluding it keeps cached summaries valid so adding it
    does NOT drift the legacy <identity> text (preserves the frozen eval baseline).
    """
    exclude = {"emoji", "voice_signature"}
    return {k: v for k, v in card.items() if k not in exclude}


def _fingerprint(card: Dict) -> str:
    """Generate SHA1 fingerprint from normalized card."""
    normalized = _normalize_for_fingerprint(card)
    blob = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _summary_file_for_key(key: str) -> Path:
    """Get cache file path for persona key."""
    d = _summary_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


def _load_cached_summary(key: str) -> Optional[Dict]:
    """Load cached summary from disk.

    Returns:
        Dict with 'summary' and 'hash' fields, or None if not found/invalid
    """
    fp = _summary_file_for_key(key)
    if not fp.is_file():
        return None
    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "summary" in data and "hash" in data:
            return data
        return None
    except Exception:
        return None


def _save_summary(key: str, hash_: str, summary: str) -> Dict:
    """Save summary to disk cache.

    Args:
        key: Persona key
        hash_: Fingerprint hash of persona card
        summary: Generated CV summary

    Returns:
        Saved payload dict
    """
    payload = {
        "key": key,
        "hash": hash_,
        "updated": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "summary": summary.strip()
    }
    fp = _summary_file_for_key(key)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def _make_cv_summary(card: Dict) -> str:
    """Generate CV-style summary using LLM.

    Creates a first-person introduction (80-120 tokens) that embodies the
    persona's voice and personality.

    Args:
        card: Persona card dict

    Returns:
        Generated summary text with complete sentences

    Raises:
        RuntimeError: If LLM invocation fails
    """
    name = (card.get("display_name") or card.get("key") or "Persona")
    # Extract first name only for more natural self-introduction
    first_name = name.split(" — ")[0].strip().split()[0]
    style = card.get("style") or ""
    lore  = card.get("lore") or []
    voice = card.get("voice") or {}
    values = {
        "name": first_name,  # Use first name for natural "I'm Eeva" intro
        "full_name": name,
        "style": style,
        "lore": "\n".join([str(x) for x in lore if isinstance(x, str)]),
        "tics": ", ".join(voice.get("tics", []) if isinstance(voice, dict) else []),
    }

    lc = _llm()
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You write vivid first-person character introductions that embody personality and voice."),
        ("user",
         "Write a compact first-person introduction (between 80-120 tokens) AS {name}.\n"
         "Tone: {style}.\n"
         "CRITICAL RULES:\n"
         "1. Use 'I', 'my', 'me' - speak AS the character, not ABOUT them.\n"
         "2. Complete all sentences with proper punctuation (. ! ?).\n"
         "3. Do NOT end mid-thought or mid-sentence.\n"
         "Focus on what defines you: your passions, strengths, quirks, and worldview.\n"
         "Make it feel personal and authentic, like you're introducing yourself to someone.\n"
         "Draw from the lore below to capture your essence, but stay concise and vivid.\n"
         "Weave in your quirks/tics naturally if provided.\n\n"
         "Character: {full_name}\n"
         "Lore:\n{lore}\n\n"
         "Quirks/Tics: {tics}\n\n"
         "Return only your first-person introduction, starting with 'I' or 'I'm'."
        )
    ]).format_prompt(**values).to_string()

    try:
        summary = lc.invoke(prompt).strip()
        original_token_count = _count_tokens(summary)

        # Enforce 120 token upper limit (80-120 range)
        if original_token_count > 120:
            logger.info(f"Truncating {first_name} summary from {original_token_count} to ≤120 tokens")
            summary = _truncate_to_sentence(summary, 120)

        # Post-processing validation: ensure sentence completion
        if summary and summary[-1] not in '.!?':
            logger.warning(
                f"Summary for {first_name} does not end with punctuation. "
                f"Last 50 chars: ...{summary[-50:]}"
            )
            # Attempt to fix by re-truncating with sentence awareness
            summary = _truncate_to_sentence(summary, 120)

            # If still incomplete, add period as last resort
            if summary and summary[-1] not in '.!?':
                logger.error(f"Could not complete sentence for {first_name}, adding period")
                summary = summary.rstrip() + '.'

        final_token_count = _count_tokens(summary)
        logger.debug(
            f"Generated summary for {first_name}: "
            f"{original_token_count}→{final_token_count} tokens, "
            f"ends with '{summary[-1] if summary else '(empty)'}'"
        )

        return summary
    except ResponseError as e:
        raise RuntimeError(str(e))


# ---------------- Inter-process lock (simple PID file) ----------------

def _lock_path() -> Path:
    """Get path to lock file."""
    return _summary_dir() / ".lock"


def _lock_owned_by_me(pid: int) -> bool:
    """Check if lock file is owned by this process.

    Args:
        pid: Process ID to check

    Returns:
        True if lock is owned by this PID
    """
    lp = _lock_path()
    try:
        if not lp.exists():
            return False
        content = lp.read_text(encoding="utf-8").strip()
        return content == str(pid)
    except Exception:
        return False


def _acquire_lock(timeout_sec: float = 300.0, poll_sec: float = 0.25) -> bool:
    """
    Create a lock file with this process PID.

    Args:
        timeout_sec: Maximum time to wait for lock (seconds)
        poll_sec: Polling interval (seconds)

    Returns:
        True if acquired, False if timeout elapsed
    """
    lp = _lock_path()
    me = os.getpid()
    start = time.time()
    while True:
        try:
            # Exclusive create; fail if exists
            fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(str(me))
            return True
        except FileExistsError:
            # Someone else holds the lock. If it's ours, consider acquired.
            if _lock_owned_by_me(me):
                return True
            if time.time() - start >= timeout_sec:
                return False
            time.sleep(poll_sec)
        except Exception:
            if time.time() - start >= timeout_sec:
                return False
            time.sleep(poll_sec)


def _release_lock():
    """Release lock file."""
    lp = _lock_path()
    try:
        if lp.exists():
            lp.unlink()
    except Exception:
        pass


# ---------------- Public API ----------------

def get_or_build_cv_summary(selector: Optional[str]) -> Dict:
    """
    Get or build CV summary for persona.

    Returns {key, hash, updated, summary}. Rebuilds if missing or stale.
    Lock-aware to avoid races with preflight or concurrent requests.

    Args:
        selector: Persona key/name

    Returns:
        Summary dict with 'key', 'hash', 'updated', 'summary' fields

    Raises:
        RuntimeError: If no personas available or lock timeout
    """
    card = resolve_persona_to_card(selector)
    if not card:
        raise RuntimeError("No personas available.")
    key = (card.get("key") or "Persona").split()[0].capitalize()
    want_hash = _fingerprint(card)
    cached = _load_cached_summary(key)
    if cached and cached.get("hash") == want_hash and isinstance(cached.get("summary"), str):
        return cached

    # Acquire lock briefly to build/update this one; avoid deadlock if we already own it
    me = os.getpid()
    need_release = False
    if not _lock_owned_by_me(me):
        if not _acquire_lock(timeout_sec=60.0, poll_sec=0.2):
            # Best-effort: if we couldn't get the lock quickly, re-check cache and bail
            cached = _load_cached_summary(key)
            if cached and cached.get("hash") == want_hash:
                return cached
            raise RuntimeError("Summary builder busy; please retry shortly.")
        need_release = True

    try:
        # Double-check cache after lock to avoid duplicate work
        cached = _load_cached_summary(key)
        if cached and cached.get("hash") == want_hash and isinstance(cached.get("summary"), str):
            return cached
        text = _make_cv_summary(card)
        return _save_summary(key, want_hash, text)
    finally:
        if need_release:
            _release_lock()


def cleanup_summary_store() -> None:
    """
    Remove summaries for personas that no longer exist.
    """
    dir_ = _summary_dir()
    if not dir_.exists():
        return
    live_keys = {
        (c.get("key") or "").split()[0].capitalize()
        for c in _load_all_cards_cached() if isinstance(c.get("key"), str)
    }
    for f in dir_.glob("*.json"):
        key = f.stem
        if key not in live_keys and f.name != ".lock":
            try:
                f.unlink()
            except Exception:
                pass


def clear_summary_cache() -> int:
    """
    Clear all cached summaries to force regeneration.

    Returns:
        Number of files deleted
    """
    dir_ = _summary_dir()
    if not dir_.exists():
        return 0

    deleted = 0
    for f in dir_.glob("*.json"):
        if f.name != ".lock":
            try:
                f.unlink()
                deleted += 1
            except Exception:
                pass
    return deleted


def ensure_all_summaries() -> Tuple[int, int]:
    """
    Build/refresh summaries for all personas.

    Not lock-safe by itself — call ensure_all_summaries_serialized for
    inter-process safety.

    Returns:
        Tuple of (built_count, skipped_count)
    """
    cleanup_summary_store()
    built = 0
    skipped = 0
    for card in _load_all_cards_cached():
        selector = card.get("key")
        key = (card.get("key") or "Persona").split()[0].capitalize()
        want_hash = _fingerprint(card)
        cached = _load_cached_summary(key)
        if cached and cached.get("hash") == want_hash and isinstance(cached.get("summary"), str):
            skipped += 1
            continue
        text = _make_cv_summary(card)
        _save_summary(key, want_hash, text)
        built += 1
    return built, skipped


def ensure_all_summaries_serialized(timeout_sec: float = 300.0, poll_sec: float = 0.25) -> Tuple[int, int]:
    """
    Acquire global summary lock, then run ensure_all_summaries() once.

    Args:
        timeout_sec: Lock acquisition timeout (seconds)
        poll_sec: Lock polling interval (seconds)

    Returns:
        Tuple of (built_count, skipped_count)
    """
    if not _acquire_lock(timeout_sec=timeout_sec, poll_sec=poll_sec):
        # Could not acquire — treat as "someone else is doing it"; give caller a benign result.
        return (0, 0)
    try:
        return ensure_all_summaries()
    finally:
        _release_lock()
