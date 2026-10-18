"""User profile management for cross-session memory.

This module enables personas to remember users across multiple chat sessions,
creating continuity and deeper relationships over time.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


class UserProfile:
    """Persistent user profile across chat sessions.

    Stores and aggregates user information from multiple conversations,
    enabling personas to:
    - Remember the user's name
    - Recall past discussions and topics
    - Track preferences and background
    - Maintain relationship context

    The profile is stored as JSON in the database for flexibility.
    """

    def __init__(self, user_id: str, profile_data: Optional[Dict[str, Any]] = None):
        """Initialize user profile.

        Args:
            user_id: Unique identifier for the user
            profile_data: Existing profile data (if loading from database)
        """
        self.user_id = user_id

        if profile_data:
            self.data = profile_data
        else:
            # Initialize empty profile
            self.data = {
                "name": None,
                "background": [],
                "preferences": {},
                "holdings": {},
                "topics_discussed": {},
                "facts": [],
                "personas_met": [],
                "total_sessions": 0,
                "total_messages": 0,
                "first_interaction": None,
                "last_updated": None
            }

    @classmethod
    def from_json(cls, user_id: str, json_str: str) -> UserProfile:
        """Create profile from JSON string.

        Args:
            user_id: User identifier
            json_str: JSON string from database

        Returns:
            UserProfile instance
        """
        try:
            profile_data = json.loads(json_str)
            return cls(user_id, profile_data)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse user profile JSON for {user_id}: {e}")
            return cls(user_id)

    def to_json(self) -> str:
        """Convert profile to JSON string for storage.

        Returns:
            JSON string representation
        """
        return json.dumps(self.data, indent=2)

    def update_from_session(self, session_summary: Dict[str, Any]) -> None:
        """Update profile with facts from a chat session.

        Aggregates information learned during a conversation into the
        persistent user profile.

        Args:
            session_summary: Dictionary with extracted session facts:
                - user_name: str
                - background: List[str]
                - topics: List[str]
                - facts: List[str]
                - preferences: Dict[str, str]
                - persona_key: str
                - message_count: int
        """
        # Update name (first valid name wins)
        if session_summary.get("user_name") and not self.data["name"]:
            self.data["name"] = session_summary["user_name"]
            logger.info(f"[UserProfile] Learned user name: {self.data['name']}")

        # Add background information (deduplicated)
        if session_summary.get("background"):
            existing_background = set(self.data["background"])
            for item in session_summary["background"]:
                if item not in existing_background:
                    self.data["background"].append(item)
                    logger.debug(f"[UserProfile] Added background: {item[:50]}...")

        # Update topic counts
        for topic in session_summary.get("topics", []):
            count = self.data["topics_discussed"].get(topic, 0)
            self.data["topics_discussed"][topic] = count + 1

        # Add new facts (deduplicated)
        existing_facts = set(self.data["facts"])
        for fact in session_summary.get("facts", []):
            if fact not in existing_facts:
                self.data["facts"].append(fact)
                logger.debug(f"[UserProfile] Added fact: {fact[:50]}...")

        # Update preferences
        if session_summary.get("preferences"):
            self.data["preferences"].update(session_summary["preferences"])

        # Update holdings (cryptocurrency balances, etc.)
        if session_summary.get("holdings"):
            self.data["holdings"].update(session_summary["holdings"])

        # Track personas met
        persona_key = session_summary.get("persona_key")
        if persona_key and persona_key not in self.data["personas_met"]:
            self.data["personas_met"].append(persona_key)

        # Update session statistics
        self.data["total_sessions"] += 1
        self.data["total_messages"] += session_summary.get("message_count", 0)

        # Update timestamps
        if not self.data["first_interaction"]:
            self.data["first_interaction"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        self.data["last_updated"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        logger.info(
            f"[UserProfile] Updated profile for {self.user_id}: "
            f"{len(self.data['facts'])} facts, "
            f"{len(self.data['topics_discussed'])} topics, "
            f"{self.data['total_sessions']} sessions"
        )

    def get_context_summary(self, max_facts: int = 10, max_topics: int = 5) -> str:
        """Generate context summary for system prompt injection.

        Creates a concise summary of what the persona knows about this user
        from previous interactions.

        Args:
            max_facts: Maximum facts to include
            max_topics: Maximum topics to list

        Returns:
            Formatted context string for system prompt
        """
        if not any([self.data["name"], self.data["background"], self.data["facts"]]):
            return ""

        summary_parts = ["**Your history with this user:**\n"]

        # User's name
        if self.data["name"]:
            summary_parts.append(f"- User's name: {self.data['name']}")

        # Relationship context
        if self.data["total_sessions"] > 0:
            summary_parts.append(
                f"- You've chatted {self.data['total_sessions']} times "
                f"({self.data['total_messages']} messages total)"
            )

        # Top topics discussed
        if self.data["topics_discussed"]:
            top_topics = sorted(
                self.data["topics_discussed"].items(),
                key=lambda x: x[1],
                reverse=True
            )[:max_topics]
            topics_str = ", ".join([f"{topic} ({count}x)" for topic, count in top_topics])
            summary_parts.append(f"- Topics discussed: {topics_str}")

        # Background information
        if self.data["background"]:
            summary_parts.append("\n**What you know about them:**")
            for bg_item in self.data["background"][:5]:  # Limit to 5 items
                summary_parts.append(f"- {bg_item}")

        # Recent facts
        if self.data["facts"]:
            summary_parts.append("\n**Key facts to remember:**")
            recent_facts = self.data["facts"][-max_facts:]  # Most recent facts
            for fact in recent_facts:
                summary_parts.append(f"- {fact}")

        # Holdings (cryptocurrency, etc.)
        if self.data["holdings"]:
            summary_parts.append("\n**Their holdings:**")
            for asset, amount in self.data["holdings"].items():
                summary_parts.append(f"- {asset}: {amount}")

        # Preferences
        if self.data["preferences"]:
            summary_parts.append("\n**Preferences:**")
            for pref_key, pref_value in list(self.data["preferences"].items())[:3]:
                summary_parts.append(f"- {pref_key}: {pref_value}")

        return "\n".join(summary_parts)

    def get_recall_facts(self, max_facts: int = 32) -> List[str]:
        """DISCRETE facts for the ADR-022 numbered recall frame.

        The sibling of ``get_narrative_context``, and deliberately NOT built from
        it: the numbered frame needs one fact per line so the model can do a
        lookup, and a prose narrative has already welded them into sentences that
        cannot be split back apart. Returns [] when there is nothing to recall, so
        ``frame_numbered_facts`` injects nothing and the dormant path stays
        byte-identical.

        ``name`` is deliberately omitted — it is already in the prompt by every
        other route, and spending numbered slot [1] on it displaces a real fact.

        ⚠️ ``background`` is emitted BEFORE ``facts``. The curation pass
        (2026-10-18) found background the higher-signal store of the two — 16
        clean entries against 277 facts of which a hand pass admitted 32 — and
        the cap truncates from the END.
        """
        out: List[str] = []
        seen: set[str] = set()
        for source in (self.data.get("background") or [], self.data.get("facts") or []):
            for item in source:
                text = str(item).strip()
                key = text.lower()
                if text and key not in seen:
                    seen.add(key)
                    out.append(text)

        # 🔴 TRUNCATION IS LOUD, because silent truncation here FREEZES her.
        # The cap keeps the OLDEST entries, so once the store reaches it she can
        # never be shown anything new — which is the opposite of the point.
        # Measured 2026-10-18, hours after the curated merge wrote exactly 32:
        # the extractor appended two more and both were cut off. They happened to
        # be paraphrases of answers she had just RECITED (she said "eggs benedict
        # and apple juice", the extractor read her own reply and stored "User eats
        # eggs benedict and apple juice for breakfast"), so nothing was lost that
        # time. A genuinely new fact would have been lost identically and just as
        # quietly.
        #
        # ⚠️ Raising the cap only moves the freeze. The real fix is a WRITE
        # policy — ADR-021 Phase 0's `asserted_by`, set from the SQLite role
        # column so her own recited output cannot re-enter as a user fact — and
        # that is deliberately deferred, so this warning is the interim guard
        # rather than a new magic number chosen here.
        if len(out) > max_facts:
            logger.warning(
                "[RecallFacts] %d facts stored, cap is %d — %d NOT shown. The cap "
                "keeps the oldest, so new facts are being dropped. See ADR-021 "
                "Phase 0 (write policy / asserted_by).",
                len(out), max_facts, len(out) - max_facts,
            )
        return out[:max_facts]

    def get_narrative_context(self, max_facts: int = 10, max_topics: int = 5) -> str:
        """Prose variant of ``get_context_summary`` for framed injection (ADR-006 P1).

        Same underlying facts, but rendered as flowing sentences instead of the
        ``**Header**\\n- field: value`` skeleton that Gate 0.1 showed homogenizes
        persona voice. The persona-neutral content is wrapped per-persona by
        ``context_framing.frame_injected_context``; here we only drop the imitable
        shape. Returns "" when there is nothing worth remembering.
        """
        if not any([self.data["name"], self.data["background"], self.data["facts"]]):
            return ""

        sentences: List[str] = []

        if self.data["total_sessions"] > 0:
            who = f" as {self.data['name']}" if self.data["name"] else ""
            sentences.append(
                f"You've spoken with this seeker{who} {self.data['total_sessions']} "
                f"times before, {self.data['total_messages']} messages in all."
            )
        elif self.data["name"]:
            sentences.append(f"You know this seeker as {self.data['name']}.")

        if self.data["topics_discussed"]:
            top_topics = sorted(
                self.data["topics_discussed"].items(), key=lambda x: x[1], reverse=True
            )[:max_topics]
            topics_str = ", ".join(topic for topic, _ in top_topics)
            sentences.append(f"Your conversations have kept returning to {topics_str}.")

        if self.data["background"]:
            bg = "; ".join(str(b).strip().rstrip(".") for b in self.data["background"][:5])
            sentences.append(f"You've come to understand that {bg}.")

        if self.data["facts"]:
            recent_facts = self.data["facts"][-max_facts:]
            facts_str = "; ".join(str(f).strip().rstrip(".") for f in recent_facts)
            sentences.append(f"You remember, too, that {facts_str}.")

        if self.data["holdings"]:
            holdings_str = ", ".join(f"{amount} {asset}" for asset, amount in self.data["holdings"].items())
            sentences.append(f"They hold {holdings_str}.")

        if self.data["preferences"]:
            prefs = list(self.data["preferences"].items())[:3]
            prefs_str = "; ".join(f"{k} — {v}" for k, v in prefs)
            sentences.append(f"They've told you their preferences: {prefs_str}.")

        return " ".join(sentences)

    def get_stats(self) -> Dict[str, Any]:
        """Get profile statistics.

        Returns:
            Dictionary with profile stats for debugging/monitoring
        """
        return {
            "user_id": self.user_id,
            "name": self.data["name"],
            "total_sessions": self.data["total_sessions"],
            "total_messages": self.data["total_messages"],
            "facts_count": len(self.data["facts"]),
            "topics_count": len(self.data["topics_discussed"]),
            "personas_met_count": len(self.data["personas_met"]),
            "first_interaction": self.data["first_interaction"],
            "last_updated": self.data["last_updated"]
        }

    def merge_profile(self, other_profile: UserProfile) -> None:
        """Merge another profile into this one.

        Useful for consolidating multiple user identities.

        Args:
            other_profile: Profile to merge from
        """
        # Keep the first known name
        if not self.data["name"] and other_profile.data["name"]:
            self.data["name"] = other_profile.data["name"]

        # Merge background (deduplicated)
        existing_bg = set(self.data["background"])
        for item in other_profile.data["background"]:
            if item not in existing_bg:
                self.data["background"].append(item)

        # Merge topics (sum counts)
        for topic, count in other_profile.data["topics_discussed"].items():
            self.data["topics_discussed"][topic] = (
                self.data["topics_discussed"].get(topic, 0) + count
            )

        # Merge facts (deduplicated)
        existing_facts = set(self.data["facts"])
        for fact in other_profile.data["facts"]:
            if fact not in existing_facts:
                self.data["facts"].append(fact)

        # Merge preferences (other profile wins on conflicts)
        self.data["preferences"].update(other_profile.data["preferences"])

        # Merge holdings
        self.data["holdings"].update(other_profile.data["holdings"])

        # Merge personas met
        for persona in other_profile.data["personas_met"]:
            if persona not in self.data["personas_met"]:
                self.data["personas_met"].append(persona)

        # Update statistics
        self.data["total_sessions"] += other_profile.data["total_sessions"]
        self.data["total_messages"] += other_profile.data["total_messages"]

        # Update timestamps
        if other_profile.data["first_interaction"]:
            if not self.data["first_interaction"]:
                self.data["first_interaction"] = other_profile.data["first_interaction"]
            else:
                # Keep earliest
                self.data["first_interaction"] = min(
                    self.data["first_interaction"],
                    other_profile.data["first_interaction"]
                )

        self.data["last_updated"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        logger.info(f"[UserProfile] Merged profile {other_profile.user_id} into {self.user_id}")
