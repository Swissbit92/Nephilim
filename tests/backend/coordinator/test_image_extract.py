# tests/backend/coordinator/test_image_extract.py
"""Grammar-constrained extraction, replacing the native tool call.

The measurement that produced this module: with 8 messages of gwen's real
conversation in context, `tools=` fired 0/5 while `format=<schema>` returned
valid JSON 6/6. Ollama's grammar engine is wired to `format` and not to
`tools=`, so a tool call is free generation plus a tag scan and any leading
prose discards it silently.

What each load-bearing test would otherwise let through:

- `test_a_failed_extraction_falls_back_to_the_users_words` — the behaviour
  being replaced degraded to SILENCE. A fallback that returns nothing would
  reproduce the defect in a new place.
- `test_it_never_raises` — this runs on the chat path; an exception here is
  a failed turn, and the tool-brain's catch-all would render it as the model
  having nothing to say.
- `test_the_trigger_phrase_is_stripped_before_the_fallback` — without it the
  degraded path draws a picture OF the request ("use your image generator").
- `test_how_is_reported_so_a_bypass_is_never_invisible` — a tool firing is
  not evidence of grounding, and a deterministic trigger sharpens that.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.coordinator.services.image_gen.extract import (
    EXTRACT_TEMPERATURE,
    extract,
    strip_trigger,
)
from src.coordinator.services.image_gen.prompt import STYLE_PHRASES, compose


def _client(payload, *, raises=False):
    c = MagicMock()
    if raises:
        c.chat.side_effect = RuntimeError("ollama is down")
    else:
        body = payload if isinstance(payload, str) else json.dumps(payload)
        c.chat.return_value = {"message": {"content": body}}
    return c


# ---------- the happy path ----------


def test_it_extracts_the_fields_and_says_who_did():
    c = _client({"subject": "a red fox", "setting": "deep snow at dusk",
                 "mood": "", "style": "photographic"})
    intent, how = extract("draw me a fox in deep snow at dusk",
                          client=c, model="m")
    assert how == "extractor"
    assert intent.subject == "a red fox"
    assert intent.setting == "deep snow at dusk"
    assert intent.mood is None, "an empty field must not become the string ''"
    assert intent.style == "photographic"


def test_the_schema_constrains_style_to_the_enum():
    """The one field where the grammar earns its place: an invalid style is
    unreachable rather than discouraged, because `compose()` would otherwise
    fall back to the default and the miss would be invisible."""
    c = _client({"subject": "a fox", "setting": "", "mood": "", "style": "x"})
    sent = extract("draw a fox", client=c, model="m")
    schema = c.chat.call_args.kwargs["format"]
    assert schema["properties"]["style"]["enum"] == sorted(STYLE_PHRASES)
    # ...and an out-of-enum value that somehow arrives is dropped, not passed on
    assert sent[0].style is None


def test_no_history_is_sent():
    """⚠️ A CONTAINMENT BOUNDARY, not a quality preference.

    This was originally justified as "the request is self-contained, and
    anything history added would be detail the user did not ask for". The
    paired A/B found the stronger reason, and it is a safety one.

    Measured 2026-10-04: asked to paint "a quiet harbour at sunrise" with 8
    messages of real history, the tool-call path composed a subject lifted
    VERBATIM from a different image request eight messages earlier in that
    session, and would have drawn that instead. Confirmed against the source
    conversation. So history does not merely suppress the call -- when the
    call fires under history, the arguments can come from the conversation
    rather than from the request, and the user gets a confidently wrong
    picture rather than a visible failure.

    Passing no history is what makes that impossible here. Anyone adding a
    `history=` argument to `extract()` for context is removing this property;
    the contamination test below is the one that will catch them.
    """
    c = _client({"subject": "a fox", "setting": "", "mood": "", "style": "anime"})
    extract("draw a fox", client=c, model="m", system_prompt="SYS")
    msgs = c.chat.call_args.kwargs["messages"]
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == "SYS"


def test_nothing_but_the_request_reaches_the_model():
    """The contamination guard, stated as a property rather than a shape.

    `test_no_history_is_sent` pins the message LIST; this pins what may
    appear in the only user turn. A future edit that threads prior turns in
    as a "context" string would satisfy the role check and still reintroduce
    exactly the failure the A/B measured.
    """
    earlier = "a latina skinny beauty teen in a green bikini"  # the real one
    c = _client({"subject": "a harbour", "setting": "sunrise",
                 "mood": "quiet", "style": "photographic"})
    extract("could you paint a quiet harbour at sunrise?",
            client=c, model="m", system_prompt="SYS")
    sent = " ".join(m["content"] for m in c.chat.call_args.kwargs["messages"])
    assert earlier not in sent
    assert "harbour" in sent, "the actual request must of course still be sent"
    # Nothing resembling a prior turn may ride along.
    assert sent.count("could you paint a quiet harbour at sunrise?") == 1


def test_extraction_runs_cold():
    """Validity is grammar-guaranteed either way; temperature only buys the
    chance of inventing a detail the user did not give."""
    c = _client({"subject": "a fox", "setting": "", "mood": "", "style": "anime"})
    extract("draw a fox", client=c, model="m")
    assert c.chat.call_args.kwargs["options"]["temperature"] == EXTRACT_TEMPERATURE
    assert EXTRACT_TEMPERATURE == 0.0


# ---------- the fallback, which must never be silence ----------


@pytest.mark.parametrize("bad", [
    "not json at all",
    '{"subject": ""}',
    '["a", "list"]',
    '{"subject": null}',
    '{"setting": "snow"}',          # subject key absent
])
def test_a_failed_extraction_falls_back_to_the_users_words(bad):
    """The behaviour this replaces degraded to SILENCE — she promised a
    picture and queued nothing. A fallback returning nothing would move the
    defect rather than fix it."""
    intent, how = extract("draw me a fox in the snow", client=_client(bad), model="m")
    assert how == "fallback"
    assert "fox" in intent.subject
    assert compose(intent), "the fallback must still compose a usable prompt"


def test_it_never_raises():
    """This runs on the chat path, and ToolBrainService's catch-all would
    render an exception as the model having nothing to say."""
    intent, how = extract("draw a fox", client=_client(None, raises=True), model="m")
    assert how == "fallback" and intent.subject


def test_no_client_degrades_rather_than_failing():
    intent, how = extract("draw me a fox in the snow")
    assert how == "fallback" and "fox" in intent.subject


# ---------- the trigger phrase ----------


@pytest.mark.parametrize("message,expected_absent", [
    ("use your image generator: a red fox in snow", "generator"),
    ("draw me a fox in the snow", "draw"),
    ("make me a picture of a cat", "picture"),
    ("paint me a field of poppies", "paint"),
])
def test_the_trigger_phrase_is_stripped_before_the_fallback(message, expected_absent):
    """Otherwise the degraded path draws a picture OF the request."""
    out = strip_trigger(message)
    assert expected_absent not in out.lower()
    assert out, "stripping must never empty the message"


def test_stripping_a_bare_trigger_leaves_the_original():
    """Never return an empty subject — compose() would reject it and the
    turn would fail where it could have degraded."""
    assert strip_trigger("draw me a") == "draw me a"


# ---------- provenance ----------


def test_how_is_reported_so_a_bypass_is_never_invisible():
    """A tool firing is not evidence of grounding — gwen fired image_search
    for a weather question and answered 103F. A deterministic trigger makes
    that question sharper, so the answer is recorded."""
    good = _client({"subject": "a fox", "setting": "", "mood": "", "style": "anime"})
    assert extract("draw a fox", client=good, model="m")[1] == "extractor"
    assert extract("draw a fox", client=_client("junk"), model="m")[1] == "fallback"
