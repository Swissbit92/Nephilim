"""PERSONA_MODEL must be set — refusing to boot beats running the wrong model.

The bug this guards: `OllamaSettings.model` used to default to a REAL model name
(`gemma2:9b-instruct-q5_K_M`). A process that could not see `.env` — a git
worktree, a container missing an env file, a test runner — resolved to that model
and ran happily at 4096 context instead of 16384, on a 9B instead of a 24B.
Nothing failed. `assert_model_available` passed, because the fallback model was
in fact pulled locally, so the one check that could have caught it confirmed the
wrong answer instead.

These tests pin the two halves of the fix separately, because they fail for
different reasons and a single "it works" test would hide either one.
"""
import pytest

from src.coordinator.config.llm import OllamaSettings
from src.coordinator.ollama_utils import ModelNotConfigured, require_model_configured


class TestRequireModelConfigured:
    """The guard itself."""

    @pytest.mark.parametrize("unset", ["", "   ", "\t", "\n", None])
    def test_an_unset_model_is_refused(self, unset):
        """Whitespace counts as unset — `PERSONA_MODEL= ` in a .env is a typo,
        not a model name, and passing it through would reach Ollama as a 404."""
        with pytest.raises(ModelNotConfigured):
            require_model_configured(unset)

    def test_the_error_says_what_to_do_about_it(self):
        """A guard that stops the server without saying why just moves the
        confusion. The message must name the variable and show a fix."""
        with pytest.raises(ModelNotConfigured) as exc:
            require_model_configured("")
        msg = str(exc.value)
        assert "PERSONA_MODEL" in msg
        assert ".env" in msg
        # It must explain the REASON, or the next person "helpfully" restores a
        # default to make the error go away and reopens the hole.
        assert "fallback" in msg.lower()

    def test_a_configured_model_passes_through_unchanged(self):
        """Returns the value so call sites can inline it rather than re-reading
        settings — re-reading is how the checked value and the used value drift."""
        assert require_model_configured("mistral-small:24b") == "mistral-small:24b"

    def test_it_does_not_validate_that_the_model_EXISTS(self):
        """Deliberate separation of concerns: this asks "was one configured?",
        `assert_model_available` asks "is it pulled?". Conflating them is what
        produced the original bug — a real-but-wrong name satisfies the second
        check, so only the first can catch a missing configuration."""
        assert require_model_configured("definitely-not-pulled:999b") == "definitely-not-pulled:999b"


class TestNoFallbackModelName:
    """The other half: the default must not be a usable model name."""

    # These assert on the FIELD DEFAULT, never on `OllamaSettings().model`.
    # BaseSettings resolves from the environment first, and conftest sets
    # PERSONA_MODEL for the whole suite — so the constructed value is "test-model"
    # and would mask a real model name restored as the default. Reading the
    # resolved value here would make this test pass no matter what ships.
    @staticmethod
    def _shipped_default() -> str:
        return OllamaSettings.model_fields["model"].default

    def test_the_shipped_default_is_empty_not_a_real_model(self):
        """The regression itself. If this ever holds a real model name again,
        every process that cannot see .env silently runs that model instead."""
        assert self._shipped_default() == ""

    def test_settings_still_CONSTRUCT_without_the_env_var(self, monkeypatch):
        """Why an empty-string sentinel and not a required field: the settings
        singleton is built at import time in config/__init__.py, so a required
        field turns a missing .env into an import-time ValidationError during
        test collection — 2400 collection errors instead of one startup message."""
        monkeypatch.delenv("PERSONA_MODEL", raising=False)
        assert OllamaSettings(_env_file=None).model == ""

    def test_the_empty_default_is_rejected_by_the_guard(self):
        """The two halves must meet: the default must be a value the guard
        refuses. If either side drifts, the hole reopens silently."""
        with pytest.raises(ModelNotConfigured):
            require_model_configured(self._shipped_default())
