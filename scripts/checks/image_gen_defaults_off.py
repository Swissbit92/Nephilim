#!/usr/bin/env python3
"""INV-2 — image generation is OFF unless switched on deliberately.

Exits 1 when any image-generation switch defaults to true.

A generation holds the whole machine for ~331 s and unloads the companion
model to do it. That must never begin because a default drifted during a
refactor, and a default is exactly the kind of thing that drifts without
anyone deciding to change it.

PARSES THE SOURCE rather than importing the settings class, and that is not
laziness: `invariants_run.py` runs under the system interpreter, which has no
pydantic, so an importing check exits 2 ("could not determine") on every
machine — and a check that can never run is worse than none, because the
runner reports it as an unresolved line nobody reads twice. Reading the field
DEFAULTS from the AST needs no dependency and no environment.

The environment is the operator's business; the defaults are the repo's.
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "src" / "coordinator" / "config" / "image_gen.py"

MUST_BE_FALSE = ("enabled", "dev_endpoint_enabled")


def _default_of(cls: ast.ClassDef, field: str):
    """The `default=` keyword of `field: T = Field(default=..., ...)`.

    Returns the literal, or the string "<absent>" / "<not-a-Field>" so the
    caller can say which of the three failure shapes it is.
    """
    for node in cls.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id != field:
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            return "<not-a-Field>"
        for kw in call.keywords:
            if kw.arg == "default":
                try:
                    return ast.literal_eval(kw.value)
                except ValueError:
                    return "<not-a-literal>"
        return "<no-default>"
    return "<absent>"


def main() -> int:
    if not SETTINGS.exists():
        print(f"INV-2 UNDETERMINED — {SETTINGS} is missing")
        return 2

    tree = ast.parse(SETTINGS.read_text(encoding="utf-8"))
    cls = next(
        (n for n in tree.body
         if isinstance(n, ast.ClassDef) and n.name == "ImageGenSettings"), None
    )
    if cls is None:
        print("INV-2 UNDETERMINED — class ImageGenSettings not found")
        return 2

    bad = []
    for name in MUST_BE_FALSE:
        value = _default_of(cls, name)
        if value is not False:
            bad.append(f"{name}: default is {value!r}, must be False")

    if bad:
        print("INV-2 VIOLATED — an image-generation switch is on by default:")
        for line in bad:
            print(f"  {line}")
        return 1

    print(f"INV-2 ok — {len(MUST_BE_FALSE)} generation switches default off")
    return 0


if __name__ == "__main__":
    sys.exit(main())
