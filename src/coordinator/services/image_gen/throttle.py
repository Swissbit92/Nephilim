# src/coordinator/services/image_gen/throttle.py
"""Stopping the model from asking for the same picture six times.

A generation occupies the whole machine for ~331 s and unloads the companion
model to do it. That makes it the most expensive thing anything in this system
can trigger, and the trigger is a language model deciding on its own that an
image is called for.

Two guards. There were three; the third was removed after it broke the
feature in live use, and the reason is worth keeping:

1. **A cooldown.** The floor on how often a generation can start at all.
2. **A per-session quota.** Bounds the total even if each request is spaced
   out — the agent-budget literature's "stop at a limit instead of running up
   the bill".

**REMOVED 2026-10-02 — a near-duplicate check.** It compared the composed
prompt against recent ones by Jaccard overlap and refused above 0.5. Replayed
against the user's REAL prompt history it refused **14 of 19 requests**,
including "red haired mature ... BLACK bikini" followed by "blond mature ...
WHITE bikini" (0.667) — a different picture by any reading. A user refines by
keeping the scaffolding and changing one or two attributes, which is exactly
what the metric scores as a repeat.

Three reasons it is gone rather than retuned:

- **Lexical overlap is a known-bad signal for "same request".** PAWS
  (arXiv:1904.01130) is built on precisely this failure: controlled word
  swaps keep the bag of words and change the meaning. Here the swapped words
  ARE the request — they are what the picture looks like.
- **It guarded a loop this design cannot have.** It came from agent-loop
  literature about a model re-calling a tool unprompted. Every call here is
  initiated by a user turn, and the tool brain already caps calls within a
  turn via `max_iterations`.
- **No comparable system does this.** No prompt-level dedup was found in
  Automatic1111, ComfyUI or Fooocus, and Midjourney ships a *Repeat* button
  for resubmitting the same prompt deliberately.

And the way it was fitted is the lesson, not the threshold: nine pairs I
wrote myself, which separated cleanly at 0.5 and told me nothing. This repo
already records that failure once — a safety scorer validated on hand-written
cases that then flagged nine correct refusals on first live contact. Validate
a detector on the population it runs against.

Deliberately in-memory and per-process. A restart forgetting the cooldown is
the correct trade: the alternative is a table to maintain for a guard whose
whole job is to smooth a few minutes, and a forgotten cooldown costs one extra
generation while a forgotten quota costs nothing at all.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    allowed: bool
    #: Shown to the user through the persona, so it has to read as speech.
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


ALLOWED = Decision(True)


@dataclass
class _SessionState:
    count: int = 0
    #: None means "never started", NOT 0.0. A float sentinel is an overloaded
    #: field: `if st.last_started` is False for a legitimate monotonic 0.0, so
    #: the cooldown silently never fires for the first-ever generation in a
    #: process. Caught by a test that injected now=0.0 — in production the
    #: clock is never 0.0, so this would have been invisible forever.
    last_started: float | None = None


class GenerationThrottle:
    """Per-session cooldown, quota and near-duplicate guard.

    Thread-safe: chat turns run in FastAPI's threadpool, so two turns in one
    session really can land at once.
    """

    def __init__(
        self,
        *,
        cooldown_seconds: float = 120.0,
        per_session_limit: int = 10,
    ) -> None:
        self._cooldown = cooldown_seconds
        self._limit = per_session_limit
        self._lock = threading.Lock()
        self._state: dict[str, _SessionState] = {}

    def check(self, session_id: str, prompt: str, *, now: float | None = None) -> Decision:
        """May this generation start? Does NOT record it — see `record`.

        `prompt` is accepted and unused — see `record`.

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

        return ALLOWED

    def record(self, session_id: str, prompt: str, *, now: float | None = None) -> None:
        """Note that a generation actually started.

        `prompt` is accepted and unused. Kept in the signature deliberately:
        every caller already passes it, and it is the one thing an exact-match
        rule would need if the duplicate guard ever comes back in a defensible
        form. Removing it would make that a signature change across the call
        sites rather than a change here.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            st = self._state.setdefault(session_id, _SessionState())
            st.count += 1
            st.last_started = now

    def forget(self, session_id: str) -> None:
        """Drop a session's history. Called on `/reset`, so clearing a
        conversation also clears the cooldown that belonged to it."""
        with self._lock:
            self._state.pop(session_id, None)
