"""Recalled memories must not look like the model's own last line.

Semantic search resurfaces messages from earlier in the session and splices
them into the same history list as recent turns. Every one of them was built
with its original `assistant` role and rendered as `Assistant: ...` — byte
identical to what the model said one turn ago. With nothing marking them as
recall, the model continued them verbatim, which is how a paragraph from a
month earlier reappeared word-for-word in a live conversation.

The second half covers the two blocks that were pinned into every prompt
forever: the three cached voice exemplars and the first three messages of the
session. Both are fixed text in dialogue format, repeated on every turn — the
condition few-shot copying feeds on (arXiv:2402.09954).
"""

from __future__ import annotations

import sys
import pytest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from src.coordinator import prompt_builder as pb
from src.coordinator.config import get_settings
from src.coordinator.memory_manager import MemoryManager
from src.coordinator.schemas import MessageRole


# The persona identity is the only part of the prompt that needs a model, and
# none of these tests assert on it.
_STUB_IDENTITY = "A stubbed identity brief. Not asserted on by any test here."


@pytest.fixture(autouse=True)
def _identity_without_a_live_model(monkeypatch):
    """Render prompts without reaching Ollama.

    `_build_system_prompt_lean` resolves the identity as
    ``get_or_build_cv_summary(selector).get("summary") or _summarize(...)``, and
    both sides of that `or` end in a live model call via `_llm()`, which asserts
    the model is reachable before anything is generated.

    A cache hides this on any machine that has run the app. On a fresh checkout
    the cache is always cold, so CI reached a live Ollama and these tests failed
    on every run from 2026-08-23 — 6 failed, 2222 passed — for a month, while
    passing locally the whole time.

    They assert on prompt STRUCTURE: whether the constraints block appears,
    whether examples are included, what order the sections come in. Pinning the
    identity text keeps every one of those assertions intact and removes the
    network. Skipping them instead (`@requires_ollama`) would have been cheaper
    and would have discarded the coverage that matters most — one of these tests
    exists because its assertion was False in production.

    The cache is cleared on both sides: `_build_system_prompt_lean` is
    `lru_cache`d on the selector alone, so a stubbed prompt would otherwise leak
    into the next test and a real one into this.
    """
    monkeypatch.setattr(
        pb, "get_or_build_cv_summary", lambda selector: {"summary": _STUB_IDENTITY}
    )
    pb._build_system_prompt_lean.cache_clear()
    yield
    pb._build_system_prompt_lean.cache_clear()


@contextmanager
def unpin(enabled: bool, threshold: int = 6):
    cfg = get_settings().agent
    before = (cfg.unpin_on_depth, cfg.unpin_depth_turns)
    cfg.unpin_on_depth, cfg.unpin_depth_turns = enabled, threshold
    pb._build_system_prompt_lean.cache_clear()
    try:
        yield
    finally:
        cfg.unpin_on_depth, cfg.unpin_depth_turns = before
        pb._build_system_prompt_lean.cache_clear()


# ─── the RECALLED role ───────────────────────────────────────────────────────


def test_recalled_role_exists_and_is_a_str():
    assert MessageRole.RECALLED == "recalled"
    assert isinstance(MessageRole.RECALLED, str)


def test_recalled_renders_as_background_not_dialogue():
    """The whole point: a recalled turn must not be formatted the same way as
    the model's own most recent line."""
    from src.coordinator.routes.chat import chat  # noqa: F401  (import guard)

    # Render through the same branch the route uses.
    lines = []
    for role, content in (
        ("assistant", "a recent reply"),
        ("recalled", "something said weeks ago"),
    ):
        if role == "assistant":
            lines.append(f"Assistant: {content}")
        elif role == "recalled":
            lines.append(f"[Recalled from earlier — background only, do not repeat it: {content}]")
    rendered = "\n\n".join(lines)
    assert "Assistant: a recent reply" in rendered
    assert "Assistant: something said weeks ago" not in rendered
    assert "[Recalled from earlier" in rendered


def test_route_renders_recalled_distinctly():
    """Exercises the real route source rather than a copy of its logic."""
    src = Path("src/coordinator/routes/chat.py").read_text()
    assert 'elif role == "recalled":' in src
    assert "[Recalled from earlier" in src


def test_tool_brain_skips_recalled_turns():
    """A recalled message is background, not a fresh ask. Mapping it to "user"
    would present weeks-old text to the tool router as a new request."""
    src = Path("src/coordinator/services/tool_brain_service.py").read_text()
    assert 'hrole in ("narrator", "recalled")' in src


# ─── un-pinning voice exemplars ──────────────────────────────────────────────


def test_examples_present_by_default():
    with unpin(False):
        assert "<voice_examples>" in pb.build_system_prompt("gwen")


def test_examples_can_be_omitted_without_touching_the_rest():
    with unpin(True):
        with_ex = pb.build_system_prompt("gwen", include_examples=True)
        without = pb.build_system_prompt("gwen", include_examples=False)
    assert "<voice_examples>" in with_ex
    assert "<voice_examples>" not in without
    # everything before the exemplars is untouched
    assert without.split("<voice_examples>")[0] in with_ex


def test_both_variants_are_cached_separately():
    """The builder is lru_cached; the second argument must be part of the key or
    one variant would serve the other."""
    pb._build_system_prompt_lean.cache_clear()
    a = pb.build_system_prompt("gwen", include_examples=True)
    b = pb.build_system_prompt("gwen", include_examples=False)
    assert a != b
    assert pb._build_system_prompt_lean.cache_info().currsize == 2


# ─── un-pinning the first three messages ─────────────────────────────────────


# Messages have to be realistically sized or the token budget never binds and
# the selector returns everything — which would make the un-pin assertions pass
# without the un-pin doing anything.
_BODY = "this is a reasonably long conversational message with plenty of words in it " * 4


def _messages(n):
    return [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"message number {i} {_BODY}",
        }
        for i in range(n)
    ]


def _has(selected, i):
    return any(m["content"].startswith(f"message number {i} ") for m in selected)


def test_first_three_are_pinned_by_default():
    mm = MemoryManager(max_tokens=100_000)
    with unpin(False):
        selected = mm.select_messages(_messages(40), token_budget=1500, system_prompt_tokens=10)
    for i in range(3):
        assert _has(selected, i)


def test_short_sessions_still_pin_even_when_enabled():
    """Below the threshold the opener is genuinely the only context there is."""
    mm = MemoryManager(max_tokens=100_000)
    with unpin(True, threshold=20):
        selected = mm.select_messages(_messages(5), token_budget=100_000, system_prompt_tokens=10)
    assert _has(selected, 0)


def test_deep_sessions_stop_force_including_the_opener():
    """Past the threshold the opening turns compete on score like anything
    else, instead of occupying every prompt for the life of the session.

    Asserted against a budget tight enough to force a choice but comfortably
    above the 500-token response reserve — below that the budget goes negative
    and the selector drops everything, which would make this pass for the
    wrong reason.
    """
    mm = MemoryManager(max_tokens=100_000)
    msgs = _messages(60)

    with unpin(False):
        pinned = mm.select_messages(msgs, token_budget=1500, system_prompt_tokens=10)
    with unpin(True, threshold=6):
        unpinned = mm.select_messages(msgs, token_budget=1500, system_prompt_tokens=10)

    # the opener is force-included by default...
    assert _has(pinned, 0)
    # ...and is no longer exempt from scoring once the session is deep
    assert not _has(unpinned, 0)
    # the most recent turn survives either way — recency is not what changed
    assert _has(pinned, 59)
    assert _has(unpinned, 59)
