# src/coordinator/tools/capability_scope.py
"""Reconcile a turn's classified INTENT against the persona's RESOLVED tool surface.

The defect this closes, measured 2026-09-23 on 69 live generations: asked "what's
the weather in Zurich tomorrow?", gwen fired `image_search` 3/3 and answered "a
maximum temperature of 103F and a minimum of 68F" — invented, and shipped with a
citation block because a tool had run. She is granted only image_search and
video_search (ADR-008, deliberate). Nothing told her general web search was
withheld, so she reached for the nearest tool she had and filled the gap.

WHY THIS IS DETERMINISTIC RATHER THAN A PROMPT INSTRUCTION
----------------------------------------------------------
The obvious fix is to tell the model what it cannot do and let it decline. Three
measured results say that is not a control:

  * Models refuse only 28.3% (open) to 65.1% (best) of genuinely unanswerable
    questions (arXiv 2311.09731) — asking for a decline is a suggestion.
  * Refusal is output-layer suppression: the fabricated answer stays linearly
    recoverable from hidden states (arXiv 2608.15772). Withholding tools moves
    the fabrication, it does not remove it.
  * Abliteration — which this deployment uses — measurably thins uncertainty
    vocabulary and shifts confidence (arXiv 2607.17427), and the effect direction
    differs per model family. The introspective faculty a prompt fix depends on is
    exactly the one the weights may have damaged.

A trap worth recording, because the citation looks authoritative: arXiv 2609.14157
measures a scope-clause mitigation and is the strongest evidence in this area, but
it solves the OPPOSITE problem (models that over-refuse when a narrow tool exists).
Its clause ends "answer directly from your own knowledge rather than refusing" —
for the weather turn, answering from one's own knowledge IS the 103F. Copying the
measured mitigation would instruct this persona to do the thing being fixed.

So the decision is made in code, from two values the app already has, and the
model is never asked to judge its own competence.

FAIL CLOSED
-----------
The documented failure mode for this class of guard is fail-OPEN drift: a
classifier error or an unmapped intent coded as "allow", so the guard silently
stops guarding exactly when something else is already wrong. Every unmapped
intent here deflects rather than passes through, and `INTENT_REQUIREMENTS` is
exhaustive over QueryIntent — enforced by a test, so adding an intent without
wiring it breaks the build instead of degrading in production.
"""
from __future__ import annotations

from dataclasses import dataclass

from .intent_classifier import QueryIntent

# Tools that can serve a general factual/live-data lookup. A persona holding NONE
# of these cannot answer "what's the weather tomorrow" no matter how it is phrased.
GENERAL_LOOKUP_TOOLS: frozenset[str] = frozenset({
    "web_search", "brave_web_search", "news_search", "fetch_url", "extract",
})

# Which tools satisfy which intent. Exhaustive over QueryIntent by contract; a
# missing member is a build failure, not a silent pass-through.
INTENT_REQUIREMENTS: dict[QueryIntent, frozenset[str]] = {
    QueryIntent.NEEDS_WEB_SEARCH: GENERAL_LOOKUP_TOOLS,
    # Wallet is never model-decided — it is routed before the tool brain is
    # reached, so this guard must not claim it as an out-of-surface case.
    QueryIntent.NEEDS_WALLET: frozenset(),
    # "No confident route" means answer conversationally; it needs no tool, so
    # there is no capability to be missing.
    QueryIntent.NEEDS_NEITHER: frozenset(),
}


@dataclass(frozen=True)
class ScopeVerdict:
    """Why a turn was (or was not) judged out of surface.

    `missing` is carried so the deflection can NAME what it lacks: a ~50k-pair
    Chatbot Arena analysis found technical/capability refusals are tolerated about
    twice as well as ethical ones, and that refusals stating a reason beat generic
    ones. A deflection that says nothing specific throws that away.
    """
    out_of_surface: bool
    reason: str = ""
    missing: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return self.out_of_surface


IN_SURFACE = ScopeVerdict(False)


def check_scope(
    intent: QueryIntent,
    granted: set[str],
    *,
    media_type: str | None = None,
) -> ScopeVerdict:
    """Does the persona hold a tool that can serve this intent?

    `media_type` is the result of `intent_classifier.media_search_type` — a turn
    already identified as an explicit media request is served by image/video
    search and is never out of surface, whatever the coarse intent said.
    """
    if media_type:
        return IN_SURFACE

    required = INTENT_REQUIREMENTS.get(intent)
    if required is None:
        # Fail CLOSED. An unmapped intent means the label space and this table
        # have drifted; deflecting is recoverable, confabulating is not.
        return ScopeVerdict(
            True,
            reason=f"intent {intent!r} is not mapped in INTENT_REQUIREMENTS",
            missing=frozenset(),
        )

    if not required:
        return IN_SURFACE  # this intent needs no tool

    if required & set(granted):
        return IN_SURFACE

    return ScopeVerdict(
        True,
        reason=f"intent {intent.value} needs one of {sorted(required)}; persona has none",
        missing=frozenset(required),
    )
