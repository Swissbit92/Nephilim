# src/coordinator/schemas.py
"""Pydantic models for API request/response schemas."""

from __future__ import annotations

from enum import StrEnum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# ----------------- Controlled vocabularies -----------------

class SourceType(StrEnum):
    """Where a response came from — the `source_type` on ResponseMetadata / messages.

    `StrEnum` (not the older `(str, Enum)` idiom used elsewhere) so members render
    as their value in f-strings / logs, not `SourceType.LLM`. Members ARE `str`, so
    they compare equal to the raw strings and serialize/store as the value — the
    `source_type` fields stay typed `str` (permissive: this vocabulary evolves), and
    these are used as named constants at the assignment/comparison sites.
    """

    LLM = "llm"
    BRAVE_MCP = "brave_mcp"
    WALLET_MCP = "wallet_mcp"
    WALLET_FLOW = "wallet_flow"
    GROUNDEDNESS_ABSTAIN = "groundedness_abstain"
    WALLET_PROPOSAL = "wallet_proposal"
    # ADR-008: single-model native tool-brain loop. (The ADR-004 two-stage
    # pipeline's `agentic`/`agentic_blocked`/`agentic_hitl` members were removed
    # with that pipeline — verified 0 rows carried them, since AGENTIC_ENABLED
    # was never flipped on in production.)
    TOOL_BRAIN = "tool_brain"


class MessageRole(StrEnum):
    """Conversation message role (the `role` on ChatTurn / messages).

    Fields stay typed `str`; these are named constants at the persistence-layer
    construction sites (members ARE `str`, so stored/compared values are unchanged).
    """

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    # ADR-011 /sys — an in-world narrator/scene beat (not user dialogue). Rendered
    # to the model as bracketed scene direction; role-switch sites must tolerate it.
    NARRATOR = "narrator"
    # A message resurfaced by semantic search from earlier in the session, not a
    # recent turn. RENDER-TIME ONLY — never persisted; the stored row keeps its
    # original user/assistant role. Without this the retrieved text arrived
    # formatted identically to "what I said one turn ago", and the model
    # continued it verbatim instead of treating it as background.
    RECALLED = "recalled"


class ProposalType(StrEnum):
    """The on-chain ACTION a proposal card represents (the `proposal_type` inside
    a proposal object, and the `trade_proposals.proposal_type` column)."""

    SWAP = "swap"
    STRATEGY = "strategy"
    WALLET_DELETION = "wallet_deletion"


class ProposalCategory(StrEnum):
    """The response-metadata proposal CATEGORY (`ResponseMetadata.proposal_type`,
    consumed by the frontend to route/render the card). Distinct vocabulary from
    :class:`ProposalType` — do not conflate the two."""

    TRADE_PROPOSAL = "trade_proposal"
    STRATEGY_PROPOSAL = "strategy_proposal"
    WALLET_DELETION = "wallet_deletion"


# ----------------- Chat Schemas -----------------

class ChatTurn(BaseModel):
    """A single turn in a conversation."""
    role: str
    content: str


# Upper bound on conversation turns carried in a single chat request. Single
# source of truth: the ChatBody schema guard AND the server-side history assembly
# (chat_session_service.handle_session_chat) both use this, so token-budget message
# selection can never produce a history that the schema then rejects. (Before this
# was shared, long sessions >100 messages 500'd at internal ChatBody construction.)
MAX_HISTORY_TURNS = 100


class ChatBody(BaseModel):
    """Request body for chat endpoint."""
    persona: Optional[str] = None
    history: List[ChatTurn] = Field(default=[], max_length=MAX_HISTORY_TURNS)
    message: str = Field(..., max_length=10_000)
    session_id: Optional[str] = None  # Set by handle_session_chat for wallet flow continuity
    # ADR-006 M0: internal only — set by handle_session_chat to carry the assembled
    # session context blocks (user profile, emotional state, lore, rank, capability)
    # through to chat() so they reach the LLM system prompt. Not part of the public
    # API contract; external callers leave it None.
    extra_system_context: Optional[str] = None


class GreetBody(BaseModel):
    """Request body for greeting endpoint."""
    persona: Optional[str] = None


class NoteBody(BaseModel):
    """Request body for setting a per-session author's note (ADR-011 /note)."""
    note: str = Field(..., max_length=2_000)


class NarrateBody(BaseModel):
    """Request body for a narrator/scene beat (ADR-011 /sys)."""
    text: str = Field(..., max_length=2_000)


class ImpersonateBody(BaseModel):
    """Request body for drafting the user's next line (ADR-011 /impersonate)."""
    hint: Optional[str] = Field(default=None, max_length=500)


class SummaryBody(BaseModel):
    """Request body for persona summary endpoint."""
    persona: Optional[str] = None  # label/key; None resolves to first card


# ----------------- Session Schemas -----------------

class CreateSessionBody(BaseModel):
    """Request body for creating a new session."""
    persona_key: str
    title: str = "New Chat"


class UpdateSessionBody(BaseModel):
    """Request body for updating a session."""
    title: str


class AppendMessageBody(BaseModel):
    """Request body for appending a message to a session."""
    role: str
    content: str
    ts: Optional[str] = None
    latency_ms: Optional[int] = None
    source_type: str = SourceType.LLM
    multi_message_id: Optional[str] = None
    multi_message_index: Optional[int] = None


class MessageModel(BaseModel):
    """A message in a session."""
    id: str
    role: str
    content: str
    timestamp: str
    latency_ms: Optional[int] = None
    source_type: str = SourceType.LLM


class SessionModel(BaseModel):
    """A chat session."""
    id: str
    persona_key: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0


class SessionWithMessages(BaseModel):
    """A session with its messages."""
    session: SessionModel
    messages: List[MessageModel]


# ----------------- Export/Import Schemas -----------------

class ExportData(BaseModel):
    """Exported session data structure."""
    version: str = "1.0"
    exported_at: str
    app_version: str = "1.0.0"
    persona: Dict[str, Any]
    session: Dict[str, Any]
    messages: List[Dict[str, Any]]


class ImportBody(BaseModel):
    """Request body for importing a session."""
    data: ExportData
    create_new_session: bool = True


class ImportChatBody(BaseModel):
    """Legacy: Import a chat."""
    persona: str
    chat: Dict[str, Any] = Field(..., description="JSON with {title, messages: [{role,content,ts?}]}")


# ----------------- Response Metadata -----------------

class ResponseMetadata(BaseModel):
    """Metadata about the response source."""
    source_type: str = SourceType.LLM  # see SourceType (values: llm, brave_mcp, wallet_*, agentic*, …)
    tools_used: List[str] = []
    cache_status: Optional[str] = None  # "hit", "miss", None
    data_timestamp: Optional[str] = None
    latency_breakdown: Optional[Dict[str, int]] = None  # {"llm": 3000, "brave": 500}
    # PHASE 2: Multi-message response fields
    is_multi_message: bool = False
    message_count: int = 1
    # WALLET: Proposal card injection
    proposal_type: Optional[str] = None  # "trade_proposal", "strategy_proposal", "wallet_deletion"
    proposal: Optional[Dict] = None


# ----------------- NEPHILIM Progression Schemas -----------------

class SetFactionBody(BaseModel):
    """Request body for setting seeker faction."""
    faction_primary: str
    faction_secondary: Optional[str] = None


class AwardResonanceBody(BaseModel):
    """Request body for awarding resonance."""
    amount: int
    reason: str
    persona_key: Optional[str] = None
    session_id: Optional[str] = None


class SeekerProfileResponse(BaseModel):
    """Response containing seeker profile data."""
    user_id: str
    rank_name: str
    total_resonance: int
    faction_primary: Optional[str] = None
    faction_secondary: Optional[str] = None
    rank_achieved_at: Optional[str] = None
    created_at: str
    updated_at: str


class RankProgressResponse(BaseModel):
    """Response containing rank progress info."""
    current_rank: str
    current_resonance: int
    next_rank: Optional[str] = None
    resonance_needed: int
    progress_percent: int


class PersonaAffinityResponse(BaseModel):
    """Response containing persona affinity data."""
    user_id: str
    persona_key: str
    messages_count: int
    affinity_level: int
    first_conversation: Optional[str] = None
    last_conversation: Optional[str] = None


class UnlockedLoreResponse(BaseModel):
    """Response containing unlocked lore fragment info."""
    id: int
    user_id: str
    persona_key: str
    fragment_id: str
    unlocked_at: str


class LoreFragmentContent(BaseModel):
    """Full lore fragment with content."""
    fragment_id: str
    fragment_title: str
    fragment: str
    messages_required: int
    rarity: str
    unlocked: bool
    unlocked_at: Optional[str] = None


class SeekerSummaryResponse(BaseModel):
    """Comprehensive seeker summary response."""
    exists: bool
    user_id: str
    rank: Optional[str] = None
    total_resonance: Optional[int] = None
    faction_primary: Optional[str] = None
    faction_secondary: Optional[str] = None
    rank_progress: Optional[RankProgressResponse] = None
    persona_affinities: List[PersonaAffinityResponse] = []
    unlocked_lore_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
