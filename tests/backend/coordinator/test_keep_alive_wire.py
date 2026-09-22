# tests/backend/coordinator/test_keep_alive_wire.py
"""keep_alive must be a value Ollama will actually accept.

Ollama parses keep_alive as a Go duration, so a bare numeric string is rejected:

    keep_alive="-1"  -> HTTP 400  time: missing unit in duration "-1"
    keep_alive=-1    -> OK
    keep_alive="10m" -> OK

Measured against the running Ollama on 2026-09-22.

The default for OLLAMA_KEEP_ALIVE is the string "-1", which is therefore
invalid on the wire. It stayed hidden because the path that sent it only runs
for a persona with no tools or for a session's opening greeting, and no new
session had been created since 2026-08-23 — the same dormancy that hid the
retired repeat_last_n sentinel. Two unrelated 400s on one never-exercised path,
both surfacing as "LLM service temporarily unavailable".

It was caught by the pilot rather than the suite, because a unit test asserting
"the dict contains keep_alive" passes on a value the server refuses.
"""

from __future__ import annotations

import pytest

from src.coordinator.config.llm import OllamaSettings


class TestWireCoercion:
    @pytest.mark.parametrize("raw,expected", [
        ("-1", -1),      # the shipped default — the whole point of this helper
        ("0", 0),
        ("300", 300),
        (-1, -1),        # already an int, unchanged
        ("10m", "10m"),  # has a unit, valid as-is
        ("-1s", "-1s"),
        ("2h30m", "2h30m"),
        (None, None),    # caller may omit the field entirely
    ])
    def test_values_are_coerced_to_something_ollama_accepts(self, raw, expected):
        assert OllamaSettings.wire_keep_alive(raw) == expected

    def test_the_shipped_default_does_not_reach_the_wire_as_a_string(self):
        """The regression itself: '-1' as a string is an HTTP 400."""
        assert OllamaSettings.wire_keep_alive(OllamaSettings().keep_alive) == -1
        assert not isinstance(OllamaSettings.wire_keep_alive("-1"), str)

    def test_a_unit_bearing_value_is_never_mangled_into_an_int(self):
        """'10m' must stay a string — int('10m') raises, and silently dropping
        the unit would turn ten minutes into something else entirely."""
        assert OllamaSettings.wire_keep_alive("10m") == "10m"

    def test_whitespace_is_tolerated(self):
        assert OllamaSettings.wire_keep_alive("  -1  ") == -1


class TestCallSitesUseIt:
    """Both paths that send keep_alive must coerce. The tool-brain call site was
    added on 2026-09-22 and would have 400'd every gwen turn without this."""

    @pytest.mark.parametrize("path", [
        "src/coordinator/services/tool_brain_service.py",
        "src/coordinator/services/llm_completion_service.py",
    ])
    def test_the_raw_setting_is_not_sent_directly(self, path):
        from pathlib import Path

        src = Path(path).read_text(encoding="utf-8")
        assert "wire_keep_alive" in src, f"{path} sends keep_alive without coercing it"
