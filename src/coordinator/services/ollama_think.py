"""One definition of the Ollama thinking control, for every transport.

`think` is a **TOP-LEVEL** key on `/api/generate` and `/api/chat`. It is NOT a
sampler option, and that distinction is the whole reason this module exists:
Ollama validates the `options` dict against a fixed list and answers HTTP 200
with a WARN log for anything it does not recognise. A `think` placed in
`options` is therefore accepted, ignored, and indistinguishable from working —
which is how this control sat dead on the HTTP transport while reading as
configured.

The second reason is the default. `OLLAMA_REASONING` unset means *omit the
key*, not *send false*, so a non-reasoning model receives a byte-identical
request to the one it received before this control existed. For a reasoning
model the omission is not neutral: the thinking stream consumes the whole
`num_predict` budget and the visible `content` arrives EMPTY. Measured on
`gemma4:26b` through the live tool-brain path, 2026-10-25:

    think omitted   10 of 12 replies EMPTY   (thinking median 1462 chars)
    think=False      0 of 12 replies EMPTY   (thinking 0 chars)

An empty reply is the worst available failure: it is not an error, nothing
raises, and the refusal detector downstream scores the empty string.
"""

from __future__ import annotations


def wire_think(reasoning: bool | None) -> dict[str, bool]:
    """Kwargs to splat into an `ollama` client call: `{}` or `{"think": bool}`.

    Returning a dict rather than a value is deliberate — it makes "omit the
    key" expressible at the call site, which a `bool | None` argument cannot
    do without every caller repeating the same `if is not None` branch.
    """
    return {} if reasoning is None else {"think": bool(reasoning)}
