"""The live image-extract pin must exercise the prompt production actually builds.

🔴 WHY THIS TEST EXISTS.

`tests/integration/test_image_extract_live.py` is described in
`src/coordinator/services/image_gen/extract.py:43-47` as a behaviour PIN on the
field-separation of the grammar-constrained extractor, and the record says it
passes 8/8. Every one of its `extract()` calls omits `system_prompt`, which
defaults to `""`.

Production has not passed `""` since `routes/chat.py:876` began splicing
`extra_system_context` into `system`. The chain is six hops and all of it is
live for gwen by default:

    chat_session_service.py:741  state.extra_system_context = _framed
    chat_session_service.py:869  -> ChatBody
    routes/chat.py:876           system = f"{system}\\n\\n{extra_system_context}"
    routes/chat.py:990           _try_tool_brain(system=system, ...)
    routes/chat.py:521           direct.queue_generation(system_prompt=system)
    image_gen/direct.py:69       extract(..., system_prompt=system_prompt)
    image_gen/extract.py:198     messages.append({"role": "system", ...})

Measured from the running backend's own log: 833 tokens, "42 NUMBERED recalled
facts + 8 self-facts". So the pin measures a condition production left behind,
and it is blind to any regression that the injected block causes.

⚠️ The block GROWS with every fact she learns -- 710, then 794, then 833 tokens
observed -- and the trim bound is 2000. So the untested gap widens on its own,
without anyone changing a line.

⚠️ INV-4 does not cover this. It guards the extractor against conversation
HISTORY and is an assertion on the signature. The system prompt is a different
channel and nothing watches it.

WHY A SEPARATE AST TEST AND NOT AN ASSERTION INSIDE THE LIVE FILE. The live
file skips wholesale unless a real model is pulled -- `tests/conftest.py:28`
sets `PERSONA_MODEL=test-model`, so in a normal run and in CI it contributes
nothing. A guard that lives inside a skipped module is not a guard. This one
parses source, needs no model, and therefore actually runs.
"""

from __future__ import annotations

import ast
import pathlib

LIVE_PIN = (pathlib.Path(__file__).resolve().parents[3]
            / "tests" / "integration" / "test_image_extract_live.py")


def _extract_calls() -> list[ast.Call]:
    tree = ast.parse(LIVE_PIN.read_text())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else "")
        if name == "extract":
            out.append(node)
    return out


def test_the_live_pin_has_extract_calls_at_all():
    """Guard the guard: if the calls are renamed away, the test below would
    vacuously pass on an empty list."""
    calls = _extract_calls()
    assert calls, (
        f"found no extract() calls in {LIVE_PIN.name} -- this test can no "
        "longer see what it is supposed to guard")


def test_at_least_one_call_passes_the_production_system_prompt():
    """The pin must measure the prompt production builds, not `system_prompt=""`."""
    calls = _extract_calls()
    with_sysp = [c for c in calls
                 if any(kw.arg == "system_prompt" for kw in c.keywords)]
    assert with_sysp, (
        f"all {len(calls)} extract() calls in {LIVE_PIN.name} omit "
        "`system_prompt`, so it defaults to \"\". Production passes the whole "
        "persona system prompt including the ~833-token numbered recall block "
        "(routes/chat.py:876 -> direct.py:69 -> extract.py:198). The pin is "
        "measuring a condition production left behind.")


def test_the_production_arm_uses_a_frozen_block_not_a_live_read():
    """A live graph read makes the pin's own arm a moving target.

    The injected block was observed at 710, 794 and 833 tokens on three
    different days as the fact worker wrote. A pin whose input drifts cannot
    distinguish a model regression from a store that grew, which is how the
    original measurement became uncomparable in the first place.
    """
    src = LIVE_PIN.read_text()
    calls = _extract_calls()
    if not any(kw.arg == "system_prompt" for c in calls for kw in c.keywords):
        import pytest
        pytest.skip("no production arm yet -- covered by the test above")
    assert "current_facts(" not in src, (
        "the pin reads operator facts from the live graph. Freeze the block "
        "into a fixture instead: the live block grew 710 -> 794 -> 833 tokens "
        "while nothing in the extractor changed.")
