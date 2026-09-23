"""
Unit tests for src/coordinator/routes/chat.py

Mocks (patched via ExitStack to handle dynamic patch lists):
  - src.coordinator.routes.chat.get_persona_card
  - src.coordinator.routes.chat.build_system_prompt / build_greeting_user_prompt
  - src.coordinator.routes.chat.create_llm_client / log_context_stats
  - src.coordinator.routes.chat.classify_query_intent / get_tools_for_query
  - src.coordinator.routes.chat.QueryHandlerService / has_active_wallet_flow
  - src.coordinator.routes.chat.post_process_first_person
  - src.coordinator.routes.chat.force_multi_message_split / parse_multi_message_response
  - src.coordinator.routes.chat.handle_session_chat (via service module)
  - src.coordinator.startup.get_* (all deps in _get_dependencies)
  - src.coordinator.config.get_settings
"""
from __future__ import annotations

import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from fastapi.testclient import TestClient
from src.coordinator.server import app
from src.coordinator.tools.intent_classifier import QueryIntent

# Create client WITHOUT context manager to skip lifespan (no live Ollama/DB)
client = TestClient(app)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_settings():
    s = MagicMock()
    s.ollama.context_window = 4096
    s.ollama.model = "test-model"
    s.db_path = "/tmp/test.db"
    # ADR-007 groundedness gate: pin OFF (MagicMock auto-attributes are truthy,
    # so an un-pinned s.groundedness.gate_enabled would silently enable a second
    # classifier LLM call in every existing no-tools/fallback test here).
    s.groundedness.gate_enabled = False
    s.groundedness.reinforcement_check_enabled = False
    # Persona prompt flags: pin to their real defaults. A MagicMock attribute is
    # truthy, so leaving these unset silently enables behaviour under test here —
    # and unpin_depth_turns is compared numerically, which a MagicMock cannot
    # satisfy at all.
    s.agent.constraints_in_prompt = False
    s.agent.unpin_on_depth = False
    s.agent.unpin_depth_turns = 6
    return s


def _make_card(key="eeva", display_name="E.E.V.A.", rarity="legendary", mcp_access=None):
    return {
        "key": key,
        "display_name": display_name,
        "rarity": rarity,
        "mcp_access": mcp_access or [],
    }


def _make_llm_client(answer="Hello there!"):
    llm = MagicMock()
    llm.complete.return_value = answer
    return llm


def _dependency_getter_names() -> list[str]:
    """The startup getters `routes.chat._get_dependencies()` actually resolves.

    Derived from the function's own source rather than hardcoded. The previous
    hardcoded list silently went stale when ADR-011 added `get_session_note_repo`
    to `_get_dependencies`; that getter RAISES when its singleton is
    uninitialised, so every test in this module failed — except in a full-suite
    run, where an earlier test happened to initialise startup and masked it.
    Deriving the list means a new dependency is neutralised automatically.
    """
    import inspect
    import re

    from src.coordinator.routes import chat as _chat_routes

    source = inspect.getsource(_chat_routes._get_dependencies)
    names = sorted(set(re.findall(r"startup\.(get_\w+)\(", source)))
    assert names, "no startup getters found in _get_dependencies() — source/regex drift"
    return names


def _startup_patches():
    """Patches that neutralise every _get_dependencies() startup call."""
    return [
        # brave stays None: these tests exercise the llm-only / non-brave paths.
        patch(
            f"src.coordinator.startup.{name}",
            return_value=None if name == "get_brave_client" else MagicMock(),
        )
        for name in _dependency_getter_names()
    ]


def _apply(stack: ExitStack, patches: list):
    """Enter all patches via an ExitStack and return the mocks."""
    return [stack.enter_context(p) for p in patches]


def _greet_patches(card, llm, answer, msgs, flow):
    return [
        patch("src.coordinator.routes.chat.get_persona_card", return_value=card),
        patch("src.coordinator.routes.chat.build_system_prompt", return_value="<s>"),
        patch("src.coordinator.routes.chat.build_greeting_user_prompt", return_value="<g>"),
        patch("src.coordinator.routes.chat.create_llm_client", return_value=llm),
        patch("src.coordinator.routes.chat.post_process_first_person", return_value=(answer, False)),
        patch("src.coordinator.routes.chat.force_multi_message_split", return_value=answer),
        patch("src.coordinator.routes.chat.parse_multi_message_response", return_value=(msgs, flow)),
    ]


def _chat_patches(card, llm, answer, msgs, flow, intent=QueryIntent.NEEDS_NEITHER, tools=None,
                  wallet_flow=False, rewritten=False):
    if tools is None:
        tools = []
    return [
        patch("src.coordinator.routes.chat.get_persona_card", return_value=card),
        patch("src.coordinator.routes.chat.build_system_prompt", return_value="<s>"),
        patch("src.coordinator.routes.chat.create_llm_client", return_value=llm),
        patch("src.coordinator.routes.chat.log_context_stats", return_value={}),
        patch("src.coordinator.routes.chat.classify_query_intent", return_value=intent),
        patch("src.coordinator.routes.chat.get_tools_for_query", return_value=tools),
        # has_active_wallet_flow is imported inside the function body, patch at source
        patch("src.coordinator.services.query_handler_service.has_active_wallet_flow", return_value=wallet_flow),
        patch("src.coordinator.routes.chat.post_process_first_person", return_value=(answer, rewritten)),
        patch("src.coordinator.routes.chat.force_multi_message_split", return_value=answer),
        patch("src.coordinator.routes.chat.parse_multi_message_response", return_value=(msgs, flow)),
        patch("src.coordinator.config.get_settings", return_value=_make_settings()),
        *_startup_patches(),
    ]


# ── /persona/greet ────────────────────────────────────────────────────────────

class TestGreet:
    def test_greet_happy_path_single_message(self):
        card = _make_card()
        llm = _make_llm_client("Greetings, seeker.")
        with ExitStack() as stack:
            _apply(stack, _greet_patches(card, llm, "Greetings, seeker.", ["Greetings, seeker."], "single"))
            resp = client.post("/persona/greet", json={"persona": "eeva"})

        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert data["message_flow"] == "single"
        assert data["message_count"] == 1

    def test_greet_happy_path_multi_message(self):
        card = _make_card()
        llm = _make_llm_client("<msg>Hello</msg><msg>World</msg>")
        with ExitStack() as stack:
            _apply(stack, _greet_patches(card, llm, "<msg>Hello</msg><msg>World</msg>", ["Hello", "World"], "multi"))
            resp = client.post("/persona/greet", json={"persona": "eeva"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["message_flow"] == "multi"
        assert data["message_count"] == 2
        assert data["answer"] == ["Hello", "World"]

    def test_greet_unknown_persona(self):
        with patch("src.coordinator.routes.chat.get_persona_card", return_value=None):
            resp = client.post("/persona/greet", json={"persona": "no-such"})
        assert resp.status_code == 400
        assert "Unknown persona" in resp.json()["detail"]

    def test_greet_llm_failure(self):
        card = _make_card()
        llm = MagicMock()
        llm.complete.side_effect = RuntimeError("Ollama down")
        with ExitStack() as stack:
            _apply(stack, [
                patch("src.coordinator.routes.chat.get_persona_card", return_value=card),
                patch("src.coordinator.routes.chat.build_system_prompt", return_value="<s>"),
                patch("src.coordinator.routes.chat.build_greeting_user_prompt", return_value="<g>"),
                patch("src.coordinator.routes.chat.create_llm_client", return_value=llm),
            ])
            resp = client.post("/persona/greet", json={"persona": "eeva"})

        assert resp.status_code == 503
        assert "LLM service temporarily unavailable" in resp.json()["detail"]

    def test_greet_rewritten_flag_propagated(self):
        card = _make_card()
        llm = _make_llm_client("She is here")
        with ExitStack() as stack:
            _apply(stack, [
                patch("src.coordinator.routes.chat.get_persona_card", return_value=card),
                patch("src.coordinator.routes.chat.build_system_prompt", return_value="<s>"),
                patch("src.coordinator.routes.chat.build_greeting_user_prompt", return_value="<g>"),
                patch("src.coordinator.routes.chat.create_llm_client", return_value=llm),
                patch("src.coordinator.routes.chat.post_process_first_person", return_value=("I am here", True)),
                patch("src.coordinator.routes.chat.force_multi_message_split", return_value="I am here"),
                patch("src.coordinator.routes.chat.parse_multi_message_response", return_value=(["I am here"], "single")),
            ])
            resp = client.post("/persona/greet", json={"persona": "eeva"})

        assert resp.status_code == 200
        assert resp.json()["rewritten"] is True

    def test_greet_none_persona(self):
        card = _make_card(key="default")
        llm = _make_llm_client("Hi")
        with ExitStack() as stack:
            _apply(stack, _greet_patches(card, llm, "Hi", ["Hi"], "single"))
            resp = client.post("/persona/greet", json={})
        assert resp.status_code == 200


# ── /persona/chat ─────────────────────────────────────────────────────────────

class TestChat:
    def test_chat_happy_path_llm_only(self):
        card = _make_card()
        llm = _make_llm_client("Hello!")
        with ExitStack() as stack:
            _apply(stack, _chat_patches(card, llm, "Hello!", ["Hello!"], "single"))
            resp = client.post("/persona/chat", json={"persona": "eeva", "message": "Hello", "history": []})

        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert data["message_flow"] == "single"
        assert data["used_search"] is False

    def test_chat_unknown_persona(self):
        # deps = _get_dependencies() runs before the persona card check, so startup patches needed
        with ExitStack() as stack:
            _apply(stack, [
                patch("src.coordinator.routes.chat.get_persona_card", return_value=None),
                *_startup_patches(),
            ])
            resp = client.post("/persona/chat", json={"persona": "ghost", "message": "Hi"})
        assert resp.status_code == 400
        assert "Unknown persona" in resp.json()["detail"]

    def test_chat_llm_failure_raises_503(self):
        card = _make_card()
        llm = MagicMock()
        llm.complete.side_effect = RuntimeError("Ollama crashed")
        with ExitStack() as stack:
            _apply(stack, [
                patch("src.coordinator.routes.chat.get_persona_card", return_value=card),
                patch("src.coordinator.routes.chat.build_system_prompt", return_value="<s>"),
                patch("src.coordinator.routes.chat.create_llm_client", return_value=llm),
                patch("src.coordinator.routes.chat.log_context_stats", return_value={}),
                patch("src.coordinator.routes.chat.classify_query_intent", return_value=QueryIntent.NEEDS_NEITHER),
                patch("src.coordinator.routes.chat.get_tools_for_query", return_value=[]),
                patch("src.coordinator.services.query_handler_service.has_active_wallet_flow", return_value=False),
                patch("src.coordinator.config.get_settings", return_value=_make_settings()),
                *_startup_patches(),
            ])
            resp = client.post("/persona/chat", json={"persona": "eeva", "message": "Hi"})

        assert resp.status_code == 503
        assert "LLM service temporarily unavailable" in resp.json()["detail"]

    def test_chat_routes_wallet_intent(self):
        card = _make_card(mcp_access=["solana_wallet"])
        wallet_response = {
            "answer": ["I can check your balance."],
            "message_flow": "single",
            "message_count": 1,
            "used_search": False,
            "metadata": {},
            "rewritten": False,
        }
        handler = MagicMock()
        handler.handle_wallet_query.return_value = wallet_response

        with ExitStack() as stack:
            _apply(stack, [
                patch("src.coordinator.routes.chat.get_persona_card", return_value=card),
                patch("src.coordinator.routes.chat.build_system_prompt", return_value="<s>"),
                patch("src.coordinator.routes.chat.log_context_stats", return_value={}),
                patch("src.coordinator.routes.chat.classify_query_intent", return_value=QueryIntent.NEEDS_WALLET),
                patch("src.coordinator.routes.chat.get_tools_for_query", return_value=[]),
                patch("src.coordinator.services.query_handler_service.has_active_wallet_flow", return_value=False),
                patch("src.coordinator.routes.chat.QueryHandlerService", return_value=handler),
                patch("src.coordinator.routes.chat.QueryHandlerService._build_wallet_state_context", return_value=""),
                patch("src.coordinator.config.get_settings", return_value=_make_settings()),
                *_startup_patches(),
            ])
            resp = client.post("/persona/chat", json={"persona": "eeva", "message": "what is my balance"})

        assert resp.status_code == 200
        handler.handle_wallet_query.assert_called_once()

    def test_chat_routes_brave_intent(self):
        card = _make_card(mcp_access=["brave_search"], rarity="warden")
        brave_tool = {"function": {"name": "brave_web_search"}}
        brave_response = {
            "answer": ["Search result."],
            "message_flow": "single",
            "message_count": 1,
            "used_search": True,
            "metadata": {},
            "rewritten": False,
        }
        handler = MagicMock()
        handler.handle_brave_query.return_value = brave_response

        with ExitStack() as stack:
            _apply(stack, [
                patch("src.coordinator.routes.chat.get_persona_card", return_value=card),
                patch("src.coordinator.routes.chat.build_system_prompt", return_value="<s>"),
                patch("src.coordinator.routes.chat.log_context_stats", return_value={}),
                patch("src.coordinator.routes.chat.classify_query_intent", return_value=QueryIntent.NEEDS_WEB_SEARCH),
                patch("src.coordinator.routes.chat.get_tools_for_query", return_value=[brave_tool]),
                patch("src.coordinator.services.query_handler_service.has_active_wallet_flow", return_value=False),
                patch("src.coordinator.routes.chat.QueryHandlerService", return_value=handler),
                patch("src.coordinator.config.get_settings", return_value=_make_settings()),
                # chat.py binds get_settings at import; patch it there too so the
                # Phase-3 agentic branch is OFF for this legacy-routing assertion.
                patch("src.coordinator.routes.chat.get_settings", return_value=_make_settings()),
                *_startup_patches(),
            ])
            resp = client.post("/persona/chat", json={"persona": "cipher", "message": "latest news"})

        assert resp.status_code == 200
        handler.handle_brave_query.assert_called_once()

    def test_chat_active_wallet_flow_bypasses_intent_classification(self):
        card = _make_card(mcp_access=["solana_wallet"])
        wallet_response = {
            "answer": ["Creating wallet..."],
            "message_flow": "single",
            "message_count": 1,
            "used_search": False,
            "metadata": {},
            "rewritten": False,
        }
        handler = MagicMock()
        handler.handle_wallet_query.return_value = wallet_response

        with ExitStack() as stack:
            _apply(stack, [
                patch("src.coordinator.routes.chat.get_persona_card", return_value=card),
                patch("src.coordinator.routes.chat.build_system_prompt", return_value="<s>"),
                patch("src.coordinator.routes.chat.log_context_stats", return_value={}),
                patch("src.coordinator.routes.chat.classify_query_intent", return_value=QueryIntent.NEEDS_NEITHER),
                patch("src.coordinator.routes.chat.get_tools_for_query", return_value=[]),
                patch("src.coordinator.services.query_handler_service.has_active_wallet_flow", return_value=True),
                patch("src.coordinator.routes.chat.QueryHandlerService", return_value=handler),
                patch("src.coordinator.routes.chat.QueryHandlerService._build_wallet_state_context", return_value=""),
                patch("src.coordinator.config.get_settings", return_value=_make_settings()),
                *_startup_patches(),
            ])
            resp = client.post("/persona/chat", json={
                "persona": "eeva",
                "message": "my-wallet-name",
                "session_id": "sess-abc",
            })

        assert resp.status_code == 200
        handler.handle_wallet_query.assert_called_once()

    def test_chat_with_history(self):
        card = _make_card()
        llm = _make_llm_client("Follow-up answer")
        with ExitStack() as stack:
            _apply(stack, _chat_patches(card, llm, "Follow-up answer", ["Follow-up answer"], "single"))
            resp = client.post("/persona/chat", json={
                "persona": "eeva",
                "message": "Tell me more",
                "history": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there!"},
                ],
            })
        assert resp.status_code == 200

    def test_chat_fallback_completion_non_brave_tool(self):
        """Tools returned but none is brave_web_search → falls back to LLM."""
        card = _make_card()
        non_brave = {"function": {"name": "some_other_tool"}}
        llm = _make_llm_client("Fallback")
        with ExitStack() as stack:
            _apply(stack, _chat_patches(card, llm, "Fallback", ["Fallback"], "single",
                                        tools=[non_brave]))
            resp = client.post("/persona/chat", json={"persona": "eeva", "message": "hi"})

        assert resp.status_code == 200
        llm.complete.assert_called_once()

    def test_chat_fallback_llm_failure_503(self):
        """Non-brave-tool path also raises 503 on LLM failure."""
        card = _make_card()
        non_brave = {"function": {"name": "other_tool"}}
        llm = MagicMock()
        llm.complete.side_effect = ConnectionError("gone")
        with ExitStack() as stack:
            _apply(stack, [
                patch("src.coordinator.routes.chat.get_persona_card", return_value=card),
                patch("src.coordinator.routes.chat.build_system_prompt", return_value="<s>"),
                patch("src.coordinator.routes.chat.create_llm_client", return_value=llm),
                patch("src.coordinator.routes.chat.log_context_stats", return_value={}),
                patch("src.coordinator.routes.chat.classify_query_intent", return_value=QueryIntent.NEEDS_NEITHER),
                patch("src.coordinator.routes.chat.get_tools_for_query", return_value=[non_brave]),
                patch("src.coordinator.services.query_handler_service.has_active_wallet_flow", return_value=False),
                patch("src.coordinator.config.get_settings", return_value=_make_settings()),
                *_startup_patches(),
            ])
            resp = client.post("/persona/chat", json={"persona": "eeva", "message": "hi"})
        assert resp.status_code == 503

    def test_chat_multi_message_response(self):
        card = _make_card()
        llm = _make_llm_client("<msg>Part one</msg><msg>Part two</msg>")
        with ExitStack() as stack:
            _apply(stack, _chat_patches(card, llm,
                                        "<msg>Part one</msg><msg>Part two</msg>",
                                        ["Part one", "Part two"], "multi"))
            resp = client.post("/persona/chat", json={"persona": "eeva", "message": "story"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["message_flow"] == "multi"
        assert data["message_count"] == 2
        assert data["answer"] == ["Part one", "Part two"]
        assert data["metadata"]["is_multi_message"] is True

    def test_chat_wallet_state_injection_for_wallet_persona(self):
        """Wallet state context is fetched for solana_wallet personas."""
        card = _make_card(mcp_access=["solana_wallet"])
        llm = _make_llm_client("Balance is 5 SOL")
        with ExitStack() as stack:
            _apply(stack, [
                patch("src.coordinator.routes.chat.get_persona_card", return_value=card),
                patch("src.coordinator.routes.chat.build_system_prompt", return_value="<system>"),
                patch("src.coordinator.routes.chat.create_llm_client", return_value=llm),
                patch("src.coordinator.routes.chat.log_context_stats", return_value={}),
                patch("src.coordinator.routes.chat.classify_query_intent", return_value=QueryIntent.NEEDS_NEITHER),
                patch("src.coordinator.routes.chat.get_tools_for_query", return_value=[]),
                patch("src.coordinator.services.query_handler_service.has_active_wallet_flow", return_value=False),
                patch("src.coordinator.routes.chat.QueryHandlerService._build_wallet_state_context", return_value="[WALLET:5SOL]"),
                patch("src.coordinator.routes.chat.post_process_first_person", return_value=("Balance is 5 SOL", False)),
                patch("src.coordinator.routes.chat.force_multi_message_split", return_value="Balance is 5 SOL"),
                patch("src.coordinator.routes.chat.parse_multi_message_response", return_value=(["Balance is 5 SOL"], "single")),
                patch("src.coordinator.config.get_settings", return_value=_make_settings()),
                *_startup_patches(),
            ])
            resp = client.post("/persona/chat", json={"persona": "eeva", "message": "balance"})

        assert resp.status_code == 200

    def test_chat_message_too_long(self):
        resp = client.post("/persona/chat", json={"persona": "eeva", "message": "x" * 10_001})
        assert resp.status_code == 422

    def test_chat_missing_message_field(self):
        resp = client.post("/persona/chat", json={"persona": "eeva"})
        assert resp.status_code == 422

    def test_chat_metadata_fields_present(self):
        card = _make_card()
        llm = _make_llm_client("Metadata test")
        with ExitStack() as stack:
            _apply(stack, _chat_patches(card, llm, "Metadata test", ["Metadata test"], "single"))
            resp = client.post("/persona/chat", json={"persona": "eeva", "message": "hi"})

        data = resp.json()
        assert "metadata" in data
        assert "is_multi_message" in data["metadata"]
        assert "message_count" in data["metadata"]
        assert data["metadata"]["source_type"] == "llm"

    def test_chat_rewritten_flag(self):
        card = _make_card()
        llm = _make_llm_client("She went away")
        with ExitStack() as stack:
            _apply(stack, _chat_patches(card, llm, "I went away", ["I went away"], "single", rewritten=True))
            resp = client.post("/persona/chat", json={"persona": "eeva", "message": "hi"})
        assert resp.json()["rewritten"] is True

    def test_chat_non_wallet_persona_no_wallet_state(self):
        """Non-wallet personas don't call _build_wallet_state_context."""
        card = _make_card(mcp_access=[])
        llm = _make_llm_client("Hello")
        mock_wallet_state = MagicMock()

        with ExitStack() as stack:
            _apply(stack, [
                patch("src.coordinator.routes.chat.get_persona_card", return_value=card),
                patch("src.coordinator.routes.chat.build_system_prompt", return_value="<s>"),
                patch("src.coordinator.routes.chat.create_llm_client", return_value=llm),
                patch("src.coordinator.routes.chat.log_context_stats", return_value={}),
                patch("src.coordinator.routes.chat.classify_query_intent", return_value=QueryIntent.NEEDS_NEITHER),
                patch("src.coordinator.routes.chat.get_tools_for_query", return_value=[]),
                patch("src.coordinator.services.query_handler_service.has_active_wallet_flow", return_value=False),
                patch("src.coordinator.routes.chat.QueryHandlerService._build_wallet_state_context", mock_wallet_state),
                patch("src.coordinator.routes.chat.post_process_first_person", return_value=("Hello", False)),
                patch("src.coordinator.routes.chat.force_multi_message_split", return_value="Hello"),
                patch("src.coordinator.routes.chat.parse_multi_message_response", return_value=(["Hello"], "single")),
                patch("src.coordinator.config.get_settings", return_value=_make_settings()),
                *_startup_patches(),
            ])
            resp = client.post("/persona/chat", json={"persona": "nyx", "message": "hi"})

        assert resp.status_code == 200
        mock_wallet_state.assert_not_called()


# ── /sessions/{session_id}/chat ───────────────────────────────────────────────

class TestChatWithSession:
    # handle_session_chat is imported at module-level in routes/chat.py, patch there.
    # add_message is imported inside the function via `from .sessions import add_message`,
    # so patch at its source module.
    _HANDLE_TARGET = "src.coordinator.routes.chat.handle_session_chat"
    _ADD_MSG_TARGET = "src.coordinator.routes.sessions.add_message"

    def test_session_chat_delegates_to_service(self):
        session_response = {
            "answer": "Session response",
            "message_flow": "single",
            "message_count": 1,
            "used_search": False,
            "metadata": {"source_type": "llm", "tools_used": [], "is_multi_message": False, "message_count": 1},
            "rewritten": False,
        }

        with ExitStack() as stack:
            _apply(stack, [
                patch(self._HANDLE_TARGET, return_value=session_response),
                patch(self._ADD_MSG_TARGET, return_value=None),
                *_startup_patches(),
            ])
            resp = client.post("/sessions/sess-123/chat", json={
                "persona": "eeva",
                "message": "Hello from session",
            })

        assert resp.status_code == 200
        assert resp.json()["answer"] == "Session response"

    def test_session_chat_passes_session_id_and_message(self):
        called_with = {}

        def mock_handle(session_id, message, deps, chat_function, add_message_function):
            called_with["session_id"] = session_id
            called_with["message"] = message
            return {
                "answer": "ok",
                "message_flow": "single",
                "message_count": 1,
                "used_search": False,
                "metadata": {},
                "rewritten": False,
            }

        with ExitStack() as stack:
            _apply(stack, [
                patch(self._HANDLE_TARGET, side_effect=mock_handle),
                patch(self._ADD_MSG_TARGET, return_value=None),
                *_startup_patches(),
            ])
            client.post("/sessions/my-unique-session/chat", json={
                "persona": "nyx",
                "message": "test message",
            })

        assert called_with["session_id"] == "my-unique-session"
        assert called_with["message"] == "test message"

    def test_session_chat_url_session_id_varies(self):
        """Different session_ids in URL are correctly forwarded."""
        captured = {}

        def mock_handle(session_id, message, deps, chat_function, add_message_function):
            captured["session_id"] = session_id
            return {
                "answer": "x",
                "message_flow": "single",
                "message_count": 1,
                "used_search": False,
                "metadata": {},
                "rewritten": False,
            }

        for sid in ["abc-123", "xyz-999", "session-alpha"]:
            captured.clear()
            with ExitStack() as stack:
                _apply(stack, [
                    patch(self._HANDLE_TARGET, side_effect=mock_handle),
                    patch(self._ADD_MSG_TARGET, return_value=None),
                    *_startup_patches(),
                ])
                client.post(f"/sessions/{sid}/chat", json={"persona": "eeva", "message": "hi"})
            assert captured["session_id"] == sid


# ── _build_llm_response (via /persona/chat) ───────────────────────────────────

class TestBuildLlmResponse:
    """Test the _build_llm_response helper paths via the /persona/chat endpoint."""

    def _chat(self, raw, msgs, flow, rewritten=False):
        card = _make_card()
        llm = _make_llm_client(raw)
        with ExitStack() as stack:
            _apply(stack, _chat_patches(card, llm, raw, msgs, flow, rewritten=rewritten))
            return client.post("/persona/chat", json={"persona": "eeva", "message": "hi"})

    def test_single_flow_answer_is_string(self):
        resp = self._chat("Hello", ["Hello"], "single")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["answer"], str)
        assert data["message_flow"] == "single"

    def test_multi_flow_answer_is_list(self):
        resp = self._chat("<msg>A</msg><msg>B</msg>", ["A", "B"], "multi")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["answer"], list)
        assert data["message_count"] == 2

    def test_rewritten_true(self):
        resp = self._chat("She went away", ["I went away"], "single", rewritten=True)
        assert resp.json()["rewritten"] is True

    def test_rewritten_false(self):
        resp = self._chat("I am here", ["I am here"], "single", rewritten=False)
        assert resp.json()["rewritten"] is False

    def test_metadata_source_type_llm(self):
        resp = self._chat("Answer", ["Answer"], "single")
        assert resp.json()["metadata"]["source_type"] == "llm"

    def test_used_search_false_for_llm_path(self):
        resp = self._chat("Answer", ["Answer"], "single")
        assert resp.json()["used_search"] is False

    def test_metadata_is_multi_message_true_for_multi_flow(self):
        resp = self._chat("<msg>A</msg>", ["A", "B"], "multi")
        assert resp.json()["metadata"]["is_multi_message"] is True

    def test_metadata_is_multi_message_false_for_single_flow(self):
        resp = self._chat("Simple", ["Simple"], "single")
        assert resp.json()["metadata"]["is_multi_message"] is False

    def test_assistant_tag_in_llm_output_converted(self):
        """<Assistant> tags in LLM output should be split into multi-message."""
        # The route converts <Assistant> to </msg>\n<msg> — we verify it doesn't crash.
        card = _make_card()
        llm = _make_llm_client("<Assistant>Part A</Assistant><Assistant>Part B</Assistant>")
        with ExitStack() as stack:
            _apply(stack, [
                patch("src.coordinator.routes.chat.get_persona_card", return_value=card),
                patch("src.coordinator.routes.chat.build_system_prompt", return_value="<s>"),
                patch("src.coordinator.routes.chat.create_llm_client", return_value=llm),
                patch("src.coordinator.routes.chat.log_context_stats", return_value={}),
                patch("src.coordinator.routes.chat.classify_query_intent", return_value=QueryIntent.NEEDS_NEITHER),
                patch("src.coordinator.routes.chat.get_tools_for_query", return_value=[]),
                patch("src.coordinator.services.query_handler_service.has_active_wallet_flow", return_value=False),
                patch("src.coordinator.routes.chat.post_process_first_person", return_value=("<Assistant>Part A</Assistant>", False)),
                # Let force_multi_message_split and parse_multi_message_response run real logic
                patch("src.coordinator.config.get_settings", return_value=_make_settings()),
                *_startup_patches(),
            ])
            resp = client.post("/persona/chat", json={"persona": "eeva", "message": "test"})

        assert resp.status_code == 200


class TestExtraSystemContext:
    """ADR-006 M0: chat() must append ChatBody.extra_system_context to the system
    prompt (the seam that was previously dropped). Covered on the pure-LLM path."""

    def test_extra_context_appended_to_system(self):
        card = _make_card()
        llm = _make_llm_client("ok")
        with ExitStack() as stack:
            _apply(stack, _chat_patches(card, llm, "ok", ["ok"], "single"))
            resp = client.post("/persona/chat", json={
                "persona": "eeva", "message": "Hello", "history": [],
                "extra_system_context": "MEMORY_CONTEXT_MARKER",
            })

        assert resp.status_code == 200
        # system prompt passed to the LLM contains BOTH the base and the injected context
        system_arg = llm.complete.call_args.kwargs.get("system", "")
        assert "<s>" in system_arg
        assert "MEMORY_CONTEXT_MARKER" in system_arg

    def test_no_extra_context_leaves_system_clean(self):
        card = _make_card()
        llm = _make_llm_client("ok")
        with ExitStack() as stack:
            _apply(stack, _chat_patches(card, llm, "ok", ["ok"], "single"))
            resp = client.post("/persona/chat", json={
                "persona": "eeva", "message": "Hello", "history": [],
            })

        assert resp.status_code == 200
        system_arg = llm.complete.call_args.kwargs.get("system", "")
        assert "MEMORY_CONTEXT_MARKER" not in system_arg


class TestDependencyPatchCoverage:
    """Guard: the neutralising patch list must cover every _get_dependencies() getter.

    Regression guard for the ADR-011 miss — a new dependency (`session_note_repo`)
    was added to `_get_dependencies()` whose real getter raises when uninitialised,
    while this module's patch list stayed stale. A full-suite run masked it (an
    earlier test initialised startup), so it only failed in a narrower scope.
    """

    def test_patch_list_covers_every_dependency_getter(self):
        from src.coordinator import startup as _startup

        names = _dependency_getter_names()
        # every derived name must actually exist on startup (catches regex drift)
        for name in names:
            assert hasattr(_startup, name), f"_get_dependencies references unknown {name}"
        # and the patch list must have one patch per getter
        assert len(_startup_patches()) == len(names)

    def test_session_note_repo_is_neutralised(self):
        """ADR-011's getter raises when uninitialised — it must be patched here."""
        assert "get_session_note_repo" in _dependency_getter_names()


class TestWordSubstitutionsWiring:
    """ADR-012: _build_llm_response applies persona word_substitutions end-to-end."""

    def _call(self, answer, subs):
        # real finalize chain (no mocks) — post-process + apply_word_substitutions
        # + <msg> parse all run for real.
        from src.coordinator.routes.chat import _build_llm_response
        from src.coordinator.schemas import ResponseMetadata
        return _build_llm_response(answer, "hi", "Gwen", ResponseMetadata(), word_substitutions=subs)

    def test_substitution_applied_in_output(self):
        out = self._call("I want your shaft tonight", {"shaft": "cock"})
        ans = out["answer"] if isinstance(out["answer"], str) else " ".join(out["answer"])
        assert "shaft" not in ans.lower()
        assert "cock" in ans.lower()

    def test_noop_without_substitutions(self):
        out = self._call("I want your shaft tonight", None)
        ans = out["answer"] if isinstance(out["answer"], str) else " ".join(out["answer"])
        assert "shaft" in ans.lower()  # untouched when the persona declares none
