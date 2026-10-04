# src/coordinator/persona_memory.py
# Persona memory, prompt construction, and CV-style summary caching.
# Modularized into persona_loader, prompt_builder, and cv_summarizer.
# This file maintains backward compatibility by re-exporting all public APIs.

from __future__ import annotations

# Re-export persona loading functions
from .persona_loader import (
    _iter_persona_files,
    _load_card_file,
    _load_all_cards_cached,
    _cards_by_all_names,
    resolve_persona_to_card,
    resolve_persona_strict,
    progression_participant,
    progression_key,
    get_persona_card,
)

# Re-export prompt building functions and constants
# (legacy section-builders + rule constants were deleted in the audit step-7
# dead-code sweep; only the still-live helpers/builders are re-exported)
from .prompt_builder import (
    _summarize,
    _join_list,
    _build_psychological_block,
    build_system_prompt,
    build_greeting_user_prompt,
)

# Re-export CV summarizer functions
from .cv_summarizer import (
    _count_tokens,
    _truncate_to_tokens,
    _truncate_to_sentence,
    _summary_dir,
    _normalize_for_fingerprint,
    _fingerprint,
    _summary_file_for_key,
    _load_cached_summary,
    _save_summary,
    _make_cv_summary,
    _lock_path,
    _lock_owned_by_me,
    _acquire_lock,
    _release_lock,
    get_or_build_cv_summary,
    cleanup_summary_store,
    clear_summary_cache,
    ensure_all_summaries,
    ensure_all_summaries_serialized,
)

# Make all imports available for backward compatibility
__all__ = [
    # Persona loading
    "_iter_persona_files",
    "_load_card_file",
    "_load_all_cards_cached",
    "_cards_by_all_names",
    "resolve_persona_to_card",
    "resolve_persona_strict",
    "progression_participant",
    "progression_key",
    "get_persona_card",
    # Prompt building
    "_summarize",
    "_join_list",
    "_build_psychological_block",
    "build_system_prompt",
    "build_greeting_user_prompt",
    # CV summarizer
    "_count_tokens",
    "_truncate_to_tokens",
    "_truncate_to_sentence",
    "_summary_dir",
    "_normalize_for_fingerprint",
    "_fingerprint",
    "_summary_file_for_key",
    "_load_cached_summary",
    "_save_summary",
    "_make_cv_summary",
    "_lock_path",
    "_lock_owned_by_me",
    "_acquire_lock",
    "_release_lock",
    "get_or_build_cv_summary",
    "cleanup_summary_store",
    "clear_summary_cache",
    "ensure_all_summaries",
    "ensure_all_summaries_serialized",
]
