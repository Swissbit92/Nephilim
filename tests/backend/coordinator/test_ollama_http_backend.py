"""min_p must actually reach Ollama, not just be passed hopefully.

`langchain_ollama.OllamaLLM` coerces its options through `ollama.Options`,
which has no `min_p` field — so the value is dropped with no error and no
warning. A test written against the langchain client can only assert that we
*passed* min_p, never that Ollama *received* it; the assertion would pass while
the feature did nothing. These tests inspect the JSON body actually put on the
wire, which is the only place the question can be answered honestly.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from src.coordinator.config import get_settings
from src.coordinator.services.ollama_http import OllamaHTTPClient


@contextmanager
def backend(name: str):
    cfg = get_settings().ollama
    before = cfg.completion_backend
    cfg.completion_backend = name
    try:
        yield
    finally:
        cfg.completion_backend = before


def _response(payload=None, status=200, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = payload or {"response": "hello", "prompt_eval_count": 42}
    return resp


@contextmanager
def _captured_post(**resp_kwargs):
    """Capture the JSON body actually sent to Ollama."""
    with patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value = _response(**resp_kwargs)
        yield client


# ─── the wire body ───────────────────────────────────────────────────────────


def test_min_p_reaches_the_wire():
    """The whole reason this transport exists."""
    with _captured_post() as client:
        OllamaHTTPClient("http://x", "m", options={"min_p": 0.05}).invoke("hi")
    body = client.post.call_args.kwargs["json"]
    assert body["options"]["min_p"] == 0.05


def test_every_sampler_survives_verbatim():
    opts = {"min_p": 0.05, "repeat_penalty": 1.15, "repeat_last_n": -1, "top_k": 40, "top_p": 0.9}
    with _captured_post() as client:
        OllamaHTTPClient("http://x", "m", options=opts).invoke("hi")
    assert client.post.call_args.kwargs["json"]["options"] == opts


def test_request_targets_the_generate_endpoint_and_is_non_streaming():
    with _captured_post() as client:
        OllamaHTTPClient("http://x/", "m").invoke("hi")
    assert client.post.call_args.args[0] == "http://x/api/generate"
    assert client.post.call_args.kwargs["json"]["stream"] is False


def test_keep_alive_is_only_sent_when_set():
    with _captured_post() as client:
        OllamaHTTPClient("http://x", "m").invoke("hi")
    assert "keep_alive" not in client.post.call_args.kwargs["json"]

    with _captured_post() as client:
        OllamaHTTPClient("http://x", "m", keep_alive="-1").invoke("hi")
    assert client.post.call_args.kwargs["json"]["keep_alive"] == "-1"


def test_options_are_copied_not_aliased():
    """The caller reuses its params dict; mutating it must not alter the client."""
    opts = {"min_p": 0.05}
    c = OllamaHTTPClient("http://x", "m", options=opts)
    opts["min_p"] = 0.9
    assert c.options["min_p"] == 0.05


# ─── parity with the langchain path ──────────────────────────────────────────


def test_generate_exposes_token_stats():
    with _captured_post(payload={"response": " hi ", "prompt_eval_count": 42, "eval_count": 7}):
        result = OllamaHTTPClient("http://x", "m").generate(["p"])
    gen = result.generations[0][0]
    assert gen.text == "hi"  # stripped, as the langchain path returns it
    assert gen.generation_info["prompt_eval_count"] == 42


def test_model_not_found_raises_the_same_guidance():
    with _captured_post(status=404, text="model 'm' not found"):
        with pytest.raises(RuntimeError, match="ollama pull m"):
            OllamaHTTPClient("http://x", "m").invoke("hi")


def test_connection_failure_names_the_host():
    import httpx

    with patch("httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.side_effect = httpx.ConnectError(
            "refused"
        )
        with pytest.raises(RuntimeError, match="Ollama unreachable at http://x"):
            OllamaHTTPClient("http://x", "m").invoke("hi")


def test_other_http_errors_surface_the_status():
    with _captured_post(status=500, text="boom"):
        with pytest.raises(RuntimeError, match="500"):
            OllamaHTTPClient("http://x", "m").invoke("hi")


# ─── the flag ────────────────────────────────────────────────────────────────


def _service(**kwargs):
    from src.coordinator.services.llm_completion_service import LLMCompletionService

    return LLMCompletionService(base="http://x", model="m", **kwargs)


def test_langchain_backend_is_the_default_and_unchanged():
    with backend("langchain"):
        with patch("src.coordinator.services.llm_completion_service.OllamaLLM") as m:
            _service()
        assert m.called


def test_http_backend_swaps_the_transport():
    with backend("http"):
        with patch("src.coordinator.services.llm_completion_service.OllamaLLM") as m:
            svc = _service(min_p=0.05)
        assert not m.called
        assert isinstance(svc.llm, OllamaHTTPClient)
        assert svc.llm.options["min_p"] == 0.05
        # transport-level identity is not smuggled into the options dict
        for key in ("base_url", "model", "keep_alive"):
            assert key not in svc.llm.options


def test_langchain_backend_warns_that_min_p_will_be_dropped(caplog):
    """Silent failure is the actual defect; a warning is the minimum fix even
    when the transport cannot carry the value."""
    with backend("langchain"):
        with patch("src.coordinator.services.llm_completion_service.OllamaLLM"):
            with caplog.at_level("WARNING"):
                _service(min_p=0.05)
    assert any("min_p" in r.message for r in caplog.records)
