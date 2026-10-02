# tests/backend/coordinator/test_resource_arbiter_reachability.py
"""Does each guard actually FIRE, from the path production uses?

Separate from `test_resource_arbiter.py` on purpose. That file proves the
arbiter's logic; this one proves the logic is *reached*. The distinction is
this repo's most expensive recurring lesson:

  - a hard wall broke seven times in live Telegram traffic while its retry sat
    on a branch production never returns from;
  - `_regenerate_once_on_violation` had one call site, on a lane no breaching
    turn used, and the log showed 68 ungated turns and zero violations;
  - a red-team eval that imported modules directly proved logic, not
    reachability, and the guard it validated was orphaned.

Every test here holds a real lease and calls a real entry point. A guard that
is deleted, or moved behind a condition production does not take, turns one of
these red — which a logic test cannot do.

`tests/conftest.py` does NOT block port 11434, so these must never reach a live
Ollama. They don't: the guard raises BEFORE any socket is opened, which is
itself part of what is being asserted — a guard that fires after the request
has gone out would not have prevented the load.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.coordinator import startup
from src.coordinator.services.resource_arbiter import (
    ResourceArbiter,
    ResourceBusyError,
)


@pytest.fixture
def leased(monkeypatch):
    """A real arbiter, holding a real lease, installed where code looks."""
    arbiter = ResourceArbiter()
    monkeypatch.setattr(startup, "get_resource_arbiter", lambda: arbiter)
    cm = arbiter.exclusive("image-gen")
    cm.__enter__()
    yield arbiter
    cm.__exit__(None, None, None)


@pytest.fixture
def idle(monkeypatch):
    arbiter = ResourceArbiter()
    monkeypatch.setattr(startup, "get_resource_arbiter", lambda: arbiter)
    return arbiter


# ---------- transport A: the raw httpx client ----------


def test_ollama_http_post_is_guarded(leased):
    from src.coordinator.services.ollama_http import OllamaHTTPClient

    client = OllamaHTTPClient(base_url="http://127.0.0.1:11434", model="m")
    with patch("httpx.Client") as sock:
        with pytest.raises(ResourceBusyError):
            client.invoke("hello")
    sock.assert_not_called(), "the guard fired AFTER opening a socket"


def test_ollama_http_post_is_allowed_when_idle(idle):
    """The other half: the guard must not be refusing everything always.

    Without this, a guard wired to `return` unconditionally would pass every
    test above and silently disable the chat path.
    """
    from src.coordinator.services.ollama_http import OllamaHTTPClient

    client = OllamaHTTPClient(base_url="http://127.0.0.1:11434", model="m")
    with patch("httpx.Client") as sock:
        sock.return_value.__enter__.return_value.post.return_value = MagicMock(
            status_code=200, json=lambda: {"response": "hi"}
        )
        client.invoke("hello")
    sock.assert_called(), "idle must reach the transport"


# ---------- transport B: langchain, both entry points ----------


def test_llm_completion_invoke_is_guarded(leased):
    from src.coordinator.services.llm_completion_service import LLMCompletionService

    with patch("src.coordinator.services.llm_completion_service.OllamaLLM") as llm:
        svc = LLMCompletionService(base="http://127.0.0.1:11434", model="m")
        with pytest.raises(ResourceBusyError):
            svc.invoke("hello")
        llm.return_value.invoke.assert_not_called()


def test_llm_completion_generate_with_stats_is_guarded(leased):
    """The second entry point. `complete()` routes here, not through invoke(),
    so guarding only invoke() would leave the main completion path open."""
    from src.coordinator.services.llm_completion_service import LLMCompletionService

    with patch("src.coordinator.services.llm_completion_service.OllamaLLM") as llm:
        svc = LLMCompletionService(base="http://127.0.0.1:11434", model="m")
        with pytest.raises(ResourceBusyError):
            svc._generate_with_stats("hello")
        llm.return_value.generate.assert_not_called()


def test_llm_completion_complete_is_guarded_through_its_real_path(leased):
    """Not a direct call to the guarded function — the public method, so the
    chain from `complete()` to the guard is what is asserted."""
    from src.coordinator.services.llm_completion_service import LLMCompletionService

    with patch("src.coordinator.services.llm_completion_service.OllamaLLM") as llm:
        svc = LLMCompletionService(base="http://127.0.0.1:11434", model="m")
        with pytest.raises(ResourceBusyError):
            svc.complete("system", "user")
        llm.return_value.generate.assert_not_called()


def test_llm_completion_is_allowed_when_idle(idle):
    from src.coordinator.services.llm_completion_service import LLMCompletionService

    with patch("src.coordinator.services.llm_completion_service.OllamaLLM") as llm:
        llm.return_value.invoke.return_value = "hi"
        svc = LLMCompletionService(base="http://127.0.0.1:11434", model="m")
        assert svc.invoke("hello") == "hi"


# ---------- transport C: the ollama package, per TURN ----------


def test_tool_brain_run_is_guarded(leased):
    """`run()` never raises by contract — it degrades so the caller can fall
    back. ResourceBusyError must NOT be swallowed into a silent result, or a
    refused turn looks like 'the brain had nothing to say' and the legacy
    floor then makes the very Ollama call the lease exists to prevent."""
    from src.coordinator.services.tool_brain_service import ToolBrainService

    fake_client = MagicMock()
    svc = ToolBrainService(interceptor=MagicMock(), ollama_client=fake_client)
    with pytest.raises(ResourceBusyError):
        svc.run(
            persona_card={"key": "gwen"},
            system_prompt="s",
            user_message="hi",
            tools=[],
        )
    fake_client.chat.assert_not_called()


# ---------- the utility path (same model, 10m keep_alive) ----------


def test_prompt_builder_llm_is_guarded(leased):
    from src.coordinator import prompt_builder

    with pytest.raises(ResourceBusyError):
        prompt_builder._llm()


def test_cv_summarizer_llm_is_guarded(leased):
    from src.coordinator import cv_summarizer

    with pytest.raises(ResourceBusyError):
        cv_summarizer._llm()


# ---------- the background thread that fires at arbitrary times ----------


def test_fact_extraction_worker_is_guarded(leased):
    from src.coordinator.fact_extraction_worker import ExtractionJob, FactExtractionWorker

    extractor = MagicMock()
    worker = FactExtractionWorker(
        repo=MagicMock(), extractor_provider=lambda: extractor
    )
    job = ExtractionJob(user_id="u", session_id="s", messages=[{"role": "user", "content": "x"}])
    with pytest.raises(ResourceBusyError):
        worker.process_job(job)
    extractor.extract_triples.assert_not_called()


# ---------- the shape, not the file ----------


def test_every_chat_model_transport_is_guarded():
    """A structural check, so a NEW caller cannot quietly join the list.

    Greps for the construction of each Ollama transport and asserts the
    containing module imports the guard. Catches the case none of the tests
    above can: a seventh path added later by someone who never read this file.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[3] / "src" / "coordinator"

    # Modules that construct a CHAT-model transport. Embeddings are out of
    # scope on purpose (bge-m3 is 0.63 GiB and sits on the routing hot path).
    transport = re.compile(
        r"OllamaLLM\s*\(|ollama\.Client\s*\(|/api/generate|/api/chat"
    )
    # Known non-callers: they describe or administer, they do not generate.
    allowed = {
        "services/ollama_admin.py",   # the evictor itself — guarding it would deadlock
        "config/llm.py",              # settings prose
        "ollama_utils.py",            # /api/tags only; does not load a model
        "server.py",                  # /api/version health probe
    }

    offenders = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if transport.search(text) and "guard_chat_model" not in text:
            offenders.append(rel)

    assert not offenders, (
        "these modules reach a chat-model transport without importing the "
        f"arbiter guard: {offenders}. Add guard_chat_model(...) at the call, "
        "or add the file to `allowed` with a reason if it cannot load a model."
    )
