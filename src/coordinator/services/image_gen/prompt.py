# src/coordinator/services/image_gen/prompt.py
"""Turning a persona's structured intent into a diffusion prompt.

**The persona does not write the prompt.** It emits fields — subject, setting,
mood — and this module composes the string. Every production system found does
something equivalent: DALL-E 3 rewrites through GPT-4 and returns the result as
`revised_prompt`, Fooocus expands through a fine-tuned GPT-2, T2I-Copilot's
"Input Interpreter Agent" produces a structured report before generation.
None of them pipe the caller's raw text to the model.

Composition is DETERMINISTIC here, which is the deliberate difference from
Fooocus. Its expansion is stochastic by design and adds unrequested detail
every run; for a companion that is a liability, because "it drew something I
didn't ask for" is indistinguishable from a bug. Same fields in, same prompt
out.

**Prose, not tags.** Qwen-Image's technical report (arXiv 2508.02324)
describes a curriculum that "evolves from simple to complex textual inputs,
and gradually scales up to paragraph-level descriptions" — it is a language
model being treated as one. Comma-separated Danbooru tags are the SD1.5/SDXL
/Pony convention and are the wrong register for this family.

**The quality suffix is Qwen's own, not invented here.** `", Ultra HD, 4K,
cinematic composition."` is what Qwen's repo appends for English prompts.

**No negative prompt, deliberately.** mflux only runs the negative pass when
`--guidance > 1`, and Qwen-Image-2.1 is trained to be sampled WITHOUT
guidance (mflux's default is 1). Turning CFG on to make negatives work would
change the sampling regime the model was trained for, and double the cost of
an already 331-second job. The one real generation measured here ran at the
default and produced a clean image.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Qwen's own English quality suffix, from its prompt-rewriter code.
_QUALITY_SUFFIX = ", Ultra HD, 4K, cinematic composition."

#: Conservative ceiling. The text encoder is reported to handle ~1000 tokens,
#: but that figure is from secondary sources and unverified against the model
#: card, so this caps well under it rather than near a number we do not trust.
MAX_PROMPT_CHARS = 1200

#: Per field, so one runaway field cannot consume the whole budget.
MAX_FIELD_CHARS = 300

#: Characters that do nothing in a diffusion prompt but can confuse the CLI or
#: smuggle shell-looking text into logs. Newlines go too: the prompt is one
#: argv element and a multi-line value makes the progress log unreadable.
_STRIP = re.compile(r"[\x00-\x1f\x7f]")


class PromptError(ValueError):
    """The intent could not be composed into a usable prompt."""


@dataclass(frozen=True)
class GenerationIntent:
    """What the persona asked for, in fields rather than prose.

    `subject` is the only required one: an image of nothing is not a request.
    """

    subject: str
    setting: str | None = None
    mood: str | None = None
    style: str | None = None


#: A small closed set. Research is explicit that enums are double-edged for
#: small local models — they give "a small, confident set of options", so a
#: wrong pick is confident too — but free text here produced junk in the
#: SD-era conventions this model does not share. Small and few is the
#: compromise: four values, each mapping to a phrase that reads as prose.
STYLE_PHRASES: dict[str, str] = {
    "photographic": "photographic, natural light, shallow depth of field",
    "illustration": "a clean digital illustration, soft shading",
    "anime": "anime illustration, crisp linework, cel shading",
    "diagram": "a clear explanatory diagram, flat colours, plain background",
}
DEFAULT_STYLE = "illustration"


def _clean(value: str | None, *, field: str) -> str:
    """Normalise one field. Never raises on content, only on structure."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PromptError(f"{field} must be text")
    text = _STRIP.sub(" ", value)
    text = " ".join(text.split())  # collapse all whitespace, including newlines
    if len(text) > MAX_FIELD_CHARS:
        text = text[:MAX_FIELD_CHARS].rsplit(" ", 1)[0]
    return text.strip()


def compose(intent: GenerationIntent) -> str:
    """Build the diffusion prompt. Deterministic: same in, same out.

    Returns prose, because that is what this model family was trained on.
    """
    subject = _clean(intent.subject, field="subject")
    if not subject:
        raise PromptError("subject is required")

    setting = _clean(intent.setting, field="setting")
    mood = _clean(intent.mood, field="mood")

    style_key = (intent.style or DEFAULT_STYLE).strip().lower()
    style_phrase = STYLE_PHRASES.get(style_key)
    if style_phrase is None:
        # Degrade rather than raise: an unknown style from a small model is an
        # ordinary miss, and failing a 5-minute request over a vocabulary slip
        # would be the wrong trade.
        style_phrase = STYLE_PHRASES[DEFAULT_STYLE]

    parts = [subject]
    if setting:
        parts.append(f"in {setting}" if not setting.lower().startswith(
            ("in ", "on ", "at ", "under ", "inside ", "outside ")) else setting)
    if mood:
        parts.append(f"{mood} mood")
    parts.append(style_phrase)

    prompt = ", ".join(parts) + _QUALITY_SUFFIX

    if len(prompt) > MAX_PROMPT_CHARS:
        # Trim the body, never the suffix: the suffix is the part Qwen's own
        # tooling appends and the part that carries the quality signal.
        room = MAX_PROMPT_CHARS - len(_QUALITY_SUFFIX)
        prompt = ", ".join(parts)[:room].rsplit(",", 1)[0] + _QUALITY_SUFFIX

    return prompt
