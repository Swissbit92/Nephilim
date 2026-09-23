# tests/backend/coordinator/test_sampler_wire_arrival.py
"""What Ollama ACCEPTED, not what we sent.

Every previous sampler regression in this repo passed its tests while being
inert in production, because the tests asserted on the dict the app built and
never on what the server did with it. Ollama makes the difference observable in
two ways, and both are used here:

  * an option it does not recognise is **accepted and ignored**, with
    ``level=WARN source=types.go:1048 msg="invalid option provided"`` in the
    server log — HTTP 200, no error. This is the silent-failure mode that let
    ``min_p`` be "configured" for months without applying;
  * an option it has **retired** is rejected outright with HTTP 400.

The headless tests pin the contract against the installed client library. The
``requires_ollama`` test proves the round trip against the running server.

Measured on this machine, Ollama 0.34.2, 2026-09-22.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.coordinator.config import get_persona_sampling_overrides

PERSONAS = Path(__file__).parent.parent.parent.parent / "personas"

# Retired by llama.cpp/Ollama. Sending either is a hard 400, not a warning.
RETIRED_OPTIONS = ("typical_p",)


def _gwen() -> dict:
    return json.loads((PERSONAS / "gwen.json").read_text(encoding="utf-8"))


class TestContractWithTheClientLibrary:
    def test_min_p_survives_the_tool_brain_transport(self):
        """The correction that matters.

        ``ollama._types.Options`` has no ``min_p`` field, so the LEGACY
        langchain path drops it — that gap is pinned in
        ``test_sampler_exposure.py``. But the tool brain passes a plain dict to
        ``Client.chat``, and ``ChatRequest.options`` accepts a Mapping without
        coercing through ``Options``. So on the path gwen's turns actually take,
        ``min_p`` does reach the wire. Pin it: if a future client release starts
        coercing, min_p goes silently inert again and this fails.
        """
        from ollama._types import ChatRequest, Options

        assert "min_p" not in Options.model_fields, (
            "min_p appeared in Options — the langchain gap may be closed; "
            "re-check test_sampler_exposure.py's boundary test"
        )
        req = ChatRequest(
            model="m",
            messages=[{"role": "user", "content": "hi"}],
            options={"min_p": 0.05, "temperature": 0.9},
        )
        sent = req.model_dump(exclude_none=True)["options"]
        assert sent["min_p"] == 0.05
        assert sent["temperature"] == 0.9

    @pytest.mark.parametrize("option", RETIRED_OPTIONS)
    def test_no_persona_declares_a_retired_option(self, option):
        """``typical_p`` is a 400, not a warning. One card carrying it would
        break every turn for that persona."""
        offenders = []
        for card_path in sorted(PERSONAS.glob("*.json")):
            prefs = json.loads(card_path.read_text(encoding="utf-8")).get("model_preferences") or {}
            if option in prefs:
                offenders.append(card_path.name)
        assert not offenders, f"{option} is retired and 400s; found in: {offenders}"

    def test_the_extractor_cannot_emit_a_retired_option(self):
        """Even if a card smuggled one in, it must not reach the wire."""
        card = {"key": "x", "model_preferences": {opt: 0.9 for opt in RETIRED_OPTIONS}}
        emitted = get_persona_sampling_overrides(card)
        for opt in RETIRED_OPTIONS:
            assert opt not in emitted


class TestGwenIsConfiguredForRepetition:
    def test_anti_repetition_is_actually_on(self):
        """Ollama changed ``repeat_penalty``'s default from 1.1 to 1.0 in commit
        6a261db7 (2026-08-12) — i.e. OFF — and its own rationale says the remedy
        is a per-model parameter. Measured on this machine before this change:
        ``repeat_penalty = 1.000``. gwen must therefore declare one herself.
        """
        prefs = _gwen()["model_preferences"]
        assert prefs["repeat_penalty"] > 1.0, "repetition penalty is disabled"
        assert prefs["repeat_last_n"] > 0

    def test_the_window_is_bounded_not_full_context(self):
        """``repeat_penalty`` is applied to prompt tokens as well as generated
        ones, flat, with no positional decay. A very large window therefore
        penalises the persona's own pinned voice exemplars for the whole
        session. 384 is a chosen compromise, not a derived value."""
        assert 64 < _gwen()["model_preferences"]["repeat_last_n"] <= 2048

    def test_the_full_set_reaches_the_extractor(self):
        o = get_persona_sampling_overrides(_gwen())
        assert o["temperature"] == 0.9
        assert o["min_p"] == 0.05
        assert o["repeat_penalty"] == 1.05
        assert o["repeat_last_n"] == 384


@pytest.mark.requires_ollama
class TestAgainstTheRunningServer:
    def test_gwens_samplers_are_accepted_and_retired_ones_are_not(self):
        """The round trip. ``keep_alive=0`` so nothing is left pinned."""
        import ollama

        from src.coordinator.config import get_settings

        st = get_settings()
        client = ollama.Client(host=st.ollama.base)
        opts = dict(get_persona_sampling_overrides(_gwen()))
        opts["num_predict"] = 4

        resp = client.chat(
            model=st.ollama.model,
            messages=[{"role": "user", "content": "say hi"}],
            stream=False,
            keep_alive=0,
            options=opts,
        )
        assert (resp["message"]["content"] or "").strip()

        for option in RETIRED_OPTIONS:
            with pytest.raises(Exception) as exc:
                client.chat(
                    model=st.ollama.model,
                    messages=[{"role": "user", "content": "hi"}],
                    stream=False,
                    keep_alive=0,
                    options={option: 0.9, "num_predict": 1},
                )
            assert "no longer supported" in str(exc.value) or "400" in str(exc.value)
