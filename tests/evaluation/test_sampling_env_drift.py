"""`sampling_env` must be recorded AND compared.

Recording a field that nothing reads is the defect this repo keeps paying for:
a control that is constructed but never called does not fail, it just does
nothing. `sampling_fingerprint` covers per-persona sampler overrides;
`sampling_env` covers the settings that apply to every persona at once, and
until now it was written into the manifest and compared nowhere.

`reasoning` is the one that matters most and is the reason these exist: on a
reasoning model, unset versus false is the difference between a real reply and
an empty string, not a difference in voice.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "persona_eval"))

import frozen_gallery as fg  # noqa: E402


def _manifest(**env):
    base = {"context_window": 16384, "max_output_tokens": 400,
            "completion_backend": "langchain", "reasoning": False}
    base.update(env)
    return {
        "embedding_model": "bge-m3:latest",
        "companion_model": "m",
        "prompt_builder_version": fg.PROMPT_BUILDER_VERSION,
        "sampling_fingerprint": "fp",
        "sampling_env": base,
        "persona_def_hashes": {},
    }


class TestReasoningDriftIsDetected:
    def test_identical_env_is_silent(self):
        errors, warnings = fg.check_staleness(_manifest(), _manifest(), active=set())
        assert errors == []
        assert not [w for w in warnings if "sampling_env" in w]

    def test_reasoning_flip_is_reported(self):
        # The exact case: a gallery frozen before the thinking control existed
        # (reasoning unset) compared against a run with it false.
        errors, warnings = fg.check_staleness(
            _manifest(reasoning=False), _manifest(reasoning=None), active=set()
        )
        hits = [w for w in warnings if "sampling_env.reasoning" in w]
        assert hits, f"reasoning drift was not reported; warnings={warnings}"
        assert "AT ALL" in hits[0], (
            "a reasoning flip must be described as deciding whether replies exist, "
            "not merely how they sound — a generic 'may have drifted' understates it"
        )

    def test_it_is_a_warning_not_a_hard_error(self):
        # Nothing in a manifest says whether the model reasons, so this cannot
        # be a hard stop without blocking every legitimate comparison.
        errors, _ = fg.check_staleness(
            _manifest(reasoning=False), _manifest(reasoning=None), active=set()
        )
        assert errors == []

    def test_other_env_keys_are_compared_too(self):
        _, warnings = fg.check_staleness(
            _manifest(context_window=16384), _manifest(context_window=4096), active=set()
        )
        assert any("sampling_env.context_window" in w and "4096" in w for w in warnings)

    def test_a_new_env_key_is_noticed_without_editing_this_test(self):
        # The comparison walks the union of keys, so a key added on one side
        # only is still reported. Guards against a future field being recorded
        # and silently ignored, which is the whole failure class here.
        _, warnings = fg.check_staleness(
            _manifest(some_future_knob="on"), _manifest(), active=set()
        )
        assert any("sampling_env.some_future_knob" in w for w in warnings)
