"""Research telemetry: written when configured, silent otherwise, never query text."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.coordinator import research_trace, startup  # noqa: F401
from src.coordinator.config import get_settings
from src.coordinator.models.mcp_models import MCPConnectionError, SearchResult
from src.coordinator.services.search_execution_service import SearchExecutionService
from src.coordinator.tool_definitions import ToolCall
from src.coordinator.tools import executor_bindings


@pytest.fixture
def trace_file(monkeypatch, tmp_path):
    path = tmp_path / "logs" / "research.jsonl"
    monkeypatch.setenv("RESEARCH_TRACE_PATH", str(path))
    monkeypatch.setenv("WEB_SEARCH_BACKEND", "routed")
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://sx:8888")
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_records_the_backend_that_answered_and_never_the_query(trace_file):
    brave = MagicMock()
    brave.search_web.return_value = [SearchResult("t", "https://u", "d")]
    with research_trace.turn("eeva", "tool_brain") as rec:
        SearchExecutionService(brave).execute_search(
            ToolCall("web_search", {"query": "my secret question"})
        )
        rec["status"] = "answered"
    line = _lines(trace_file)[0]
    assert line["searches"] == [{"backend": "brave", "category": "general", "results": 1}]
    assert line["status"] == "answered"
    assert line["latency_ms"] >= 0
    assert "my secret question" not in trace_file.read_text()


def test_a_failed_backend_is_distinct_from_an_empty_one(trace_file):
    sx = MagicMock()
    sx.search.side_effect = MCPConnectionError("refused")
    with patch("src.coordinator.searxng_client.SearxngClient", return_value=sx):
        with research_trace.turn("gwen", "tool_brain"):
            SearchExecutionService(None).execute_search(
                ToolCall("image_search", {"query": "q", "category": "images"})
            )
    assert _lines(trace_file)[0]["searches"][0]["results"] is None


def test_fetch_counts_success_and_failure(trace_file):
    with patch(
        "src.coordinator.services.web_fetch_service.fetch_url",
        side_effect=["page text", "ERROR: blocked"],
    ):
        with research_trace.turn("eeva", "tool_brain"):
            executor_bindings._fetch_url_executor({"url": "https://a"}, {})
            executor_bindings._fetch_url_executor({"url": "https://b"}, {})
    line = _lines(trace_file)[0]
    assert (line["pages_fetched"], line["pages_failed"]) == (1, 1)


def test_written_even_when_the_turn_raises(trace_file):
    with pytest.raises(ValueError):
        with research_trace.turn("eeva", "legacy"):
            raise ValueError
    assert len(_lines(trace_file)) == 1


def test_deflection_is_recorded(trace_file):
    research_trace.note_deflection("gwen")
    assert _lines(trace_file)[0]["status"] == "deflected"


def test_off_by_default_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_TRACE_PATH", "")
    get_settings.cache_clear()
    monkeypatch.chdir(tmp_path)
    with research_trace.turn("eeva", "tool_brain"):
        pass
    get_settings.cache_clear()
    assert list(tmp_path.iterdir()) == []


def test_an_unwritable_path_does_not_break_the_turn(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("RESEARCH_TRACE_PATH", str(blocker / "nested" / "t.jsonl"))
    get_settings.cache_clear()
    with research_trace.turn("eeva", "tool_brain"):
        pass
    get_settings.cache_clear()


def test_outside_a_turn_notes_are_ignored():
    research_trace.note_search("brave", "general", 1)
    research_trace.note_fetch(True)
