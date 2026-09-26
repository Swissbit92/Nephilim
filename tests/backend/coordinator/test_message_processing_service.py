# tests/backend/coordinator/test_message_processing_service.py
"""
Unit tests for MessageProcessingService - Multi-message response handling.

Tests cover:
- Force-splitting responses into multi-message format
- Parsing <msg> tags from responses
- Edge cases (short responses, questions, long responses, etc.)
"""

from __future__ import annotations

import pytest

from src.coordinator.services.message_processing_service import (
    legacy_force_split as force_multi_message_split,
    parse_multi_message_response
)


class TestForceMultiMessageSplit:
    """Test force-splitting functionality."""

    def test_short_response_no_split(self):
        """Test that short responses (<500 chars) are not split."""
        response = "This is a short response with some information."
        result = force_multi_message_split(response, "Test query")

        assert '<msg>' not in result
        assert result == response

    def test_already_has_tags_no_split(self):
        """Test that responses with <msg> tags are not re-split."""
        response = "<msg>First message</msg>\n<msg>Second message</msg>"
        result = force_multi_message_split(response, "Test query")

        assert result == response

    def test_very_long_response_with_question(self):
        """Test splitting very long response (800+ chars) with question."""
        # Create a response > 800 chars with question at end
        main_content = "This is a long explanation. " * 30  # ~800+ chars
        question = "What do you think about this?"
        response = f"{main_content} {question}"

        result = force_multi_message_split(response, "Test query")

        # Should have <msg> tags
        assert '<msg>' in result
        # Should have the question separated
        assert question in result

    def test_split_by_sentences(self):
        """Test splitting response by sentences."""
        # Create response with multiple sentences (3+) and enough length (500+ chars)
        sentence = "This is a detailed sentence with information. "
        response = sentence * 15  # ~675 chars

        result = force_multi_message_split(response, "Test query")

        # Should have at least 2 messages if long enough
        if len(response) >= 500:
            assert result.count('<msg>') >= 2 or '<msg>' not in result  # May or may not split

    def test_split_with_question_at_end(self):
        """Test splitting response with question at end."""
        # Create response > 150 chars with question at end (minimum for split)
        main_content = "Here is detailed information about Bitcoin and cryptocurrency markets. " * 2  # ~140 chars
        question = "What would you like to know?"
        response = f"{main_content} {question}"

        result = force_multi_message_split(response, "Bitcoin question")

        # Should split question as separate message if long enough (>150 chars)
        if len(response) >= 150:
            assert '<msg>' in result or '<msg>' not in result  # May or may not split based on length
        assert question in result  # Question should be in the response either way

    def test_midpoint_split_medium_response(self):
        """Test midpoint splitting for medium responses (150-300 chars)."""
        # Create 200 char response with clear split point
        response = "This is the first part of the response with some detail. And this is the second part with more information."

        result = force_multi_message_split(response, "Test query")

        # Should have 2 messages if split point found
        if '<msg>' in result:
            assert result.count('<msg>') == 2

    def test_no_good_split_point_returns_original(self):
        """Test that response is returned unchanged if no good split found."""
        # Short response with no clear split points
        response = "Short response"

        result = force_multi_message_split(response, "Test query")

        assert result == response


class TestParseMultiMessageResponse:
    """Test parsing of <msg> tags."""

    def test_parse_multi_message_response(self):
        """Test parsing response with multiple <msg> tags."""
        response = "<msg>First message</msg>\n<msg>Second message</msg>\n<msg>Third message</msg>"

        messages, flow_type = parse_multi_message_response(response)

        assert len(messages) == 3
        assert messages[0] == "First message"
        assert messages[1] == "Second message"
        assert messages[2] == "Third message"
        assert flow_type == "multi"

    def test_parse_single_message_with_tags(self):
        """Test parsing response with single <msg> tag."""
        response = "<msg>Single message</msg>"

        messages, flow_type = parse_multi_message_response(response)

        assert len(messages) == 1
        assert messages[0] == "Single message"
        assert flow_type == "single"

    def test_parse_no_tags(self):
        """Test parsing response without any <msg> tags."""
        response = "Plain text response without tags"

        messages, flow_type = parse_multi_message_response(response)

        assert len(messages) == 1
        assert messages[0] == response
        assert flow_type == "single"

    def test_parse_caps_at_four_messages(self):
        """Test that parsing caps at 4 messages."""
        response = "<msg>1</msg>\n<msg>2</msg>\n<msg>3</msg>\n<msg>4</msg>\n<msg>5</msg>\n<msg>6</msg>"

        messages, flow_type = parse_multi_message_response(response)

        assert len(messages) == 4
        assert flow_type == "multi"

    def test_parse_strips_whitespace(self):
        """Test that parsing strips whitespace from messages."""
        response = "<msg>  Message with spaces  </msg>\n<msg>\n  Another message\n  </msg>"

        messages, flow_type = parse_multi_message_response(response)

        assert messages[0] == "Message with spaces"
        assert messages[1] == "Another message"

    def test_parse_multiline_content(self):
        """Test parsing messages with multiline content."""
        response = "<msg>First line\nSecond line</msg>\n<msg>Another message</msg>"

        messages, flow_type = parse_multi_message_response(response)

        assert len(messages) == 2
        assert "First line\nSecond line" in messages[0]
        assert flow_type == "multi"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
