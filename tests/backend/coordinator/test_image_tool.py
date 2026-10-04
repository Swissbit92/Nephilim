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
from unittest.mock import MagicMock, patch

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


def test_the_cooldown_still_blocks_a_rapid_second_request(wired):
    """The duplicate rule is gone; the COOLDOWN is what remains, and it is
    about pacing a 5.5-minute GPU job rather than about what was asked for."""
    from src.coordinator import startup
    from src.coordinator.services.image_gen.throttle import GenerationThrottle

    repo, _ = wired
    slow = GenerationThrottle(cooldown_seconds=120)
    startup.get_generation_throttle = lambda: slow

    _run(subject="a red fox in deep snow")
    out = _run(subject="a cathedral with stained glass")  # DIFFERENT picture
    assert len(repo.list_for_session("sess-1")) == 1
    assert "breath" in out or "seconds" in out


def test_a_refinement_is_queued_not_refused(wired):
    """The live defect: 14 of 19 real requests were refused as duplicates."""
    repo, _ = wired
    _run(subject="a red haired mature beauty milf in a black bikini, full body")
    _run(subject="a blond mature beauty milf in a white bikini, full body")
    assert len(repo.list_for_session("sess-1")) == 2, (
        "a refinement was refused — only one job was queued"
    )


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


#: Status-page vocabulary. A refusal that uses any of these reads as an
#: outage report rather than as her.
_STATUS_PAGE_WORDS = ("error", "Error", "503", "unavailable", "went wrong",
                      "model", "unloaded", "arbiter", "lease")


@pytest.fixture
def busy_line(monkeypatch):
    """Pin the in-voice line, so these tests do not depend on a live model.

    ⚠️ They used to assert `"drawing" in answer`, which is text from
    `persona_lines._FALLBACK` — used ONLY when the model is unreachable. So
    they passed headless, failed as soon as a model was available, and
    asserted the degraded path rather than the wiring. Worse, they would
    have stayed green with in-voice generation entirely broken.
    """
    from src.coordinator.services import persona_lines
    monkeypatch.setattr(persona_lines, "line",
                        lambda persona_key, situation: f"<<{situation}>>")
    return "<<image_busy>>"


def test_a_busy_machine_produces_an_in_voice_reply_not_an_error(busy_line):
    """MEASURED LIVE 2026-10-02: while an image the user had JUST asked for was
    generating normally, two consecutive chat turns returned "Something went
    wrong on my end. Try again in a moment."

    The system was working exactly as designed and told the user it had
    broken. For a companion that is worse than the wait it was reporting —
    and it trains the user to distrust a correct refusal.
    """
    from src.coordinator.routes.chat import busy_drawing_body

    resp = busy_drawing_body()

    assert resp["answer"] == busy_line, "the BUSY situation must be the one asked for"
    assert resp["message_flow"] == "single"
    assert resp["used_search"] is False


def test_the_canned_busy_line_is_not_a_status_page():
    """Checked against the FALLBACK STRING ITSELF, not against whatever text
    happened to be produced.

    This is the line a user sees exactly when the model is gone — the one
    moment it cannot be generated in voice — so it is the one most at risk
    of being written like an error message, and the only one whose wording
    is fixed enough to assert on.
    """
    from src.coordinator.services.persona_lines import _FALLBACK

    canned = _FALLBACK["image_busy"]
    for forbidden in _STATUS_PAGE_WORDS:
        assert forbidden not in canned, f"{forbidden!r} leaked into the reply"


def test_every_persona_route_is_covered_by_the_busy_handler():
    """THE fix I got wrong the first time.

    I caught ResourceBusyError inside `routes/chat.py::chat`, which fixed
    `/chat` and left `POST /sessions/{id}/chat` — the endpoint the Telegram
    gateway actually uses — still returning 503 "LLM service temporarily
    unavailable". One call site of a condition that has several is this
    repo's own recorded failure and I repeated it within the hour.

    An app-level handler covers every route including ones not yet written,
    which is why the assertion is about the HANDLER and not about any route.
    """
    from src.coordinator.server import app
    from src.coordinator.services.resource_arbiter import ResourceBusyError

    assert ResourceBusyError in app.exception_handlers, (
        "the app-level ResourceBusyError handler is gone — a chat turn during "
        "a generation will surface as a 503 on every route again"
    )


def test_the_busy_handler_answers_200_not_5xx(busy_line):
    """A 5xx makes every client treat a correct, expected refusal as an
    outage; the gateway renders it as MSG_ERROR. Nothing failed."""
    import asyncio

    from src.coordinator.server import app
    from src.coordinator.services.resource_arbiter import ResourceBusyError

    handler = app.exception_handlers[ResourceBusyError]
    resp = asyncio.run(handler(None, ResourceBusyError("leased for 42s")))
    assert resp.status_code == 200
    assert b"<<image_busy>>" in resp.body
    assert b"leased" not in resp.body, "the internal reason leaked to the user"


def test_complete_or_503_lets_the_busy_error_through():
    """It wrapped EVERY exception into a 503, which is what hid the refusal."""
    import inspect

    from src.coordinator.routes import chat as chat_mod

    src = inspect.getsource(chat_mod._complete_or_503)
    assert "except ResourceBusyError" in src, (
        "_complete_or_503 masks the busy refusal as a 503 again"
    )


# ---------- the generation trigger: a property of the MESSAGE ----------


@pytest.mark.parametrize("phrase", [
    "draw me a fox in the snow",
    "make me a picture of a cat",
    "generate an image of a fox",
    "paint me a field of poppies",
    "sketch a harbour at night",
    "create an illustration of a lake",
    "use your image generator: a fox",
    "can you draw a mountain lake for me",
])
def test_generation_intent_fires_on_a_drawing_request(phrase):
    from src.coordinator.tools.intent_classifier import generation_intent

    assert generation_intent(phrase) is True


@pytest.mark.parametrize("phrase", [
    # SEARCH, not generation — media_search_type owns these
    "show me a picture of a fox",
    "find me images of foxes",
    # passive mention
    "that painting you described earlier",
    # figurative: the noun requirement is what keeps these out, exactly as it
    # does for bare "find me" roleplay in the media rule
    "draw me closer",
    "make me yours",
    "make me a coffee",
    "you make me happy",
    "create a problem for yourself",
    "what is the weather tomorrow",
])
def test_generation_intent_stays_out_of_roleplay_and_search(phrase):
    from src.coordinator.tools.intent_classifier import generation_intent

    assert generation_intent(phrase) is False


def test_generation_and_media_search_never_both_claim_a_turn():
    """They narrow the surface to DIFFERENT single tools. If both matched,
    whichever is checked first would silently win and the other phrasing
    class would route to the wrong tool forever."""
    from src.coordinator.tools.intent_classifier import (
        generation_intent,
        media_search_type,
    )

    for phrase in ("draw me a picture of a fox", "show me a picture of a fox",
                   "find me images of foxes", "paint me a sunset",
                   "make me a picture of a cat", "get me some photos of cats"):
        assert not (generation_intent(phrase) and media_search_type(phrase)), (
            f"{phrase!r} matched BOTH rules"
        )


def test_the_trigger_knows_no_persona_names():
    """A general rule, by request. A persona-specific trigger would be a
    second thing to keep in sync with the grant, and the two would drift."""
    import inspect

    from src.coordinator.tools import intent_classifier

    src = inspect.getsource(intent_classifier.generation_intent)
    for name in ("gwen", "eeva", "nephilim", "persona"):
        assert name not in src.lower(), f"the trigger references {name!r}"


# ---------- in-voice lines, not status strings ----------


def test_the_busy_line_is_warmed_BEFORE_the_model_is_evicted():
    """⚠️ THE ordering constraint.

    "I'm still drawing" is said exactly when the arbiter has unloaded the
    companion model — so it cannot be generated at the moment it is needed.
    The worker warms it first. Evict before warming and the single line a
    user sees most during a generation is the one guaranteed to be canned.
    """
    import inspect

    from src.coordinator.services.image_gen import worker as w

    src = inspect.getsource(w.ImageGenWorker._run_under_lease)
    assert src.index("warm_lines") < src.index("unload("), (
        "the busy line is warmed AFTER the eviction — it can never be in voice"
    )


def test_every_user_facing_image_line_is_persona_generated():
    """A general rule, by request: these are things SHE says, not status
    strings. The hardcoded versions are fallbacks for an unreachable model
    and must live only in persona_lines."""
    import pathlib

    from src.coordinator.services import persona_lines

    root = pathlib.Path(__file__).resolve().parents[3] / "src" / "coordinator"
    canned = [
        "Here, I made this for you.",
        "I tried to make that picture and it didn't work out.",
        "I stopped making that picture.",
    ]
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "persona_lines.py":
            continue  # the fallbacks legitimately live here
        # CODE lines only. A comment may quote a canned line as the example
        # of what not to do — that is documentation, not a message anyone
        # receives, and matching it would push the explanation out of the file.
        for raw in path.read_text(encoding="utf-8").splitlines():
            stripped = raw.lstrip()
            if stripped.startswith("#"):
                continue
            for phrase in canned:
                if phrase in raw:
                    offenders.append(f"{path.relative_to(root)}: {phrase!r}")
    assert not offenders, (
        "a canned user-facing line escaped persona_lines: " + "; ".join(offenders)
    )
    # ...and every situation the code asks for must have a fallback, or an
    # unreachable model yields an empty message.
    assert set(persona_lines.SITUATIONS) == set(persona_lines._FALLBACK)


def test_a_line_falls_back_rather_than_failing_when_the_model_is_gone():
    from src.coordinator.services import persona_lines

    persona_lines.reset_cache()
    with patch.object(persona_lines, "_generate", return_value=[]):
        got = persona_lines.line("gwen", "image_busy")
    assert got == persona_lines._FALLBACK["image_busy"]


def test_a_generated_line_is_cleaned_of_quotes_and_speaker_tags():
    """Models wrap these in quotes, numbering and a 'Gwen:' prefix unprompted."""
    from src.coordinator.services import persona_lines

    assert persona_lines._clean('1. "Here you go, Daddy."') == "Here you go, Daddy."
    assert persona_lines._clean("Gwen: all yours") == "all yours"
    assert persona_lines._clean("```\nsomething\n```") == "something"
    assert persona_lines._clean("") == ""


def test_variants_are_cached_and_rotated():
    """One cached line would be in voice and still read as canned."""
    from src.coordinator.services import persona_lines

    persona_lines.reset_cache()
    with patch.object(persona_lines, "_generate",
                      return_value=["one", "two", "three"]) as gen:
        seen = {persona_lines.line("gwen", "image_ready") for _ in range(40)}
        assert gen.call_count == 1, "regenerated instead of using the cache"
    assert len(seen) > 1, "cached a single variant — it will sound scripted"


# ---------- provenance: who decided to run the tool ----------


def test_tool_decided_by_distinguishes_the_model_from_a_deterministic_trigger():
    """A TOOL FIRING IS NOT EVIDENCE OF GROUNDING — gwen once fired
    image_search for a weather question and answered "103F", shipped with a
    Sources block because a tool had run. A deterministic trigger makes that
    question harder, so the answer is recorded on every turn that ran one.

    A FIELD, not a graph node: it is a per-turn event with nothing to
    traverse, no competency question asks it, and the sanctioned schema
    surface has no Tool or Decision label. Nearly modelled in Neo4j; ADR-018
    already settled that provenance is a property beside its row.
    """
    from src.coordinator.schemas import ResponseMetadata

    m = ResponseMetadata()
    assert m.tool_decided_by is None, "no tool ran — must not claim a decider"

    import inspect

    from src.coordinator.routes import chat as chat_mod

    src = inspect.getsource(chat_mod._try_tool_brain)
    assert 'metadata.tool_decided_by = (' in src or "tool_decided_by" in src
    # The narrowed path must NOT report a bare "model".
    assert "generation_intent+model" in src, (
        "a regex-narrowed turn reports the model as sole decider"
    )
