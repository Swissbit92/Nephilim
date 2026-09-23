# tests/backend/coordinator/test_tool_brain_route.py
"""ADR-008 TB4/TB5: route orchestration of the tool-brain loop.

TB5: the loop is WEB-LANE ONLY — engages only on NEEDS_WEB_SEARCH, offers only
web tools (never wallet), and only returns a search-grounded answer (else falls
through to the legacy force-search floor).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.coordinator.routes import chat as chat_mod
from src.coordinator.tools import registrations  # noqa: F401 - register specs
from src.coordinator.tools.executor_bindings import bind_web_executors
from src.coordinator.services.tool_brain_service import (
    ToolBrainResult, ST_ANSWERED, ST_SILENT, ST_HITL,
)
from src.coordinator.schemas import ResponseMetadata, SourceType
from src.coordinator.tools.intent_classifier import QueryIntent


@pytest.fixture(autouse=True)
def _bound():
    bind_web_executors()
    yield


def _body(msg="latest news"):
    return SimpleNamespace(message=msg, session_id="s1", persona="nephilim_eeva")


# Real EEVA-shaped card: granted web + wallet (the toolset that caused the live
# wallet fixation). TB5 must scope the loop to web only.
EEVA = {"key": "nephilim_eeva", "mcp_access": ["brave_search", "solana_wallet"], "nsfw": False}
GWEN = {"key": "gwen", "toolsets": ["web"], "tools": ["image_search", "video_search"],
        "mcp_access": ["brave_search"], "nsfw": True}


def _invoke(result, *, card=EEVA, intent=QueryIntent.NEEDS_WEB_SEARCH, msg="latest news"):
    """Run _try_tool_brain with ToolBrainService.run mocked to `result`;
    capture the tools actually passed to the loop (real registry scoping)."""
    meta = ResponseMetadata(source_type=SourceType.LLM, tools_used=[])
    captured = {}
    fake_svc = MagicMock()

    def _run(**kw):
        captured["tools"] = kw.get("tools")
        return result
    fake_svc.run.side_effect = _run

    with patch("src.coordinator.services.tool_brain_service.ToolBrainService",
               return_value=fake_svc):
        resp = chat_mod._try_tool_brain(
            card=card, system="sys", body=_body(msg), history=[], intent=intent,
            metadata=meta, persona_name="P", deps={"brave_client": None})
    return resp, meta, captured


def _grounded(answer="Real news"):
    return ToolBrainResult(status=ST_ANSWERED, answer=answer, used_search=True,
                           search_results=[MagicMock(title="T", url="https://x",
                                                      description="d", age=None)])


class TestIntentScoping:
    def test_wallet_intent_returns_none(self):
        resp, _, cap = _invoke(_grounded(), intent=QueryIntent.NEEDS_WALLET)
        assert resp is None
        assert "tools" not in cap  # loop never even ran

    def test_neither_intent_returns_none(self):
        resp, _, cap = _invoke(_grounded(), intent=QueryIntent.NEEDS_NEITHER)
        assert resp is None
        assert "tools" not in cap

    def test_web_intent_offers_only_web_tools(self):
        # EEVA has wallet access, but the loop must see WEB tools only.
        with patch("src.coordinator.services.citation_service.CitationService.auto_generate_citations",
                   return_value=""):
            _, _, cap = _invoke(_grounded(), intent=QueryIntent.NEEDS_WEB_SEARCH)
        names = {t["function"]["name"] for t in cap["tools"]}
        assert names
        assert not any(n.startswith("wallet_") or n.startswith("solana_") for n in names)
        assert "web_search" in names

    def test_gwen_web_subset_only(self):
        """Her surface is the image/video subset — asserted on a MEDIA turn.

        This used to be asserted on a NEEDS_WEB_SEARCH turn, which encoded the
        defect: offering media tools for a general-lookup intent is exactly how
        the weather question got answered with an invented forecast. That turn
        now deflects (see test_a_web_intent_turn_deflects_instead_of_offering_media),
        so the subset is checked where offering it is correct."""
        with patch("src.coordinator.services.citation_service.CitationService.auto_generate_citations",
                   return_value=""):
            # NEEDS_WEB_SEARCH keeps the lane open (ungated mode is OFF by
            # default, so NEEDS_NEITHER would return None before any tool is
            # built); the media phrasing is what makes the turn legitimately
            # servable by her surface.
            _, _, cap = _invoke(_grounded(), card=GWEN, msg="show me a picture of that dress",
                                intent=QueryIntent.NEEDS_WEB_SEARCH)
        names = {t["function"]["name"] for t in cap["tools"]}
        assert names <= {"image_search", "video_search"}
        assert names, "gwen was offered no web tools at all on a media turn"

    def test_a_web_intent_turn_deflects_instead_of_offering_media(self):
        """The fix. gwen holds no general-lookup tool, so a web-intent turn must
        not be handed her media tools to improvise with."""
        resp, _, cap = _invoke(_grounded(), card=GWEN, msg="what's the weather tomorrow?",
                               intent=QueryIntent.NEEDS_WEB_SEARCH)
        assert resp is not None, "fell through to legacy, which ignores her allowlist"
        assert "tools" not in cap, "a tool surface was built for an out-of-surface turn"
        assert resp["metadata"]["tools_used"] == []
        assert resp["used_search"] is False


class TestMediaToolForcing:
    """A colloquial media query narrows the surface to the ONE matching tool,
    so native reliably calls it instead of choice-paralysing over 4 web tools."""

    def _names(self, msg, card=EEVA):
        with patch("src.coordinator.services.citation_service.CitationService.auto_generate_citations",
                   return_value=""):
            _, _, cap = _invoke(_grounded(), card=card, msg=msg)
        return {t["function"]["name"] for t in cap["tools"]}

    def test_video_query_forces_video_search_only(self):
        assert self._names("find me a video of a sunset") == {"video_search"}

    def test_image_query_forces_image_search_only(self):
        assert self._names("find me some images of a sunset") == {"image_search"}

    def test_gwen_video_query_forces_video_only(self):
        assert self._names("find me a video of a redhead", card=GWEN) == {"video_search"}

    def test_non_media_query_keeps_full_web_surface(self):
        names = self._names("what's the latest bitcoin news")
        assert len(names) > 1 and "web_search" in names

    def test_forcing_skipped_if_persona_lacks_tool(self):
        # A web persona WITHOUT video_search must not be narrowed to nothing.
        card = {"key": "x", "mcp_access": ["brave_search"],
                "toolsets": ["web"], "tools": ["web_search"]}
        names = self._names("find me a video of a sunset", card=card)
        assert names == {"web_search"}


class TestGroundednessCoverage:
    def test_answered_with_search_returns(self):
        with patch("src.coordinator.services.citation_service.CitationService.auto_generate_citations",
                   return_value="\n\nSources"):
            resp, meta, _ = _invoke(_grounded())
        assert resp is not None
        assert meta.source_type == SourceType.TOOL_BRAIN and meta.tools_used == ["web_search"]
        assert resp.get("used_search") is True

    def test_inline_ref_markers_stripped(self):
        result = ToolBrainResult(
            status=ST_ANSWERED, used_search=True,
            answer="Bitcoin is $62k[REF]5[/REF] today[REF]2[/REF].",
            search_results=[MagicMock(title="T", url="https://x", description="d", age=None)])
        with patch("src.coordinator.services.citation_service.CitationService.auto_generate_citations",
                   return_value="\n\n🔍 Sources"):
            resp, _, _ = _invoke(result)
        body = " ".join(resp["messages"]) if resp.get("messages") else str(resp)
        assert "[REF]" not in body and "[/REF]" not in body

    def test_answered_WITHOUT_search_falls_through(self):
        # The live-test fabrication case: model answered a web-intent query from
        # training data (used_search=False) -> must NOT return; fall to legacy.
        resp, _, _ = _invoke(ToolBrainResult(status=ST_ANSWERED,
                             answer="Switzerland news today...", used_search=False))
        assert resp is None

    def test_silent_falls_through(self):
        resp, _, _ = _invoke(ToolBrainResult(status=ST_SILENT, answer="partial"))
        assert resp is None

    def test_refused_synthesis_falls_through_no_citations(self):
        # M3: search succeeded but synthesis refused (survived the prefill retry).
        # Must NOT staple citations onto a refusal -> fall through to legacy.
        called = {"cited": False}

        def _cite(_results):
            called["cited"] = True
            return "\n\n🔍 Sources"

        result = ToolBrainResult(
            status=ST_ANSWERED, used_search=True, refused=True,
            answer="I cannot and will not search for images.",
            search_results=[MagicMock(title="T", url="https://x", description="d", age=None)])
        with patch("src.coordinator.services.citation_service.CitationService.auto_generate_citations",
                   side_effect=_cite):
            resp, _, _ = _invoke(result)
        assert resp is None                 # fell through to legacy floor
        assert called["cited"] is False     # citations were NEVER generated


class TestWalletDefensive:
    def test_hitl_still_handed_off_if_it_ever_fires(self):
        # Defence-in-depth: web-only tools mean the model can't call wallet, but
        # if a wallet status ever surfaces it must still go to the wallet flow.
        with patch("src.coordinator.routes.chat.QueryHandlerService") as QHS:
            QHS.return_value.handle_wallet_query.return_value = {"answer": "propose"}
            resp, _, _ = _invoke(ToolBrainResult(status=ST_HITL, hitl_tool="solana_propose_swap"))
        assert resp == {"answer": "propose"}


class TestGuards:
    def test_no_web_tools_returns_none(self):
        card = {"key": "x", "mcp_access": ["solana_wallet"]}
        resp, _, _ = _invoke(_grounded(), card=card)
        assert resp is None

    def test_loop_exception_falls_through(self):
        meta = ResponseMetadata(source_type=SourceType.LLM, tools_used=[])
        fake_svc = MagicMock()
        fake_svc.run.side_effect = RuntimeError("boom")
        with patch("src.coordinator.services.tool_brain_service.ToolBrainService",
                   return_value=fake_svc):
            resp = chat_mod._try_tool_brain(
                card=EEVA, system="s", body=_body(), history=[],
                intent=QueryIntent.NEEDS_WEB_SEARCH,
                metadata=meta, persona_name="P", deps={"brave_client": None})
        assert resp is None

    def test_flag_default_off(self):
        from src.coordinator.config import ToolBrainSettings
        assert ToolBrainSettings.model_fields["enabled"].default is False
