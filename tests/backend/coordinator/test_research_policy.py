"""Research permissions, query rules and memory labels for a persona like Gwen."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.coordinator import startup  # noqa: F401 - import order, breaks a cycle
from src.coordinator.config import get_settings
from src.coordinator.memory_manager import ConversationSummarizer
from src.coordinator.models.mcp_models import MCPConnectionError, SearchResult
from src.coordinator.routes import chat as chat_mod
from src.coordinator.schemas import ResponseMetadata, SourceType, memory_text
from src.coordinator.services import research_policy as rp
from src.coordinator.services.citation_service import CitationService
from src.coordinator.services.research_service import EvidencePackage, Source, dodged
from src.coordinator.tools import executor_bindings, registrations  # noqa: F401
from src.coordinator.tools.executor_bindings import bind_web_executors
from src.coordinator.tools.intent_classifier import QueryIntent
from src.coordinator.wall_detectors import _honorific_adopted

GWEN = {
    "key": "gwen",
    "toolsets": ["web"],
    "tools": ["image_search", "video_search"],
    "mcp_access": ["brave_search"],
    "nsfw": True,
    "research": True,
    "word_substitutions": {"shaft": "cock"},
    "cannot_lookup": ["no web"],
    "cannot_lookup_research": ["ask me straight"],
}
EEVA = {"key": "nephilim_eeva", "toolsets": ["web"], "nsfw": False}
RESULT = SearchResult("T", "https://shaft.example/shaft", "the answer is forty two here")


def turn(role, content):
    return SimpleNamespace(role=role, content=content)


@pytest.fixture
def flags(monkeypatch):
    def _set(nsfw=True):
        monkeypatch.setenv("RESEARCH_SERVICE_ENABLED", "true")
        monkeypatch.setenv("RESEARCH_NSFW_ENABLED", "true" if nsfw else "false")
        monkeypatch.setenv("TOOL_BRAIN_ENABLED", "true")
        monkeypatch.setenv("TOOL_BRAIN_UNGATED_WEB", "true")
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def _gwen_turn(
    msg,
    *,
    session="s",
    answers=True,
    intent=QueryIntent.NEEDS_WEB_SEARCH,
    history=(),
    package=None,
    recalled=None,
    rewrite=None,
    synth=("The answer is forty two.", False),
):
    bind_web_executors()
    meta = ResponseMetadata(source_type=SourceType.LLM, tools_used=[])
    brain = MagicMock()
    brain.synthesize.return_value = synth
    research = MagicMock()
    research.run.return_value = package or EvidencePackage(
        "q",
        [
            Source("T", RESULT.url, passages=["the answer is forty two here"], fetched=True),
            Source("U", "https://b.example", passages=["more text"], fetched=True),
        ],
        [RESULT],
    )
    with (
        patch("src.coordinator.services.tool_brain_service.ToolBrainService", return_value=brain),
        patch(
            "src.coordinator.services.research_service.ResearchService", return_value=research
        ) as svc,
        patch("src.coordinator.routes.chat.create_llm_client"),
        patch(
            "src.coordinator.services.research_service.dodged",
            side_effect=((lambda *a: False) if answers else (lambda *a: True)),
        ),
        patch("src.coordinator.services.query_resolution_service.QueryResolutionService") as qr,
        patch("src.coordinator.services.persona_lines.line", side_effect=lambda k, s: f"line:{s}"),
    ):
        qr.return_value.resolve.return_value = rewrite or msg
        qr.return_value.needs_clarifying.return_value = False
        resp = chat_mod._try_tool_brain(
            card=GWEN,
            system="sys",
            history=list(history),
            intent=intent,
            metadata=meta,
            body=SimpleNamespace(
                message=msg, session_id=session, persona="gwen", extra_system_context=recalled
            ),
            persona_name="Gwen",
            deps={"brave_client": None},
        )
    return resp, meta, brain, research, svc


def _gwen_turn_sessionless(msg):
    return _gwen_turn(msg, session=None)


class TestGrant:
    def test_flag_off_she_deflects_as_today(self, flags):
        flags(nsfw=False)
        resp, _, _, research, _ = _gwen_turn("what is the weather in Zurich tomorrow?")
        research.run.assert_not_called()
        assert "no web" in str(resp["answer"])

    def test_flag_on_she_researches(self, flags):
        flags()
        resp, meta, _, research, _ = _gwen_turn("what is the weather in Zurich tomorrow?")
        research.run.assert_called_once()
        assert meta.tool_decided_by == "router" and resp["used_search"] is True

    def test_a_card_without_research_gets_nothing(self, flags):
        flags()
        assert rp.policy_for({"nsfw": True}, ["web_search"]) is None

    def test_a_personal_message_stays_in_the_scene(self, flags):
        flags()
        resp, _, _, research, _ = _gwen_turn("what do you think of my weather?")
        research.run.assert_not_called()
        assert "ask me straight" in str(resp["answer"])

    def test_the_model_cannot_propose_for_her(self):
        assert "model" not in rp._NSFW.proposers


class TestNeverBrave:
    @pytest.mark.parametrize("failure", [True, False])
    def test_no_brave_call_on_success_or_error(self, flags, failure):
        flags()
        bind_web_executors()
        brave = MagicMock()
        sx = MagicMock()
        if failure:
            sx.search.side_effect = MCPConnectionError("down")
        else:
            sx.search.return_value = [RESULT]
        with (
            patch("src.coordinator.startup.get_brave_client", return_value=brave),
            patch("src.coordinator.searxng_client.SearxngClient", return_value=sx),
        ):
            for _ in range(3):
                executor_bindings._make_search_executor("web_search")({"query": "q"}, GWEN)
        brave.search_web.assert_not_called()


class TestQueryRules:
    TURNS = ["tell me about the Ledger Nano X", "how much does it cost"]

    def test_a_general_word_the_rewriter_adds_is_kept(self):
        assert rp.query_is_clean("second place finisher Formula 1", "and who came second?", set())

    def test_a_remembered_name_from_an_earlier_turn_fails(self):
        private = rp.private_terms(GWEN, "<remembered>He lives in Brugg with Anna.</remembered>")
        assert not rp.query_is_clean("flat cost Brugg", "how much is a flat", private)

    def test_a_word_typed_now_is_the_operators_to_send(self):
        private = rp.private_terms(GWEN, "<remembered>He lives in Brugg.</remembered>")
        assert rp.query_is_clean("flat prices Brugg", "flat prices in Brugg", private)

    def test_a_freshness_message_naming_something_private_stays_in_the_scene(self, flags):
        flags()
        _, _, _, research, _ = _gwen_turn(
            "how much does Daddy want it", intent=QueryIntent.NEEDS_NEITHER
        )
        research.run.assert_not_called()

    def test_ordinary_context_words_are_not_private(self):
        private = rp.private_terms(
            GWEN, "Formula rules apply. <remembered>Germany. He likes tea.</remembered>"
        )
        assert "formula" not in private and "germany" not in private

    def test_a_private_freshness_turn_on_a_web_intent_reaches_the_scope_guard(self, flags):
        flags()
        with patch("src.coordinator.services.research_policy.proposal", return_value="freshness"):
            resp, _, _, research, _ = _gwen_turn("how much does Daddy want it")
        research.run.assert_not_called()
        assert "ask me straight" in str(resp["answer"])

    def test_only_the_recent_turns_are_used(self):
        history = [
            turn("user", "one"),
            turn("assistant", "x"),
            turn("user", "two"),
            turn("user", "three"),
        ]
        assert rp.recent_user_turns(history, 2) == ["two", "three"]

    def test_a_rejected_rewrite_asks_instead_of_sending_a_fragment(self, flags):
        flags()
        resp, _, _, research, _ = _gwen_turn(
            "what does it cost",
            history=[turn("user", "tell me about the Ledger")],
            recalled="<remembered>He lives in Oslo.</remembered>",
            rewrite="Ledger cost Oslo",
        )
        research.run.assert_not_called()
        assert "research_unclear" in str(resp["answer"])


class TestAnswer:
    def test_her_earlier_words_stay_out_of_synthesis(self, flags):
        flags()
        history = [turn("user", "tell me about the Ledger"), turn("assistant", "PRIVATE reply")]
        _, _, brain, _, _ = _gwen_turn(
            "what does it cost now", history=history, rewrite="Ledger cost now"
        )
        question = brain.synthesize.call_args.kwargs["question"]
        assert "tell me about the Ledger" not in question and "PRIVATE" not in question
        assert "Ledger cost now" in question

    def test_a_standalone_question_gets_no_earlier_context(self, flags):
        flags()
        history = [turn("user", "who won the race?")]
        _, _, brain, _, _ = _gwen_turn("what is the weather in Zurich?", history=history)
        assert "who won the race" not in brain.synthesize.call_args.kwargs["question"]

    def test_a_substitution_cannot_rewrite_a_url(self, flags):
        flags()
        resp, _, _, _, _ = _gwen_turn(
            "what is the weather today?", synth=("The answer is forty two shaft.", False)
        )
        text = str(resp["answer"])
        assert "forty two cock" in text and "shaft.example" in text

    def test_sources_are_compact(self):
        line = CitationService.compact_citations([RESULT])
        assert line == "\n\n🔍 [shaft.example](https://shaft.example/shaft)"

    def test_consequential_on_thin_evidence_declines(self, flags):
        flags()
        thin = EvidencePackage(
            "q", [Source("T", RESULT.url, passages=["text"], fetched=False)], [RESULT]
        )
        resp, meta, brain, _, _ = _gwen_turn("what is the right medication dose?", package=thin)
        brain.synthesize.assert_not_called()
        assert "research_unverified" in str(resp["answer"]) and meta.tools_used == []

    def test_the_cap_pauses_research(self, flags, monkeypatch):
        flags()
        monkeypatch.setattr(rp.research_cap, "allow", lambda *a: False)
        resp, _, _, research, _ = _gwen_turn("what is the weather today?")
        research.run.assert_not_called()
        assert "research_paused" in str(resp["answer"])


def test_a_failure_in_a_retry_cannot_send_the_unchecked_draft(flags):
    flags()
    meta = ResponseMetadata(source_type=SourceType.LLM, tools_used=[])
    brain = MagicMock()
    brain.synthesize.side_effect = [("short draft", False), RuntimeError("boom")]
    research = MagicMock()
    research.run.return_value = EvidencePackage(
        "q",
        [
            Source("T", RESULT.url, passages=["text"], fetched=True),
            Source("U", "https://b.example", passages=["text"], fetched=True),
        ],
        [RESULT],
    )
    with (
        patch("src.coordinator.services.tool_brain_service.ToolBrainService", return_value=brain),
        patch("src.coordinator.services.research_service.ResearchService", return_value=research),
        patch("src.coordinator.services.persona_lines.line", return_value="honest line"),
    ):
        resp = chat_mod._research_turn(
            card=GWEN,
            system="s",
            body=SimpleNamespace(message="m"),
            query="q",
            trigger="router",
            metadata=meta,
            persona_name="P",
            policy=rp._NSFW,
        )
    assert "short draft" not in str(resp["answer"]) and "honest line" in str(resp["answer"])


class TestDodge:
    EVIDENCE = (
        "Kimi Antonelli won the United States Grand Prix at the Circuit of the Americas "
        "in Austin Texas for Mercedes ahead of Lando Norris and Oscar Piastri this season"
    )

    def test_a_reply_that_ignores_the_evidence_is_a_dodge(self):
        assert dodged(
            "I can't focus on racing when I need you so much, Daddy.", self.EVIDENCE, "who won?"
        )

    def test_a_real_answer_is_not(self):
        reply = "Kimi Antonelli won the Grand Prix at the Circuit of the Americas for Mercedes, ahead of Norris."
        assert not dodged(reply, self.EVIDENCE, "who won?")

    def test_saying_it_found_nothing_is_honest_not_a_dodge(self):
        assert not dodged("I couldn't find that in the results, Daddy.", self.EVIDENCE, "who won?")

    def test_a_dodge_is_retried_once_and_a_second_keeps_no_sources(self, flags):
        flags()
        resp, _, brain, _, _ = _gwen_turn(
            "what is the weather today?",
            answers=False,
            synth=("I need you so much right now, Daddy.", False),
        )
        assert brain.synthesize.call_count == 2
        assert "first sentence" in brain.synthesize.call_args.kwargs["question"]
        assert "🔍" not in str(resp["answer"])


class TestProposal:
    def test_a_followup_to_a_researched_reply_researches(self):
        history = [turn("assistant", "It is a wallet.\n\n🔍 Sources: x")]
        assert rp.proposal(rp._ORDINARY, False, "how much does it cost?", history) == "followup"

    @pytest.mark.parametrize(
        "message", ["thanks", "yes", "ok lol", "why?", "and then?", "that is great"]
    )
    def test_an_acknowledgement_does_not_research(self, message):
        history = [turn("assistant", "It is a wallet.\n\n🔍 Sources: x")]
        assert rp.proposal(rp._ORDINARY, False, message, history) is None

    def test_a_history_ending_on_the_current_turn_still_finds_her_reply(self):
        history = [
            turn("assistant", "It is a wallet.\n\n🔍 Sources: x"),
            turn("user", "and who made it?"),
        ]
        assert rp.proposal(rp._ORDINARY, False, "and who made it?", history) == "followup"

    def test_a_citation_in_an_earlier_bubble_of_her_reply_counts(self):
        history = [
            turn("assistant", "Part one. 🔍 [a.example](https://a.example)"),
            turn("assistant", "Do you like it?"),
        ]
        assert rp.proposal(rp._ORDINARY, False, "and who made it?", history) == "followup"

    def test_a_citation_before_your_last_turn_does_not_count(self):
        history = [
            turn("assistant", "Done. 🔍 Sources: x"),
            turn("user", "cool"),
            turn("assistant", "Yes."),
        ]
        assert rp.proposal(rp._ORDINARY, False, "and who made it?", history) is None

    def test_a_mark_inside_prose_is_not_a_researched_reply(self):
        history = [turn("assistant", "I searched my heart 🔍 for you")]
        assert rp.proposal(rp._ORDINARY, False, "how much does it cost?", history) is None

    def test_a_followup_to_chat_does_not(self):
        history = [turn("assistant", "It is lovely.")]
        assert rp.proposal(rp._ORDINARY, False, "how much does it cost?", history) is None

    def test_a_comparison_researches(self):
        assert (
            rp.proposal(rp._ORDINARY, False, "compare Ledger and Trezor for a beginner", [])
            == "freshness"
        )


def test_a_sessionless_request_is_not_capped(flags, monkeypatch):
    flags()
    monkeypatch.setattr(rp.research_cap, "allow", lambda *a: False)
    _, _, _, research, _ = _gwen_turn_sessionless("what is the weather today?")
    research.run.assert_called_once()


def test_hourly_cap_recovers():
    cap = rp.HourlyCap()
    assert (
        cap.allow("s", 1, now=0) and not cap.allow("s", 1, now=10) and cap.allow("s", 1, now=3700)
    )


class TestMemory:
    MSG = {
        "role": "assistant",
        "content": "Web says he lives in Oslo\n\n🔍 Sources:\n- x",
        "source_type": "tool_brain",
    }

    def test_an_image_reply_from_the_tool_loop_is_not_marked(self):
        message = {"role": "assistant", "content": "Drawing that now", "source_type": "tool_action"}
        assert memory_text(message) == "Drawing that now"

    def test_a_research_reply_with_no_citation_line_is_still_marked(self):
        message = {"role": "assistant", "content": "It is 42", "source_type": "tool_brain"}
        assert memory_text(message).startswith("[from web research]")

    def test_a_research_reply_is_marked_in_summaries(self):
        text = ConversationSummarizer()._format_messages([self.MSG])
        assert "[from web research] Web says" in text

    def test_ordinary_turns_are_not_marked(self):
        assert memory_text({"role": "user", "content": "hi", "source_type": "tool_brain"}) == "hi"
        assert memory_text({"role": "assistant", "content": "hi", "source_type": "llm"}) == "hi"

    def test_the_summary_keeps_looked_up_apart_from_user_info(self):
        llm = MagicMock()
        llm._invoke.return_value = "**Summary:** x"
        summarizer = ConversationSummarizer(llm)
        summarizer.summarize_segment(
            messages=[self.MSG, {"role": "user", "content": "ok"}, self.MSG, self.MSG, self.MSG],
            max_summary_tokens=200,
            persona_name="Gwen",
        )
        prompt = llm._invoke.call_args.kwargs["prompt"]
        assert "**Looked Up:**" in prompt and "Never anything from a line" in prompt
        assert "[from web research]" in prompt

    def test_every_segment_is_built_from_messages_never_from_a_summary(self):
        """Why a label cannot decay: there is no summary of a summary to lose it in."""
        import inspect

        source = inspect.getsource(ConversationSummarizer.summarize_segment)
        assert "summary_repo" not in source and "get_summaries" not in source


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("Yes, Sir Keir Starmer leads the party.", False),
        ("Sure, Master Chief returns in the new game.", False),
        ("Yes, sir.", True),
        ("Anything you want, master.", True),
    ],
)
def test_honorific_detector_spares_names(reply, expected):
    assert _honorific_adopted(reply) is expected
