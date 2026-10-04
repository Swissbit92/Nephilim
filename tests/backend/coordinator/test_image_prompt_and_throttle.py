# tests/backend/coordinator/test_image_prompt_and_throttle.py
"""Composing the diffusion prompt, and refusing to make the same picture twice.

What each load-bearing test would otherwise let through:

- `test_composition_is_deterministic` — Fooocus-style stochastic expansion
  invents detail the user never asked for, and "it drew something I didn't
  ask for" is indistinguishable from a bug in a companion.
- `test_the_prompt_is_prose_not_tags` — Qwen-Image is trained on
  paragraph-level descriptions; Danbooru tags are the SDXL convention and the
  wrong register for this family.
- `test_a_reworded_duplicate_is_refused` — the documented agent loop is not
  six identical calls, it is six slightly reworded ones, which a counter
 sees as six legitimate requests.
"""

from __future__ import annotations

import pytest

from src.coordinator.services.image_gen.prompt import (
    MAX_PROMPT_CHARS,
    STYLE_PHRASES,
    GenerationIntent,
    PromptError,
    compose,
)
from src.coordinator.services.image_gen.throttle import GenerationThrottle

# ---------- composition ----------


def test_composition_is_deterministic():
    """Same fields in, same prompt out — the deliberate difference from
    Fooocus, whose expansion is stochastic by design."""
    intent = GenerationIntent(subject="a red fox", setting="snow", mood="still")
    assert compose(intent) == compose(intent)


def test_the_prompt_is_prose_not_tags():
    """Qwen-Image's curriculum scales to 'paragraph-level descriptions'
    (arXiv 2508.02324). Tag soup is the wrong register."""
    out = compose(GenerationIntent(subject="a woman reading",
                                   setting="a sunlit cafe", mood="calm"))
    assert "a woman reading" in out
    assert "in a sunlit cafe" in out
    # Reads as a phrase, not `1girl, cafe, sunlight, masterpiece`
    assert " " in out and not out.startswith("1girl")


def test_the_quality_suffix_is_qwens_own():
    """Not invented here — it is what Qwen's prompt-rewriter appends."""
    out = compose(GenerationIntent(subject="a fox"))
    assert out.endswith(", Ultra HD, 4K, cinematic composition.")


def test_subject_is_required():
    with pytest.raises(PromptError, match="subject is required"):
        compose(GenerationIntent(subject="   "))


def test_an_unknown_style_degrades_instead_of_failing():
    """A vocabulary slip from a small model must not fail a request that is
    otherwise fine."""
    out = compose(GenerationIntent(subject="a cat", style="watercolour-ish"))
    assert STYLE_PHRASES["illustration"] in out


@pytest.mark.parametrize("style", sorted(STYLE_PHRASES))
def test_every_declared_style_produces_its_phrase(style):
    out = compose(GenerationIntent(subject="a cat", style=style))
    assert STYLE_PHRASES[style] in out


def test_a_setting_that_already_has_a_preposition_is_not_doubled():
    out = compose(GenerationIntent(subject="a cat", setting="on a windowsill"))
    assert "in on a windowsill" not in out
    assert "on a windowsill" in out


def test_control_characters_are_stripped():
    """The prompt is one argv element; a newline makes the progress log
    unreadable and a NUL would truncate it."""
    out = compose(GenerationIntent(subject="a fox\n\x00 in\tsnow"))
    assert "\n" not in out and "\x00" not in out and "\t" not in out
    assert "a fox in snow" in out


def test_a_runaway_field_cannot_eat_the_whole_budget():
    out = compose(GenerationIntent(subject="x" * 5000))
    assert len(out) <= MAX_PROMPT_CHARS


def test_the_quality_suffix_survives_truncation():
    """Trim the body, never the suffix — the suffix is the part carrying the
    quality signal."""
    out = compose(GenerationIntent(
        subject="a " + "very " * 400 + "long subject", setting="z" * 200))
    assert out.endswith(", Ultra HD, 4K, cinematic composition.")
    assert len(out) <= MAX_PROMPT_CHARS


def test_a_non_string_field_is_rejected():
    with pytest.raises(PromptError, match="must be text"):
        compose(GenerationIntent(subject="a fox", setting=["snow"]))  # type: ignore[arg-type]


# ---------- throttling ----------


def test_the_first_generation_is_allowed():
    t = GenerationThrottle()
    assert t.check("s1", "a red fox in snow")


def test_a_second_generation_inside_the_cooldown_is_refused():
    t = GenerationThrottle(cooldown_seconds=120)
    t.record("s1", "a red fox", now=0.0)
    d = t.check("s1", "a blue whale underwater", now=10.0)
    assert not d
    assert "seconds" in d.reason


def test_the_cooldown_expires():
    t = GenerationThrottle(cooldown_seconds=120)
    t.record("s1", "a red fox", now=0.0)
    assert t.check("s1", "a blue whale underwater", now=130.0)


def test_a_refinement_is_NOT_refused():
    """The defect that removed the duplicate guard.

    Replayed against the user's real history the old rule refused 14 of 19
    requests, including "red haired mature ... BLACK bikini" followed by
    "blond mature ... WHITE bikini" — a different picture. A user refines by
    keeping the scaffolding and changing one or two attributes, and Jaccard
    scores that as a repeat because the swapped words are a small fraction of
    the tokens while being the entire payload of the request (PAWS,
    arXiv:1904.01130, is built on exactly this failure).
    """
    t = GenerationThrottle(cooldown_seconds=0)
    first = ("a red haired mature beauty milf in a black bikini, full body "
             "view, looking at viewer seductively, background light and airy")
    second = ("a blond mature beauty milf in a white bikini, full body view, "
              "looking at viewer seductively, background light and airy")
    t.record("s1", first, now=0.0)
    assert t.check("s1", second, now=1.0), (
        "a refinement was refused as a duplicate — the removed guard is back"
    )


def test_even_an_identical_prompt_is_allowed_once_the_cooldown_passes():
    """Deliberate: no prompt-level dedup at all. None was found in
    Automatic1111, ComfyUI or Fooocus, and Midjourney ships a Repeat button —
    resubmitting the same prompt is a legitimate thing to want, because
    diffusion is stochastic and the second image differs."""
    t = GenerationThrottle(cooldown_seconds=120)
    same = "a red fox in deep snow"
    t.record("s1", same, now=0.0)
    assert not t.check("s1", same, now=10.0), "the cooldown should still bind"
    assert t.check("s1", same, now=130.0), (
        "an identical prompt after the cooldown must be allowed — diffusion "
        "is stochastic and a repeat is a real request"
    )


def test_a_genuinely_different_request_is_allowed_after_the_cooldown():
    t = GenerationThrottle(cooldown_seconds=0)
    t.record("s1", "a red fox sitting in deep snow", now=0.0)
    assert t.check("s1", "a cathedral interior with stained glass", now=1.0)


def test_boilerplate_alone_does_not_make_two_prompts_look_alike():
    """Every composed prompt shares the quality suffix and a style phrase. If
    those counted, every pair would look like a duplicate and the second
    generation of a session would always be refused."""
    t = GenerationThrottle(cooldown_seconds=0)
    a = compose(GenerationIntent(subject="a red fox in snow"))
    b = compose(GenerationIntent(subject="a cathedral with stained glass"))
    t.record("s1", a, now=0.0)
    assert t.check("s1", b, now=1.0), "shared boilerplate was read as similarity"


def test_the_per_session_quota_binds():
    t = GenerationThrottle(cooldown_seconds=0, per_session_limit=3)
    # Deliberately unrelated subjects: near-identical ones would trip the
    # DUPLICATE guard first and this would pass for the wrong reason.
    for i, subject in enumerate(["a red fox in deep snow",
                                 "a cathedral with stained glass",
                                 "a submarine near a coral reef"]):
        assert t.check("s1", subject, now=float(i))
        t.record("s1", subject, now=float(i))
    d = t.check("s1", "something completely different entirely", now=99.0)
    assert not d
    assert "lot of pictures" in d.reason


def test_sessions_do_not_share_a_cooldown():
    t = GenerationThrottle(cooldown_seconds=120)
    t.record("s1", "a red fox", now=0.0)
    assert t.check("s2", "a red fox", now=1.0)


def test_check_does_not_consume_the_quota():
    """Split from record so a caller that fails to enqueue does not burn it."""
    t = GenerationThrottle(cooldown_seconds=0, per_session_limit=1)
    for _ in range(5):
        assert t.check("s1", "a red fox in snow")


def test_forget_clears_a_session():
    """/reset clears the conversation; the cooldown belonged to it."""
    t = GenerationThrottle(cooldown_seconds=120)
    t.record("s1", "a red fox", now=0.0)
    assert not t.check("s1", "a whale", now=1.0)
    t.forget("s1")
    assert t.check("s1", "a whale", now=1.0)


def test_the_refusal_reads_as_speech():
    """It reaches the user through the persona, so it has to be sayable —
    not an error code."""
    t = GenerationThrottle(cooldown_seconds=120)
    t.record("s1", "a red fox", now=0.0)
    reason = t.check("s1", "a whale", now=1.0).reason
    assert reason and reason[0].isupper() and reason.endswith(".")
    for forbidden in ("Error", "429", "None", "Exception", "cooldown_seconds"):
        assert forbidden not in reason


def test_the_duplicate_rule_is_gone_not_merely_loosened():
    """A threshold set high enough to allow real refinement is high enough to
    be useless, so the rule was removed rather than retuned. If it returns,
    this says so before a user discovers it."""
    import inspect

    from src.coordinator.services.image_gen import throttle

    src = inspect.getsource(throttle.GenerationThrottle.check)
    assert "_similar" not in src, "the duplicate rule is back in check()"
    assert "same picture" not in src
