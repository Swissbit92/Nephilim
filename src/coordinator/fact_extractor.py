"""Extract structured facts from conversations using LLM.

This module analyzes conversation transcripts to extract:
- User's name and personal information
- Background details
- Topics discussed
- Preferences and holdings
- Key facts worth remembering

Used to build persistent user profiles for cross-session memory.
"""

from __future__ import annotations

import json
import re
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


_REDACTED_TURN = (
    "Assistant: [her reply omitted — never a source of facts about the user]"
)


class FactExtractor:
    """Extract key facts from conversation segments using LLM analysis.

    Uses structured prompts to extract JSON-formatted information from
    chat transcripts, enabling automatic user profile building.
    """

    def __init__(self, llm_client):
        """Initialize fact extractor.

        Args:
            llm_client: LLM client for inference (from llm_client.py)
        """
        self.llm = llm_client

    def extract_facts(
        self,
        messages: List[Dict[str, Any]],
        persona_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Extract structured facts from conversation messages.

        Analyzes the conversation and returns a structured dictionary
        of information learned about the user.

        Args:
            messages: List of message dicts with keys: role, content, timestamp
            persona_key: Persona involved in the conversation (optional)

        Returns:
            Dictionary with extracted facts:
            {
                "user_name": str | None,
                "background": List[str],
                "topics": List[str],
                "facts": List[str],
                "preferences": Dict[str, str],
                "holdings": Dict[str, str],
                "message_count": int,
                "persona_key": str | None
            }
        """
        if not messages:
            logger.warning("[FactExtraction] No messages to extract from")
            return self._empty_result(persona_key)

        # Format conversation for analysis
        conversation_text = self._format_messages(messages)

        # Build extraction prompt
        prompt = self._build_extraction_prompt(conversation_text)

        # System prompt for structured extraction
        system = """You are a fact extraction AI. Your job is to analyze conversations and extract structured information about the user.

IMPORTANT: Return ONLY valid JSON. No markdown, no explanations, just the JSON object."""

        try:
            # Call LLM for fact extraction
            response = self.llm.complete(system=system, user_prompt=prompt)

            # Parse JSON response
            facts = self._parse_llm_response(response)

            # Add metadata
            facts["message_count"] = len(messages)
            facts["persona_key"] = persona_key

            # Log extraction results
            logger.info(
                f"[FactExtraction] Extracted: "
                f"name={facts.get('user_name')}, "
                f"{len(facts.get('facts', []))} facts, "
                f"{len(facts.get('topics', []))} topics, "
                f"{len(facts.get('background', []))} background items"
            )

            return facts

        except Exception as e:
            logger.error(f"[FactExtraction] Failed: {e}")
            return self._empty_result(persona_key, message_count=len(messages))

    def _format_messages(self, messages: List[Dict[str, Any]], max_messages: int = 50) -> str:
        """Format messages for LLM analysis.

        Args:
            messages: Message list
            max_messages: Maximum messages to include (to avoid token limits)

        Returns:
            Formatted conversation text
        """
        lines = []

        # Limit to most recent messages if too many
        messages_to_analyze = messages[-max_messages:] if len(messages) > max_messages else messages

        for msg in messages_to_analyze:
            if msg["role"] == "narrator":
                continue  # ADR-011: scene direction, not a speaker's facts
            if msg["role"] != "user":
                # 🔴 HER WORDS ARE NEVER A SOURCE OF FACTS ABOUT HIM.
                #
                # The turn's POSITION is kept so a short user reply still has a
                # visible antecedent, but the CONTENT is removed. That makes the
                # rule physical rather than judged, which is the whole point:
                # Mem0 is the only production memory system with a provenance
                # field (`attributed_to`) and its LLM-judged version failed in
                # exactly this shape — roles were stripped upstream of
                # extraction so everything defaulted to the user
                # (mem0ai/mem0#5642). A prompt instruction is a soft mitigation;
                # absent text is a guarantee.
                #
                # Measured here: she recited "Eggs benedict and apple juice" and
                # the extractor wrote "User eats eggs benedict and apple juice
                # for breakfast" as a NEW FACT ABOUT HIM. 32 stored facts became
                # 36 during one probe burst, every addition a paraphrase of
                # something she had just said. The loop did not exist before the
                # numbered frame made her recite stored facts at all.
                lines.append(f"{_REDACTED_TURN}")
                continue
            content = msg["content"][:500]  # Truncate very long messages
            lines.append(f"User: {content}")

        return "\n".join(lines)

    def _build_extraction_prompt(self, conversation_text: str) -> str:
        """Build the fact extraction prompt.

        Args:
            conversation_text: Formatted conversation

        Returns:
            Extraction prompt
        """
        return f"""Analyze this conversation and extract key information about the user.

Focus on extracting:
1. User's name (if they introduce themselves)
2. Background information (job, location, experience, etc.)
3. Topics discussed
4. Important facts shared
5. Preferences mentioned
6. Holdings or assets mentioned (e.g., "I have 0.5 BTC")

Format your response as JSON with these exact fields:
{{
  "user_name": "Name if mentioned, otherwise null",
  "background": ["Background fact 1", "Background fact 2"],
  "topics": ["Topic 1", "Topic 2"],
  "facts": ["Important fact 1", "Important fact 2"],
  "preferences": {{"preference_type": "value"}},
  "holdings": {{"asset": "amount"}}
}}

Conversation:
{conversation_text}

JSON output:"""

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM response and extract JSON.

        Handles cases where LLM wraps JSON in markdown or adds explanations.

        Args:
            response: Raw LLM response

        Returns:
            Parsed facts dictionary

        Raises:
            ValueError: If JSON parsing fails
        """
        # Try to extract JSON from response
        # Case 1: Response is pure JSON
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Case 2: JSON wrapped in markdown code blocks
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Case 3: Find JSON object in text
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        # Failed to parse
        logger.error(f"[FactExtraction] Could not parse LLM response: {response[:200]}...")
        raise ValueError("Failed to parse JSON from LLM response")

    def _empty_result(
        self,
        persona_key: Optional[str] = None,
        message_count: int = 0
    ) -> Dict[str, Any]:
        """Return empty fact extraction result.

        Args:
            persona_key: Persona key
            message_count: Number of messages analyzed

        Returns:
            Empty facts dictionary
        """
        return {
            "user_name": None,
            "background": [],
            "topics": [],
            "facts": [],
            "preferences": {},
            "holdings": {},
            "message_count": message_count,
            "persona_key": persona_key
        }

    def extract_name_heuristic(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        """Fast heuristic name extraction without LLM.

        Looks for common name introduction patterns:
        - "My name is X"
        - "I'm X"
        - "Call me X"

        Args:
            messages: Message list

        Returns:
            Extracted name or None
        """
        name_patterns = [
            r"my name is (\w+)",
            r"i'm (\w+)",
            r"call me (\w+)",
            r"i am (\w+)",
            r"this is (\w+)"
        ]

        for msg in messages:
            if msg["role"] != "user":
                continue

            content_lower = msg["content"].lower()

            for pattern in name_patterns:
                match = re.search(pattern, content_lower)
                if match:
                    name = match.group(1).capitalize()
                    logger.info(f"[FactExtraction] Heuristic name extraction: {name}")
                    return name

        return None

    def extract_holdings_heuristic(self, messages: List[Dict[str, Any]]) -> Dict[str, str]:
        """Fast heuristic holdings extraction.

        Looks for patterns like:
        - "I have 0.5 BTC"
        - "I own 10 ETH"

        Args:
            messages: Message list

        Returns:
            Dictionary of asset -> amount
        """
        holdings = {}

        # Common crypto symbols
        crypto_symbols = ['BTC', 'ETH', 'SATS', 'SOL', 'ADA', 'DOT', 'MATIC']

        holdings_patterns = [
            r"i have ([\d.]+) (\w+)",
            r"i own ([\d.]+) (\w+)",
            r"holding ([\d.]+) (\w+)"
        ]

        for msg in messages:
            if msg["role"] != "user":
                continue

            content_upper = msg["content"].upper()

            for pattern in holdings_patterns:
                matches = re.finditer(pattern, content_upper, re.IGNORECASE)
                for match in matches:
                    amount = match.group(1)
                    asset = match.group(2).upper()

                    # Only capture known crypto assets
                    if asset in crypto_symbols:
                        holdings[asset] = amount
                        logger.debug(f"[FactExtraction] Heuristic holdings: {amount} {asset}")

        return holdings

    def get_extraction_stats(self, facts: Dict[str, Any]) -> str:
        """Generate human-readable summary of extraction results.

        Args:
            facts: Extracted facts dictionary

        Returns:
            Summary string for logging
        """
        stats = []

        if facts.get("user_name"):
            stats.append(f"Name: {facts['user_name']}")

        if facts.get("background"):
            stats.append(f"{len(facts['background'])} background items")

        if facts.get("topics"):
            stats.append(f"{len(facts['topics'])} topics")

        if facts.get("facts"):
            stats.append(f"{len(facts['facts'])} facts")

        if facts.get("holdings"):
            stats.append(f"{len(facts['holdings'])} holdings")

        return ", ".join(stats) if stats else "No facts extracted"
