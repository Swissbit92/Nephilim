# src/coordinator/services/image_gen/direct.py
"""Queueing a generation without asking the model to call a tool.

ARM B. The deterministic path: `generation_intent()` has already decided
this is a drawing request (18/18 on the boundary cases, including "draw me
closer" and "make me a coffee"), so the only thing left for the model is
filling in the arguments — which `extract` does through a grammar rather
than through a tool call it reliably fails to emit.

This exists because arm A does not work in a real conversation. Measured:
with gwen's real history at depth >= 2 the native tool call fires 0/5, while
the constrained extraction returns valid JSON 6/6 on the same input. The
precedent is ADR-015 — bubble boundaries are a pure function of the text,
not a prompt instruction — and the shape is the same: something computable
was being asked of the model, so compute it.

What is NOT bypassed: the throttle, the persona's grant, and the honest
reply. The model still writes what she says; it is only the *decision* and
the *arguments* that stop depending on a tag appearing first.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Returned to the caller so the route can tell these apart without parsing
#: prose. `queued` is the only one that produced a job.
QUEUED = "queued"
THROTTLED = "throttled"
FAILED = "failed"


def queue_generation(
    *,
    session_id: str,
    persona_key: str,
    message: str,
    system_prompt: str = "",
) -> tuple[str, str, str]:
    """Extract, throttle-check and enqueue. Returns (outcome, text, decided_by).

    `text` is what to tell the user — already in their persona's voice,
    because a status string in the middle of a conversation is the thing
    `persona_lines` exists to prevent.

    `decided_by` names what made the call and reaches
    `ResponseMetadata.tool_decided_by`. It is NEVER a bare "model" on this
    path: a tool firing is not evidence of grounding, and a deterministic
    trigger makes that question sharper rather than softer, so a bypassed
    decision has to be visible as one.

    Never raises. This runs on the chat path, and the tool brain's catch-all
    would render an exception as the model having nothing to say — which is
    the silent failure this whole path replaces.
    """
    from ... import startup
    from .. import persona_lines
    from ..image_gen.extract import default_client, extract
    from ..image_gen.prompt import PromptError, compose

    if not session_id:
        logger.error("[ImageDirect] no session — the route did not supply one")
        return FAILED, persona_lines.line(persona_key, "image_not_started"), "none"

    client, model = default_client()

    intent, how = extract(
        message, client=client, model=model, system_prompt=system_prompt
    )
    decided_by = f"generation_intent+{how}"

    try:
        prompt = compose(intent)
    except PromptError as exc:
        logger.info("[ImageDirect] nothing usable to draw: %s", exc)
        return FAILED, persona_lines.line(persona_key, "image_not_started"), decided_by

    throttle = startup.get_generation_throttle()
    decision = throttle.check(session_id, prompt)
    if not decision:
        logger.info("[ImageDirect] throttled for %s: %s", session_id[:8], decision.reason)
        return THROTTLED, decision.reason, decided_by

    try:
        job = startup.get_image_job_repo().create(
            session_id=session_id, persona_key=persona_key, prompt=prompt
        )
    except Exception:
        logger.exception("[ImageDirect] could not queue a generation")
        return FAILED, persona_lines.line(persona_key, "image_not_started"), decided_by

    throttle.record(session_id, prompt)
    # Logged in full so the user can audit WHY it drew what it drew — the
    # transparency DALL-E 3 provides through `revised_prompt`, and the thing
    # a deterministic trigger owes the person it decided on behalf of.
    logger.info("[ImageDirect] queued %s via %s for %s: %r",
                job.id, how, session_id[:8], prompt)
    return QUEUED, persona_lines.line(persona_key, "image_queued"), decided_by
