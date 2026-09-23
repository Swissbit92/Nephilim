"""Sampling parameters declared by a persona must actually reach Ollama.

``SamplingPreset`` has long declared ``top_k``, ``top_p`` and
``repetition_penalty``, but ``get_persona_sampling_overrides`` only ever read
``temperature``, ``min_p`` and ``repeat_penalty`` — so a persona that set
``top_k`` got silence, not an error. ``repeat_last_n`` did not exist anywhere,
leaving Ollama's 64-token default in force: long enough to stop a sentence
repeating inside one reply, far too short to notice a whole paragraph being
reproduced.

The last test in this file pins the boundary of what the current client can
carry, so the ``min_p`` gap stays visible rather than being quietly assumed
fixed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from src.coordinator.config import get_persona_sampling_overrides
from src.coordinator.models.persona_schema import SamplingPreset


def _card(**prefs):
    return {"key": "test", "model_preferences": prefs}


# ─── override extraction ─────────────────────────────────────────────────────


def test_top_k_and_top_p_are_extracted():
    o = get_persona_sampling_overrides(_card(top_k=40, top_p=0.9))
    assert o["top_k"] == 40
    assert o["top_p"] == 0.9


def test_repeat_last_n_is_extracted():
    assert get_persona_sampling_overrides(_card(repeat_last_n=-1))["repeat_last_n"] == -1
    assert get_persona_sampling_overrides(_card(repeat_last_n=512))["repeat_last_n"] == 512


def test_repeat_penalty_accepts_both_spellings():
    """The schema field is `repetition_penalty`; every shipped persona JSON
    writes `repeat_penalty`. Reading only one silently ignored the other."""
    assert get_persona_sampling_overrides(_card(repeat_penalty=1.15))["repeat_penalty"] == 1.15
    assert get_persona_sampling_overrides(_card(repetition_penalty=1.15))["repeat_penalty"] == 1.15


@pytest.mark.parametrize(
    "prefs",
    [
        {"top_k": 101},  # above range
        {"top_k": -1},  # below range
        {"top_p": 1.5},  # above range
        {"repeat_last_n": -2},  # below the -1 sentinel
        {"top_k": True},  # bool is an int subclass — must not slip through
        {"top_p": "0.9"},  # wrong type
    ],
)
def test_out_of_range_values_are_dropped_not_clamped(prefs):
    """A bad value must be omitted so the caller's default applies, rather than
    being silently coerced into something the persona did not ask for."""
    o = get_persona_sampling_overrides(_card(**prefs))
    assert set(o) == {"temperature"}


def test_absent_prefs_stay_absent():
    """A persona that sets nothing must produce byte-identical params to
    before this change — only temperature, which always has a fallback."""
    assert set(get_persona_sampling_overrides(_card())) == {"temperature"}


def test_schema_accepts_repeat_last_n():
    assert SamplingPreset(repeat_last_n=-1).repeat_last_n == -1
    with pytest.raises(ValidationError):
        SamplingPreset(repeat_last_n=-2)


# ─── the params actually handed to the client ────────────────────────────────


def _built_params(**prefs):
    with patch("src.coordinator.services.llm_completion_service.OllamaLLM") as m:
        from src.coordinator.services.llm_completion_service import LLMCompletionService

        LLMCompletionService(base="http://x", model="m", **prefs)
        return m.call_args.kwargs


def test_repeat_last_n_reaches_ollama_params():
    assert _built_params(repeat_last_n=-1)["repeat_last_n"] == -1


def test_top_k_and_top_p_reach_ollama_params():
    p = _built_params(top_k=40, top_p=0.9)
    assert p["top_k"] == 40
    assert p["top_p"] == 0.9


def test_unset_samplers_are_not_passed_at_all():
    """Absent means absent — not None. Passing None would override Ollama's
    own default with a null."""
    p = _built_params()
    for key in ("repeat_penalty", "repeat_last_n", "top_k", "top_p", "min_p"):
        assert key not in p


def test_create_llm_client_forwards_persona_samplers():
    card = _card(temperature=0.9, top_k=40, top_p=0.9, repeat_penalty=1.15, repeat_last_n=-1)
    with patch("src.coordinator.llm_client.LC_OllamaClient") as client:
        from src.coordinator.llm_client import create_llm_client

        create_llm_client(card)
    kwargs = client.call_args.kwargs
    assert kwargs["top_k"] == 40
    assert kwargs["top_p"] == 0.9
    assert kwargs["repeat_penalty"] == 1.15
    assert kwargs["repeat_last_n"] == -1


# ─── the boundary of what this client can carry ──────────────────────────────


def test_min_p_is_still_dropped_by_the_langchain_client():
    """Pins the known gap so it cannot be silently assumed fixed.

    ``OllamaLLM`` has no ``min_p`` field and coerces its options through
    ``ollama.Options``, which has none either — so no kwarg combination gets
    ``min_p`` to the server. Closing this needs a different transport (M4),
    not a config change. If this test ever fails, min_p became reachable and
    the workaround can go.
    """
    from langchain_ollama import OllamaLLM
    from ollama._types import Options

    assert not hasattr(OllamaLLM(base_url="http://x", model="m", min_p=0.05), "min_p")
    assert "min_p" not in Options.model_fields
    # ...while the samplers this milestone exposes genuinely do survive.
    assert {"repeat_last_n", "repeat_penalty", "top_k", "top_p"} <= set(Options.model_fields)
