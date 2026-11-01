"""The route's research decision: one path, one run, from either trigger."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.coordinator.config import get_settings
from src.coordinator.models.mcp_models import SearchResult
from src.coordinator.routes import chat as chat_mod
from src.coordinator.schemas import ResponseMetadata, SourceType
from src.coordinator.services.research_service import EvidencePackage, Source
from src.coordinator.services.tool_brain_service import ST_RESEARCH, ST_SILENT, ToolBrainResult
from src.coordinator.tools import registrations  # noqa: F401
from src.coordinator.tools.executor_bindings import bind_web_executors
from src.coordinator.tools.intent_classifier import QueryIntent

EEVA = {"key": "nephilim_eeva", "mcp_access": ["brave_search", "solana_wallet"], "nsfw": False}
GWEN = {
    "key": "gwen",
    "toolsets": ["web"],
    "tools": ["image_search", "video_search"],
    "mcp_access": ["brave_search"],
    "nsfw": True,
}
RESULT = SearchResult("T", "https://a.example", "snippet about the answer")


def _package(usable=True):
    passages = ["the answer is 42"] if usable else []
    return EvidencePackage("q", [Source("T", RESULT.url, passages=passages)], [RESULT])


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    bind_web_executors()
    monkeypatch.setenv("RESEARCH_SERVICE_ENABLED", "true")
    monkeypatch.setenv("TOOL_BRAIN_ENABLED", "true")
    monkeypatch.setenv("TOOL_BRAIN_UNGATED_WEB", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _invoke(
    *,
    intent,
    card=EEVA,
    msg="what is the answer?",
    run=None,
    package=None,
    synth=("The answer is 42.", False),
):
    meta = ResponseMetadata(source_type=SourceType.LLM, tools_used=[])
    brain = MagicMock()
    brain.run.return_value = run or ToolBrainResult(status=ST_SILENT, answer="chat")
    brain.synthesize.return_value = synth
    research = MagicMock()
    research.run.return_value = package or _package()
    with (
        patch("src.coordinator.services.tool_brain_service.ToolBrainService", return_value=brain),
        patch("src.coordinator.services.research_service.ResearchService", return_value=research),
        patch("src.coordinator.routes.chat.create_llm_client"),
        patch("src.coordinator.services.query_resolution_service.QueryResolutionService") as qr,
        patch("src.coordinator.services.persona_lines.line", return_value="nothing found"),
    ):
        qr.return_value.resolve.return_value = "resolved question"
        resp = chat_mod._try_tool_brain(
            card=card,
            system="sys",
            body=SimpleNamespace(message=msg, session_id="s", persona="p"),
            history=[],
            intent=intent,
            metadata=meta,
            persona_name="P",
            deps={"brave_client": None},
        )
    return resp, meta, brain, research


def test_router_trigger_runs_research_without_the_tool_loop():
    resp, meta, brain, research = _invoke(intent=QueryIntent.NEEDS_WEB_SEARCH)
    brain.run.assert_not_called()
    research.run.assert_called_once_with("resolved question")
    assert meta.tool_decided_by == "router" and resp["used_search"] is True


def test_model_trigger_runs_research_once():
    run = ToolBrainResult(status=ST_RESEARCH, research_query="model query")
    _, meta, brain, research = _invoke(intent=QueryIntent.NEEDS_NEITHER, run=run)
    brain.run.assert_called_once()
    research.run.assert_called_once_with("model query")
    assert meta.tool_decided_by == "model"


def test_model_is_offered_research_tools_on_chitchat_turns():
    _, _, brain, research = _invoke(intent=QueryIntent.NEEDS_NEITHER)
    assert "web_search" in brain.run.call_args.kwargs["research_tools"]
    research.run.assert_not_called()


def test_no_evidence_gives_an_honest_line_and_never_falls_to_legacy():
    resp, meta, brain, _ = _invoke(intent=QueryIntent.NEEDS_WEB_SEARCH, package=_package(False))
    assert resp is not None and meta.tools_used == []
    brain.synthesize.assert_not_called()


def test_a_refused_synthesis_gets_no_citations():
    resp, meta, _, _ = _invoke(intent=QueryIntent.NEEDS_WEB_SEARCH, synth=("I cannot", True))
    assert resp is not None and meta.source_type == SourceType.LLM
    assert "Sources" not in str(resp["answer"])


def test_an_unsupported_figure_is_regenerated_once_then_removed():
    resp, _, brain, _ = _invoke(
        intent=QueryIntent.NEEDS_WEB_SEARCH, synth=("It is 42%. It reached 990 once.", False)
    )
    assert brain.synthesize.call_count == 2
    assert "990" in brain.synthesize.call_args.kwargs["question"]
    assert "990" not in str(resp["answer"]) and "42" in str(resp["answer"])


def test_a_supported_answer_is_not_regenerated():
    _, _, brain, _ = _invoke(intent=QueryIntent.NEEDS_WEB_SEARCH)
    assert brain.synthesize.call_count == 1


def test_a_reply_with_nothing_supported_left_gets_the_honest_line():
    resp, meta, _, _ = _invoke(intent=QueryIntent.NEEDS_WEB_SEARCH, synth=("It is 990.", False))
    assert meta.source_type == SourceType.LLM and "Sources" not in str(resp["answer"])


def test_a_rule_retry_keeps_the_evidence(monkeypatch):
    monkeypatch.setenv("GRAPH_ENFORCE_RULES", "true")
    get_settings.cache_clear()
    violation = SimpleNamespace(rule="dont[13]")
    with (
        patch.object(chat_mod, "check_reply", side_effect=[[violation], []]),
        patch.object(chat_mod, "reinforcement_for", return_value="Stay in role."),
    ):
        _, _, brain, _ = _invoke(intent=QueryIntent.NEEDS_WEB_SEARCH)
    assert brain.synthesize.call_count == 2
    retry = brain.synthesize.call_args.kwargs
    assert retry["evidence"] and "Stay in role." in retry["question"]


def test_a_rule_broken_by_the_figure_retry_ends_in_the_honest_line(monkeypatch):
    monkeypatch.setenv("GRAPH_ENFORCE_RULES", "true")
    get_settings.cache_clear()
    violation = SimpleNamespace(rule="dont[13]")
    with patch.object(chat_mod, "check_reply", side_effect=[[], [violation]]):
        _, meta, brain, _ = _invoke(
            intent=QueryIntent.NEEDS_WEB_SEARCH, synth=("It is 42%. It reached 990 once.", False)
        )
    assert brain.synthesize.call_count == 2 and meta.source_type == SourceType.LLM


def test_a_research_response_is_not_enforced_again_without_evidence():
    resp, _, _, _ = _invoke(intent=QueryIntent.NEEDS_WEB_SEARCH)
    assert resp[chat_mod._RULES_ENFORCED] is True


def test_a_failure_after_retrieval_gives_the_honest_line_not_legacy():
    meta = ResponseMetadata(source_type=SourceType.LLM, tools_used=[])
    with (
        patch.object(chat_mod, "_run_research", side_effect=RuntimeError("late")),
        patch("src.coordinator.services.persona_lines.line", return_value="nothing found"),
    ):
        resp = chat_mod._research_turn(
            card=EEVA,
            system="s",
            body=SimpleNamespace(message="m"),
            query="q",
            trigger="router",
            metadata=meta,
            persona_name="P",
        )
    assert resp[chat_mod._RULES_ENFORCED] is True and meta.tools_used == []


def test_a_service_crash_does_not_search_again_on_legacy():
    meta = ResponseMetadata(source_type=SourceType.LLM, tools_used=[])
    with (
        patch(
            "src.coordinator.services.research_service.ResearchService",
            side_effect=RuntimeError("boom"),
        ),
        patch("src.coordinator.services.persona_lines.line", return_value="nothing found"),
    ):
        resp = chat_mod._research_turn(
            card=EEVA,
            system="s",
            body=SimpleNamespace(message="m"),
            query="q",
            trigger="router",
            metadata=meta,
            persona_name="P",
        )
    assert resp is not None


def test_gwen_still_deflects_and_never_researches():
    _, _, brain, research = _invoke(
        intent=QueryIntent.NEEDS_WEB_SEARCH, card=GWEN, msg="what's the weather in Zurich tomorrow?"
    )
    research.run.assert_not_called()
    brain.run.assert_not_called()


def test_media_request_stays_on_the_tool_loop():
    _, _, brain, research = _invoke(
        intent=QueryIntent.NEEDS_WEB_SEARCH, msg="find me a video of cats"
    )
    research.run.assert_not_called()
    assert brain.run.call_args.kwargs["research_tools"] == frozenset()


def test_flag_off_changes_nothing(monkeypatch):
    monkeypatch.setenv("RESEARCH_SERVICE_ENABLED", "false")
    get_settings.cache_clear()
    _, _, brain, research = _invoke(intent=QueryIntent.NEEDS_WEB_SEARCH)
    research.run.assert_not_called()
    assert brain.run.call_args.kwargs["research_tools"] == frozenset()


def test_tool_brain_hands_a_web_call_to_the_coordinator():
    from src.coordinator.services.tool_brain_service import ToolBrainService

    client = MagicMock()
    client.chat.return_value = {
        "message": {
            "content": "",
            "tool_calls": [
                {"function": {"name": "brave_web_search", "arguments": {"query": "planted"}}}
            ],
        }
    }
    interceptor = MagicMock()
    interceptor.validate.return_value = SimpleNamespace(
        allowed=True, requires_hitl=False, blocked_category=None, reason=""
    )
    svc = ToolBrainService(interceptor=interceptor)
    svc._client_or_default = lambda: client
    with patch("src.coordinator.services.resource_arbiter.guard_chat_model"):
        out = svc.run(
            persona_card=EEVA,
            system_prompt="s",
            user_message="u",
            tools=[{}],
            research_tools=frozenset({"brave_web_search"}),
        )
    assert out.status == ST_RESEARCH and out.research_query == "planted"


def test_freshness_word_triggers_research_when_router_and_model_both_miss():
    _, meta, brain, research = _invoke(
        intent=QueryIntent.NEEDS_NEITHER, msg="what is the latest macOS release?"
    )
    brain.run.assert_not_called()
    research.run.assert_called_once()
    assert meta.tool_decided_by == "freshness"
    research.run.assert_called_once_with("what is the latest macOS release?")
