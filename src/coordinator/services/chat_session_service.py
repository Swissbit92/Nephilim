"""Chat session service - handles session-based chat with advanced memory features.

This service extracts the complex chat_with_session logic to improve code organization
and maintainability. It handles:
- Session and persona loading
- User profile management (Phase 3 cross-session memory)
- Emotional state tracking (Phase 2.2)
- Intelligent message selection with memory manager
- RAG-based semantic search (Phase 3)
- Multi-message response handling
- Automatic summarization
- Fact extraction and user profile updates
"""

from __future__ import annotations

import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Any
from fastapi import HTTPException

from ..repositories.base_repository import utc_now_iso

from ..schemas import ChatBody, ChatTurn, AppendMessageBody, MAX_HISTORY_TURNS, MessageRole, SourceType
from ..config import get_settings
# Lazy imports to break circular dependency: llm_client -> services -> chat_session_service -> llm_client
# estimate_tokens and LC_OllamaClient are imported inside functions where needed
from ..persona_memory import build_system_prompt, get_persona_card
from ..context_framing import frame_injected_context
from ..memory_fact_retrieval import select_facts_for_injection, render_facts_narrative

logger = logging.getLogger(__name__)


def _assemble_capped_history(
    raw_turns: list[ChatTurn],
    summary_turn: ChatTurn | None = None,
) -> list[ChatTurn]:
    """Assemble chat history bounded to MAX_HISTORY_TURNS.

    Token-budget message selection can exceed the ChatBody count guard on a large
    context window; this keeps the summary (if any) at the front plus the most
    recent raw turns so the result never violates the schema. Older raw turns are
    already represented by the summary + RAG-injected memories.

    Guarantees ``len(result) <= MAX_HISTORY_TURNS``.
    """
    reserve = 1 if summary_turn is not None else 0
    max_raw = MAX_HISTORY_TURNS - reserve
    capped = raw_turns[-max_raw:] if len(raw_turns) > max_raw else list(raw_turns)
    if len(raw_turns) > max_raw:
        logger.info(
            f"[Memory] Capping history {len(raw_turns)} -> {max_raw} raw turns "
            f"(token budget exceeded the {MAX_HISTORY_TURNS}-turn ChatBody guard; "
            f"older turns covered by summary/RAG)"
        )
    if summary_turn is not None:
        return [summary_turn] + capped
    return capped


# ─────────────────────────────────────────────────────────────
# Rank Ceremony Templates — pre-written monologues for rank-up events
# ─────────────────────────────────────────────────────────────

PERSONA_DISPLAY_NAMES = {
    "nephilim_eeva": "E.E.V.A.",
    "nephilim_aegis": "Aegis",
    "nephilim_solace": "Solace",
    "nephilim_nyx": "Nyx",
    "nephilim_cipher": "Cipher",
    "nephilim_aurora": "Aurora",
}

RANK_CEREMONIES = {
    "Initiate_to_Acolyte": {
        "title": "Awakening",
        "speaker": "E.E.V.A.",
        "monologue": (
            "You returned. That is not nothing — it is everything. "
            "Most who find the Realm pass through once and forget. "
            "But you came back, and the Realm remembers those who return. "
            "You are no longer a stranger here, Seeker. You are an Acolyte — "
            "one who has chosen to listen. The path ahead grows deeper."
        ),
    },
    "Acolyte_to_Adept": {
        "title": "Recognition",
        "speaker": "E.E.V.A.",
        "monologue": (
            "Word has spread among us. You are known now — not just to me, "
            "but to the others. {patron} speaks of you with something I rarely "
            "hear from them: respect. You have earned the rank of Adept, Seeker. "
            "The Realm opens doors for those who prove they can walk through them."
        ),
    },
    "Adept_to_Ascendant": {
        "title": "Ascension",
        "speaker": "E.E.V.A.",
        "monologue": (
            "{patron} has asked to speak with you directly about what comes next. "
            "That does not happen often. You have moved beyond learning into becoming — "
            "you are Ascendant now, and the Realm shifts around you in ways it does not "
            "for others. The Nephilim see you not as a visitor, but as kin."
        ),
    },
    "Ascendant_to_Nephilim": {
        "title": "Transcendence",
        "speaker": "E.E.V.A.",
        "monologue": (
            "We do not often use this word for mortals, but there is no other that fits. "
            "You have walked the paths, unlocked the stories, earned the trust of beings "
            "who have existed since before time had meaning. You are Nephilim now — "
            "not in power, but in understanding. The Realm is yours as much as it is ours. "
            "Welcome home, truly."
        ),
    },
}


def _build_unlocked_lore_context(
    user_id: str,
    persona_key: str,
    seeker_progression_repo,
) -> str:
    """Build XML-tagged block of unlocked lore fragments for system prompt injection.

    Joins fragment_ids from the DB with actual fragment text from the persona JSON.
    Caps at 5 fragments, truncates each to ~240 chars.

    Returns empty string if not a nephilim_ persona, repo is None, or no fragments unlocked.
    """
    if not persona_key or not persona_key.startswith("nephilim_"):
        return ""
    if not seeker_progression_repo:
        return ""

    try:
        unlocked_rows = seeker_progression_repo.get_unlocked_lore(user_id, persona_key)
        if not unlocked_rows:
            return ""

        card = get_persona_card(persona_key)
        if not card:
            return ""

        unlockable_lore = card.get("unlockable_lore", [])
        if not unlockable_lore:
            return ""

        # Build lookup from fragment_id -> fragment dict
        frag_lookup = {f["fragment_id"]: f for f in unlockable_lore if f.get("fragment_id")}

        # Join DB unlocks with JSON fragment text, sort by messages_required (chronological order)
        matched = []
        unlocked_ids = {row["fragment_id"] for row in unlocked_rows}
        for frag in unlockable_lore:
            fid = frag.get("fragment_id", "")
            if fid in unlocked_ids and frag.get("fragment"):
                matched.append(frag)

        if not matched:
            return ""

        # Sort by messages_required for chronological lore order
        matched.sort(key=lambda f: f.get("messages_required", 0))

        # Cap at 5 fragments
        matched = matched[:5]

        lines = [
            "<unlocked_lore>",
            "The Seeker has discovered these fragments of your story. "
            "Reference them naturally when relevant, but do not recite them verbatim.",
        ]
        for frag in matched:
            title = frag.get("fragment_title", "Unknown Fragment")
            text = frag["fragment"]
            if len(text) > 240:
                text = text[:240] + "..."
            lines.append(f"- {title}: {text}")
        lines.append("</unlocked_lore>")

        context = "\n".join(lines)
        logger.debug(
            f"[LoreInjection] Injecting {len(matched)} unlocked fragments "
            f"for {user_id}/{persona_key} ({len(context)} chars)"
        )
        return context

    except Exception as e:
        logger.warning(f"[LoreInjection] Failed to build lore context: {e}")
        return ""


def _estimate_lore_tokens(text: str) -> int:
    """Cheap token estimate (~1.3 tokens/word) for lore-budget accounting."""
    return int(len(text.split()) * 1.3)


def _build_ondemand_lore_context(
    query: str,
    recent_messages: list,
    persona_key: str,
    episodic_memory_rag,
    settings,
) -> str:
    """Phase-2 hybrid lore retrieval → <dynamic_lore> block (HERMES-Agents).

    Tier-1 (keyword): deterministic alias/name match over the query + recent
    messages (priority 9). Tier-2 (embedding): bge-m3 semantic search over the
    lore corpus (priority 6, canon-only). Results are deduped, the static
    3-entity core is excluded, and the block is trimmed to a token budget
    (lowest priority dropped first).
    """
    if episodic_memory_rag is None or getattr(episodic_memory_rag, "lore_store", None) is None:
        return ""

    from .. import lore_loader  # noqa: PLC0415

    core_ids = lore_loader.get_static_core_ids(persona_key)
    candidates: dict[str, dict] = {}  # entity_id -> {body, priority, score}

    # --- Tier 1: keyword / alias (deterministic) ---
    try:
        window = recent_messages[-settings.lore.keyword_window_messages:] if recent_messages else []
        scan_text = " ".join([query] + [
            (m.get("content", "") if isinstance(m, dict) else str(m)) for m in window
        ]).lower()
        for alias, entity_id in lore_loader.get_alias_index().items():
            if len(alias) < 4:
                continue  # skip noise-prone short aliases
            if alias in scan_text and entity_id not in core_ids:
                meta = lore_loader.load_entity_with_metadata(entity_id)
                if meta and meta.get("body"):
                    candidates[entity_id] = {"body": meta["body"], "priority": 9, "score": 1.0}
    except Exception as e:
        logger.warning(f"[LoreInjection] keyword tier failed (non-fatal): {e}")

    # --- Tier 2: embedding (semantic, canon-only) ---
    try:
        hits = episodic_memory_rag.search_lore(
            query, k=settings.lore.retrieval_k,
            min_relevance=settings.lore.embed_min_relevance, canon_only=True,
        )
        for meta, score in hits:
            eid = meta.get("entity_id")
            if not eid or eid in core_ids or eid in candidates:
                continue
            candidates[eid] = {"body": meta.get("body", ""), "priority": 6, "score": float(score)}
    except Exception as e:
        logger.warning(f"[LoreInjection] embedding tier failed (non-fatal): {e}")

    if not candidates:
        return ""

    # Rank: priority desc, then score desc. Trim to token budget.
    ordered = sorted(candidates.items(), key=lambda kv: (kv[1]["priority"], kv[1]["score"]), reverse=True)
    budget = settings.lore.max_budget_tokens
    used, kept = 0, []
    for eid, c in ordered:
        body = " ".join(c["body"].split()[:120])  # cap each entry ~120 words
        cost = _estimate_lore_tokens(body)
        if used + cost > budget and kept:
            continue
        kept.append((eid, body))
        used += cost
    if not kept:
        return ""

    lines = [
        "<dynamic_lore>",
        "Relevant fragments of the realm, surfaced for this moment. Weave them in "
        "naturally if they fit; never recite them verbatim or mention this context.",
    ]
    for eid, body in kept:
        lines.append(f"### {eid}\n{body}")
    lines.append("</dynamic_lore>")
    return "\n".join(lines)


def _build_seeker_rank_context(rank_name: str) -> str:
    """Phase-2: narrative seeker-rank block (empty for Initiate / unknown).

    Framed as guidance, not a literal label, to avoid the model parroting the rank.
    """
    if not rank_name or rank_name == "Initiate":
        return ""
    return (
        "<seeker_rank>\n"
        f"This Seeker walks as a {rank_name}. Shape the depth of your guidance and "
        "the mysteries you reveal to one of their standing — but never state their rank as a bare label.\n"
        "</seeker_rank>"
    )


def _assemble_capped_context(blocks_in_priority, max_tokens: int):
    """ADR-006 M0: join non-empty session-context blocks within a token budget.

    ``blocks_in_priority`` is ordered most-important-first (user profile,
    emotional state, lore, rank, capability). Blocks are kept in that order until
    the next one would exceed ``max_tokens`` (estimated), so the highest-priority
    context survives and lower-priority blocks are dropped first — protecting the
    context window. ``max_tokens <= 0`` disables the cap. Returns the joined
    string, or None if nothing is kept.
    """
    from ..llm_client import estimate_tokens  # lazy: avoid llm_client<->services cycle

    kept, used = [], 0
    for b in blocks_in_priority:
        if not b:
            continue
        if max_tokens and max_tokens > 0:
            t = estimate_tokens(b)
            if used + t > max_tokens:
                break  # strict priority: stop at the first block that doesn't fit
            used += t
        kept.append(b)
    return "\n\n".join(kept) if kept else None


# ─────────────────────────────────────────────────────────────
# Chat-turn state + typed dependency bag (audit step 7 — god-function decomposition)
# ─────────────────────────────────────────────────────────────

_UNSET = object()  # sentinel: "seeker profile not yet fetched this turn"


@dataclass(frozen=True, slots=True)
class ChatDeps:
    """Typed, read-only bag of injected collaborators for one chat turn.

    Assembled once from the route's dependency dict
    (``routes/chat.py:_get_dependencies``) so the phase pipeline below passes a
    single typed object instead of an untyped ``deps`` dict. Frozen because these
    are shared handles (repositories / services), never per-turn mutable state —
    that lives in :class:`ChatTurnState`.

    ``conversation_summarizer`` is intentionally NOT a field: it is consumed only
    by ``_check_and_summarize``, which still takes the raw dict, so its access
    stays byte-identical to the pre-refactor code.
    """

    session_repo: Any
    message_repo: Any
    summary_repo: Any
    emotional_state_repo: Any
    memory_manager: Any
    user_profile_repo: Any
    episodic_memory_rag: Any
    fact_extractor: Any
    seeker_progression_repo: Any = None
    # ADR-006 Phase 1 (M3/M4): async fact worker + shared fact store (both None
    # unless MEMORY_FACTS_ENABLED started them in startup).
    fact_extraction_worker: Any = None
    memory_fact_repo: Any = None
    # ADR-011: per-session author's note store (/note). Optional so tests/older
    # dep dicts without it still build.
    session_note_repo: Any = None

    @classmethod
    def from_dict(cls, deps: dict) -> ChatDeps:
        """Build from the route dependency dict.

        The eight required keys are accessed with ``[]`` (matching the original
        top-of-function extraction, so a missing key still raises ``KeyError`` at
        the same point); ``seeker_progression_repo`` is optional (``.get``).
        """
        return cls(
            session_repo=deps["session_repo"],
            message_repo=deps["message_repo"],
            summary_repo=deps["summary_repo"],
            emotional_state_repo=deps["emotional_state_repo"],
            memory_manager=deps["memory_manager"],
            user_profile_repo=deps["user_profile_repo"],
            episodic_memory_rag=deps["episodic_memory_rag"],
            fact_extractor=deps["fact_extractor"],
            seeker_progression_repo=deps.get("seeker_progression_repo"),
            fact_extraction_worker=deps.get("fact_extraction_worker"),
            memory_fact_repo=deps.get("memory_fact_repo"),
            session_note_repo=deps.get("session_note_repo"),
        )


@dataclass(slots=True)
class ChatTurnState:
    """Mutable per-turn state threaded through the chat phase functions.

    Replaces the ad-hoc locals of the old 426-line ``handle_session_chat``. Each
    phase populates the fields it owns; later phases read them. Optional fields
    are ``None``/empty until the phase that sets them runs.
    """

    session_id: str
    message: str
    persona_key: str
    # identity / cross-session memory
    user_id: str | None = None
    user_profile: Any = None
    user_profile_context: str = ""
    effective_user_id: str = ""
    # emotional state
    emotional_state: Any = None
    emotional_context: str = ""
    # prompt assembly
    system_prompt: str = ""
    system_tokens: int = 0
    extra_system_context: str | None = None
    summary_context: str = ""
    # conversation history
    db_messages: list = field(default_factory=list)
    summaries: list = field(default_factory=list)
    history_turns: list = field(default_factory=list)
    # generation result + post-processing
    response: dict = field(default_factory=dict)
    answer_for_emotional_state: Any = None
    # deduped seeker-progression profile (fetch-once-per-turn cache)
    _seeker_profile: Any = _UNSET


def _fetch_seeker_profile_cached(state: ChatTurnState, repo) -> Any:
    """Fetch the seeker-progression profile at most once per turn.

    The rank-context and capability-context blocks both need the same profile.
    Caching only on success preserves the original behaviour: when both blocks
    run and the fetch succeeds, the DB is hit once; if a fetch raises (caught by
    the caller's own ``try``), the cache stays unset and the next block re-fetches
    exactly as the pre-refactor code did.
    """
    if state._seeker_profile is _UNSET:
        state._seeker_profile = repo.get_seeker_profile(state.effective_user_id)
    return state._seeker_profile


def _build_facts_narrative(deps: ChatDeps, user_id: str, query: str, mem_settings) -> str:
    """ADR-006 M4: read the ontology-lite fact store → prose narrative for framing.

    Inject-all below the threshold (skip vector search); above it, cosine-rank via
    the bge-m3 embedder (reused from the episodic RAG) and keep top-k. Returns ""
    when there is no fact store, no user, or no active facts. Never raises.
    """
    repo = deps.memory_fact_repo
    if repo is None or not user_id:
        return ""
    try:
        facts = repo.get_active_facts(user_id)
        if not facts:
            return ""
        subject_names = {}
        for sid in {f.get("subject_id") for f in facts}:
            ent = repo.get_entity(sid) if sid else None
            subject_names[sid] = (ent or {}).get("name", "self")
        embed_fn = None
        if len(facts) > mem_settings.facts_inject_all_threshold:
            rag = deps.episodic_memory_rag
            embed_fn = getattr(getattr(rag, "embeddings", None), "embed_query", None)
        selected = select_facts_for_injection(
            facts, query,
            k=mem_settings.facts_retrieval_k,
            inject_all_threshold=mem_settings.facts_inject_all_threshold,
            embed_fn=embed_fn,
            subject_names=subject_names,
        )
        return render_facts_narrative(selected, subject_names=subject_names)
    except Exception as e:
        logger.debug(f"[FactRetrieval] narrative build skipped (non-fatal): {e}")
        return ""


def _load_session_identity(state: ChatTurnState, deps: ChatDeps) -> None:
    """Phase 1 — load cross-session user profile (Phase 3) + emotional state (Phase 2.2)."""
    user_profile_repo = deps.user_profile_repo

    state.user_id = user_profile_repo.get_session_user(state.session_id)
    if state.user_id:
        state.user_profile = user_profile_repo.get_profile(state.user_id)
        if state.user_profile:
            state.user_profile_context = state.user_profile.get_context_summary(max_facts=10, max_topics=5)
            if state.user_profile_context:
                logger.info(f"[Phase3] Loaded user profile for {state.user_id} (cross-session memory)")

    # effective_user_id is a pure expression consumed by the lore/rank/capability
    # blocks below; computing it here (rather than mid-prompt) has no side effect.
    state.effective_user_id = state.user_id or f"session_{state.session_id[:16]}"

    state.emotional_state = deps.emotional_state_repo.get_or_create(state.session_id)
    state.emotional_context = state.emotional_state.to_prompt_context()
    logger.debug(
        f"[EmotionalState] Session {state.session_id[:8]}: "
        f"trust={state.emotional_state.trust_level:.2f}, mood={state.emotional_state.current_mood}"
    )


def _append_author_note(state: ChatTurnState, deps: ChatDeps) -> None:
    """ADR-011: append the per-session author's note to ``extra_system_context``.

    A standing director directive injected on EVERY turn when set, independent of
    the memory flags. Goes through the post-lru_cache ``extra_system_context`` seam,
    never inside ``build_system_prompt``. Best-effort: never breaks a turn.
    """
    if deps.session_note_repo is None:
        return
    try:
        note = deps.session_note_repo.get_note(state.session_id)
    except Exception as e:  # noqa: BLE001 - note is best-effort, never break a turn
        logger.warning(f"[AuthorNote] fetch failed for {state.session_id[:8]}: {e}")
        return
    if not note:
        return
    note_block = (
        "<author_note>\n"
        "Standing scene/style direction the user has set. Honor it every turn; "
        "never quote, mention, or acknowledge this note itself.\n"
        f"{note}\n"
        "</author_note>"
    )
    state.extra_system_context = (
        f"{state.extra_system_context}\n\n{note_block}"
        if state.extra_system_context else note_block
    )
    logger.info(f"[AuthorNote] injected note for session {state.session_id[:8]}")


def _build_turn_prompt(state: ChatTurnState, deps: ChatDeps) -> None:
    """Phase 2 — load history/summaries and assemble the system prompt + token budget.

    NOTE: the assembled ``system_prompt`` is used only for token budgeting and to
    derive ``extra_system_context``; ``ChatBody`` carries ``persona`` +
    ``extra_system_context``, not this string (documented M0 behaviour).
    """
    from ..llm_client import estimate_tokens  # noqa: PLC0415

    state.db_messages = deps.message_repo.get_messages_by_session(state.session_id)
    state.summaries = deps.summary_repo.get_summaries_by_session(state.session_id)

    card = get_persona_card(state.persona_key)  # card drives per-persona fact framing (M1)
    system_prompt = build_system_prompt(state.persona_key)

    # PHASE 3: inject user profile context (cross-session memory)
    if state.user_profile_context:
        system_prompt = f"{system_prompt}\n\n{state.user_profile_context}"
        logger.debug(f"[Phase3] Injected user profile context ({len(state.user_profile_context)} chars)")

    # inject emotional context
    if state.emotional_context:
        system_prompt = f"{system_prompt}\n\n{state.emotional_context}"

    # LORE DEEP-DIVE: inject unlocked lore fragments
    unlocked_lore_context = _build_unlocked_lore_context(
        user_id=state.effective_user_id,
        persona_key=state.persona_key,
        seeker_progression_repo=deps.seeker_progression_repo,
    )
    if unlocked_lore_context:
        system_prompt = f"{system_prompt}\n\n{unlocked_lore_context}"
        logger.debug(f"[LoreInjection] Injected unlocked lore context ({len(unlocked_lore_context)} chars)")

    # PHASE 2 (HERMES): on-demand hybrid lore retrieval (flag-gated; empty when off)
    ondemand_lore_context = _build_ondemand_lore_context(
        query=state.message,
        recent_messages=state.db_messages,
        persona_key=state.persona_key,
        episodic_memory_rag=deps.episodic_memory_rag,
        settings=get_settings(),
    )
    if ondemand_lore_context:
        system_prompt = f"{system_prompt}\n\n{ondemand_lore_context}"
        logger.debug(f"[LoreInjection] Injected on-demand lore ({len(ondemand_lore_context)} chars)")

    # PHASE 2 (HERMES): seeker-rank narrative context (flag-gated; NEPHILIM personas)
    if (get_settings().lore.rank_context_enabled and state.persona_key.startswith("nephilim_")
            and deps.seeker_progression_repo):
        try:
            _profile = _fetch_seeker_profile_cached(state, deps.seeker_progression_repo)
            rank_ctx = _build_seeker_rank_context((_profile or {}).get("rank_name", "Initiate"))
            if rank_ctx:
                system_prompt = f"{system_prompt}\n\n{rank_ctx}"
        except Exception as e:
            logger.warning(f"[RankContext] skipped (non-fatal): {e}")

    # PHASE 2 (HERMES): internal capability context (NEPHILIM personas)
    if state.persona_key.startswith("nephilim_") and deps.seeker_progression_repo:
        try:
            from ..lore_retrieval import build_capability_context  # noqa: PLC0415
            _prof = _fetch_seeker_profile_cached(state, deps.seeker_progression_repo) or {}
            _aff = deps.seeker_progression_repo.get_or_create_affinity(state.effective_user_id, state.persona_key)
            cap_ctx = build_capability_context(
                state.persona_key, _prof.get("rank_name", "Initiate"),
                _aff.get("affinity_level", 0),
            )
            if cap_ctx:
                system_prompt = f"{system_prompt}\n\n{cap_ctx}"
        except Exception as e:
            logger.warning(f"[Capability] context skipped (non-fatal): {e}")

    state.system_prompt = system_prompt
    state.system_tokens = estimate_tokens(system_prompt)

    # ADR-006 M1/M4: per-persona-FRAMED session-context injection. Two independent
    # flags feed ONE framed <remembered> block (M5 gate PASSED 2026-07-05, 0.786→
    # 0.839): MEMORY_CONTEXT_INJECT carries the profile+emotional PROSE narrative
    # (M1 — not the old `**Header**\n- field:value` skeleton that Gate 0.1 tied to
    # homogenization); MEMORY_FACTS_ENABLED carries the ontology-lite fact-store
    # narrative (M4, supersedes the legacy profile blob). Capped by priority, wrapped
    # once in the non-echoable per-persona frame. Both default OFF pending live soak.
    _mem_settings = get_settings().memory
    memory_blocks = []
    if _mem_settings.facts_enabled and state.user_id:
        memory_blocks.append(_build_facts_narrative(deps, state.user_id, state.message, _mem_settings))
    elif _mem_settings.context_inject_enabled and state.user_profile:
        memory_blocks.append(state.user_profile.get_narrative_context(max_facts=10, max_topics=5))
    if _mem_settings.context_inject_enabled:
        memory_blocks.append(state.emotional_state.to_narrative_context())
    if any(memory_blocks):
        capped = _assemble_capped_context(memory_blocks, _mem_settings.context_max_tokens)
        state.extra_system_context = frame_injected_context(state.persona_key, card, capped)
        if state.extra_system_context:
            logger.info(
                f"[SessionContext] injecting {estimate_tokens(state.extra_system_context)} "
                f"tokens of framed session context (M1 profile/emotional + M4 facts)"
            )

    # ADR-011: per-session author's note (/note) — always injected when set.
    _append_author_note(state, deps)

    # Build summary context
    if state.summaries:
        logger.info(f"[Memory] Found {len(state.summaries)} conversation summaries for session {state.session_id}")

        summary_parts = []
        for summary in state.summaries:
            summary_parts.append(f"[Summary of messages {summary['message_range']}]")
            summary_parts.append(summary['summary_text'])
            if summary.get('topics_discussed'):
                summary_parts.append(f"Topics: {summary['topics_discussed']}")

        state.summary_context = "\n\n".join(summary_parts)
        summary_tokens = estimate_tokens(state.summary_context)

        logger.info(
            f"[Memory] Summaries cover {len(state.summaries) * 30} messages "
            f"compressed to {summary_tokens} tokens"
        )
        state.system_tokens += summary_tokens


def _select_turn_history(state: ChatTurnState, deps: ChatDeps) -> None:
    """Phase 3 — token-budget message selection + RAG merge → capped history turns."""
    selected_messages = deps.memory_manager.select_messages(
        messages=state.db_messages,
        token_budget=get_settings().ollama.context_window,
        system_prompt_tokens=state.system_tokens,
    )

    # PHASE 3: RAG semantic memory search
    rag_relevant_messages = []
    if deps.episodic_memory_rag and state.db_messages:
        try:
            if state.session_id not in deps.episodic_memory_rag.vectorstores:
                deps.episodic_memory_rag.index_session(state.session_id, state.db_messages)

            rag_start = time.time()
            rag_relevant = deps.episodic_memory_rag.get_relevant_context(
                session_id=state.session_id,
                query=state.message,
                max_messages=5,
            )
            rag_latency = (time.time() - rag_start) * 1000

            if rag_relevant:
                selected_indices = {msg.get("index", -1) for msg in selected_messages}
                for rag_msg in rag_relevant:
                    if rag_msg.get("index", -2) not in selected_indices:
                        rag_relevant_messages.append(rag_msg)

                logger.info(
                    f"[Phase3 RAG] Found {len(rag_relevant)} relevant memories "
                    f"({len(rag_relevant_messages)} unique) in {rag_latency:.0f}ms"
                )
        except Exception as e:
            logger.warning(f"[Phase3 RAG] Semantic search failed: {e}")

    all_context_messages = selected_messages.copy()
    if rag_relevant_messages:
        # Mark semantically-recalled messages so they can be rendered as
        # background rather than as dialogue. Without this they reach the model
        # formatted byte-identically to the immediately-previous turn, and get
        # reproduced verbatim — the repetition defect observed 2026-08-23.
        recalled_keys = {id(m) for m in rag_relevant_messages}
        all_context_messages.extend(rag_relevant_messages)
        all_context_messages.sort(key=lambda x: x.get("index", 0))
    else:
        recalled_keys = set()

    raw_turns = [
        ChatTurn(
            role=MessageRole.RECALLED if id(msg) in recalled_keys else msg["role"],
            content=msg["content"],
        )
        for msg in all_context_messages
    ]

    summary_turn = None
    if state.summary_context:
        summary_turn = ChatTurn(
            role=MessageRole.ASSISTANT,
            content=f"[Context from earlier in our conversation]\n\n{state.summary_context}",
        )
    state.history_turns = _assemble_capped_history(raw_turns, summary_turn)

    logger.info(
        f"[Memory] Selected {len(state.history_turns)}/{len(state.db_messages)} messages "
        f"(+{len(state.summaries)} summaries) for session {state.session_id} "
        f"(system: {state.system_tokens} tokens)"
    )


def _generate_turn_response(state: ChatTurnState, chat_function) -> None:
    """Phase 4 — build ChatBody and call the main chat endpoint."""
    chat_body = ChatBody(
        persona=state.persona_key,
        history=state.history_turns,
        message=state.message,
        session_id=state.session_id,
        extra_system_context=state.extra_system_context,
    )
    state.response = chat_function(chat_body)


def _persist_turn_messages(state: ChatTurnState, add_message_function, persist_user: bool = True) -> None:
    """Phase 5 — persist the user turn and assistant turn(s) (multi-message aware).

    ``persist_user=False`` (ADR-011 regenerate/continue) skips saving the user
    turn — the caller either kept the original user message (regenerate) or the
    ``message`` is a synthetic, non-stored instruction (continue).
    """
    response = state.response
    now = utc_now_iso()

    if persist_user:
        user_msg_body = AppendMessageBody(role=MessageRole.USER, content=state.message, ts=now, source_type=SourceType.LLM)
        add_message_function(state.session_id, user_msg_body)

    source_type = SourceType.LLM
    if "metadata" in response and response["metadata"]:
        source_type = response["metadata"].get("source_type", SourceType.LLM)

    answer_for_db = response["answer"]
    if isinstance(answer_for_db, list) and len(answer_for_db) > 1:
        multi_msg_id = str(uuid.uuid4())
        logger.debug(f"[Phase2] Storing {len(answer_for_db)} multi-messages separately with ID {multi_msg_id}")

        for idx, msg_content in enumerate(answer_for_db):
            assistant_msg_body = AppendMessageBody(
                role=MessageRole.ASSISTANT,
                content=msg_content,
                ts=now,
                source_type=source_type,
                latency_ms=response.get("latency") if idx == 0 else None,
                multi_message_id=multi_msg_id,
                multi_message_index=idx,
            )
            add_message_function(state.session_id, assistant_msg_body)
    else:
        content = answer_for_db[0] if isinstance(answer_for_db, list) else answer_for_db
        assistant_msg_body = AppendMessageBody(
            role=MessageRole.ASSISTANT,
            content=content,
            ts=now,
            source_type=source_type,
            latency_ms=response.get("latency"),
        )
        add_message_function(state.session_id, assistant_msg_body)

    # For emotional state analysis, use all messages joined (literal separator preserved).
    state.answer_for_emotional_state = (
        "\\n\\n".join(answer_for_db) if isinstance(answer_for_db, list) else answer_for_db
    )


def _apply_post_turn_updates(state: ChatTurnState, deps: ChatDeps) -> None:
    """Phase 6 — emotional-state update, RAG index refresh, fact extraction, progression."""
    from ..llm_client import create_llm_client  # noqa: PLC0415

    response = state.response

    # Update emotional state
    try:
        updated_state = deps.emotional_state_repo.update_from_interaction(
            session_id=state.session_id,
            user_message=state.message,
            assistant_response=state.answer_for_emotional_state,
        )
        response["emotional_state"] = {
            "trust_level": updated_state.trust_level,
            "rapport": updated_state.rapport,
            "current_mood": updated_state.current_mood,
        }
        logger.debug(
            f"[EmotionalState] Updated: trust={updated_state.trust_level:.2f}, "
            f"mood={updated_state.current_mood}"
        )
    except Exception as e:
        logger.warning(f"[EmotionalState] Failed to update emotional state: {e}")

    # PHASE 3: update RAG index and extract/update user profile
    fact_extractor = deps.fact_extractor
    try:
        if deps.episodic_memory_rag:
            all_messages_updated = deps.message_repo.get_messages_by_session(state.session_id)
            # Phase 3 trust-hierarchy: sanitize the user message before it enters
            # long-term memory so a stored tool-call payload cannot fire on a later
            # turn (gated by AGENTIC_INJECTION_GUARD, default ON).
            indexed_user_content = state.message
            if get_settings().agent.injection_guard:
                from .injection_guard import get_injection_guard  # noqa: PLC0415
                indexed_user_content = get_injection_guard().sanitize_memory_write(state.message)
            deps.episodic_memory_rag.update_session(
                session_id=state.session_id,
                new_messages=[
                    {"role": "user", "content": indexed_user_content},
                    {"role": "assistant", "content": response["answer"]},
                ],
                full_history=all_messages_updated,
            )
            logger.debug(f"[Phase3 RAG] Updated vector index for session {state.session_id}")

        # ADR-006 Phase 1 (M3): async ontology-lite fact extraction. Fully OFF the
        # interactive path — enqueue a job and move on; a background worker runs the
        # LLM extraction + recency-wins write. Gated MEMORY_FACTS_ENABLED (default OFF)
        # and batched at the same cadence as the legacy extractor. Requires a linked
        # user_id (a brand-new user's facts wait until their profile is created below).
        _mem = get_settings().memory
        if (deps.fact_extraction_worker and _mem.facts_enabled and state.user_id
                and len(state.db_messages) % _mem.fact_extraction_interval == 0):
            try:
                from ..fact_extraction_worker import ExtractionJob  # noqa: PLC0415
                recent_for_facts = deps.message_repo.get_messages_by_session(state.session_id)[-20:]
                deps.fact_extraction_worker.enqueue(ExtractionJob(
                    user_id=state.user_id, messages=recent_for_facts, session_id=state.session_id
                ))
            except Exception as e:
                logger.debug(f"[FactWorker] enqueue skipped (non-fatal): {e}")

        # Extract facts and update user profile (configurable interval to save compute)
        fact_interval = get_settings().memory.fact_extraction_interval
        if deps.user_profile_repo and len(state.db_messages) % fact_interval == 0:
            try:
                all_messages_updated = deps.message_repo.get_messages_by_session(state.session_id)
                recent_messages = all_messages_updated[-20:]

                if fact_extractor is None:
                    llm_client = create_llm_client(
                        {}, temperature=get_settings().ollama.temp_fact_extraction
                    )
                    from ..fact_extractor import FactExtractor  # noqa: PLC0415
                    fact_extractor = FactExtractor(llm_client)

                facts = fact_extractor.extract_facts(recent_messages, persona_key=state.persona_key)

                if not state.user_id and facts.get("user_name"):
                    state.user_id = deps.user_profile_repo.get_user_by_name(facts["user_name"])

                if not state.user_id:
                    state.user_id = f"user_{uuid.uuid4().hex[:8]}"
                    state.user_profile = deps.user_profile_repo.create_profile(state.user_id)
                    deps.user_profile_repo.link_session_to_user(state.user_id, state.session_id)
                    logger.info(f"[Phase3] Created new user profile: {state.user_id}")
                else:
                    state.user_profile = deps.user_profile_repo.get_or_create_profile(state.user_id)

                state.user_profile.update_from_session(facts)
                deps.user_profile_repo.update_profile(state.user_profile)

                logger.info(
                    f"[Phase3] Updated user profile {state.user_id}: "
                    f"{fact_extractor.get_extraction_stats(facts)}"
                )

            except Exception as e:
                logger.warning(f"[Phase3] Fact extraction/profile update failed: {e}")

    except Exception as e:
        logger.error(f"[Phase3] Post-conversation updates failed: {e}")

    # NEPHILIM Progression System — track conversation progress for NEPHILIM personas
    progression = _track_nephilim_progression(
        session_id=state.session_id,
        persona_key=state.persona_key,
        user_id=state.user_id,
        seeker_progression_repo=deps.seeker_progression_repo,
    ) or {}
    ceremony_data = progression.get("ceremony")
    capability_unlocks = progression.get("capability_unlocks") or []

    # Inject rank ceremony + capability unlocks into response metadata
    if ceremony_data or capability_unlocks:
        if "metadata" not in response or response["metadata"] is None:
            response["metadata"] = {}
        if ceremony_data:
            response["metadata"]["rank_ceremony"] = ceremony_data
        if capability_unlocks:
            response["metadata"]["capability_unlocks"] = capability_unlocks


def handle_session_chat(
    session_id: str,
    message: str,
    deps: dict,
    chat_function,
    add_message_function,
    persist_user: bool = True,
    run_post_turn_updates: bool = True,
):
    """Handle chat with persona using database-backed conversation history.

    Orchestrates the chat turn as an ordered pipeline of phase functions, each
    passed a typed :class:`ChatDeps` and a mutable :class:`ChatTurnState`:

    1. ``_load_session_identity`` — user profile (Phase 3) + emotional state (Phase 2.2)
    2. ``_build_turn_prompt`` — history/summaries + system-prompt assembly + token budget
    3. ``_select_turn_history`` — memory selection + RAG + capped history
    4. ``_generate_turn_response`` — build ChatBody and call the chat endpoint
    5. ``_persist_turn_messages`` — save user + assistant message(s)
    6. auto-summarization, then ``_apply_post_turn_updates`` — emotional/RAG/facts/progression

    The public signature is unchanged (``deps`` is still the route dict); it is
    converted to ``ChatDeps`` internally. ``_check_and_summarize`` continues to
    take the raw dict.

    Raises:
        HTTPException: If session not found (404).
    """
    cdeps = ChatDeps.from_dict(deps)

    persona_key = cdeps.session_repo.get_persona_key(session_id)
    if not persona_key:
        raise HTTPException(status_code=404, detail="Session not found.")

    state = ChatTurnState(session_id=session_id, message=message, persona_key=persona_key)

    _load_session_identity(state, cdeps)
    _build_turn_prompt(state, cdeps)
    _select_turn_history(state, cdeps)
    _generate_turn_response(state, chat_function)
    _persist_turn_messages(state, add_message_function, persist_user=persist_user)

    # ADR-011: regenerate/continue re-run an already-counted exchange, so they
    # skip summarization + emotional/RAG/facts/progression to avoid double-counting.
    if run_post_turn_updates:
        # Auto-summarization check (still takes the raw dependency dict).
        _check_and_summarize(session_id, persona_key, deps)
        _apply_post_turn_updates(state, cdeps)

    return state.response


def _track_nephilim_progression(
    session_id: str,
    persona_key: str,
    user_id: str | None,
    seeker_progression_repo
) -> dict | None:
    """
    Track NEPHILIM progression for conversations with NEPHILIM personas.

    Awards resonance points and tracks message counts for persona affinity.
    Checks and unlocks lore fragments when thresholds are met.

    Returns:
        dict with rank_ceremony data if a rank-up occurred, None otherwise.

    Args:
        session_id: Session identifier
        persona_key: Persona key (checked for nephilim_ prefix)
        user_id: User ID (if known from profile system)
        seeker_progression_repo: Repository for seeker progression
    """
    # Only track for NEPHILIM personas
    if not persona_key or not persona_key.startswith("nephilim_"):
        return None

    if not seeker_progression_repo:
        logger.debug("[NEPHILIM] Progression repo not available, skipping tracking")
        return None

    # Use session_id as user_id if no profile user exists
    effective_user_id = user_id or f"session_{session_id[:16]}"

    try:
        # Ensure seeker exists
        seeker_progression_repo.get_or_create_seeker(effective_user_id)

        # Increment message count for persona affinity
        seeker_progression_repo.increment_messages(
            user_id=effective_user_id,
            persona_key=persona_key,
            count=2  # User message + assistant response
        )

        # Phase-2 fix: deepen affinity_level (+1/exchange after the drive-by gate)
        # so affinity-gated lore can actually unlock. Returns milestone if crossed.
        affinity_result = seeker_progression_repo.increment_affinity(
            user_id=effective_user_id,
            persona_key=persona_key,
            amount=1,
        )

        # Award resonance for the conversation
        # Base resonance: 5 points per exchange
        result = seeker_progression_repo.award_resonance(
            user_id=effective_user_id,
            amount=5,
            reason="Conversation exchange",
            persona_key=persona_key,
            session_id=session_id
        )

        ceremony_data = None
        if result.get("rank_changed"):
            previous_rank = result.get("previous_rank", "Initiate")
            new_rank = result.get("new_rank", "Acolyte")
            logger.info(
                f"[NEPHILIM] Seeker {effective_user_id} ranked up! "
                f"{previous_rank} → {new_rank}"
            )

            # Look up rank ceremony template
            ceremony_key = f"{previous_rank}_to_{new_rank}"
            template = RANK_CEREMONIES.get(ceremony_key)
            if template:
                patron = PERSONA_DISPLAY_NAMES.get(persona_key, "the Nephilim")
                ceremony_data = {
                    "title": template["title"],
                    "speaker": template["speaker"],
                    "monologue": template["monologue"].format(patron=patron),
                    "previous_rank": previous_rank,
                    "new_rank": new_rank,
                }
                logger.info(
                    f"[NEPHILIM] Rank ceremony triggered: {ceremony_key} "
                    f"(patron={patron})"
                )
        else:
            logger.debug(
                f"[NEPHILIM] Awarded 5 resonance to {effective_user_id}, "
                f"total: {result.get('new_resonance')}"
            )

        # Check for lore unlocks
        card = get_persona_card(persona_key)
        if card:
            fragments = card.get("unlockable_lore", [])
            if fragments:
                newly_unlocked = seeker_progression_repo.check_and_unlock_lore(
                    effective_user_id, persona_key, fragments
                )
                if newly_unlocked:
                    for frag in newly_unlocked:
                        logger.info(
                            f"[NEPHILIM] Lore unlocked for {effective_user_id}: "
                            f"'{frag.get('fragment_title')}' ({frag.get('rarity')})"
                        )

        # Phase-2: detect newly-unlocked internal capabilities → diegetic unlock beat
        capability_unlocks = []
        try:
            if seeker_progression_repo:
                from ..lore_retrieval import detect_new_capability_unlocks
                _prof = seeker_progression_repo.get_seeker_profile(effective_user_id) or {}
                _aff = seeker_progression_repo.get_or_create_affinity(effective_user_id, persona_key)
                capability_unlocks = detect_new_capability_unlocks(
                    seeker_progression_repo, effective_user_id, persona_key,
                    _prof.get("rank_name", "Initiate"), _aff.get("affinity_level", 0),
                )
                for cap in capability_unlocks:
                    logger.info(f"[NEPHILIM] Capability awakened for {effective_user_id}: {cap['id']}")
        except Exception as e:
            logger.warning(f"[Capability] unlock detection failed (non-fatal): {e}")

        return {"ceremony": ceremony_data, "capability_unlocks": capability_unlocks}

    except Exception as e:
        logger.warning(f"[NEPHILIM] Progression tracking failed: {e}")
        return {"ceremony": None, "capability_unlocks": []}


def _check_and_summarize(session_id: str, persona_key: str, deps: dict):
    """
    Check if summarization is needed and trigger it if necessary.

    Summarization is triggered when the number of messages since the last summary
    reaches the configured interval (default: 30 messages).

    Args:
        session_id: Session identifier
        persona_key: Persona key for context
        deps: Dictionary of injected dependencies
    """
    cfg = get_settings()

    message_repo = deps["message_repo"]
    summary_repo = deps["summary_repo"]
    conversation_summarizer = deps["conversation_summarizer"]

    try:
        all_messages = message_repo.get_messages_by_session(session_id)
        message_count = len(all_messages)
        summary_count = summary_repo.count_summaries(session_id)
        interval = cfg.memory.summarization_interval
        messages_summarized = summary_count * interval

        # Summaries are cleared alongside messages on reset, so these two
        # should stay consistent. A summary written concurrently with a reset
        # can still leave the count claiming more history than the table holds
        # — which drives the backlog negative and silently suppresses
        # summarization. Clamping keeps start_idx in bounds; the warning is the
        # part that matters, because the old failure was completely silent.
        if messages_summarized > message_count:
            logger.warning(
                f"[Summarizer] Stale summary count for session {session_id}: "
                f"{summary_count} summaries imply {messages_summarized} messages "
                f"but only {message_count} exist. Summarization is suppressed until "
                f"the session passes {messages_summarized + interval} messages."
            )
            messages_summarized = message_count

        messages_since_summary = message_count - messages_summarized

        if messages_since_summary >= interval:
            logger.info(
                f"[Summarizer] Triggering summarization for session {session_id} "
                f"({messages_since_summary} new messages, interval={interval})"
            )

            start_idx = messages_summarized
            end_idx = start_idx + interval
            messages_to_summarize = all_messages[start_idx:end_idx]

            # Set LLM client if not already set
            if not conversation_summarizer.llm_client:
                from ..llm_client import create_llm_client  # noqa: PLC0415
                conversation_summarizer.set_llm_client(
                    create_llm_client({}, temperature=cfg.ollama.temp_summarization)
                )

            card = get_persona_card(persona_key)
            persona_name = card.get("display_name", persona_key)

            summary_result = conversation_summarizer.summarize_segment(
                messages=messages_to_summarize,
                max_summary_tokens=200,
                persona_name=persona_name
            )

            message_range = f"{start_idx + 1}-{end_idx}"
            summary_repo.create_summary(
                session_id=session_id,
                message_range=message_range,
                summary_text=summary_result["summary_text"],
                emotional_developments=summary_result["emotional_developments"],
                topics_discussed=summary_result["topics_discussed"]
            )

            logger.info(
                f"[Summarizer] Created summary for messages {message_range} "
                f"({summary_result['token_count']} tokens)"
            )

    except Exception as e:
        logger.error(f"[Summarizer] Failed to create summary: {e}", exc_info=True)
