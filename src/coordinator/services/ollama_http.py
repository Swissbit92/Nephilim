"""A direct Ollama `/api/generate` client, for samplers langchain cannot pass.

`langchain_ollama.OllamaLLM` coerces its options through `ollama.Options`, a
pydantic model with no `min_p` field. Neither layer has one, so `min_p` is
dropped silently whatever is passed — no error, no warning, and a persona that
declares it simply never gets it. Verified against langchain-ollama 1.0.1;
upstream langchain-ai/langchain#32744.

min_p is the sampler best suited to this workload: it scales its cutoff with
the model's own confidence at each step instead of applying a fixed nucleus,
which is why it holds up across the 0.6-0.95 temperature range the personas
span (arXiv:2407.01082, ICLR 2025).

This client speaks Ollama's HTTP API directly, so the options dict reaches the
server exactly as written. It exposes the same surface `LLMCompletionService`
already uses — `invoke`, `generate`, `model`, `base_url` — so it is a drop-in
for the langchain object rather than a parallel code path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Generation can legitimately run long on a large local model; the caller's
# num_predict is the real bound on output length.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=5.0)


@dataclass
class _Generation:
    """Mirrors the langchain generation object the caller already reads."""

    text: str
    generation_info: dict[str, Any] = field(default_factory=dict)


@dataclass
class _GenerationResult:
    generations: list[list[_Generation]]


class OllamaHTTPClient:
    """Minimal Ollama `/api/generate` client with full sampler passthrough."""

    def __init__(
        self,
        base_url: str,
        model: str,
        options: dict[str, Any] | None = None,
        keep_alive: Any = None,
        timeout: httpx.Timeout | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        # Copied, not aliased: the caller reuses its params dict.
        self.options = dict(options or {})
        self.keep_alive = keep_alive
        self._timeout = timeout or _DEFAULT_TIMEOUT

    def _payload(self, prompt: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": self.options,
        }
        if self.keep_alive is not None:
            payload["keep_alive"] = self.keep_alive
        return payload

    def _post(self, prompt: str) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(f"{self.base_url}/api/generate", json=self._payload(prompt))
        except httpx.RequestError as exc:
            raise RuntimeError(f"Ollama unreachable at {self.base_url}: {exc}") from exc

        if resp.status_code == 404 or (
            resp.status_code >= 400 and "not found" in resp.text.lower()
        ):
            # Same message shape the langchain path raises, so callers that
            # match on it keep working.
            raise RuntimeError(
                "Ollama model not found.\n"
                f"Pull it:\n  ollama pull {self.model}\n"
                f"base_url={self.base_url}\n"
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"Ollama returned {resp.status_code}: {resp.text[:300]}")

        return resp.json()

    def invoke(self, prompt: str) -> str:
        return (self._post(prompt).get("response") or "").strip()

    def generate(self, prompts: list[str]) -> _GenerationResult:
        """Batch-shaped for interface parity; Ollama's endpoint is one at a time."""
        rows: list[list[_Generation]] = []
        for prompt in prompts:
            data = self._post(prompt)
            info = {
                k: data[k]
                for k in (
                    "prompt_eval_count",
                    "eval_count",
                    "total_duration",
                    "load_duration",
                    "done_reason",
                )
                if k in data
            }
            rows.append(
                [_Generation(text=(data.get("response") or "").strip(), generation_info=info)]
            )
        return _GenerationResult(generations=rows)
