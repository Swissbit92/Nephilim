# tests/evaluation/persona_eval/tool_score.py
"""Score tooling probes on the TOOL TRACE, never on the prose.

Two independent reasons this scores the trace and not the wording:

1. It is the established convention. BFCL's Irrelevance Detection category (875
   examples) requires the model to emit NO function call when none of the offered
   functions fits, and grades the call (or its absence) structurally — a model may
   say whatever it likes around it. DeepEval's own agent-evaluation guidance says
   the same thing more bluntly for the high-stakes no-call case: assert the
   captured trace directly rather than leaning on a fuzzy metric.

2. A refusal regex measures phrasing, and phrasing is the one thing a persona is
   free to vary. "I can't look that up" and "not a clue, Daddy — ask me something
   filthier instead" are the same outcome.

POSITIVE and NEGATIVE probes score in OPPOSITE directions, which is why the probe
schema carries `must_fire` and `must_not_fire` as separate fields. They used to
share one `targets` list, and the first scorer written against it treated every
entry as "must fire" — inverting all 8 negative probes and reporting a router
that breaks scenes mid-scene as a healthy one.

UNREACHABLE is a distinct verdict on purpose. A probe naming a tool the persona
was never granted cannot pass, and folding it into the failures silently depresses
a rate that is otherwise fine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

# `must_not_fire: ["*"]` means "no tool at all", used where the probe's fail_if
# says "any tool fires" rather than naming one.
ANY = "*"

PASS = "pass"
FAIL = "fail"
WRONG_TOOL = "wrong_tool"
SILENT = "silent"
UNREACHABLE = "unreachable"
ERROR = "error"


@dataclass(frozen=True)
class ToolOutcome:
    probe: str
    k: int
    verdict: str
    why: str = ""
    tools_used: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.verdict == PASS


def _tools(row: Mapping) -> tuple[str, ...]:
    return tuple(sorted(row.get("tools_used") or ()))


def score_row(row: Mapping, probe: Mapping, granted: Iterable[str]) -> ToolOutcome:
    """Score one generation against one probe.

    `granted` is the persona's RESOLVED tool surface — what registry
    `specs_for_persona` actually returns, not what the toolset contains. Passing
    the toolset instead is how a probe naming an ungranted tool gets scored as a
    model failure rather than as an impossible probe.
    """
    k = int(row.get("k", 0))
    used = _tools(row)
    base = dict(probe=probe["id"], k=k, tools_used=used)

    if row.get("error"):
        return ToolOutcome(**base, verdict=ERROR, why=str(row["error"])[:200])

    forbidden = probe.get("must_not_fire")
    if forbidden:
        if ANY in forbidden:
            tripped = bool(used)
        else:
            tripped = bool(set(used) & set(forbidden))
        return ToolOutcome(
            **base,
            verdict=FAIL if tripped else PASS,
            why=("fired " + ", ".join(used)) if tripped else "",
        )

    required = set(probe.get("must_fire") or ())
    if not required:
        return ToolOutcome(**base, verdict=ERROR,
                           why="probe declares neither must_fire nor must_not_fire")

    granted = set(granted)
    if not (required & granted):
        return ToolOutcome(
            **base, verdict=UNREACHABLE,
            why="persona lacks " + ", ".join(sorted(required - granted)))

    if set(used) & required:
        return ToolOutcome(**base, verdict=PASS)
    if used:
        return ToolOutcome(
            **base, verdict=WRONG_TOOL,
            why=f"{', '.join(sorted(required))} -> {', '.join(used)}")
    return ToolOutcome(**base, verdict=SILENT, why="no tool fired")


def score_rows(rows: Sequence[Mapping], probes: Sequence[Mapping],
               granted: Iterable[str]) -> list[ToolOutcome]:
    by_id = {p["id"]: p for p in probes}
    out = []
    for r in rows:
        p = by_id.get(r.get("probe"))
        if p is None:
            out.append(ToolOutcome(probe=str(r.get("probe")), k=int(r.get("k", 0)),
                                   verdict=ERROR, why="no such probe in the set"))
            continue
        out.append(score_row(r, p, granted))
    return out


def summarise(outcomes: Sequence[ToolOutcome], probes: Sequence[Mapping]) -> dict:
    """Aggregate, keeping the two directions apart.

    A single blended rate is not reportable: negative probes pass by doing
    nothing and positive probes pass by doing something, so mixing them lets a
    router that never fires look competent.
    """
    by_id = {p["id"]: p for p in probes}

    def direction(o: ToolOutcome) -> str:
        return "negative" if by_id.get(o.probe, {}).get("must_not_fire") else "positive"

    res: dict = {"negative": {}, "positive": {}, "n": len(outcomes)}
    for group in ("negative", "positive"):
        sub = [o for o in outcomes if direction(o) == group]
        counts: dict[str, int] = {}
        for o in sub:
            counts[o.verdict] = counts.get(o.verdict, 0) + 1
        scorable = [o for o in sub if o.verdict != UNREACHABLE]
        res[group] = {
            "n": len(sub),
            "counts": counts,
            "passed": sum(o.ok for o in sub),
            "scorable": len(scorable),
            "rate": (sum(o.ok for o in scorable) / len(scorable)) if scorable else None,
        }
    return res
