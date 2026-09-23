# src/coordinator/ollama_utils.py
# Utilities for interacting with Ollama LLM in GraphRAG Local QA Chat with Personas
# Functions to list local models and assert model availability.
# Handles Ollama connectivity errors.

import requests
from typing import List

class OllamaModelNotFound(RuntimeError):
    pass


class ModelNotConfigured(RuntimeError):
    """PERSONA_MODEL is unset. Distinct from OllamaModelNotFound on purpose.

    `assert_model_available` cannot catch this case and never could: it checks
    that whatever name it is handed exists in Ollama, so a fallback default that
    happens to be pulled locally passes it happily — on the wrong model, at the
    wrong context window. That is the silent failure this exception exists to
    convert into a loud one.
    """


def require_model_configured(model: str) -> str:
    """Reject an unset model name. Returns the model so call sites can inline it.

    Called at startup (not at import) so a missing .env produces one readable
    error instead of a ValidationError during test collection.
    """
    if not (model or "").strip():
        raise ModelNotConfigured(
            "PERSONA_MODEL is not set, so there is no model to run.\n"
            "Refusing to start rather than falling back to a different model — a "
            "fallback runs silently at the wrong size and context window, which "
            "looks like a working server.\n"
            "Fix: set PERSONA_MODEL in .env (or export it), e.g.\n"
            "  PERSONA_MODEL=huihui_ai/mistral-small-abliterated:24b\n"
            "Then restart. `ollama list` shows what is pulled locally."
        )
    return model


def list_local_models(base_url: str) -> List[str]:
    r = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=10)
    r.raise_for_status()
    data = r.json() or {}
    return [m.get("name") for m in data.get("models", []) if m.get("name")]

def assert_model_available(base_url: str, model: str) -> None:
    try:
        available = list_local_models(base_url)
    except Exception as e:
        raise RuntimeError(
            f"Could not reach Ollama at {base_url}. Is it running?\n"
            f"Original error: {e}"
        )
    if model not in available:
        hint = ""
        if available:
            hint = "Available models:\n  - " + "\n  - ".join(available)
        raise OllamaModelNotFound(
            f"Ollama model '{model}' is not available at {base_url}.\n"
            f"Pull it:\n  ollama pull {model}\n\n{hint}"
        )
