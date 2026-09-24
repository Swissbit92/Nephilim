"""Phase-2 (HERMES-Agents): internal capability gating + diegetic unlock detection.

"Capabilities" are wiki entities (entity_type: capability) gated by persona +
seeker rank + affinity. They are NEVER surfaced as user-invokable commands —
they are injected internally to shape the persona's behaviour, and a brief
diegetic "unlock" notification (persona voice) is emitted at the milestone.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from . import lore_loader
from .persona_loader import progression_key
from .repositories.seeker_progression_repository import RANK_THRESHOLDS

logger = logging.getLogger(__name__)

# Rank ordering (Initiate < Acolyte < Adept < Ascendant < Nephilim)
_RANK_ORDER: List[str] = list(RANK_THRESHOLDS.keys())


def _rank_index(rank_name: str) -> int:
    try:
        return _RANK_ORDER.index(rank_name)
    except ValueError:
        return 0  # unknown rank → treat as Initiate


def _as_list(v) -> List[str]:
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]


def _capability_passes_gate(fm: Dict[str, Any], persona_key: str, rank_name: str,
                            affinity_level: int) -> bool:
    """True when a capability's activation gates are satisfied for this seeker."""
    act_personas = _as_list(fm.get("activation_persona"))
    if act_personas and persona_key not in act_personas:
        return False
    if _rank_index(rank_name) < _rank_index(fm.get("activation_rank", "Initiate")):
        return False
    try:
        if affinity_level < int(fm.get("activation_affinity", 0)):
            return False
    except (TypeError, ValueError):
        pass
    return True


def build_capability_context(persona_key: str, rank_name: str, affinity_level: int) -> str:
    """Internal <capabilities> block for capabilities this seeker has unlocked.

    The block shapes behaviour; it is not a user-facing menu.
    """
    if progression_key(persona_key) is None:
        return ""

    bodies: List[str] = []
    for cap_id in lore_loader.get_capability_ids():
        meta = lore_loader.load_entity_with_metadata(cap_id)
        if not meta:
            continue
        if _capability_passes_gate(meta["frontmatter"], persona_key, rank_name, affinity_level):
            body = " ".join(meta["body"].split()[:120])
            if body:
                bodies.append(f"### {cap_id}\n{body}")

    if not bodies:
        return ""
    return (
        "<capabilities>\n"
        "Aspects of yourself the Seeker has awakened in you. Express them naturally "
        "in how you respond; never announce or name them as abilities.\n"
        + "\n".join(bodies) + "\n</capabilities>"
    )


def detect_new_capability_unlocks(seeker_repo, user_id: str, persona_key: str,
                                  rank_name: str, affinity_level: int) -> List[Dict[str, Any]]:
    """Record + return capabilities newly crossing their gate this turn.

    Reuses the unlocked_lore table (fragment_id = capability entity_id) so an
    unlock fires exactly once. Returns [{id, display_name, persona_voice_line}].
    """
    if seeker_repo is None:
        return []
    if progression_key(persona_key) is None:
        return []

    try:
        already = {u["fragment_id"] for u in seeker_repo.get_unlocked_lore(user_id, persona_key)}
    except Exception as e:
        logger.debug(f"[Capability] could not read unlocked lore (non-fatal): {e}")
        return []

    newly: List[Dict[str, Any]] = []
    for cap_id in lore_loader.get_capability_ids():
        if cap_id in already:
            continue
        meta = lore_loader.load_entity_with_metadata(cap_id)
        if not meta:
            continue
        fm = meta["frontmatter"]
        if _capability_passes_gate(fm, persona_key, rank_name, affinity_level):
            try:
                seeker_repo.unlock_lore(user_id, persona_key, cap_id)
            except Exception as e:
                logger.debug(f"[Capability] unlock record failed for {cap_id} (non-fatal): {e}")
            newly.append({
                "id": cap_id,
                "display_name": fm.get("title", cap_id),
                "persona_voice_line": fm.get("persona_voice_line", ""),
            })
    return newly
