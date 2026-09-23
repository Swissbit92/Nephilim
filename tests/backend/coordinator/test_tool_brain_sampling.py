# tests/backend/coordinator/test_tool_brain_sampling.py
"""The persona's sampler settings must reach the call that writes the prose.

ADR-008's Status header resolved the two-brain split to a single model, and
TB6 then made the tool brain's own answer user-facing on ordinary chitchat
(`TOOL_BRAIN_UNGATED_WEB`). TB6 argued only about *generation count* — one
generation per turn either way — and nobody checked sampler parity. So the
prose that reaches the user was written at a hardcoded ``temperature: 0.4``
while the persona card asked for 0.9, and ``repeat_penalty``/``repeat_last_n``/
``min_p``/``top_k``/``top_p`` never arrived at all.

That is why ``c00cf084 feat(gwen): widen the repetition-penalty window`` was a
no-op in production: it was verified end-to-end through ``OllamaLLM``, which is
the *legacy* transport, on a path gwen's turns no longer take.

A tool *decision* still wants determinism, so the low temperature is kept for
calls where a tool call is expected. These tests pin the split.
"""

from __future__ import annotations

import pytest

from src.coordinator.config import get_settings
from src.coordinator.services.tool_brain_service import ToolBrainService, ST_SILENT
from src.coordinator.services.tool_interceptor import ToolCallInterceptor
from src.coordinator.tools import registrations  # noqa: F401 - register specs
from src.coordinator.tools.executor_bindings import bind_web_executors
from src.coordinator.tools.registry import registry


@pytest.fixture(autouse=True)
def _setup():
    bind_web_executors()
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakeOllama:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def _msg(content="", tool_calls=None):
    return {"message": {"content": content, "tool_calls": tool_calls or []}}


# gwen's real shipped preferences, minus the retired -1 sentinel.
GWEN = {
    "key": "gwen",
    "toolsets": ["web"],
    "tools": ["image_search", "video_search"],
    "mcp_access": ["brave_search"],
    "nsfw": True,
    "model_preferences": {"temperature": 0.9, "repeat_last_n": 384},
}

PLAIN = {"key": "nephilim_eeva", "mcp_access": ["brave_search"], "nsfw": False}


def _svc(responses):
    return ToolBrainService(
        interceptor=ToolCallInterceptor(enforce_arguments=True),
        ollama_client=FakeOllama(responses),
    )


def _run(svc, card, **kw):
    return svc.run(
        persona_card=card,
        system_prompt="sys",
        user_message="hi",
        history=[],
        tools=registry.definitions_for_persona(card),
        **kw,
    )


class TestProseSampling:
    def test_persona_temperature_reaches_prose_call(self):
        """The headline: gwen asks for 0.9 and the reply is written at 0.9."""
        svc = _svc([_msg(content="Hello, Daddy.")])
        r = _run(svc, GWEN, sampling_overrides={"temperature": 0.9}, prose_expected=True)
        assert r.status == ST_SILENT
        assert svc._client.calls[0]["options"]["temperature"] == 0.9

    def test_persona_repetition_window_reaches_prose_call(self):
        """c00cf084's intent, on the path that actually runs."""
        svc = _svc([_msg(content="Hello, Daddy.")])
        _run(
            svc,
            GWEN,
            sampling_overrides={"temperature": 0.9, "repeat_last_n": 384, "repeat_penalty": 1.05},
            prose_expected=True,
        )
        opts = svc._client.calls[0]["options"]
        assert opts["repeat_last_n"] == 384
        assert opts["repeat_penalty"] == 1.05

    def test_tool_decision_keeps_the_low_temperature(self):
        """A turn where a tool call is expected is a decision, not prose.

        Persona voice must not bleed into tool-argument JSON, so the deliberate
        0.4 survives wherever the model is being asked to choose a tool.
        """
        svc = _svc([_msg(content="Hello.")])
        _run(svc, GWEN, sampling_overrides={"temperature": 0.9}, prose_expected=False)
        assert svc._client.calls[0]["options"]["temperature"] == 0.4

    def test_absent_overrides_leave_the_existing_behaviour_untouched(self):
        """A persona that declares nothing must be byte-identical to before."""
        svc = _svc([_msg(content="Hello, Seeker.")])
        _run(svc, PLAIN, prose_expected=True)
        opts = svc._client.calls[0]["options"]
        assert opts["temperature"] == 0.4
        for key in ("repeat_penalty", "repeat_last_n", "top_k", "top_p", "min_p"):
            assert key not in opts

    def test_keep_alive_is_sent_so_the_model_pin_is_not_defeated(self):
        """``OLLAMA_KEEP_ALIVE=-1`` pins the 24B indefinitely, but Ollama applies
        keep_alive per request, last-one-wins. A tool-brain call that omits it
        silently reverts the pin to the server default on every turn.

        Compared against the WIRE form, not the raw setting. Those stopped being
        the same value in ``5ce9d5dc``: Ollama parses keep_alive as a Go duration
        and rejects the shipped default ``"-1"`` with an HTTP 400, so
        ``wire_keep_alive`` coerces it to the integer ``-1``. This assertion kept
        comparing against the raw string and went red on the merge.

        Asserting the raw setting here would be worse than a red suite — it would
        demand the code send back the exact value the server refuses, so making
        the test pass would reintroduce the 400 the coercion exists to prevent.
        See ``test_keep_alive_wire.py`` for the measured contract.
        """
        svc = _svc([_msg(content="Hi.")])
        _run(svc, GWEN, sampling_overrides={"temperature": 0.9}, prose_expected=True)
        ollama = get_settings().ollama
        sent = svc._client.calls[0]["keep_alive"]
        assert sent == type(ollama).wire_keep_alive(ollama.keep_alive)
        # The regression itself: a bare numeric string is an HTTP 400 on arrival.
        assert not (isinstance(sent, str) and sent.lstrip("-").isdigit())
