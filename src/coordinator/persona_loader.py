# src/coordinator/persona_loader.py
# Persona file loading and validation with Pydantic schema support.
# Part of modular refactor from persona_memory.py.

from __future__ import annotations

import logging
import os

from .config import get_settings
from .models.persona_schema import load_persona_card_lenient

# Setup logger
logger = logging.getLogger(__name__)


# ---------------- Persona discovery ----------------

def _iter_persona_files() -> list[str]:
    """Return absolute paths to all *.json in PERSONA_DIR (sorted, stable)."""
    pdir = get_settings().persona_dir
    try:
        files = [os.path.join(pdir, f) for f in os.listdir(pdir) if f.endswith(".json")]
    except FileNotFoundError:
        files = []
    return sorted(files, key=lambda s: os.path.basename(s).lower())


def _load_card_file(path: str) -> dict | None:
    """Load and validate a persona card from JSON file.

    Uses Pydantic validation in lenient mode (warnings, not failures) for
    backward compatibility during migration to typed schemas.

    Args:
        path: Path to persona JSON file

    Returns:
        Validated persona dict, or None if file cannot be loaded
    """
    # Use lenient validation - logs warnings but returns dict for compatibility
    card = load_persona_card_lenient(path)

    if card is None:
        return None

    # Ensure key exists (backward compatibility)
    if "key" not in card or not isinstance(card["key"], str) or not card["key"].strip():
        stem = os.path.splitext(os.path.basename(path))[0]
        card["key"] = stem.capitalize()
        logger.debug(f"Auto-generated key '{card['key']}' for persona at {path}")

    return card


def _load_all_cards_cached() -> list[dict]:
    """Load all persona cards with caching."""
    cards: list[dict] = []
    for fp in _iter_persona_files():
        card = _load_card_file(fp)
        if card:
            cards.append(card)
    return cards


def _cards_by_all_names() -> dict[str, dict]:
    """Build index mapping all persona name variants to their cards."""
    idx: dict[str, dict] = {}
    for c in _load_all_cards_cached():
        cand = set()
        for field in ("coordinator_label", "display_name", "key"):
            v = c.get(field)
            if isinstance(v, str) and v.strip():
                cand.add(v.strip())
                cand.add(v.strip().lower())
        for k in cand:
            idx[k] = c
    return idx


def resolve_persona_to_card(selector: str | None) -> dict | None:
    """Resolve persona selector to a card.

    Args:
        selector: Persona key/name, or None for default

    Returns:
        Persona card dict, or None if no personas exist
    """
    cards = _load_all_cards_cached()
    if not cards:
        return None
    if not selector:
        return cards[0]
    idx = _cards_by_all_names()
    hit = idx.get(selector) or idx.get(selector.lower())
    return hit or cards[0]


def resolve_persona_strict(selector: str | None) -> dict | None:
    """Resolve a selector to its card, or None when nothing matches it.

    The difference from ``resolve_persona_to_card`` is the whole point: that one
    falls back to ``cards[0]`` on a miss, so an unknown name silently becomes
    whichever persona sorts first on disk. That is the right call for *rendering*
    a chat — the user gets a working companion instead of a 500 — and the wrong
    call for anything that records data under a persona's name, because the
    record then belongs to a persona nobody selected.

    Measured on the live DB (2026-09-24): ``nephilim_gojo`` matches no card, yet
    carries 2 ``persona_affinity`` rows and 1 ``resonance_log`` row. It reached
    those tables by passing a ``startswith("nephilim_")`` gate on the SELECTOR
    while the fallback quietly supplied Gojo's card.
    """
    if not selector:
        return None
    idx = _cards_by_all_names()
    return idx.get(selector) or idx.get(selector.lower())


def progression_participant(card: dict | None) -> bool:
    """Whether this persona takes part in the NEPHILIM progression system.

    Progression means resonance, affinity, lore unlocks and capability unlocks —
    the things that accumulate in the DB per (user, persona).

    A card may declare ``progression: true`` / ``false``; absent or non-boolean,
    the historical rule applies and the card's KEY (never the selector) is tested
    for the ``nephilim_`` prefix. No shipped card declares it, so this is
    behaviour-preserving for every persona today.

    Deliberately NOT the same question as realm immersion. ``_lean_world_block``
    decides separately whether to tell a persona she is a Fallen Nephilim in the
    Realm who addresses the user as "Seeker", and it already has its own
    card-driven override (``nephilim_lore``). Collapsing the two would mean a
    persona could not accrue affinity without also being handed an origin story —
    which for gwen would contradict a rule her own card states.
    """
    if not card:
        return False
    declared = card.get("progression")
    if isinstance(declared, bool):
        return declared
    key = card.get("key")
    return isinstance(key, str) and key.startswith("nephilim_")


def progression_key(selector: str | None) -> str | None:
    """The canonical name to record progression under, or None for no progression.

    One function so the gates that each re-asked
    ``persona_key.startswith("nephilim_")`` now agree, and so the answer is a
    persona rather than a spelling.

    Why a spelling was the wrong unit: ``_cards_by_all_names`` accepts
    ``coordinator_label``, ``display_name`` and ``key``, each cased and lowercased
    — 40 selectors for 8 cards. Exactly ONE per persona starts with
    ``nephilim_``, so a client naming Nyx "Nyx — The Muse" got a correct
    conversation and silently zero progression; and had it instead written a row,
    that row would have been a second, separate Nyx.

    Idempotent by construction — a card key is itself an accepted selector — which
    matters because the result is stored, read back, and passed down a call chain
    that re-gates on it.
    """
    card = resolve_persona_strict(selector)
    if not progression_participant(card):
        return None
    return card["key"]


def get_persona_card(selector: str | None) -> dict:
    """Get persona card with fallback to default.

    Args:
        selector: Persona key/name

    Returns:
        Persona card dict (never None, falls back to default)
    """
    card = resolve_persona_to_card(selector)
    return card or {
        "key": "Persona",
        "display_name": "Persona — Helpful",
        "style": "helpful & concise"
    }
