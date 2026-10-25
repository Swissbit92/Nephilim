"""The thinking control, asserted where it actually matters: on the wire.

The three pre-existing tests in `test_llm_completion_service.py::TestReasoningControl`
patch `OllamaLLM` and assert the *kwarg*. That proves the settings plumbing and
nothing about the request, which is where this control was broken: on the HTTP
transport `reasoning` was being folded into the `options` dict, and Ollama
answers HTTP 200 with a WARN for an unknown option — so the toggle read as
configured and did nothing.

`think` is a TOP-LEVEL request key. Every test here asserts that, by reading
the payload or the call kwargs rather than the constructor.
"""

from __future__ import annotations

import pytest

from src.coordinator.services.ollama_http import OllamaHTTPClient
from src.coordinator.services.ollama_think import wire_think


class TestWireThink:
    """`wire_think` expresses 'omit the key', which a bool cannot."""

    def test_unset_omits_the_key_entirely(self):
        # Not {"think": None} and not {"think": False}: ABSENT. An unset
        # OLLAMA_REASONING must leave a non-reasoning model's request
        # byte-identical to what it received before this control existed.
        assert wire_think(None) == {}

    def test_false_is_sent_explicitly(self):
        assert wire_think(False) == {"think": False}

    def test_true_is_sent_explicitly(self):
        assert wire_think(True) == {"think": True}

    @pytest.mark.parametrize("truthy", ["false", 0, 1, ""])
    def test_coerces_to_a_real_bool(self, truthy):
        # Ollama rejects a non-bool `think`, and pydantic may hand us a string
        # from the environment.
        assert isinstance(wire_think(truthy)["think"], bool)


class TestHttpTransportPayload:
    """The transport the control was dead on."""

    def _payload(self, **kw):
        return OllamaHTTPClient(base_url="http://x", model="m", **kw)._payload("hi")

    def test_think_is_top_level_not_an_option(self):
        p = self._payload(options={"temperature": 0.9}, think=False)
        assert p["think"] is False, "think must be a top-level request key"
        assert "think" not in p["options"], (
            "think inside options is silently ignored by Ollama — HTTP 200 + a WARN "
            "log — which is indistinguishable from the control working"
        )

    def test_absent_when_not_configured(self):
        p = self._payload(options={"temperature": 0.9})
        assert "think" not in p

    def test_options_are_still_passed_through(self):
        # Guards against a fix that drops the sampler to make room for think.
        p = self._payload(options={"temperature": 0.9, "min_p": 0.05}, think=False)
        assert p["options"] == {"temperature": 0.9, "min_p": 0.05}


class TestCompletionServiceDoesNotLeakReasoningIntoOptions:
    """The exact defect: `reasoning` surviving into the options dict."""

    def test_http_backend_puts_think_top_level_and_not_in_options(self, monkeypatch):
        captured = {}

        class _Spy(OllamaHTTPClient):
            def __init__(self, **kw):
                captured.update(kw)
                super().__init__(**kw)

        monkeypatch.setenv("PERSONA_MODEL", "gemma4:26b")
        monkeypatch.setenv("OLLAMA_REASONING", "false")
        monkeypatch.setenv("OLLAMA_COMPLETION_BACKEND", "http")

        from src.coordinator.config import get_settings
        get_settings.cache_clear()
        import src.coordinator.services.ollama_http as oh
        monkeypatch.setattr(oh, "OllamaHTTPClient", _Spy)

        from src.coordinator.services.llm_completion_service import LLMCompletionService
        LLMCompletionService(base="http://127.0.0.1:11434", model="gemma4:26b")
        get_settings.cache_clear()

        assert captured, "the http backend was not selected — test is not measuring anything"
        assert captured.get("think") is False
        assert "reasoning" not in captured.get("options", {}), (
            "reasoning leaked into options — the original silent no-op"
        )
