# tests/evaluation/test_transport_preflight.py
"""The gate before any expensive eval run.

Static checks run headless. The live round trip is `requires_ollama` and uses
`keep_alive=0`, so it never leaves a model resident on a box where the chat
model is pinned indefinitely by design.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "persona_eval"))

import transport_preflight as tp  # noqa: E402


class TestOptionChecks:
    def test_a_clean_option_set_passes(self):
        failures, warnings = tp.check_options(
            {"temperature": 0.9, "min_p": 0.05, "repeat_penalty": 1.05, "repeat_last_n": 384}
        )
        assert failures == []
        assert warnings == []

    def test_a_retired_option_is_a_hard_failure(self):
        """typical_p returns HTTP 400 — it breaks every turn, not just quality."""
        failures, _ = tp.check_options({"typical_p": 0.9})
        assert any("400" in f for f in failures)

    def test_mirostat_is_caught_as_retired(self):
        failures, _ = tp.check_options({"mirostat": 2})
        assert failures

    def test_a_silently_ignored_option_is_a_failure_not_a_warning(self):
        """DRY is compiled into the engine Ollama spawns but absent from its API.
        Sending it returns 200 and does nothing, so it reads as configured while
        having no effect — strictly worse than being rejected."""
        failures, _ = tp.check_options({"dry_multiplier": 0.8})
        assert any("accepted and ignored" in f for f in failures)

    def test_negative_repeat_last_n_is_caught_before_the_request(self):
        """The retired full-context sentinel. On the greet path a 400 here
        surfaces as 'LLM service temporarily unavailable', which names neither
        the option nor the cause."""
        failures, _ = tp.check_options({"repeat_last_n": -1})
        assert any("repeat_last_n" in f for f in failures)

    def test_zero_is_a_legitimate_window(self):
        """0 disables the penalty and is valid; only negatives are retired."""
        failures, _ = tp.check_options({"repeat_last_n": 0})
        assert failures == []

    def test_an_unknown_option_warns_rather_than_fails(self):
        """Unknown is not the same as known-bad — Ollama's option set changes,
        and a warning keeps the check useful without blocking on it."""
        failures, warnings = tp.check_options({"some_future_sampler": 1})
        assert failures == []
        assert warnings


class TestResultReporting:
    def test_a_static_failure_short_circuits_the_live_call(self):
        """No point spending a generation to confirm what the option set already
        proves."""
        res = tp.preflight("nonexistent-model", options={"typical_p": 0.9})
        assert res.ok is False
        assert res.content_chars == 0  # never called out

    def test_report_names_the_verdict_and_the_numbers(self):
        res = tp.PreflightResult(ok=False, model="m", content_chars=0,
                                 thinking_chars=459, done_reason="length",
                                 failures=["content is 0 chars"])
        out = res.report()
        assert "FAIL" in out and "thinking=459" in out and "length" in out


@pytest.mark.requires_ollama
class TestLiveRoundTrip:
    def test_the_deployed_model_returns_text(self):
        """The actual gate. If this fails, nothing downstream is worth running."""
        from src.coordinator.config import get_settings

        st = get_settings()
        res = tp.preflight(
            st.ollama.model,
            base=st.ollama.base,
            options={"temperature": 0.9, "min_p": 0.05,
                     "repeat_penalty": 1.05, "repeat_last_n": 384,
                     "num_predict": 120},
            think=False,
        )
        assert res.ok, res.report()
        assert res.content_chars >= 20
        assert res.done_reason != "length"


class TestResolvedConfigGuard:
    """The guard that would have caught this harness measuring the wrong model."""

    def test_matching_config_passes(self):
        expected = {"model": "huihui_ai/mistral-small-abliterated:24b", "num_ctx": 16384}
        assert tp.verify_resolved_config(dict(expected), expected) == []

    def test_a_different_model_is_reported(self):
        """The real case: a worktree has no .env, settings fall through to
        pydantic defaults, and the run silently measures gemma2:9b at 4096."""
        problems = tp.verify_resolved_config(
            {"model": "gemma2:9b-instruct-q5_K_M", "num_ctx": 4096},
            {"model": "huihui_ai/mistral-small-abliterated:24b", "num_ctx": 16384},
        )
        assert len(problems) == 2
        assert any("gemma2" in p for p in problems)
        assert any("4096" in p for p in problems)

    def test_a_missing_key_is_a_mismatch_not_a_pass(self):
        """Absent must not read as agreement — that is how a silent default wins."""
        assert tp.verify_resolved_config({}, {"model": "x"})
