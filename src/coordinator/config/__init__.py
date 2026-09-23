# src/coordinator/config/__init__.py
"""
Configuration management for MCP Coordinator.

Uses Pydantic BaseSettings for centralized, validated configuration.
Provides both class-based access (settings.field) and function-based
access (get_field()) for backward compatibility.

Phase 1.2 of Persona Quality Enhancement Roadmap.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings

from .agent import AgentSettings, ToolBrainSettings
from .auth import AuthSettings
from .groundedness import GroundednessSettings
from .llm import OllamaSettings
from .lore import LoreSettings
from .memory import MemorySettings
from .routing import RoutingSettings
from .search import BraveSettings, SearchSettings, WebSearchSettings
from .wallet import EmailSettings, JupiterSettings

logger = logging.getLogger(__name__)


class CoordinatorSettings(BaseSettings):
    """Main coordinator configuration.

    Aggregates all subsystem settings into a single settings object.
    Access via `settings` singleton or `get_settings()` function.
    """

    # Core settings
    persona_dir: str = Field(
        default="personas",
        description="Directory containing persona JSON files",
        alias="PERSONA_DIR"
    )
    coord_port: int = Field(
        default=8000,
        ge=1024,
        le=65535,
        description="Coordinator server port",
        alias="COORD_PORT"
    )
    coord_url: str = Field(
        default="http://127.0.0.1:8000",
        description="Coordinator base URL",
        alias="COORD_URL"
    )
    db_path: str = Field(
        default="data/chats.db",
        description="SQLite database path",
        alias="COORDINATOR_DB_PATH"
    )

    # Subsystem settings (nested)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    brave: BraveSettings = Field(default_factory=BraveSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    jupiter: JupiterSettings = Field(default_factory=JupiterSettings)
    email: EmailSettings = Field(default_factory=EmailSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    routing: RoutingSettings = Field(default_factory=RoutingSettings)
    lore: LoreSettings = Field(default_factory=LoreSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    tool_brain: ToolBrainSettings = Field(default_factory=ToolBrainSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    web_search: WebSearchSettings = Field(default_factory=WebSearchSettings)
    groundedness: GroundednessSettings = Field(default_factory=GroundednessSettings)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }


@lru_cache(maxsize=1)
def get_settings() -> CoordinatorSettings:
    """Get the singleton settings instance.

    Uses lru_cache to ensure settings are only loaded once.
    """
    return CoordinatorSettings()


# Module-level singleton for convenient importing
settings = get_settings()


def get_persona_temperature_override(persona_card: dict) -> float:
    """Get persona-specific temperature or fallback to global default.

    Args:
        persona_card: Persona dictionary from JSON (must contain model_preferences)

    Returns:
        Per-persona temperature if defined, otherwise global PERSONA_TEMPERATURE
    """
    model_prefs = persona_card.get("model_preferences", {})
    if isinstance(model_prefs, dict) and "temperature" in model_prefs:
        temp = model_prefs["temperature"]
        if isinstance(temp, (int, float)) and 0.0 <= temp <= 2.0:
            return float(temp)
        else:
            logger.warning(
                f"Invalid temperature in persona model_preferences: {temp}. "
                f"Using global default {settings.ollama.temperature}"
            )

    return settings.ollama.temperature


def get_persona_sampling_overrides(persona_card: dict) -> dict:
    """Get all sampling parameter overrides from a persona card.

    Reads model_preferences from the persona JSON and returns a dict of
    sampling params that should be passed to the LLM client. Only includes
    values explicitly set in the persona card; missing values are omitted so
    callers can apply their own defaults.

    ``top_k``/``top_p``/``repeat_last_n`` were declared on ``SamplingPreset``
    but never read here, so a persona setting them had no effect whatsoever.
    ``repeat_last_n`` matters most: Ollama defaults it to 64 tokens (~50
    words), which is long enough to stop a sentence repeating inside one reply
    and far too short to notice a whole paragraph being reproduced. Passing
    ``-1`` scales the penalty window to the full context.

    Args:
        persona_card: Persona dictionary from JSON

    Returns:
        Dict of sampling params; every key except ``temperature`` is present
        only when the persona (or a global fallback) sets it.
    """
    model_prefs = persona_card.get("model_preferences", {})
    if not isinstance(model_prefs, dict):
        return {"temperature": settings.ollama.temperature}

    overrides: dict = {}

    # Temperature
    temp = model_prefs.get("temperature")
    if isinstance(temp, (int, float)) and 0.0 <= temp <= 2.0:
        overrides["temperature"] = float(temp)
    else:
        overrides["temperature"] = settings.ollama.temperature

    # Min-P
    min_p = model_prefs.get("min_p")
    if isinstance(min_p, (int, float)) and 0.0 < min_p <= 1.0:
        overrides["min_p"] = float(min_p)
    elif settings.ollama.min_p > 0.0:
        overrides["min_p"] = settings.ollama.min_p

    # Repeat penalty. Accept both spellings: SamplingPreset declares the field
    # as `repetition_penalty` while every shipped persona JSON writes
    # `repeat_penalty` (Ollama's own name). Reading only one silently ignored
    # the other.
    repeat_penalty = model_prefs.get("repeat_penalty", model_prefs.get("repetition_penalty"))
    if isinstance(repeat_penalty, (int, float)) and 1.0 <= repeat_penalty <= 2.0:
        overrides["repeat_penalty"] = float(repeat_penalty)

    # Repeat-penalty lookback window. -1 means "the whole context"; 0 disables.
    repeat_last_n = model_prefs.get("repeat_last_n")
    if isinstance(repeat_last_n, int) and not isinstance(repeat_last_n, bool) and repeat_last_n >= -1:
        overrides["repeat_last_n"] = repeat_last_n

    # Top-K
    top_k = model_prefs.get("top_k")
    if isinstance(top_k, int) and not isinstance(top_k, bool) and 0 <= top_k <= 100:
        overrides["top_k"] = top_k

    # Top-P (nucleus)
    top_p = model_prefs.get("top_p")
    if isinstance(top_p, (int, float)) and not isinstance(top_p, bool) and 0.0 <= top_p <= 1.0:
        overrides["top_p"] = float(top_p)

    return overrides


__all__ = [
    "AgentSettings",
    "ToolBrainSettings",
    "AuthSettings",
    "BraveSettings",
    "EmailSettings",
    "GroundednessSettings",
    "JupiterSettings",
    "LoreSettings",
    "MemorySettings",
    "OllamaSettings",
    "RoutingSettings",
    "SearchSettings",
    "WebSearchSettings",
    "CoordinatorSettings",
    "get_settings",
    "settings",
    "get_persona_temperature_override",
    "get_persona_sampling_overrides",
]
