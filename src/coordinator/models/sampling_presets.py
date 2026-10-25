# src/coordinator/models/sampling_presets.py
"""
Sampling presets for LLM generation.

Defines named presets for different use cases (creative writing, factual answers,
etc.) and provides utilities for applying them to Ollama LLM instances.

Phase 1.3 of Persona Quality Enhancement Roadmap.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class SamplingConfig:
    """Complete sampling configuration for LLM generation."""

    temperature: float = 0.7
    top_k: Optional[int] = None
    top_p: Optional[float] = None
    repeat_penalty: Optional[float] = None
    min_p: Optional[float] = None

    # Metadata
    name: str = "custom"
    description: str = ""

    def to_ollama_params(self) -> Dict[str, Any]:
        """Convert to Ollama-compatible parameters dict.

        Only includes non-None values to allow Ollama defaults where appropriate.
        """
        params = {"temperature": self.temperature}

        if self.top_k is not None:
            params["top_k"] = self.top_k

        if self.top_p is not None:
            params["top_p"] = self.top_p

        if self.repeat_penalty is not None:
            params["repeat_penalty"] = self.repeat_penalty

        # min_p requires Ollama v0.1.32+
        if self.min_p is not None:
            params["min_p"] = self.min_p

        return params

    def __str__(self) -> str:
        params = self.to_ollama_params()
        param_str = ", ".join(f"{k}={v}" for k, v in params.items())
        return f"{self.name}({param_str})"


# ============================================================================
# NAMED PRESETS
# ============================================================================

# ⚠️ FITTED TO A MODEL THAT IS GONE. These values were tuned for Magidonia-24B-v4.3,
# deleted from disk 2026-10-25. They were kept unchanged through the 2026-07-05 switch
# to huihui_ai/mistral-small-abliterated:24b because that model shares the same
# Mistral-Small base, so the tuning still applied. It would NOT survive a switch to a
# different base — the note below says Mistral-Small degrades above ~1.1 temp *unlike
# Gemma*, so a Gemma-family daily driver would inherit sampling fitted to the wrong
# curve. Re-tune before, not after, any base-family change.
#
# Re-tuned 2026-06-21 for Magidonia-24B-v4.3 (Mistral-Small-3.2 base). vs the
# original gemma2:9b values: base temperatures lowered (Mistral-Small degrades
# above ~1.1 temp, unlike Gemma) and repeat_penalty softened to ~1.03-1.08
# (Mistral is sensitive to >1.1 — causes odd word-avoidance). min_p centered on
# ~0.05. NOTE: the nephilim personas override `temperature` per-JSON (0.6-0.95),
# so for them the impactful change here is the inherited min_p / repeat_penalty.
PRESETS: Dict[str, SamplingConfig] = {
    "creative": SamplingConfig(
        temperature=1.0,
        top_k=50,
        top_p=0.95,
        repeat_penalty=1.05,
        min_p=0.05,
        name="creative",
        description="High creativity for storytelling, roleplay, and imaginative responses"
    ),

    "balanced": SamplingConfig(
        temperature=0.8,
        top_k=40,
        top_p=0.95,
        repeat_penalty=1.05,
        min_p=0.05,
        name="balanced",
        description="Balanced creativity and coherence for general conversation"
    ),

    "precise": SamplingConfig(
        temperature=0.4,
        top_k=30,
        top_p=0.90,
        repeat_penalty=1.05,
        min_p=0.08,
        name="precise",
        description="High precision for factual answers, technical explanations"
    ),

    "chaotic": SamplingConfig(
        temperature=1.1,
        top_k=60,
        top_p=0.97,
        repeat_penalty=1.03,
        min_p=0.03,
        name="chaotic",
        description="Maximum creativity and unpredictability"
    ),

    "deterministic": SamplingConfig(
        temperature=0.15,
        top_k=10,
        top_p=0.7,
        repeat_penalty=1.08,
        min_p=0.1,
        name="deterministic",
        description="Near-deterministic output for consistent, reproducible responses"
    ),
}


def get_preset(name: str) -> Optional[SamplingConfig]:
    """Get a named preset by name.

    Args:
        name: Preset name (creative, balanced, precise, chaotic, deterministic)

    Returns:
        SamplingConfig if preset exists, None otherwise
    """
    return PRESETS.get(name.lower())


def get_preset_or_default(name: Optional[str], default: str = "balanced") -> SamplingConfig:
    """Get a named preset, falling back to default if not found.

    Args:
        name: Preset name (or None to use default)
        default: Default preset name if name is None or not found

    Returns:
        SamplingConfig for the requested or default preset
    """
    if name:
        preset = get_preset(name)
        if preset:
            return preset
        logger.warning(f"Unknown sampling preset '{name}', using '{default}'")

    return PRESETS[default]


def build_sampling_config(
    temperature: Optional[float] = None,
    top_k: Optional[int] = None,
    top_p: Optional[float] = None,
    repeat_penalty: Optional[float] = None,
    min_p: Optional[float] = None,
    preset: Optional[str] = None,
) -> SamplingConfig:
    """Build a sampling config from individual parameters or preset.

    If preset is provided, starts from preset defaults and overrides with
    any explicitly provided parameters.

    Args:
        temperature: Sampling temperature (0.0-2.0)
        top_k: Top-K sampling (0-100)
        top_p: Nucleus sampling threshold (0.0-1.0)
        repeat_penalty: Repetition penalty (1.0-2.0)
        min_p: Min-P sampling threshold (0.0-1.0)
        preset: Named preset to start from

    Returns:
        SamplingConfig with merged parameters
    """
    # Start from preset if provided
    if preset:
        base = get_preset_or_default(preset)
        config = SamplingConfig(
            temperature=base.temperature,
            top_k=base.top_k,
            top_p=base.top_p,
            repeat_penalty=base.repeat_penalty,
            min_p=base.min_p,
            name=f"{base.name}+custom",
            description=f"Based on {base.name} with custom overrides"
        )
    else:
        config = SamplingConfig(name="custom", description="Custom sampling configuration")

    # Override with explicit parameters
    if temperature is not None:
        config.temperature = temperature
    if top_k is not None:
        config.top_k = top_k
    if top_p is not None:
        config.top_p = top_p
    if repeat_penalty is not None:
        config.repeat_penalty = repeat_penalty
    if min_p is not None:
        config.min_p = min_p

    return config


def get_sampling_for_persona(persona_card: dict) -> SamplingConfig:
    """Extract sampling configuration from a persona card.

    Reads model_preferences from persona JSON and builds appropriate config.

    Args:
        persona_card: Persona card dict (from JSON)

    Returns:
        SamplingConfig for this persona
    """
    prefs = persona_card.get("model_preferences", {})

    if not prefs:
        # No preferences, use balanced default
        return get_preset_or_default(None, "balanced")

    # Check for preset
    preset_name = prefs.get("preset")

    # Build from individual params with preset as base
    return build_sampling_config(
        temperature=prefs.get("temperature"),
        top_k=prefs.get("top_k"),
        top_p=prefs.get("top_p"),
        repeat_penalty=prefs.get("repetition_penalty"),
        min_p=prefs.get("min_p"),
        preset=preset_name
    )


def list_presets() -> Dict[str, str]:
    """Get all available presets with descriptions.

    Returns:
        Dict mapping preset names to descriptions
    """
    return {name: preset.description for name, preset in PRESETS.items()}
