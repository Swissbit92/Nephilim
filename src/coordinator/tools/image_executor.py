# src/coordinator/tools/image_executor.py
"""Executing `generate_image`: queue a job, say so, return immediately.

It cannot block. A generation is ~331 s measured; a chat turn that waited for
one would exceed the Telegram client's 180 s timeout and hold a threadpool
worker for five and a half minutes. So the executor enqueues and returns a
sentence, and the image arrives later through the notification poller.

**The session id reaches here through a ContextVar.** The executor contract is
`executor(arguments, persona_card)` and `persona_card` carries `key` but not
the session — there is no existing channel, and widening the signature would
break the four search executors that are bound with two parameters. A
ContextVar is correct rather than merely convenient here: the tool brain runs
synchronously inside the request's own thread, so the value set by the route
is the value this sees, and a concurrent turn in another thread has its own.

**A failure here must not vanish.** `ToolBrainService.run` wraps its whole
body in `except Exception` and degrades to a silent result, so an exception
raised in this executor would produce an empty answer and a fall-through to
the legacy path — the user would get an ordinary reply and no indication that
anything was attempted. Every failure path therefore RETURNS a sentence
instead of raising.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any

from ..services.image_gen.prompt import GenerationIntent, PromptError, compose

logger = logging.getLogger(__name__)

#: Set by `routes/chat.py` for the duration of one turn. Default "" means the
#: executor was reached from somewhere that did not set it, which is a wiring
#: error and is reported as one rather than guessed at.
current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "image_gen_session_id", default=""
)

#: Returned to the model as the tool result. It reads these and writes the
#: user-facing sentence itself, in voice.
_QUEUED = (
    "Image generation started. It takes about five minutes and will arrive "
    "on its own. Tell the user it is coming — do NOT describe the picture, "
    "you have not seen it."
)


def generate_image_executor(
    arguments: dict[str, Any], persona_card: dict[str, Any]
) -> str:
    """Queue one generation. Returns a sentence for the model, never raises."""
    from .. import startup

    session_id = current_session_id.get()
    if not session_id:
        logger.error("[ImageTool] no session in context — the route did not set it")
        return "Image generation is not available right now."

    try:
        intent = GenerationIntent(
            subject=arguments.get("subject") or "",
            setting=arguments.get("setting"),
            mood=arguments.get("mood"),
            style=arguments.get("style"),
        )
        prompt = compose(intent)
    except PromptError as exc:
        logger.info("[ImageTool] refused a malformed intent: %s", exc)
        return f"Could not start that image: {exc}. Ask the user what to draw."

    throttle = startup.get_generation_throttle()
    decision = throttle.check(session_id, prompt)
    if not decision:
        logger.info("[ImageTool] throttled for %s: %s", session_id[:8], decision.reason)
        # The reason is written as speech so the model can relay it directly.
        return f"Not starting a new image. Tell the user: {decision.reason}"

    try:
        job = startup.get_image_job_repo().create(
            session_id=session_id,
            persona_key=persona_card.get("key", "unknown"),
            prompt=prompt,
        )
    except Exception:
        logger.exception("[ImageTool] could not queue a generation")
        return "Image generation is not available right now."

    throttle.record(session_id, prompt)
    # Logged in full so the user can later audit WHY it drew what it drew —
    # the transparency DALL-E 3 provides through `revised_prompt`.
    logger.info("[ImageTool] queued %s for %s: %r", job.id, session_id[:8], prompt)
    return _QUEUED
