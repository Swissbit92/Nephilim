"""Every outbound word has an authorized origin, and follow-ups still work."""
from unittest.mock import MagicMock

import pytest

from src.coordinator import startup  # noqa: F401
from src.coordinator.config import get_settings
from src.coordinator.services.query_resolution_service import (
    QueryResolutionService,
    query_origin_ok,
)


@pytest.mark.parametrize("query,turns", [
    ("Ollama latest release November 2026", ["What's new with Ollama?"]),
    ("M5 Pro M5 Max benchmark comparison", ["Compare M5 Pro and M5 Max"]),
    ("BTC price USD today", ["How is Bitcoin doing?"]),
    ("gemma 32B version", ["tell me about gemma", "what about the 32B version?"]),
    ("Zurich weather", ["wetter in Zürich"]),
    ("ZURICH WEATHER", ["zurich?"]),
])
def test_legitimate_queries_pass(query, turns):
    assert query_origin_ok(query, turns)


@pytest.mark.parametrize("query,turns", [
    ("local LLM for a Swiss developer", ["best local LLM?"]),
    ("Oslo apartment prices", ["how are apartment prices?"]),
    ("nephilim companion hosting", ["best hosting?"]),
])
def test_words_from_nowhere_are_rejected(query, turns):
    assert not query_origin_ok(query, turns)


@pytest.fixture
def resolution_on(monkeypatch):
    monkeypatch.setenv("SEARCH_QUERY_RESOLUTION_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _resolver(rewrite):
    llm = MagicMock()
    llm.complete.return_value = rewrite
    return QueryResolutionService(llm), llm


CHAT = (
    "User: tell me about the Ledger Nano X\n"
    "Assistant: You live in Oslo and it suits your wallet.\n"
    "User: how much does it cost?"
)


def test_a_followup_resolves_from_the_operators_own_turns(resolution_on):
    resolver, _ = _resolver("Ledger Nano X cost")
    assert resolver.resolve(CHAT) == "Ledger Nano X cost"


def test_a_general_word_the_rewriter_adds_is_kept(resolution_on):
    resolver, _ = _resolver("Ledger Nano X hardware wallet security cost")
    assert resolver.resolve(CHAT) == "Ledger Nano X hardware wallet security cost"


def test_assistant_text_is_not_given_to_the_rewriter(resolution_on):
    resolver, llm = _resolver("Ledger Nano X cost")
    resolver.resolve(CHAT)
    system, user = llm.complete.call_args.args
    assert "Oslo" not in system + user and "Assistant:" not in user


@pytest.mark.parametrize("query,expected", [
    ("search the web", True),
    ("what about it?", True),
    ("how much does it cost?", False),
    ("Ledger Nano X cost", False),
    ("what about the 32B version?", False),
])
def test_needs_clarifying(query, expected):
    assert QueryResolutionService.needs_clarifying(query) is expected
