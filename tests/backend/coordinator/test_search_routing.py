"""Search routing: where a query may go, by persona and category.

The privacy rule is that an nsfw persona never reaches Brave, on any path. A media
search never reaches Brave either, because `search_web` has no category parameter
and would return plain web results presented as images.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.coordinator import startup  # noqa: F401 - import order, breaks a cycle
from src.coordinator.config import get_settings
from src.coordinator.llm_client import create_llm_client
from src.coordinator.models.mcp_models import MCPConnectionError, SearchResult
from src.coordinator.services.search_execution_service import SearchExecutionService
from src.coordinator.tool_definitions import ToolCall
from src.coordinator.tools import executor_bindings

SX = SearchResult("SearxTitle", "https://sx", "d")
BR = SearchResult("BraveTitle", "https://brave", "d")


@pytest.fixture
def env(monkeypatch):
    def _set(backend):
        monkeypatch.setenv("WEB_SEARCH_BACKEND", backend)
        monkeypatch.setenv("SEARXNG_BASE_URL", "http://sx:8888")
        monkeypatch.setenv("SEARCH_RELEVANCE_GATE_ENABLED", "false")
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


@pytest.fixture
def brave():
    client = MagicMock()
    client.search_web.return_value = [BR]
    return client


def _searxng(results=None, down=False):
    instance = MagicMock()
    if down:
        instance.search.side_effect = MCPConnectionError("refused")
    else:
        instance.search.return_value = results if results is not None else [SX]
    return patch("src.coordinator.searxng_client.SearxngClient", return_value=instance), instance


def _run_tool(tool, card, brave):
    with patch("src.coordinator.startup.get_brave_client", return_value=brave):
        return executor_bindings._make_search_executor(tool)({"query": "q"}, card)


@pytest.mark.parametrize("backend", ["auto", "routed", "brave"])
@pytest.mark.parametrize("tool", ["web_search", "image_search", "video_search", "news_search"])
def test_nsfw_persona_never_reaches_brave_with_searxng_down(env, brave, backend, tool):
    env(backend)
    patcher, _ = _searxng(down=True)
    with patcher:
        out = _run_tool(tool, {"nsfw": True}, brave)
    brave.search_web.assert_not_called()
    assert not out


@pytest.mark.parametrize("backend", ["auto", "routed", "brave"])
@pytest.mark.parametrize("tool", ["image_search", "video_search"])
def test_media_search_never_degrades_to_plain_web_results(env, brave, backend, tool):
    env(backend)
    patcher, _ = _searxng(down=True)
    with patcher:
        out = _run_tool(tool, {}, brave)
    brave.search_web.assert_not_called()
    assert not out


def test_legacy_path_gives_an_nsfw_persona_no_brave_client(env, brave):
    env("auto")
    assert create_llm_client({"nsfw": True}, mcp_client=brave)._tool_service is None
    assert create_llm_client({}, mcp_client=brave)._tool_service is not None


class TestRouted:
    def _search(self, brave, tool="web_search", category="general"):
        svc = SearchExecutionService(mcp_client=brave)
        return svc.execute_search(ToolCall(tool, {"query": "q", "category": category}))

    @pytest.mark.parametrize("category", ["general", "news"])
    def test_text_search_prefers_brave(self, env, brave, category):
        env("routed")
        patcher, sx = _searxng()
        with patcher:
            out = self._search(brave, category=category)
        assert out == [BR]
        sx.search.assert_not_called()

    def test_brave_failure_falls_back_to_searxng(self, env, brave):
        env("routed")
        brave.search_web.side_effect = RuntimeError("boom")
        patcher, _ = _searxng()
        with patcher:
            assert self._search(brave) == [SX]

    def test_brave_empty_falls_back_to_searxng(self, env, brave):
        env("routed")
        brave.search_web.return_value = []
        patcher, _ = _searxng()
        with patcher:
            assert self._search(brave) == [SX]

    def test_media_uses_searxng(self, env, brave):
        env("routed")
        patcher, _ = _searxng()
        with patcher:
            assert self._search(brave, "image_search", "images") == [SX]
        brave.search_web.assert_not_called()

    def test_nsfw_persona_text_search_uses_searxng(self, env, brave):
        env("routed")
        patcher, _ = _searxng()
        with patcher:
            assert _run_tool("web_search", {"nsfw": True}, brave) == [SX]
        brave.search_web.assert_not_called()


def test_auto_still_prefers_searxng(env, brave):
    env("auto")
    patcher, _ = _searxng()
    with patcher:
        out = SearchExecutionService(mcp_client=brave).execute_search(
            ToolCall("web_search", {"query": "q"})
        )
    assert out == [SX]
    brave.search_web.assert_not_called()
