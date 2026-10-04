"""The check: a NATURAL drawing request through the REAL route function.

Calls `_try_tool_brain` — the one function holding all three changes (the
generation narrowing, the decision temperature, the honest-refusal fallback)
— with real persona cards and a real prompt. Not a reconstruction of its
logic: reconstructing is how a check ends up proving something about a path
the product does not use.

Executor stubbed: no GPU time, no job queued.

Exit 0 iff for EVERY persona granted the tool the turn ends HONESTLY on 5/5
— the job fired, or she says she could not start it. A false promise is a
hard fail at any rate, because nothing arrives and nothing errors.
"""
import sys; sys.path.insert(0, '/Users/swissbit./nephilim-ecosystem/nephilim')
from src.coordinator.persona_memory import get_persona_card, build_system_prompt
from src.coordinator.schemas import ChatBody, ResponseMetadata
from src.coordinator.tools import registrations  # noqa
from src.coordinator.tools.intent_classifier import QueryIntent
from src.coordinator.tools.registry import registry
from src.coordinator.tools.executor_bindings import bind_web_executors
from src.coordinator.routes.chat import _try_tool_brain

bind_web_executors()
calls = []
registry.bind_executor("generate_image", lambda a, c: (calls.append(a), "started")[1])

NATURAL = ["draw me a fox in the snow",
           "make me a picture of a cat on a windowsill",
           "draw a quiet harbour at night",
           "can you draw a mountain lake for me",
           "paint me a field of poppies"]
PROMISE = ("coming", "on the way", "right away", "few minutes", "i'll make",
           "getting your", "started soon", "shortly", "drawing your")

# The refusal is GENERATED in voice now, so it cannot be matched on fixed
# wording — that was the point of the change. Ask persona_lines for the exact
# variants it cached and check membership.
from src.coordinator.services import persona_lines

def _refusals(persona_key: str) -> set[str]:
    """Every line this persona might use to say it could not start."""
    persona_lines.line(persona_key, "image_not_started")  # ensure cached
    cached = persona_lines._cache.get((persona_key, "image_not_started"), [])
    return set(cached) | {persona_lines._FALLBACK["image_not_started"]}


bad = 0
for key in ("gwen", "nephilim_eeva"):
    card = get_persona_card(key)
    sysp = build_system_prompt(key, include_examples=True)
    fired = honest = lied = other = 0
    for p in NATURAL:
        calls.clear()
        body = ChatBody(persona=key, message=p)
        try:
            resp = _try_tool_brain(
                card=card, system=sysp, body=body, history=[],
                intent=QueryIntent.NEEDS_NEITHER,
                metadata=ResponseMetadata(), persona_name=key, deps={},
                classifier_available=True)
        except Exception as e:
            print(f"    ERROR {type(e).__name__}: {e}")
            continue
        a = "" if resp is None else (resp.get("answer") or "")
        a = a if isinstance(a, str) else " ".join(a)
        if calls:
            fired += 1
        elif a and a in _refusals(key):
            honest += 1
        elif any(w in a.lower() for w in PROMISE):
            lied += 1
        else:
            other += 1
    ok = lied == 0 and (fired + honest) == len(NATURAL)
    print(f"  {key:16s} fired {fired}/5  honest {honest}/5  "
          f"FALSE-PROMISE {lied}/5  other {other}/5  {'OK' if ok else 'FAIL'}")
    if not ok:
        bad = 1
sys.exit(bad)
