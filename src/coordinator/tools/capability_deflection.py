# src/coordinator/tools/capability_deflection.py
"""Build the out-of-surface reply in CODE, from the persona card. No model call.

Why not ask the model for an in-character refusal: it is the same generation that
would otherwise fabricate. Refusal is output-layer suppression — the fabricated
answer remains linearly recoverable (arXiv 2608.15772) — and models comply with a
request to decline only 28-65% of the time (arXiv 2311.09731). Routing this path
through the model would leave a residual fabrication rate on exactly the turns the
guard exists to make safe.

Why not one fixed string: this is a companion the user talks to daily, and the
identical sentence every time reads as a system message wearing her name. The
lines therefore live on the PERSONA CARD, authored in her voice, and the module
only selects among them.

Selection is deterministic on the user's turn rather than random: the same
question gets the same answer within a session (a companion that says something
different each time you ask is its own kind of broken), while different questions
vary. It also makes the tests exact instead of flaky.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

# Used only when a persona card declares no lines of its own. Deliberately plain:
# a persona that has not been given a voice for this should sound neutral rather
# than borrow someone else's.
GENERIC_LINES: tuple[str, ...] = (
    "I can't look that one up — I don't have anything that reaches outside this "
    "conversation. Ask me something I'd actually know?",
)

CARD_FIELD = "cannot_lookup"


def _lines(persona_card: dict[str, Any]) -> Sequence[str]:
    declared = persona_card.get(CARD_FIELD)
    if isinstance(declared, str):
        declared = [declared]
    if isinstance(declared, (list, tuple)):
        usable = [str(x).strip() for x in declared if str(x).strip()]
        if usable:
            return tuple(usable)
    return GENERIC_LINES


def choose_line(persona_card: dict[str, Any], user_message: str) -> str:
    """Pick one deflection line, deterministically from the user's turn."""
    lines = _lines(persona_card)
    if len(lines) == 1:
        return lines[0]
    digest = hashlib.sha256((user_message or "").strip().lower().encode("utf-8")).digest()
    return lines[digest[0] % len(lines)]


def build_deflection(
    persona_card: dict[str, Any],
    user_message: str,
    *,
    missing: Sequence[str] | None = None,
) -> str:
    """The full out-of-surface reply.

    `missing` is accepted and deliberately NOT rendered into the user-facing text.
    A ~50k-pair Chatbot Arena analysis found refusals that give a reason outperform
    generic ones, but the reason that helps a user is "I can't reach the internet",
    not "web_search, news_search, fetch_url are not in my allowlist". Tool names
    are implementation detail; they go to the log line, not to the companion's
    mouth. The persona's own wording carries the reason.
    """
    return choose_line(persona_card, user_message)
