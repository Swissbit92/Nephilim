"""The manual scorer's prompt-tag list must cover what the builder actually emits.

WHY THIS TEST EXISTS. tests/manual/scoring_engine.py carries _PROMPT_TAGS, the list
the no_leak dimension and the no_system_prompt_leak check search replies for. It was
written against an older builder and still named <world_context>,
<companion_behavior> and <response_format> after those blocks had been renamed to
<world>, <companion> and <format> -- so five ADVERSARIAL probes were scoring a leak
surface that no longer existed, and reporting passes for it.

A list of names cannot notice that it is stale. Deriving the names from a real built
prompt can. This test is the mechanical guard for that class, not for the names.

It lives in tests/backend/ deliberately: tests/manual/ contains no pytest test
functions, so nothing under it is ever collected and a guard placed there would
never run.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "manual"))


def _builder_tags(prompt: str) -> set[str]:
    """Opening structural tags in a built prompt, excluding closing tags."""
    return {m for m in re.findall(r"<[a-z_]+>", prompt)}


def test_scorer_prompt_tags_cover_builder(monkeypatch):
    from scoring_engine import _PROMPT_TAGS
    from src.coordinator import prompt_builder as pb
    from tests.backend.coordinator.test_lean_prompt import _ADVISORY_CARD, _patch_persona

    _patch_persona(monkeypatch, {**_ADVISORY_CARD, "mcp_access": ["solana_wallet"]})
    pb._build_system_prompt_lean.cache_clear()
    prompt = pb._build_system_prompt_lean("nephilim_test")
    pb._build_system_prompt_lean.cache_clear()

    emitted = _builder_tags(prompt)
    known = {t.strip("\\") for t in _PROMPT_TAGS}
    missing = emitted - known
    assert not missing, (
        f"the builder emits {sorted(missing)} but the scorer's _PROMPT_TAGS does not "
        "list them, so a reply leaking those tags scores clean"
    )
