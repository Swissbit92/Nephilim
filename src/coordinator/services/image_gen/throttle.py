# src/coordinator/services/image_gen/throttle.py
"""Stopping the model from asking for the same picture six times.

A generation occupies the whole machine for ~331 s and unloads the companion
model to do it. That makes it the most expensive thing anything in this system
can trigger, and the trigger is a language model deciding on its own that an
image is called for.

Three guards, because they catch different failures and a counter alone
catches only one of them:

1. **A cooldown.** The floor on how often a generation can start at all.
2. **A per-session quota.** Bounds the total even if each request is spaced
   out — the agent-budget literature's "stop at a limit instead of running up
   the bill".
3. **A near-duplicate check.** The one a rate limit cannot do. The documented
   loop is not six identical calls; it is six *slightly reworded* ones, which
   a counter sees as six legitimate requests. Comparing the composed prompt
   against recent ones catches "a red fox in snow" following "a fox sitting
   in the snow".

Deliberately in-memory and per-process. A restart forgetting the cooldown is
the correct trade: the alternative is a table to maintain for a guard whose
whole job is to smooth a few minutes, and a forgotten cooldown costs one extra
generation while a forgotten quota costs nothing at all.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Decision:
    allowed: bool
    #: Shown to the user through the persona, so it has to read as speech.
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


ALLOWED = Decision(True)


def _tokens(text: str) -> set[str]:
    """Content words, lowercased. Crude on purpose — see `_similar`."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


_STOPWORDS = {
    "the", "and", "with", "for", "from", "into", "that", "this", "her", "his",
    "its", "ultra", "cinematic", "composition", "mood", "shading", "soft",
    "clean", "digital", "illustration", "natural", "light", "depth", "field",
}


def _similar(a: str, b: str, threshold: float) -> bool:
    """Jaccard overlap on content words.

    Not embeddings, deliberately. An embedding call on the chat path would add
    latency and a dependency to a guard whose job is to be cheap and obvious,
    and the failure it catches — the same request reworded — is lexical almost
    by definition. The stopword list strips the composed prompt's boilerplate
    so two different subjects do not look similar merely because they share
    the quality suffix.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


@dataclass
class _SessionState:
    count: int = 0
    #: None means "never started", NOT 0.0. A float sentinel is an overloaded
    #: field: `if st.last_started` is False for a legitimate monotonic 0.0, so
    #: the cooldown silently never fires for the first-ever generation in a
    #: process. Caught by a test that injected now=0.0 — in production the
    #: clock is never 0.0, so this would have been invisible forever.
    last_started: float | None = None
    recent_prompts: list[str] = field(default_factory=list)


class GenerationThrottle:
    """Per-session cooldown, quota and near-duplicate guard.

    Thread-safe: chat turns run in FastAPI's threadpool, so two turns in one
    session really can land at once.
    """

    #: FITTED, not chosen. Measured over nine composed-prompt pairs — four
    #: rewordings of one request and five genuinely different pictures:
    #:
    #:   reworded duplicates scored 0.600 .. 1.000  (lowest 0.600)
    #:   different pictures  scored 0.000 .. 0.400  (highest 0.400)
    #:
    #: so any threshold in (0.400, 0.600] separates them, and 0.5 is the
    #: midpoint of that gap. The first value here was 0.8, picked by feel, and
    #: it MISSED the central case this guard exists for: "a red fox in deep
    #: snow" followed by "a fox sitting in the deep snow" scores 0.600 and
    #: sailed through. `test_the_fitted_threshold_separates_the_two_populations`
    #: pins both sides, so narrowing the gap fails rather than silently
    #: degrading to a guard that never fires.
    DUPLICATE_THRESHOLD = 0.5

    def __init__(
        self,
        *,
        cooldown_seconds: float = 120.0,
        per_session_limit: int = 10,
        duplicate_threshold: float | None = None,
        remember: int = 5,
    ) -> None:
        self._cooldown = cooldown_seconds
        self._limit = per_session_limit
        self._threshold = (self.DUPLICATE_THRESHOLD
                           if duplicate_threshold is None else duplicate_threshold)
        self._remember = remember
        self._lock = threading.Lock()
        self._state: dict[str, _SessionState] = {}

    def check(self, session_id: str, prompt: str, *, now: float | None = None) -> Decision:
        """May this generation start? Does NOT record it — see `record`.

        Split from `record` so a caller that fails to enqueue does not burn
        the quota, and so a test can ask the same question twice.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            st = self._state.get(session_id)
            if st is None:
                return ALLOWED

            if st.count >= self._limit:
                return Decision(
                    False,
                    "I've made a lot of pictures this session — let's pick "
                    "this back up in a new one.",
                )

            if st.last_started is not None:
                waited = now - st.last_started
            else:
                waited = None
            if waited is not None and waited < self._cooldown:
                remaining = int(self._cooldown - waited)
                return Decision(
                    False,
                    f"I'm still catching my breath from the last one — "
                    f"give me about {max(remaining, 1)} more seconds.",
                )

            for previous in st.recent_prompts:
                if _similar(prompt, previous, self._threshold):
                    return Decision(
                        False,
                        "That's basically the same picture I just made — "
                        "tell me what to change and I'll do it differently.",
                    )

        return ALLOWED

    def record(self, session_id: str, prompt: str, *, now: float | None = None) -> None:
        """Note that a generation actually started."""
        now = time.monotonic() if now is None else now
        with self._lock:
            st = self._state.setdefault(session_id, _SessionState())
            st.count += 1
            st.last_started = now
            st.recent_prompts.append(prompt)
            if len(st.recent_prompts) > self._remember:
                st.recent_prompts.pop(0)

    def forget(self, session_id: str) -> None:
        """Drop a session's history. Called on `/reset`, so clearing a
        conversation also clears the cooldown that belonged to it."""
        with self._lock:
            self._state.pop(session_id, None)
