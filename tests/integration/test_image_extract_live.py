# tests/integration/test_image_extract_live.py
"""The extractor against a real model, on the two things a mock cannot check.

Everything in `tests/backend/coordinator/test_image_extract.py` runs against a
MagicMock, which proves the plumbing and nothing about the model. Two
properties here are only observable live, and both were measured before this
file existed rather than assumed:

1. **Time of day belongs in `setting`, never `mood`.** The grammar guarantees
   SHAPE and never correctness: the first probe returned `mood: "dusk"` for
   "a fox in deep snow at dusk" -- a perfectly valid object with the value in
   the wrong field.

   ⚠️ THESE TESTS ARE A BEHAVIOUR PIN, NOT A GUARD ON A FIX, and the
   difference was measured rather than assumed. The honest sequence: the
   defect was observed; an instruction line and field descriptions were
   written against it; this file was then written to guard them; and the
   guard was checked by DELETING what it supposedly guards. Removing the
   instruction line -> still 8/8. Removing the line AND the mood/setting
   schema descriptions -> still 8/8. At temperature 0 on
   `mistral-small-abliterated:24b` the model separates the fields unprompted.

   So nothing in the prompt is currently holding this up, the original
   observation does not reproduce, and these tests have never been watched
   failing on this tree. They are kept because the realistic way this
   regresses is a model swap or a sampling change, which is exactly what a
   pin catches and what the deletion test could not rule out. A repair
   function was deliberately NOT written: it would never fire, and unreached
   code is worse than none.

2. **`style` is not inert.** The A/B produced `photographic` 48/48, which is
   indistinguishable from a field nothing reads -- and this codebase has
   shipped exactly that (persona sliders, read by nothing, warmth 0.40 and
   0.95 giving byte-identical prompts). Measured: 4/4 when a style IS stated.
   The uniform default was the correct answer to four requests that named no
   style, not a dead field. This test keeps that distinction checkable.

Marked `requires_ollama`, so a headless run skips rather than hanging on live
generations at ~16 tok/s.
"""

from __future__ import annotations

import pytest

from src.coordinator.services.image_gen.extract import default_client, extract

pytestmark = pytest.mark.requires_ollama

#: A bare time-of-day or lighting word. If one of these IS the whole mood, the
#: model has put the answer in the wrong field -- the observed defect.
TIME_OF_DAY = {
    "dusk", "dawn", "midnight", "night", "sunrise", "sunset", "twilight",
    "golden hour", "blue hour", "morning", "evening", "noon", "daylight",
    "moonlight", "afternoon",
}


@pytest.fixture(scope="module")
def client_and_model():
    client, model = default_client()
    if client is None or not model:
        pytest.skip("no ollama client")
    return client, model


@pytest.mark.parametrize("request_text", [
    "draw me a fox in deep snow at dusk",
    "paint a castle at midnight",
    "a lone tree in the golden hour light",
])
def test_time_of_day_lands_in_setting_not_mood(client_and_model, request_text):
    """The exact defect that produced the instruction line, pinned.

    Grammar cannot catch this: `mood: "dusk"` is a valid string in a required
    field. Only a semantic check sees it.
    """
    client, model = client_and_model
    intent, how = extract(request_text, client=client, model=model)
    if how != "extractor":
        pytest.skip("extraction fell back; nothing to assert about the model")
    mood = (intent.mood or "").strip().lower()
    assert mood not in TIME_OF_DAY, (
        f"{request_text!r} put the time of day in `mood` ({mood!r}). The "
        f"instruction line in extract._INSTRUCTION is what prevents this -- "
        f"check it still says time of day and lighting are SETTING."
    )


def test_a_mood_and_a_time_of_day_are_separated(client_and_model):
    """The hard case: both are present and must go to different fields."""
    client, model = client_and_model
    intent, how = extract("draw a harbour in the blue hour, melancholy",
                          client=client, model=model)
    if how != "extractor":
        pytest.skip("extraction fell back")
    mood = (intent.mood or "").strip().lower()
    assert mood not in TIME_OF_DAY, f"`blue hour` leaked into mood: {mood!r}"


@pytest.mark.parametrize("stated,request_text", [
    ("anime", "draw me an anime girl with silver hair"),
    ("diagram", "make me a technical diagram of a bicycle gear system"),
    ("photographic", "take a photorealistic photo of a red fox in snow"),
    ("illustration", "a children's book illustration of a sleepy bear"),
])
def test_style_tracks_a_stated_style(client_and_model, stated, request_text):
    """Distinguishes "the default is photographic" from "the field is inert".

    Without this, 48/48 photographic in the A/B reads the same either way.
    """
    client, model = client_and_model
    intent, how = extract(request_text, client=client, model=model)
    if how != "extractor":
        pytest.skip("extraction fell back")
    assert intent.style == stated, (
        f"{request_text!r} named the {stated!r} style and got {intent.style!r}. "
        f"If every request returns one value regardless, the field is carrying "
        f"no information and compose() is decorating with a constant."
    )
