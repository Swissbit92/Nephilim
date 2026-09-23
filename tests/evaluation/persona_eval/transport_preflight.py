# tests/evaluation/persona_eval/transport_preflight.py
"""Does a reply actually come back? Run this before anything expensive.

Every other number in the persona eval assumes the model produced text. Three
documented ways that assumption fails silently on this stack, all of which
return HTTP 200:

1. **Thinking is not actually off.** Ollama's ``think`` field is *"silently
   ignored rather than throwing an error for models that don't support it"*,
   and the Qwen renderer family has a recurring bug class where the value is
   received and then discarded. Reproduced on this machine 2026-09-22 with
   ``gemma4:26b``: ``think=True`` returned **0 characters of content**, 459
   characters of thinking, ``done_reason="length"``. At
   ``MODEL_MAX_OUTPUT_TOKENS=400`` a thinking trace eats the whole budget and
   the user gets an empty message.
2. **An unrecognised sampler option is accepted and ignored.** HTTP 200, no
   error, just ``level=WARN source=types.go:1048 "invalid option provided"`` in
   the server log. This is how ``min_p`` sat in a config for months doing
   nothing. A *retired* option behaves differently and fails loudly
   (``typical_p`` → 400), so the two need separate handling.
3. **A generation is truncated rather than finished.** ``done_reason="length"``
   with plausible-looking content is a partial reply that scores like a whole
   one.

The check is cheap, and it gates a run measured in hours. Run it per
configuration — per model, per option set — not once per session: a toggle that
worked at startup can stop working, and the failure is invisible.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

__all__ = [
    "PreflightResult", "ACCEPTED_OPTIONS", "RETIRED_OPTIONS",
    "preflight", "check_options", "verify_resolved_config",
]

# Sampler keys Ollama's Options struct accepts, verified against the installed
# client on 2026-09-22. Anything outside this set is accepted-and-ignored, which
# is worse than rejected: it looks configured and does nothing.
ACCEPTED_OPTIONS = frozenset({
    "temperature", "top_k", "top_p", "min_p", "repeat_penalty", "repeat_last_n",
    "presence_penalty", "frequency_penalty", "seed", "num_ctx", "num_predict",
    "num_keep", "stop", "num_batch", "num_gpu", "main_gpu", "use_mmap", "num_thread",
})

# Removed upstream. These fail LOUDLY (HTTP 400), so sending one breaks every
# turn rather than degrading quietly — which makes them the easier case.
RETIRED_OPTIONS = frozenset({"typical_p", "mirostat", "mirostat_tau", "mirostat_eta", "tfs_z"})

# DRY and XTC are compiled into the engine Ollama spawns but absent from its
# API. Sending them is a silent no-op, not an error — the single most
# misleading failure mode available here.
SILENTLY_IGNORED = frozenset({
    "dry_multiplier", "dry_base", "dry_allowed_length", "dry_penalty_last_n",
    "dry_sequence_breakers", "xtc_probability", "xtc_threshold",
})


@dataclass
class PreflightResult:
    ok: bool
    model: str
    content_chars: int = 0
    thinking_chars: int = 0
    done_reason: str = ""
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def report(self) -> str:
        head = f"{'PASS' if self.ok else 'FAIL'}  {self.model}"
        detail = (f"    content={self.content_chars} thinking={self.thinking_chars} "
                  f"done_reason={self.done_reason!r}")
        lines = [head, detail]
        lines += [f"    ✗ {f}" for f in self.failures]
        lines += [f"    ! {w}" for w in self.warnings]
        return "\n".join(lines)


def check_options(options: dict) -> tuple[list[str], list[str]]:
    """Static check on an option dict, before any request is made.

    Returns (failures, warnings). Separated from the live call because a retired
    option breaks every turn and should be caught without spending a generation.
    """
    failures, warnings = [], []
    for key in options:
        if key in RETIRED_OPTIONS:
            failures.append(f"{key!r} was removed upstream and returns HTTP 400 — never send it")
        elif key in SILENTLY_IGNORED:
            failures.append(
                f"{key!r} is compiled into the engine but absent from Ollama's API: it is "
                f"accepted and ignored, so it will look configured and do nothing"
            )
        elif key not in ACCEPTED_OPTIONS:
            warnings.append(f"{key!r} is not in the known-accepted set; verify it reaches the wire")
    if isinstance(options.get("repeat_last_n"), int) and options["repeat_last_n"] < 0:
        failures.append(
            "repeat_last_n < 0 returns HTTP 400 (llama.cpp PR #26524 retired the "
            "full-context sentinel); on the greet path that surfaces as a 503"
        )
    return failures, warnings


def preflight(
    model: str,
    *,
    base: str = "http://127.0.0.1:11434",
    options: dict | None = None,
    think: bool | None = False,
    prompt: str = "Say one short sentence about the weather.",
    min_content_chars: int = 20,
    timeout: int = 300,
) -> PreflightResult:
    """One live round trip that must produce text.

    ``keep_alive=0`` so a preflight never leaves a model resident — this runs
    against a machine where the chat model is pinned indefinitely by design.
    """
    options = dict(options or {})
    options.setdefault("num_predict", 120)
    failures, warnings = check_options(options)
    res = PreflightResult(ok=False, model=model, failures=failures, warnings=warnings)
    if failures:
        return res  # a static failure means the live call would only confirm it

    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "stream": False, "keep_alive": 0, "options": options}
    if think is not None:
        body["think"] = think

    req = urllib.request.Request(
        f"{base}/api/chat", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        res.failures.append(f"HTTP {e.code}: {detail}")
        return res
    except Exception as e:  # noqa: BLE001 - a preflight reports, never raises
        res.failures.append(f"{type(e).__name__}: {e}")
        return res

    msg = payload.get("message") or {}
    content = msg.get("content") or ""
    thinking = msg.get("thinking") or ""
    res.content_chars = len(content)
    res.thinking_chars = len(thinking)
    res.done_reason = payload.get("done_reason", "")

    if len(content.strip()) < min_content_chars:
        res.failures.append(
            f"content is {len(content.strip())} chars — the reply is empty or near-empty. "
            f"With think={think!r} and thinking={len(thinking)} chars, the most likely cause "
            f"is a thinking trace consuming the output budget"
        )
    if res.done_reason == "length":
        res.failures.append(
            "done_reason='length': the generation was truncated, not finished. A partial "
            "reply scores like a whole one"
        )
    if think is False and thinking:
        res.failures.append(
            f"think=False but {len(thinking)} chars of thinking came back — the toggle was "
            f"not honoured for this model"
        )
    low = content.lower()
    for marker in ("<think>", "<|channel>", "</think>"):
        if marker in low:
            res.failures.append(f"reasoning markup {marker!r} leaked into the visible content")
            break

    res.ok = not res.failures
    return res


def verify_resolved_config(resolved: dict, expected: dict) -> list[str]:
    """Refuse to run when the harness resolved a different configuration.

    This exists because it happened. Run from a git worktree, which starts with
    no ``.env``, settings fell through to their pydantic defaults and the
    preflight cheerfully passed against ``gemma2:9b`` at ``num_ctx=4096``
    instead of the deployed abliterated 24B at 16384. Every number that followed
    would have described a model nobody runs, and nothing would have looked
    wrong — the run completes, the report renders, the figures are plausible.

    It is the same defect class already live in production, where a utility path
    that does not see ``.env`` loads the default model and leaves 7.5 GB
    resident alongside the intended one.

    Config drift of this kind cannot be caught downstream: by the time you are
    reading violation rates, the evidence of which model produced them is gone.
    So state the expected values explicitly at the top of a run and compare.
    """
    problems = []
    for key, want in expected.items():
        got = resolved.get(key)
        if got != want:
            problems.append(f"{key}: expected {want!r}, resolved {got!r}")
    return problems
