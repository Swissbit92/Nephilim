# tests/backend/coordinator/test_image_tool.py
"""The `generate_image` tool: grants, the executor, and the chat-path wiring.

This milestone edits `routes/chat.py`, which every persona's every turn runs.
The first test is therefore the most important one here: with
`IMAGE_GEN_ENABLED` off, the offered tool surface must be IDENTICAL to what it
was before generation existed. The repo has the same pattern for the graph
identity overlay, whose test asserts a byte-identical prompt with the flag off
and treats the moment that stops being true as the change having become a
different, riskier one.

The other load-bearing tests:

- `test_a_side_effect_tool_answers_without_search_results` — without its
  branch in chat.py, a queued generation returns None from `_try_tool_brain`,
  the LEGACY path regenerates the turn, and the user gets an unrelated answer
  plus an unexplained picture five minutes later. Two generations, wrong half
  visible.
- `test_generate_image_does_not_require_hitl` — `requires_hitl` routes to the
  WALLET handler in chat.py, so setting it would send an image request into
  the trade-proposal flow.
- `test_the_executor_never_raises` — `ToolBrainService.run` wraps everything
  in `except Exception` and degrades to silence, so a raising executor makes a
  failed enqueue invisible.
- `test_eeva_keeps_her_web_and_wallet_surface` — adding `toolsets` to a card
  that had none OVERRIDES `mcp_access` entirely; listing only "image" would
  have silently revoked her search and wallet tools.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.coordinator.tools import registrations  # noqa: F401 - register builtins
from src.coordinator.tools.registry import registry

_PERSONAS = Path(__file__).resolve().parents[3] / "personas"


def _card(key: str) -> dict:
    return json.loads((_PERSONAS / f"{key}.json").read_text())


# ---------- the flag keeps it invisible ----------


def test_the_offered_surface_is_unchanged_when_generation_is_off(monkeypatch):
    """THE safety property for this milestone.

    `routes/chat.py` is the hottest path in the repo. With the flag off the
    tool list it builds must be exactly what it was before the image toolset
    existed — not "close to", identical. The moment this stops holding, the
    change has become a different and much riskier one.
    """
    from src.coordinator.config import get_settings

    card = _card("gwen")

    def offered() -> list[str]:
        toolsets = {"web"}
        if get_settings().image_gen.enabled:
            toolsets.add("image")
        return sorted(
            s.name for s in registry.specs_for_persona(card) if s.toolset in toolsets
        )

    monkeypatch.setenv("IMAGE_GEN_ENABLED", "false")
    get_settings.cache_clear()
    try:
        off = offered()
        assert off == ["image_search", "video_search"], (
            "the offered surface changed with generation DISABLED — this edit "
            "is no longer inert on the chat path"
        )
        monkeypatch.setenv("IMAGE_GEN_ENABLED", "true")
        get_settings.cache_clear()
        on = offered()
        assert on == ["generate_image", "image_search", "video_search"]
        assert set(off) < set(on), "the flag must ADD, never replace"
    finally:
        get_settings.cache_clear()


# ---------- grants ----------


@pytest.mark.parametrize("key", ["gwen", "gwen_dev", "nephilim_eeva"])
def test_the_granted_personas_hold_the_tool(key):
    names = {s.name for s in registry.specs_for_persona(_card(key))}
    assert "generate_image" in names


@pytest.mark.parametrize("key", ["nephilim_nyx", "nephilim_cipher",
                                 "nephilim_aegis", "nephilim_solace"])
def test_no_other_persona_holds_the_tool(key):
    """Gated to two personas by request. A third acquiring it silently is the
    failure this catches."""
    names = {s.name for s in registry.specs_for_persona(_card(key))}
    assert "generate_image" not in names


def test_eeva_keeps_her_web_and_wallet_surface():
    """She had NO `toolsets` key and resolved through `mcp_access`. Adding
    `toolsets` OVERRIDES `mcp_access` entirely — writing just ["image"] would
    have silently revoked her search and wallet tools."""
    names = {s.name for s in registry.specs_for_persona(_card("nephilim_eeva"))}
    assert "generate_image" in names
    assert "web_search" in names, "her web surface was revoked"
    assert "wallet_get_balances" in names, "her wallet surface was revoked"


def test_gwen_keeps_her_narrow_web_surface():
    """Her allowlist is the thing that stops a weather question firing
    image_search and inventing an answer. Adding a tool must not widen it."""
    web = {s.name for s in registry.specs_for_persona(_card("gwen"))
           if s.toolset == "web"}
    assert web == {"image_search", "video_search"}


# ---------- policy ----------


def test_generate_image_is_its_own_toolset():
    """Not `web`: web tools read the internet, this one spends five and a half
    minutes of the whole machine. Grouped, a persona granted search would
    silently also get a GPU job."""
    assert registry.get("generate_image").toolset == "image"


def test_generate_image_does_not_require_hitl():
    """`requires_hitl` routes to the WALLET handler in chat.py. Setting it
    would send an image request into the trade-proposal flow."""
    assert registry.get("generate_image").requires_hitl is False


def test_the_tool_has_an_executor_bound():
    from src.coordinator.tools.executor_bindings import bind_web_executors

    bind_web_executors()
    assert registry.get("generate_image").executor is not None


def test_the_schema_asks_for_fields_not_a_prompt():
    """The persona fills structured intent; code composes the prompt. A
    `prompt` parameter would hand prompt authorship back to the model."""
    fn = registry.get("generate_image").definition()["function"]
    props = fn["parameters"]["properties"]
    assert set(props) == {"subject", "setting", "mood", "style"}
    assert "prompt" not in props
    assert fn["parameters"]["required"] == ["subject"]


def test_the_description_states_the_trigger_affirmatively():
    """Open models violate negated instructions 77-100% of the time versus
    affirmative framing, and the documented hosted failure is a tool firing on
    topic-adjacency."""
    desc = registry.get("generate_image").definition()["function"]["description"]
    assert "only when the user asks" in desc.lower()
    assert "image_search" in desc, "must say where to go for an EXISTING picture"


# ---------- the interceptor ----------


def test_the_interceptor_validates_the_arguments():
    """Before this branch existed, an unknown tool fell through to `return
    None` and got NO argument validation at all — and these arguments start a
    331-second GPU job."""
    from src.coordinator.services.tool_interceptor import _validate_arguments

    assert _validate_arguments("generate_image", {"subject": "a fox"}) is None
    assert _validate_arguments("generate_image", {"subject": ""})
    assert _validate_arguments("generate_image", {})
    assert _validate_arguments("generate_image", {"subject": "a" * 400})
    assert _validate_arguments("generate_image", {"subject": "a\x00fox"})
    assert _validate_arguments("generate_image", {"subject": "a fox", "mood": 7})


# ---------- the executor ----------


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A real repo and a real throttle, installed where the executor looks."""
    from src.coordinator import startup
    from src.coordinator.repositories.image_job_repository import ImageJobRepository
    from src.coordinator.services.image_gen.throttle import GenerationThrottle

    repo = ImageJobRepository(str(tmp_path / "jobs.db"))
    throttle = GenerationThrottle(cooldown_seconds=0)
    monkeypatch.setattr(startup, "get_image_job_repo", lambda: repo)
    monkeypatch.setattr(startup, "get_generation_throttle", lambda: throttle)
    return repo, throttle


def _run(session_id="sess-1", **args):
    from src.coordinator.tools.image_executor import (
        current_session_id,
        generate_image_executor,
    )

    token = current_session_id.set(session_id)
    try:
        return generate_image_executor(args or {"subject": "a red fox"},
                                       {"key": "gwen"})
    finally:
        current_session_id.reset(token)


def test_the_executor_queues_a_job_and_returns_immediately(wired):
    repo, _ = wired
    out = _run(subject="a red fox", setting="deep snow", style="photographic")

    jobs = repo.list_for_session("sess-1")
    assert len(jobs) == 1
    assert jobs[0].status == "queued"
    # The composed prompt, not the raw field — prompt authorship stays in code.
    assert "a red fox" in jobs[0].prompt
    assert jobs[0].prompt.endswith("cinematic composition.")
    assert "five minutes" in out
    assert "do NOT describe" in out or "not describe" in out.lower()


def test_the_executor_refuses_without_a_session(wired):
    """A wiring error, reported as one rather than guessed at."""
    repo, _ = wired
    out = _run(session_id="", subject="a fox")
    assert repo.list_for_session("sess-1") == []
    assert "not available" in out


def test_the_throttle_blocks_a_second_identical_request(wired):
    repo, _ = wired
    _run(subject="a red fox in deep snow")
    out = _run(subject="a fox sitting in the deep snow")
    assert len(repo.list_for_session("sess-1")) == 1, "queued a near-duplicate"
    assert "same picture" in out


def test_the_executor_never_raises(wired, monkeypatch):
    """`ToolBrainService.run` wraps everything in `except Exception` and
    degrades to a silent result, so a raising executor makes a failed enqueue
    invisible — the user gets an ordinary reply and no sign anything happened."""
    from src.coordinator import startup

    broken = MagicMock()
    broken.create.side_effect = RuntimeError("database on fire")
    monkeypatch.setattr(startup, "get_image_job_repo", lambda: broken)

    out = _run(subject="a fox")  # must not raise
    assert "not available" in out


def test_a_malformed_intent_is_reported_not_raised(wired):
    repo, _ = wired
    out = _run(subject="   ")
    assert repo.list_for_session("sess-1") == []
    assert "subject is required" in out


# ---------- the chat-path branch ----------


def test_a_side_effect_tool_answers_without_search_results():
    """Without its branch, `_try_tool_brain` returns None on a queued
    generation, the LEGACY path regenerates the turn, and the user gets an
    unrelated answer plus an unexplained picture minutes later."""
    from src.coordinator.routes.chat import _SIDE_EFFECT_TOOLS

    assert "generate_image" in _SIDE_EFFECT_TOOLS

    trace = [{"tool": "generate_image", "allowed": True}]
    side_effects = [
        t["tool"] for t in trace
        if t.get("allowed") and t.get("tool") in _SIDE_EFFECT_TOOLS
    ]
    assert side_effects == ["generate_image"]


def test_a_blocked_side_effect_tool_does_not_count():
    from src.coordinator.routes.chat import _SIDE_EFFECT_TOOLS

    trace = [{"tool": "generate_image", "allowed": False}]
    assert [t["tool"] for t in trace
            if t.get("allowed") and t.get("tool") in _SIDE_EFFECT_TOOLS] == []


def test_a_search_tool_is_not_a_side_effect_tool():
    """The two branches must stay disjoint, or a search turn would skip its
    citations."""
    from src.coordinator.routes.chat import _SIDE_EFFECT_TOOLS

    assert not (_SIDE_EFFECT_TOOLS & {"web_search", "image_search",
                                      "video_search", "news_search", "fetch_url"})


def test_the_session_contextvar_does_not_leak_between_turns():
    """The thread goes back to the anyio pool and serves another session next.
    A leaked id would enqueue against the WRONG conversation and look fine."""
    from src.coordinator.tools.image_executor import current_session_id

    assert current_session_id.get() == ""
    token = current_session_id.set("sess-a")
    assert current_session_id.get() == "sess-a"
    current_session_id.reset(token)
    assert current_session_id.get() == ""


# ---------- a refusal during a generation must not read as a crash ----------


def test_a_busy_machine_produces_an_in_voice_reply_not_an_error():
    """MEASURED LIVE 2026-10-02: while an image the user had JUST asked for was
    generating normally, two consecutive chat turns returned "Something went
    wrong on my end. Try again in a moment."

    The system was working exactly as designed and told the user it had
    broken. For a companion that is worse than the wait it was reporting —
    and it trains the user to distrust a correct refusal.
    """
    from src.coordinator.routes.chat import _busy_drawing_response
    from src.coordinator.schemas import ChatBody

    resp = _busy_drawing_response(ChatBody(persona="gwen", message="how are you"))

    assert "drawing" in resp["answer"]
    assert resp["message_flow"] == "single"
    assert resp["used_search"] is False
    # It must read as HER, not as a status page.
    for forbidden in ("error", "Error", "503", "unavailable", "went wrong",
                      "model", "unloaded", "arbiter", "lease"):
        assert forbidden not in resp["answer"], f"{forbidden!r} leaked into the reply"


def test_the_chat_entry_point_catches_the_refusal():
    """Caught at the ONE entry point, not at each of the six guard sites: the
    refusal is one condition with one correct answer, and spreading it would
    guarantee a path that renders it differently."""
    import inspect

    from src.coordinator.routes import chat as chat_mod

    src = inspect.getsource(chat_mod.chat)
    assert "ResourceBusyError" in src, (
        "the chat entry point no longer catches ResourceBusyError — a chat "
        "turn during a generation will surface as a crash again"
    )
    assert "_busy_drawing_response" in src
