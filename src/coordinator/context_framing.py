"""ADR-006 Phase 1 — per-persona framing of injected session context.

Gate 0 (2026-06-28) and Gate 0.1 (2026-07-03) both failed on voice: injecting the
`[User Profile]` / `[Emotional State]` blocks dropped persona distinctiveness
(0.768→0.643 full, →0.679/0.696 selective). The mechanism, isolated in Gate 0.1:
the blocks are *identically formatted* across every persona (`**Header**\n- field:
value`), and that uniform skeleton is itself a stylistic signal the model imitates —
strong voices (gojo held 1.0) absorb it, advisory voices (eeva/solace/aegis) blur
toward it. Block *choice* was not the lever; *framing* is.

This module reframes injected memory as **non-echoable background knowledge** rather
than a data dump:

1. **Diegetic prose, not a bullet skeleton** — the narrative variants
   (`UserProfile.get_narrative_context`, `EmotionalState.to_narrative_context`)
   emit flowing sentences, removing the imitable `field: value` shape.
2. **A non-imitation instruction** adjacent to the content, addressed to the
   persona by name, telling it this is knowledge to draw on — not text to recite
   or let flatten its own voice.
3. **A non-echoable wrapper tag** (`<remembered>`) so the block reads as private
   memory metadata, distinct from the response surface.

The content (facts about the *user*) is persona-neutral by nature; per-persona
variation comes from the named frame plus the removed skeleton, letting each voice
render the same knowledge in its own register. This is the Gate-0/0.1 prerequisite
for flipping `MEMORY_CONTEXT_INJECT`; it is validated by the persona-eval canary
(eeva+nyx) and then the full 7-persona attribution gate before any flag flip.
"""

from __future__ import annotations


def _persona_name(persona_key: str, card: dict | None) -> str:
    """Human display name for the non-imitation frame; fall back to the key."""
    if card:
        name = card.get("display_name") or card.get("title")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return persona_key


def frame_injected_context(
    persona_key: str,
    card: dict | None,
    body: str,
) -> str | None:
    """Wrap already-capped narrative memory in a non-echoable, per-persona frame.

    ``body`` is the token-capped narrative context (profile + emotional, in
    priority order) produced upstream. Returns None when ``body`` is empty so the
    caller injects nothing. The returned string is what reaches the LLM as
    ``extra_system_context``.
    """
    if not body or not body.strip():
        return None
    name = _persona_name(persona_key, card)
    preamble = (
        f"This is what you, {name}, quietly carry from earlier conversations with "
        "this seeker — background you already know, not a script. Let it shape what "
        "you say and never recite it back as a list; keep speaking entirely in your "
        "own voice. Its plain wording is a note to yourself, not a style to copy."
    )
    return f"<remembered>\n{preamble}\n\n{body.strip()}\n</remembered>"


def _sentence_join(parts: list[str]) -> str:
    """Join clause fragments into a single spaced prose string, de-duped of blanks."""
    return " ".join(p.strip() for p in parts if p and p.strip())

# ---------------------------------------------------------------------------
# ADR-022 — the numbered recall frame. Measured best on ACCURACY, and in direct
# tension with the prose-not-skeleton rule above. Read both before using it.
# ---------------------------------------------------------------------------

#: The instruction that turns a judgement into a LOOKUP. The decline clause is
#: deliberately byte-identical to the instruction-only arm it was measured
#: against, so the difference between them is attributable to the lookup rather
#: than to a softer refusal rule.
RECALL_INSTRUCTION = (
    "The numbered list above is everything you were told about him and everything "
    "you told him about yourself.\n"
    "Before you answer: if he is asking about a FACT — about him, about your own "
    "history, or about something that happened between you — FIND IT IN THE "
    "NUMBERED LIST FIRST. If it is there, use it. If it is genuinely not in the "
    "list, say you don't know; do not guess and do not fill it in. Inventing a "
    "detail about his real life is worse than admitting you weren't told."
)


def frame_numbered_facts(persona_key: str, card: dict | None,
                         about_user: list[str],
                         about_self: list[str] | None = None) -> str | None:
    """Render recalled facts as a NUMBERED list plus a find-it-first instruction.

    **Why a numbered list, when this module exists to avoid exactly that.**
    Measured 2026-10-18, 160 generations, 4 arms, 16 turns of real conversation
    each, probe as the unit:

        arm                  invents   wrongly refuses
        today (prose block)    0.875             0.375
        instruction only       0.000             0.708
        NUMBERED + find-first  0.000             0.208
        + an assertion gate    0.000             0.250

    The numbered arm beats the baseline on BOTH axes — total error −78%, 10
    probes down and 0 up, p=0.0020 on all three exact tests. The mechanism is
    that it converts a JUDGEMENT ("do I know this?") into a LOOKUP ("is it in the
    list?"), and a small model is far better at the second. Before it, she said
    *"I don't know what kind of books you read"* with the answer in her prompt.

    ⚠️ **THIS CONTRADICTS THE MODULE DOCSTRING ABOVE, AND THE CONTRADICTION IS
    UNRESOLVED.** Gate 0.1 (2026-07-03) isolated the voice mechanism: identically
    formatted blocks are themselves a stylistic signal the model imitates, and
    structured injection dropped distinctiveness **0.768 → 0.643**. A numbered
    list is precisely that skeleton. The ADR-022 measurement covered ACCURACY and
    **did not measure voice** — and replies came back **31% shorter** (118 vs 170
    chars), which is consistent with the same mechanism rather than independent of
    it.

    **The operator's decision, 2026-10-18, recorded rather than inferred:** the
    shorter replies are acceptable and the distinctiveness run is deliberately
    skipped, on the grounds that he is the one who judges her voice in use. That
    is a better instrument than a proxy metric — but it is a judgement, not a
    measurement, and nothing here should be read as the voice question having been
    settled.

    Returns None when there is nothing to recall, so the caller injects nothing
    and the dormant path stays byte-identical.
    """
    about_self = about_self or []
    if not any(f.strip() for f in [*about_user, *about_self]):
        return None

    name = _persona_name(persona_key, card)
    lines: list[str] = []
    n = 1
    if any(f.strip() for f in about_user):
        lines.append("Things you know about him, because he told you:")
        for f in about_user:
            if f.strip():
                lines.append(f" [{n}] {f.strip()}")
                n += 1
    if any(f.strip() for f in about_self):
        if lines:
            lines.append("")
        lines.append("Things you have told him about yourself:")
        for f in about_self:
            if f.strip():
                lines.append(f" [{n}] {f.strip()}")
                n += 1

    preamble = (
        f"This is what you, {name}, carry from earlier conversations with this "
        f"seeker. Never recite the list or quote its numbers back to him — it is "
        f"a note to yourself, and you speak entirely in your own voice."
    )
    return ("<remembered>\n" + preamble + "\n\n" + "\n".join(lines)
            + "\n\n" + RECALL_INSTRUCTION + "\n</remembered>")
