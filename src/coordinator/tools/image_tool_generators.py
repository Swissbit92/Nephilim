# src/coordinator/tools/image_tool_generators.py
"""The `generate_image` tool definition.

**The persona does not write the diffusion prompt.** It fills `subject`,
`setting` and `mood`; `services/image_gen/prompt.compose` builds the string.
Every production system found does the equivalent — DALL-E 3 rewrites through
GPT-4, Fooocus expands through a fine-tuned GPT-2, T2I-Copilot interprets into
a structured report — and none of them pass the caller's raw text to the image
model.

The schema is deliberately small. Research on local tool-calling is explicit
that reducing the number of tools and the number of parameters improves
reliability on small models, and that enums are double-edged: they give the
model "a small, confident set of options", so a wrong pick is a confident
wrong pick. So there is ONE enum, with four values, and the rest is short free
text. No `aspect_ratio`, no `negative_prompt`, no `steps` — none of them would
change the decision the model is making, and the guidance is to leave a
parameter out of the schema entirely if it is not meaningful to that decision.

The description carries an AFFIRMATIVE trigger condition rather than a
prohibition. This repo has measured that open models violate negated
instructions 77-100% of the time versus affirmative framing, and the
documented hosted-chatbot failure is a tool firing on topic-adjacency — the
user mentions a picture and the model draws one. "Only when the user asks you
to make one" states the condition positively.
"""

from __future__ import annotations

from typing import Any

from ..services.image_gen.prompt import STYLE_PHRASES


def get_generate_image_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "Draw a NEW picture. Use this only when the user asks you to "
                "make, draw, create or generate an image for them — if they "
                "want to FIND an existing picture, use image_search instead. "
                "Describe what to draw in plain words across the fields: put "
                "the main thing in `subject`, where it is in `setting`, and "
                "the feeling in `mood`. Do not write a list of tags. "
                "This takes about five minutes and the picture arrives later, "
                "so tell the user it is coming rather than describing an image "
                "you have not seen."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": (
                            "The main thing to draw, in a few plain words — "
                            "e.g. 'a red fox sitting upright'."
                        ),
                    },
                    "setting": {
                        "type": "string",
                        "description": (
                            "Where it is, if the user said — e.g. 'deep snow "
                            "at dusk'. Leave out if they did not say."
                        ),
                    },
                    "mood": {
                        "type": "string",
                        "description": (
                            "The feeling, if the user said — e.g. 'calm', "
                            "'tense'. Leave out if they did not say."
                        ),
                    },
                    "style": {
                        "type": "string",
                        "enum": sorted(STYLE_PHRASES),
                        "description": (
                            "How it should look. Use 'diagram' only for an "
                            "explanatory figure."
                        ),
                    },
                },
                "required": ["subject"],
            },
        },
    }
