"""Map a persona card onto identity nodes (ADR-018).

THE CARD IS MOSTLY PROSE, and that bounds this file. Her appearance is not a structured
field — it lives inside 21 free-text `lore` strings ("striking blue eyes that water...").
`signature_moves` is 8 more. So one card entry becomes one node, verbatim. Nothing is
decomposed into "blue eyes" / "155cm" / "red hair".

That is a decision with evidence behind it, not conservatism:
  * Anthropic measured context-stripping raising retrieval failure 49-67%, and
    decomposition IS a context-stripping operation.
  * GraphRAG's production design keeps source text alongside extracted structure rather
    than replacing it.
  * This repo's own extractor produced 2 useful facts out of 12 — one factually wrong,
    two duplicated, one about her own processing — which is exactly the failure the
    literature predicts for inference-shaped extraction. "Striking" is not literally
    "beautiful"; pulling an attribute out of that clause requires inference, and that is
    the regime where structured output measurably degrades.

So her appearance IS in the graph — as the lore nodes that describe it. She can reinforce
lore entry 7, and entry 7 is the appearance. What is lost is only a spec-sheet version of
her that would have read worse.

EXCLUSIONS ARE DECLARED, NOT IMPLIED. Under ecosystem ADR-012 the graph is the system of
record, so a card field nobody modelled does not degrade gracefully — it disappears from
who she is and a rebuild makes that permanent with no error. Every card key must
therefore appear in exactly one of: a mapping below, or `EXCLUDED_FIELDS` with a stated
reason. A key in neither is the defect `identity_completeness` exists to catch.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterator, List, Tuple

from .identity_shapes import BOUNDARY, EXPERTISE, LORE_ENTRY, TRAIT, Shape

#: Card keys deliberately NOT in the identity graph, each with the reason it is out.
#: A reason is required — "excluded" without one is how a field gets quietly dropped to
#: make a completeness check go green.
EXCLUDED_FIELDS: Dict[str, str] = {
    # ── plumbing: configuration, not identity ────────────────────────────────
    "key": "routing identifier, not a fact about her",
    "display_name": "rendered as the name in 'You are {who}, ...' and as the first-name "
                    "seed for the generated <identity> paragraph, so it IS identity "
                    "prose — but it is a single scalar label, not a fact, and it is "
                    "stored as a PROPERTY on the Persona node by apply_card() rather "
                    "than as its own node. Accounted for there, not dropped.",
    "coordinator_label": "routing label for the multi-persona coordinator",
    "nsfw": "capability flag read at request time",
    "rarity": "progression metadata, not identity",
    "celestial_order": "progression metadata",
    "emoji": "presentation glyph for the persona picker",
    "toolsets": "capability grant — ADR-011: authorization stays in git, read at "
               "startup, never seeded. Seeding it would make the graph an authority "
               "on what she may DO, which is a different and more dangerous claim.",
    "tools": "capability grant — see toolsets",
    "mcp_access": "capability grant — see toolsets",
    "cannot_lookup": "capability boundary, enforced at tool-call time not prompt time",
    "model_preferences": "sampler settings; belongs to inference, not identity",
    "image": "asset path for the frontend, not a fact about her",
    "avatar": "asset path for the frontend",
    "logo": "asset path for the frontend",
    "bg": "background asset path for the frontend",
    "word_substitutions": "render-time text filter",
    # ── consumer layer: already a documented non-goal ────────────────────────
    "voice_signature": "voice exemplars and diction cues — CONSUMER LAYER, the same class "
                       "as example_dialogues and voice.greeting/signoff. Added to gwen "
                       "2026-09-28 to carry an in-voice refusal exemplar (measured: leak "
                       "39% -> 20%, p=0.0063). Deliberately NOT identity: it is a "
                       "demonstration of HOW she says things, not a fact about her, and "
                       "it is also fingerprint-exempt in cv_summarizer precisely so that "
                       "editing it cannot regenerate <identity> through an LLM.",
    "example_phrases": "voice exemplar — see voice",
    "example_dialogues": "voice exemplar — see voice",
    "dialogue_prefs": "render-shape preference consumed by prompt_builder directly",
    # ── already modelled elsewhere in the graph ──────────────────────────────
    "do": "modelled as :Rule by ADR-014, not duplicated here",
    "dont": "modelled as :Rule by ADR-014, not duplicated here",
    "escalation_policy": "rule-shaped; belongs with :Rule, and no competency question "
                        "demands a separate label for it yet",
    "user_relationship": "describes the OPERATOR, not her. Needs an :Operator node and "
                        "a question that asks for one — cmp.cq01/02 are about facts he "
                        "has told her, which is a different thing. Deferred, not dropped.",
}

#: Leaf paths excluded INSIDE an otherwise-modelled key. This exists because key-level
#: exclusion cannot express "this key is modelled except for these leaves", and the first
#: draft of this file had exactly that gap: 7 slider leaves sat under the modelled
#: `emotional_profile` key with their reason written only in a code comment, so the
#: key-level check reported 33-of-33 complete while 9 leaves went nowhere.
#:
#: The lesson is about the METRIC, not the fields: a coverage claim made at a coarser
#: granularity than the data is technically true and practically misleading, which is
#: worse than no claim.
EXCLUDED_LEAVES: Dict[str, str] = {
    # voice.tics IS consumed (see _voice_nodes) -- these two are the exemplars.
    "voice.greeting": "voice exemplar: a canned opening line, CONSUMER LAYER. "
                      "SEMANTIC_PLATFORM.md states exemplars are not seeded.",
    "voice.signoff": "voice exemplar: a canned closing line -- see voice.greeting.",
    **{f"emotional_profile.sliders.{d}": (
        "a slider is a NUMBER, and a number modelled as a node is a value pretending "
        "to be a thing. ADR-016 also measured them inert as behaviour controls. NOT a "
        "node, but no longer deferred: IdentityRepository._capture_baseline writes the "
        "shipped values onto an immutable :Baseline (ON CREATE only) and "
        "_set_current_dials keeps the live values on the Persona node, so evo.cq01's "
        "diff is answerable from the graph alone. Excluded from NODES, present in the "
        "graph."
    ) for d in ("warmth", "assertiveness", "playfulness", "skepticism",
                "competitiveness", "manipulativeness", "sluttiness")},
}


def _hash(s: str) -> str:
    """Source hash: detects a card edited AFTER a node was built."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _node(shape: Shape, persona_id: str, field: str, index: int, text: str,
          **extra: Any) -> Dict[str, Any]:
    return {
        "_shape": shape.label,
        "persona_id": persona_id,
        "source_field": field,
        "source_index": index,
        "text": text.strip(),
        "source_hash": _hash(text.strip()),
        "origin": "card",
        **extra,
    }


def identity_nodes(card: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every identity node this card implies, in a stable order.

    Stable order matters: `source_index` is part of the MERGE key, so a reordered list
    silently retargets annotations onto the wrong node. The rule store guards the same
    hazard by failing loudly on a card/overlay text mismatch; the equivalent guard here
    is `source_hash` plus the completeness check.
    """
    pid = card.get("key") or "unknown"
    out: List[Dict[str, Any]] = []

    # `style` and `voice.tics` reach her prompt directly. prompt_builder renders
    # `style` as the first sentence of the identity block and cv_summarizer feeds both
    # to the LLM that generates <identity>. The first pass excluded them as
    # presentation, which was wrong: under ADR-012 the graph is the system of record, so
    # anything that reaches her prompt must be IN it or dropping the card loses it.
    style = card.get("style")
    if isinstance(style, str) and style.strip():
        out.append(_node(TRAIT, pid, "style", 0, style, kind="style"))

    for i, entry in enumerate((card.get("voice") or {}).get("tics") or []):
        if isinstance(entry, str) and entry.strip():
            out.append(_node(TRAIT, pid, "voice.tics", i, entry, kind="tic"))

    for i, entry in enumerate(card.get("lore") or []):
        if isinstance(entry, str) and entry.strip():
            out.append(_node(LORE_ENTRY, pid, "lore", i, entry, kind="lore"))

    for i, entry in enumerate(card.get("signature_moves") or []):
        if isinstance(entry, str) and entry.strip():
            out.append(_node(LORE_ENTRY, pid, "signature_moves", i, entry,
                             kind="signature_move"))

    for i, entry in enumerate((card.get("behavior") or {}).get("traits") or []):
        if isinstance(entry, str) and entry.strip():
            out.append(_node(TRAIT, pid, "behavior.traits", i, entry, kind="behaviour"))

    psych = card.get("psychological_profile") or {}
    for fname in ("core_wound", "coping_mechanism", "defense_style", "growth_edge"):
        v = psych.get(fname)
        if isinstance(v, str) and v.strip():
            out.append(_node(TRAIT, pid, f"psychological_profile.{fname}", 0, v,
                             kind="psychological"))
    for i, entry in enumerate(psych.get("contradiction_pairs") or []):
        if isinstance(entry, str) and entry.strip():
            out.append(_node(TRAIT, pid, "psychological_profile.contradiction_pairs", i,
                             entry, kind="psychological"))

    emo = card.get("emotional_profile") or {}
    for fname in ("baseline",):
        v = emo.get(fname)
        if isinstance(v, str) and v.strip():
            out.append(_node(TRAIT, pid, f"emotional_profile.{fname}", 0, v,
                             kind="emotional"))
    # `sliders` are NOT nodes. ADR-016 measured them inert as behaviour controls, and a
    # number modelled as a node is a value pretending to be a thing. They belong on the
    # Persona node or on a Baseline, which evo.cq02 asks for and this milestone defers.
    for i, entry in enumerate(emo.get("strengths") or []):
        if isinstance(entry, str) and entry.strip():
            out.append(_node(TRAIT, pid, "emotional_profile.strengths", i, entry,
                             kind="emotional"))
    for i, entry in enumerate(emo.get("pitfalls") or []):
        if isinstance(entry, str) and entry.strip():
            out.append(_node(TRAIT, pid, "emotional_profile.pitfalls", i, entry,
                             kind="emotional"))

    for bkind in ("ethics", "content", "personal"):
        for i, entry in enumerate((card.get("boundaries") or {}).get(bkind) or []):
            if isinstance(entry, str) and entry.strip():
                out.append(_node(BOUNDARY, pid, f"boundaries.{bkind}", i, entry,
                                 kind=bkind, priority=100 if bkind == "ethics" else 50))

    for level in ("strong", "familiar", "avoid"):
        for i, entry in enumerate((card.get("expertise") or {}).get(level) or []):
            if isinstance(entry, str) and entry.strip():
                out.append(_node(EXPERTISE, pid, f"expertise.{level}", i, entry,
                                 level=level))

    # `relationship_to_user` and `clarifying_questions` were MISSED by the first draft
    # and found only by checking coverage at LEAF granularity — the key-level check
    # counted `behavior` as modelled and could not see two of its leaves going nowhere.
    for fname in ("pace", "humor", "formality", "small_talk", "emoji_policy",
                  "clarifying_questions", "relationship_to_user"):
        v = (card.get("behavior") or {}).get(fname)
        if isinstance(v, str) and v.strip():
            out.append(_node(TRAIT, pid, f"behavior.{fname}", 0, v, kind="behaviour"))

    return out


#: Which top-level card keys the mapping above actually consumes. Derived from the
#: mapper rather than hand-listed, so the two cannot drift.
#: Container shape per source_field: "list" if the card holds a JSON array there,
#: "scalar" for a single string. Reconstruction CANNOT infer this from the nodes: a
#: one-element list and a scalar both yield exactly one node at source_index 0, so a
#: graph->card rebuild would silently turn ["x"] into "x". Declared once here and
#: consumed by identity_to_card, with test_source_field_kind_covers_every_node
#: failing when a builder is added without a matching entry.
SOURCE_FIELD_KIND: Dict[str, str] = {
    "lore": "list",
    "signature_moves": "list",
    "style": "scalar",
    "voice.tics": "list",
    "behavior.traits": "list",
    "behavior.pace": "scalar",
    "behavior.humor": "scalar",
    "behavior.formality": "scalar",
    "behavior.small_talk": "scalar",
    "behavior.emoji_policy": "scalar",
    "behavior.relationship_to_user": "scalar",
    "behavior.clarifying_questions": "scalar",
    "psychological_profile.core_wound": "scalar",
    "psychological_profile.coping_mechanism": "scalar",
    "psychological_profile.defense_style": "scalar",
    "psychological_profile.growth_edge": "scalar",
    "psychological_profile.contradiction_pairs": "list",
    "emotional_profile.baseline": "scalar",
    "emotional_profile.strengths": "list",
    "emotional_profile.pitfalls": "list",
    "boundaries.ethics": "list",
    "boundaries.content": "list",
    "boundaries.personal": "list",
    "expertise.strong": "list",
    "expertise.familiar": "list",
    "expertise.avoid": "list",
}


MODELLED_FIELDS: frozenset[str] = frozenset({
    "lore", "signature_moves", "behavior", "psychological_profile",
    "emotional_profile", "boundaries", "expertise",
    # Added after the first pass excluded these as presentation. They are not:
    # prompt_builder.py renders `style` as the literal first sentence of the identity
    # block ("You are Gwen, seductive, analytical, devoted."), and cv_summarizer.py
    # feeds both `style` and `voice.tics` to the LLM that writes <identity>. Under
    # ADR-012 the graph is the system of record, so a field that reaches her prompt
    # and is absent from the graph is a field that dropping the card would lose.
    "style", "voice",
})
