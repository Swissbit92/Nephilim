# src/coordinator/services/image_gen/extract.py
"""Getting the picture request out of the message, without a tool call.

**Why this exists instead of asking the model to call a tool.** Measured on
this deployment: with 8 messages of gwen's real conversation in context,
`/api/chat` with `tools=` fired the tool **0 of 5** times, while
`format=<schema>` returned valid JSON **6 of 6** under the same history.

That asymmetry is not luck, it is architecture. Ollama's grammar engine is
wired to `format` and **not** to `tools=`: a tool call is free generation
followed by a post-hoc scan for a literal `[TOOL_CALLS]` tag, so any leading
prose silently discards it — empty `tool_calls`, no error, prose becomes the
reply. A roleplay history is a strong prior for prose. Constrained decoding
removes the question: the model cannot emit anything but the schema.

The precedent is ADR-015 — *bubble boundaries are a pure function of the
text, not a prompt instruction*. Something computable was being asked of the
model and the fix was to compute it. Here the computable part is "did they
ask for a picture" (`generation_intent`, deterministic) and the model is left
with the part it is genuinely good at: pulling "a fox" and "deep snow at
dusk" out of a sentence.

**Grammar guarantees SHAPE, never correctness.** The first probe returned
`mood: "dusk"` for "a fox in deep snow at dusk" — valid JSON, wrong field.
The field descriptions below, and the explicit sentence that time of day is
setting rather than mood, are what fixed that; they are load-bearing and were
written against observed output, not guessed.

**It never raises and never returns nothing.** A failed extraction falls back
to the user's own words as the subject. Qwen takes prose, so the degraded
path produces a worse picture rather than no picture — which is the whole
point, because the behaviour being replaced degraded to silence.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .prompt import MAX_FIELD_CHARS, STYLE_PHRASES, GenerationIntent

logger = logging.getLogger(__name__)

#: Low, because this is extraction and not writing. Validity is guaranteed by
#: the grammar either way; what temperature buys here is only the chance of
#: inventing a detail the user did not give.
EXTRACT_TEMPERATURE = 0.0

#: Enough for four short fields. A truncated generation under a grammar is
#: still a truncated object, so this is sized well clear of the longest
#: plausible subject rather than trimmed to taste.
EXTRACT_NUM_PREDICT = 300

#: Phrases that introduce a request rather than describe a picture. Stripped
#: before the fallback uses the raw message, so "use your image generator: a
#: fox" does not become a subject that literally mentions the generator.
_TRIGGER_PREFIX = re.compile(
    r"^\s*(?:(?:can|could|would)\s+you\s+)?"
    r"(?:please\s+)?"
    r"(?:use\s+your\s+(?:image\s+)?(?:generator|image\s+gen)\s*[:,-]?\s*"
    r"|(?:draw|paint|sketch|render|generate|create|make|design|illustrate)"
    r"\s+(?:me\s+)?(?:a|an|the|some)?\s*"
    r"(?:picture|image|drawing|painting|sketch|illustration|art|artwork)?"
    r"\s*(?:of\s+)?)",
    re.IGNORECASE,
)


def _schema() -> dict[str, Any]:
    """The JSON schema handed to Ollama's grammar.

    `style` is an enum, so the grammar makes an invalid style unreachable
    rather than merely discouraged — the one field where that matters,
    because `compose()` would otherwise silently fall back to the default and
    the miss would be invisible.
    """
    return {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "The main thing to draw, in the user's own words.",
            },
            "setting": {
                "type": "string",
                "description": (
                    "Where it is, and the light or time of day, if they said. "
                    "Empty string if they did not."
                ),
            },
            "mood": {
                "type": "string",
                "description": (
                    "The FEELING or emotional tone, if they said. Empty string "
                    "if they did not."
                ),
            },
            "style": {"type": "string", "enum": sorted(STYLE_PHRASES)},
        },
        "required": ["subject", "setting", "mood", "style"],
    }


#: Written against observed output. The last line exists because without it
#: "a fox in deep snow at dusk" put `dusk` in mood.
_INSTRUCTION = (
    "Extract the picture the user is asking you to make. Use ONLY what they "
    "actually said — do not invent details they did not give.\n"
    "subject: the main thing to draw.\n"
    "setting: where it is, and the light or time of day. Empty if unstated.\n"
    "mood: the FEELING or emotional tone. Empty if unstated. Time of day and "
    "lighting are SETTING, never mood.\n"
    "style: pick the closest one.\n\n"
    "The user said: "
)


def strip_trigger(message: str) -> str:
    """The request with its leading "draw me a..." removed.

    Used by the fallback, so a failed extraction does not produce a subject
    that describes the asking rather than the picture.
    """
    out = _TRIGGER_PREFIX.sub("", message or "", count=1).strip()
    return out or (message or "").strip()


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text[:MAX_FIELD_CHARS].strip()


def default_client() -> tuple[object | None, str | None]:
    """The Ollama client this module talks to, built here rather than by the
    caller — so the chat-model transport stays in ONE file and the arbiter
    guard below is the only place it has to be enforced. INV-1 flagged the
    caller when it built its own, which is the check working."""
    try:
        import ollama

        from ...config import get_settings

        st = get_settings()
        return ollama.Client(host=st.ollama.base), st.ollama.model
    except Exception:  # noqa: BLE001 — extraction degrades without a client
        logger.warning("[ImageExtract] no ollama client; will fall back")
        return None, None


def extract(message: str, *, client=None, model: str | None = None,
            system_prompt: str = "") -> tuple[GenerationIntent, str]:
    """Pull the request out of `message`. Returns (intent, how).

    `how` names what produced it — "extractor" or "fallback" — and reaches
    `ResponseMetadata.tool_decided_by`, so a bypassed decision can never be
    read as the model's own. A tool firing is not evidence of grounding, and
    a deterministic trigger makes that question sharper rather than softer.

    NO CONVERSATION HISTORY is passed, deliberately. The request is
    self-contained, history is what suppresses the structured path's cousin,
    and anything history could add here would be detail the user did not ask
    for.
    """
    fallback = GenerationIntent(subject=strip_trigger(message))

    if client is None or not model:
        return fallback, "fallback"

    # The arbiter guard, because this IS a chat-model call. In practice the
    # route refuses the turn before reaching here while a generation holds
    # the machine, so this is defence in depth — and INV-1 exists precisely
    # because the fifth transport was added by someone who had not read the
    # other four. It caught this file the first time the suite ran.
    from ..resource_arbiter import guard_chat_model

    guard_chat_model("image intent extraction")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": _INSTRUCTION + (message or "")})

    try:
        resp = client.chat(
            model=model,
            messages=messages,
            format=_schema(),
            stream=False,
            options={
                "temperature": EXTRACT_TEMPERATURE,
                "num_predict": EXTRACT_NUM_PREDICT,
            },
        )
        raw = (resp.get("message") or {}).get("content") or ""
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 — never fail a request over phrasing
        logger.warning("[ImageExtract] falling back to the raw message: %s", exc)
        return fallback, "fallback"

    if not isinstance(data, dict):
        return fallback, "fallback"

    subject = _clean(data.get("subject"))
    if not subject:
        # The grammar guarantees the KEY exists; it cannot guarantee content.
        logger.info("[ImageExtract] empty subject — using the raw message")
        return fallback, "fallback"

    style = _clean(data.get("style")).lower()
    return (
        GenerationIntent(
            subject=subject,
            setting=_clean(data.get("setting")) or None,
            mood=_clean(data.get("mood")) or None,
            style=style if style in STYLE_PHRASES else None,
        ),
        "extractor",
    )
