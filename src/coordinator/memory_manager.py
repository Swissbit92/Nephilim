"""Memory management with importance scoring for intelligent context selection.

This module implements Phase 2 of the Persona Memory Enhancement project:
- Message importance scoring based on content type, recency, and relevance
- Intelligent message selection within token budgets
- Dynamic context window sizing
- Conversation summarization for long-term memory compression
"""

from __future__ import annotations
from typing import List, Dict, Optional, Any, TYPE_CHECKING
from datetime import datetime, timezone
import logging

from .llm_client import estimate_tokens

if TYPE_CHECKING:
    from .llm_client import LC_OllamaClient

logger = logging.getLogger(__name__)


class MessageImportanceScorer:
    """Score message importance for context selection.

    Uses multiple factors to determine which messages are most important
    to include in the LLM context:
    - Personal information (names, preferences, holdings)
    - User questions (intent signals)
    - Message length (very short messages less important)
    - Recency (newer messages weighted higher)
    - Time-based decay (messages age over days)
    """

    # Personal info keywords get HIGHEST priority - NEVER drop these
    PERSONAL_INFO_KEYWORDS = [
        "my name", "i am", "i'm", "i have", "i own", "i like",
        "i prefer", "i want", "i need", "call me", "i hold",
        "my portfolio", "i bought", "i sold", "my goal",
        "my background", "my experience", "i work", "my job",
        "i live", "i'm from", "my wife", "my husband", "my family",
        "my btc", "my bitcoin", "my eth", "my crypto"
    ]

    # Name introduction patterns - get CRITICAL priority
    NAME_INTRO_PATTERNS = [
        "my name is", "i'm called", "call me", "name's", "i am "
    ]

    QUESTION_KEYWORDS = ["?", "how", "what", "why", "when", "where", "who", "which"]

    # Threshold for "critical" messages that should always be included
    # Value: 4.0 (calibrated to scoring weights below)
    # - Name introductions score 6.0 → always critical
    # - Personal info (holdings, goals) score 4.0+ → critical
    # - Regular messages score 1.5-2.0 → can be dropped if needed
    # This ensures user names and key personal info are NEVER forgotten.
    # DO NOT change unless you understand the scoring algorithm impact.
    CRITICAL_SCORE_THRESHOLD = 4.0

    def score_message(self, message: dict, position: int, total: int) -> float:
        """
        Calculate importance score for a message.

        Args:
            message: Message dict with role, content, timestamp
            position: Message position in conversation (0 = oldest)
            total: Total messages in conversation

        Returns:
            Importance score (0.0-10.0+, higher = more important)
        """
        score = 1.0
        content_lower = message["content"].lower()

        # 1. Role multiplier (user messages more important for context)
        if message["role"] == "user":
            score *= 1.5

        # 2. CRITICAL: Name introduction detection (HIGHEST PRIORITY - 6x boost)
        # This ensures user's name is NEVER forgotten
        name_intro_found = False
        for pattern in self.NAME_INTRO_PATTERNS:
            if pattern in content_lower:
                score *= 6.0  # Extremely high weight for name introductions
                name_intro_found = True
                logger.info(f"[Importance] ⭐ NAME INTRO detected: {message['content'][:60]}...")
                break

        # 3. Personal information boost (4x if not already boosted by name)
        if not name_intro_found:
            for keyword in self.PERSONAL_INFO_KEYWORDS:
                if keyword in content_lower:
                    score *= 4.0  # Increased from 3.0
                    logger.debug(f"[Importance] Personal info detected: {message['content'][:50]}...")
                    break

        # 4. Questions boost (user intent signals)
        if message["role"] == "user":
            # Check for question marks
            if "?" in message["content"]:
                score *= 1.3
            # Check for question words
            elif any(kw in content_lower for kw in self.QUESTION_KEYWORDS if kw != "?"):
                score *= 1.2

        # 5. Length penalty (very short messages less important)
        msg_length = len(message["content"])
        if msg_length < 10:
            score *= 0.5
        elif msg_length > 200:
            # Long messages often contain important details
            score *= 1.2

        # 6. Recency boost (exponential decay)
        # More recent messages score higher
        # Position 0 = oldest (score 1.0), Position N = newest (score 3.0)
        if total > 0:
            recency_factor = 1.0 + (position / total) * 2.0  # 1.0 to 3.0 range
            score *= recency_factor

        # 7. Time-based decay (if timestamp available)
        if "timestamp" in message and message["timestamp"]:
            try:
                # Handle both ISO format and Unix timestamps
                if isinstance(message["timestamp"], str):
                    msg_time = datetime.fromisoformat(message["timestamp"].replace('Z', '+00:00'))
                    msg_time = msg_time.replace(tzinfo=None)  # Remove timezone for comparison
                else:
                    msg_time = datetime.fromtimestamp(message["timestamp"])

                age_hours = (datetime.now(timezone.utc).replace(tzinfo=None) - msg_time).total_seconds() / 3600
                # Decay over days: 1.0 at 0 hours, 0.5 at 24 hours, 0.33 at 48 hours
                time_decay = 1.0 / (1.0 + age_hours / 24)
                score *= (0.5 + 0.5 * time_decay)  # 0.5-1.0 multiplier
            except Exception as e:
                logger.debug(f"[Importance] Could not parse timestamp: {e}")

        return round(score, 2)

    def is_critical_message(self, message: dict) -> bool:
        """Check if a message is critical and should ALWAYS be included.

        Critical messages include:
        - User name introductions
        - Key personal information (holdings, goals)
        """
        content_lower = message["content"].lower()

        # Name introductions are always critical
        for pattern in self.NAME_INTRO_PATTERNS:
            if pattern in content_lower:
                return True

        # Specific holdings/amounts are critical
        critical_patterns = ["btc", "bitcoin", "eth", "bought", "sold", "hold"]
        has_number = any(c.isdigit() for c in message["content"])
        if has_number and any(p in content_lower for p in critical_patterns):
            return True

        return False


class MemoryManager:
    """Manage conversation context with intelligent message selection.

    This manager uses importance scoring to select the most relevant messages
    within the available token budget, ensuring:
    - Personal information is always preserved
    - Recent context is maintained
    - Important questions/topics are retained
    - Token budget is never exceeded
    """

    def __init__(self, max_tokens: int = 4096):
        """Initialize memory manager.

        Args:
            max_tokens: Maximum context window for the model (default: 4096)
        """
        self.max_tokens = max_tokens
        self.scorer = MessageImportanceScorer()

    def select_messages(
        self,
        messages: List[dict],
        token_budget: int,
        system_prompt_tokens: int
    ) -> List[dict]:
        """
        Select most important messages within token budget.

        Strategy:
        1. Always include first 3 messages (session context/greetings)
        2. Always include last 10 messages (recent context)
        3. Fill remaining budget with highest-scoring messages from the middle

        Args:
            messages: All messages from session (dicts with role, content, timestamp)
            token_budget: Total token budget for conversation
            system_prompt_tokens: Tokens used by system prompt

        Returns:
            Selected messages list (chronologically ordered)
        """
        if not messages:
            return []

        # Calculate available tokens for history
        available_tokens = token_budget - system_prompt_tokens
        # Reserve tokens for response generation (~500 tokens)
        available_tokens -= 500

        logger.debug(f"[MemoryManager] Available tokens for history: {available_tokens}")

        # Score all messages
        scored_messages = []
        for i, msg in enumerate(messages):
            score = self.scorer.score_message(msg, i, len(messages))
            tokens = estimate_tokens(msg["content"])
            scored_messages.append({
                "message": msg,
                "score": score,
                "tokens": tokens,
                "index": i
            })

        # Define must-include indices
        must_include_indices = set()

        # CRITICAL: Always include messages with critical personal information
        # (names, holdings, etc.) - these should NEVER be dropped
        critical_count = 0
        for i, msg in enumerate(messages):
            if self.scorer.is_critical_message(msg):
                must_include_indices.add(i)
                critical_count += 1

        if critical_count > 0:
            logger.info(f"[MemoryManager] Found {critical_count} CRITICAL messages (names, holdings)")

        # First 3 messages (greetings, initial context). Pinned unconditionally
        # by default, which keeps a scripted opener in every prompt for the life
        # of the session and re-anchors the model on it long after the
        # conversation has moved on. Once there is real history to anchor to,
        # those turns should compete on their own score like anything else.
        # Flag-gated: when off, the pin is unconditional — today's behaviour.
        from .config import get_settings  # noqa: PLC0415 — avoids a config<->client import cycle

        _agent_cfg = get_settings().agent
        if not (_agent_cfg.unpin_on_depth and len(messages) >= _agent_cfg.unpin_depth_turns):
            must_include_indices.update(range(min(3, len(messages))))

        # Always include: last 10 messages (recent context)
        must_include_indices.update(range(max(0, len(messages) - 10), len(messages)))

        # Calculate tokens for must-include messages
        must_include_tokens = sum(
            sm["tokens"] for sm in scored_messages
            if sm["index"] in must_include_indices
        )

        logger.debug(
            f"[MemoryManager] Must-include: {len(must_include_indices)} messages "
            f"({must_include_tokens} tokens)"
        )

        # If must-include messages already exceed budget, prioritize critical + recent
        if must_include_tokens > available_tokens:
            logger.warning(
                f"[MemoryManager] Must-include messages exceed budget! "
                f"({must_include_tokens} > {available_tokens})"
            )
            # Fallback: prioritize critical messages first, then recent
            selected_indices = set()
            tokens_used = 0

            # FIRST: Include all critical messages (names, holdings)
            for i, msg in enumerate(messages):
                if self.scorer.is_critical_message(msg):
                    msg_tokens = scored_messages[i]["tokens"]
                    if tokens_used + msg_tokens <= available_tokens:
                        selected_indices.add(i)
                        tokens_used += msg_tokens
                        logger.debug(f"[MemoryManager] Fallback: added critical msg {i}")

            # THEN: Add recent messages that fit
            for i in range(len(messages) - 1, -1, -1):
                if i in selected_indices:
                    continue  # Already included
                msg_tokens = scored_messages[i]["tokens"]
                if tokens_used + msg_tokens <= available_tokens:
                    selected_indices.add(i)
                    tokens_used += msg_tokens
                else:
                    break

            selected = [
                scored_messages[i]["message"]
                for i in sorted(selected_indices)
            ]

            logger.info(
                f"[Memory] Selected {len(selected)}/{len(messages)} messages "
                f"({tokens_used}/{available_tokens} tokens, {tokens_used/available_tokens*100:.1f}% usage) "
                f"[BUDGET EXCEEDED FALLBACK - Critical messages prioritized]"
            )

            return selected

        # Select additional messages by importance score
        remaining_budget = available_tokens - must_include_tokens
        optional_messages = [
            sm for sm in scored_messages
            if sm["index"] not in must_include_indices
        ]
        # Sort by score descending (highest importance first)
        optional_messages.sort(key=lambda x: x["score"], reverse=True)

        selected_indices = must_include_indices.copy()
        tokens_used = must_include_tokens

        # Add high-scoring messages until budget exhausted
        for sm in optional_messages:
            if tokens_used + sm["tokens"] <= available_tokens:
                selected_indices.add(sm["index"])
                tokens_used += sm["tokens"]
                logger.debug(
                    f"[MemoryManager] Added message {sm['index']} "
                    f"(score: {sm['score']}, tokens: {sm['tokens']})"
                )
            else:
                logger.debug(
                    f"[MemoryManager] Skipped message {sm['index']} "
                    f"(would exceed budget: {tokens_used + sm['tokens']} > {available_tokens})"
                )

        # Return selected messages in chronological order
        selected = [
            scored_messages[i]["message"]
            for i in sorted(selected_indices)
        ]

        # Log selection summary
        logger.info(
            f"[Memory] Selected {len(selected)}/{len(messages)} messages "
            f"({tokens_used}/{available_tokens} tokens, {tokens_used/available_tokens*100:.1f}% usage)"
        )

        # Log importance distribution
        selected_scores = [scored_messages[i]["score"] for i in selected_indices]
        if selected_scores:
            logger.debug(
                f"[MemoryManager] Importance range: "
                f"{min(selected_scores):.2f} - {max(selected_scores):.2f} "
                f"(avg: {sum(selected_scores)/len(selected_scores):.2f})"
            )

        return selected


class ConversationSummarizer:
    """Generate compressed summaries of conversation segments.

    This class handles automatic summarization of long conversations to:
    - Compress old messages into concise summaries
    - Preserve key information (names, facts, topics, emotions)
    - Save token budget for recent context
    - Enable unlimited conversation length
    """

    def __init__(self, llm_client: Optional['LC_OllamaClient'] = None):
        """Initialize conversation summarizer.

        Args:
            llm_client: LLM client for generating summaries (optional, can be set later)
        """
        self.llm_client = llm_client

    def set_llm_client(self, llm_client: 'LC_OllamaClient'):
        """Set the LLM client for summary generation.

        Args:
            llm_client: LLM client instance
        """
        self.llm_client = llm_client

    def summarize_segment(
        self,
        messages: List[dict],
        max_summary_tokens: int = 200,
        persona_name: str = "Assistant"
    ) -> Dict[str, Any]:
        """Summarize a segment of conversation.

        Args:
            messages: Messages to summarize (dicts with role, content)
            max_summary_tokens: Maximum tokens for summary (default: 200)
            persona_name: Name of the persona for context

        Returns:
            Dictionary with:
            - summary_text: Compressed summary string
            - emotional_developments: Key emotional moments
            - topics_discussed: List of topics covered
            - token_count: Estimated tokens in summary
        """
        if not self.llm_client:
            logger.warning("[Summarizer] No LLM client available, skipping summarization")
            return {
                "summary_text": "[Summary unavailable - no LLM client]",
                "emotional_developments": "",
                "topics_discussed": "",
                "token_count": 0
            }

        if not messages:
            return {
                "summary_text": "",
                "emotional_developments": "",
                "topics_discussed": "",
                "token_count": 0
            }

        # Format messages for summarization
        conversation_text = self._format_messages(messages, max_length=3000)

        # Build summarization prompt
        prompt = f"""Summarize this conversation segment in ≤{max_summary_tokens} tokens.

Focus on:
1. User's name, background, goals, preferences (if mentioned)
2. Key facts shared by both parties
3. Important decisions or conclusions
4. Emotional developments in the relationship
5. Topics discussed

Conversation between User and {persona_name}:
{conversation_text}

Provide a concise summary in this format:

**Summary:**
[2-3 sentences capturing key points]

**User Info:**
[Name, important details about the user - if mentioned]

**Topics:**
[Comma-separated list of topics discussed]

**Emotional Tone:**
[Brief note on relationship development or emotional moments]

Be concise and factual. Prioritize names, numbers, and specific facts."""

        system_prompt = "You create ultra-concise conversation summaries that preserve key information."

        try:
            # Generate summary using LLM
            logger.info(f"[Summarizer] Generating summary for {len(messages)} messages...")
            summary = self.llm_client._invoke(
                prompt=f"System: {system_prompt}\n\nUser: {prompt}"
            )

            # Parse summary into components
            parsed = self._parse_summary(summary)
            token_count = estimate_tokens(summary)

            logger.info(
                f"[Summarizer] Compressed {len(messages)} messages "
                f"({estimate_tokens(conversation_text)} tokens) "
                f"into {token_count} token summary"
            )

            return {
                "summary_text": summary.strip(),
                "emotional_developments": parsed.get("emotional_tone", ""),
                "topics_discussed": parsed.get("topics", ""),
                "token_count": token_count
            }

        except Exception as e:
            logger.error(f"[Summarizer] Failed to generate summary: {e}")
            return {
                "summary_text": f"[Summary generation failed: {str(e)}]",
                "emotional_developments": "",
                "topics_discussed": "",
                "token_count": 0
            }

    def _format_messages(self, messages: List[dict], max_length: int = 3000) -> str:
        """Format messages for summarization.

        Args:
            messages: List of message dicts
            max_length: Maximum character length (default: 3000)

        Returns:
            Formatted conversation text
        """
        lines = []
        total_chars = 0

        for msg in messages:
            role = msg["role"].capitalize()
            content = msg["content"]

            # Truncate very long messages
            if len(content) > 500:
                content = content[:500] + "..."

            line = f"{role}: {content}"

            # Check if adding this line would exceed max_length
            if total_chars + len(line) > max_length:
                lines.append("... [conversation truncated for summarization]")
                break

            lines.append(line)
            total_chars += len(line)

        return "\n".join(lines)

    def _parse_summary(self, summary: str) -> Dict[str, str]:
        """Parse structured summary into components.

        Args:
            summary: Raw summary text from LLM

        Returns:
            Dictionary with parsed components
        """
        # Simple parsing - extract sections if present
        result = {
            "summary": summary,
            "user_info": "",
            "topics": "",
            "emotional_tone": ""
        }

        # Try to extract topics (look for comma-separated list)
        if "**Topics:**" in summary:
            parts = summary.split("**Topics:**")
            if len(parts) > 1:
                topics_section = parts[1].split("**")[0].strip()
                result["topics"] = topics_section

        # Try to extract emotional tone
        if "**Emotional Tone:**" in summary or "**Emotional" in summary:
            parts = summary.split("**Emotional")
            if len(parts) > 1:
                emotional_section = parts[1].split("**")[0].replace("Tone:**", "").strip()
                result["emotional_tone"] = emotional_section

        return result

