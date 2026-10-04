# src/coordinator/di/repositories.py
"""Repository cluster: the 12 SQLite repositories + DB init + orphan cleanup.

Split out of ``startup.py`` (2026-08-22 decomposition, audit rec #7). Owns the
DB path, the Alembic migration bootstrap, and every repository singleton.
Moved verbatim — no behavior change; see ``startup.py`` for the re-export
contract that keeps ``startup.get_X`` / ``startup.init_X`` working unchanged.
"""

from __future__ import annotations

import logging
import os

from ..persona_memory import _load_all_cards_cached
from ..repositories.emotional_state_repository import EmotionalStateRepository
from ..repositories.image_job_repository import ImageJobRepository
from ..repositories.media_repository import MediaRepository
from ..repositories.message_repository import MessageRepository
from ..repositories.seeker_progression_repository import SeekerProgressionRepository
from ..repositories.session_note_repository import SessionNoteRepository
from ..repositories.session_repository import SessionRepository
from ..repositories.summary_repository import SummaryRepository
from ..repositories.trade_history_repository import TradeHistoryRepository
from ..repositories.user_profile_repository import UserProfileRepository
from ..repositories.user_repository import UserRepository
from ..repositories.wallet_flow_repository import WalletFlowRepository
from ..repositories.wallet_registry_repository import WalletRegistryRepository
from ..repositories.wallet_summary_repository import WalletSummaryRepository

logger = logging.getLogger(__name__)

# ----------------- Global State -----------------
_DB_PATH = os.environ.get("COORDINATOR_DB_PATH", "data/chats.db")
os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True) if os.path.dirname(_DB_PATH) else None

_session_repo: SessionRepository | None = None
_message_repo: MessageRepository | None = None
_session_note_repo: SessionNoteRepository | None = None
_media_repo: MediaRepository | None = None
_image_job_repo: ImageJobRepository | None = None
_summary_repo: SummaryRepository | None = None
_emotional_state_repo: EmotionalStateRepository | None = None
_user_profile_repo: UserProfileRepository | None = None
_seeker_progression_repo: SeekerProgressionRepository | None = None
_user_repo: UserRepository | None = None

# Wallet Metadata Layer
_wallet_registry_repo: WalletRegistryRepository | None = None
_wallet_summary_repo: WalletSummaryRepository | None = None
_wallet_flow_repo: WalletFlowRepository | None = None
_trade_history_repo: TradeHistoryRepository | None = None


# ----------------- Getters -----------------

def get_session_repo() -> SessionRepository:
    """Get the session repository."""
    if _session_repo is None:
        raise RuntimeError("SessionRepository not initialized — server startup incomplete")
    return _session_repo


def get_media_repo() -> MediaRepository:
    """Return the per-session media-directory repo (image transport, phase 1)."""
    if _media_repo is None:
        raise RuntimeError("MediaRepository not initialized — server startup incomplete")
    return _media_repo


def get_image_job_repo():
    """Return the image-generation job repo (M3).

    Raises rather than lazily constructing, matching every other repo here:
    a repo built outside init_repositories would point at a default DB path
    and write to a different file than the rest of the app.
    """
    if _image_job_repo is None:
        raise RuntimeError("ImageJobRepository not initialized — server startup incomplete")
    return _image_job_repo


def get_session_note_repo() -> SessionNoteRepository:
    """Return the per-session author's-note repo (ADR-011)."""
    if _session_note_repo is None:
        raise RuntimeError("SessionNoteRepository not initialized — server startup incomplete")
    return _session_note_repo


def get_message_repo() -> MessageRepository:
    """Get the message repository."""
    if _message_repo is None:
        raise RuntimeError("MessageRepository not initialized — server startup incomplete")
    return _message_repo


def get_summary_repo() -> SummaryRepository:
    """Get the summary repository."""
    if _summary_repo is None:
        raise RuntimeError("SummaryRepository not initialized — server startup incomplete")
    return _summary_repo


def get_emotional_state_repo() -> EmotionalStateRepository:
    """Get the emotional state repository."""
    if _emotional_state_repo is None:
        raise RuntimeError("EmotionalStateRepository not initialized — server startup incomplete")
    return _emotional_state_repo


def get_user_profile_repo() -> UserProfileRepository:
    """Get the user profile repository."""
    if _user_profile_repo is None:
        raise RuntimeError("UserProfileRepository not initialized — server startup incomplete")
    return _user_profile_repo


def get_user_repo() -> UserRepository:
    """Get the OAuth user repository."""
    if _user_repo is None:
        raise RuntimeError("UserRepository not initialized — server startup incomplete")
    return _user_repo


def get_seeker_progression_repo() -> SeekerProgressionRepository:
    """Get the NEPHILIM seeker progression repository."""
    if _seeker_progression_repo is None:
        raise RuntimeError("SeekerProgressionRepository not initialized — server startup incomplete")
    return _seeker_progression_repo


def get_wallet_registry_repo() -> WalletRegistryRepository | None:
    """Get the wallet registry repository."""
    return _wallet_registry_repo


def get_wallet_summary_repo() -> WalletSummaryRepository | None:
    """Get the wallet summary repository."""
    return _wallet_summary_repo


def get_wallet_flow_repo() -> WalletFlowRepository | None:
    """Get the guided wallet-creation flow-state repository."""
    return _wallet_flow_repo


def get_trade_history_repo() -> TradeHistoryRepository | None:
    """Get the trade history repository."""
    return _trade_history_repo


# ----------------- Initialization Functions -----------------

def init_repositories():
    """Initialize database repositories."""
    global _session_repo, _message_repo, _summary_repo, _emotional_state_repo
    global _user_profile_repo, _seeker_progression_repo, _user_repo
    global _wallet_registry_repo, _wallet_summary_repo, _trade_history_repo
    global _wallet_flow_repo, _session_note_repo, _media_repo
    global _image_job_repo

    _session_repo = SessionRepository(_DB_PATH)
    _message_repo = MessageRepository(_DB_PATH)
    _session_note_repo = SessionNoteRepository(_DB_PATH)
    _media_repo = MediaRepository(_DB_PATH)
    _image_job_repo = ImageJobRepository(_DB_PATH)
    _summary_repo = SummaryRepository(_DB_PATH)
    _emotional_state_repo = EmotionalStateRepository(_DB_PATH)
    _user_profile_repo = UserProfileRepository(_DB_PATH)
    _seeker_progression_repo = SeekerProgressionRepository(_DB_PATH)
    _user_repo = UserRepository(_DB_PATH)
    _user_repo._ensure_tables()

    # Wallet Metadata Layer
    _wallet_registry_repo = WalletRegistryRepository(_DB_PATH)
    _wallet_summary_repo = WalletSummaryRepository(_DB_PATH)
    _trade_history_repo = TradeHistoryRepository(_DB_PATH)
    _wallet_flow_repo = WalletFlowRepository(_DB_PATH)

    # Reset all wallet unlock states on startup (wallets are locked until user provides password)
    _wallet_summary_repo.reset_all_unlock_states()
    # Sweep any wallet-creation flows abandoned before a restart
    _wallet_flow_repo.sweep_stale()

    logger.info("Repositories initialized (Phase 1-3 + NEPHILIM Progression + OAuth + Wallet Metadata)")


def init_db():
    """Initialize database using Alembic migrations (if available), otherwise repositories auto-initialize schema."""
    try:
        from alembic.config import Config

        from alembic import command

        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{_DB_PATH}")

        # Run migrations to latest version
        command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations applied successfully")

    except ImportError:
        # Alembic not installed - repositories will auto-initialize schema when first used
        logger.info("Alembic not available, repositories will auto-initialize database schema")

    except Exception as e:
        # Alembic config or migration files not found (e.g., Docker without alembic dir)
        # Repositories with _ensure_tables() will self-initialize schema
        logger.warning(f"Alembic migration skipped ({e}), repositories will auto-initialize schema")

    # Users table creation is handled by UserRepository._ensure_tables() in init_repositories()


def cleanup_orphaned_sessions():
    """Remove chat sessions for personas that no longer exist."""
    if _session_repo is None:
        logger.debug("cleanup_orphaned_sessions skipped: repo not yet initialized")
        return
    try:
        cards = _load_all_cards_cached()
        current_persona_keys = {card.get("key") for card in cards if card.get("key")}

        all_sessions = _session_repo.get_all_sessions()

        orphaned_persona_keys = []
        for session in all_sessions:
            persona_key = session["persona_key"]
            if persona_key not in current_persona_keys:
                orphaned_persona_keys.append(persona_key)

        if orphaned_persona_keys:
            orphaned_personas = list(set(orphaned_persona_keys))
            deleted_count = _session_repo.delete_sessions_by_persona(orphaned_personas)
            logger.info(f"Cleaned up {deleted_count} orphaned sessions for removed personas: {orphaned_personas}")

    except Exception as e:
        logger.warning(f"Failed to cleanup orphaned sessions: {e}")
