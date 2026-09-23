# tests/backend/test_prod_backend_guard.py
"""The live backend is not a test fixture.

On 2026-09-22 a full-suite run wrote 476 messages across 108 sessions into the
production database, taking it from 232 sessions to 340 — against only 3 real
conversations. Nothing was corrupted; the organic signal was simply buried.

Eleven modules reach localhost:8000, four via an EVAL_BASE_URL that defaults to
production. `requires_ollama` gates whether such a test runs, not what it
touches, so on a machine where the backend is always up under launchd that was
every run.

These pin the guard itself. If it ever stops firing, the next suite run quietly
writes to the companion's real store again.
"""

from __future__ import annotations

import urllib.request

import pytest

from tests.conftest import _is_prod_backend


class TestPortDetection:
    @pytest.mark.parametrize("url", [
        "http://localhost:8000",
        "http://127.0.0.1:8000/sessions",
        "http://0.0.0.0:8000",
        "localhost:8000",
    ])
    def test_production_urls_are_recognised(self, url):
        assert _is_prod_backend(url) is True

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:8001",          # the scratch instance
        "http://127.0.0.1:11434",         # Ollama
        "http://localhost:3001",          # the frontend
        "https://api.search.brave.com",   # anything remote
    ])
    def test_everything_else_is_allowed(self, url):
        assert _is_prod_backend(url) is False

    def test_a_malformed_url_does_not_break_the_run(self):
        """A guard must never be the thing that fails a test for its own reasons."""
        assert _is_prod_backend("::::not a url::::") is False


class TestGuardFires:
    def test_urlopen_to_production_raises_with_the_reason(self):
        with pytest.raises(RuntimeError, match="PRODUCTION backend"):
            urllib.request.urlopen("http://localhost:8000/sessions", timeout=1)

    def test_the_error_names_the_way_out(self):
        """An error that only says 'no' gets worked around."""
        with pytest.raises(RuntimeError) as exc:
            urllib.request.urlopen("http://127.0.0.1:8000/", timeout=1)
        msg = str(exc.value)
        assert "8001" in msg
        assert "NEPHILIM_ALLOW_PROD_BACKEND" in msg

    def test_other_ports_are_untouched(self):
        """The guard must not break tests that legitimately reach Ollama or a
        scratch backend — it refuses one port, not the network."""
        with pytest.raises(Exception) as exc:
            urllib.request.urlopen("http://127.0.0.1:1/", timeout=1)
        assert "PRODUCTION backend" not in str(exc.value)
