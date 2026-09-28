"""Tests for the lean persona system prompt (ADR-005 Phase B).

The lean builder is now the only builder — PERSONA_LEAN_PROMPT was retired
2026-07-04 (audit cleanup step 5) after graduating to default-on for every
persona. The flag-logic / legacy-dispatch tests were removed with it.

Hermetic: the full-prompt tests monkeypatch the persona loader + CV summarizer
so nothing hits Ollama or the on-disk persona cache.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.coordinator import prompt_builder as pb
from src.coordinator.models.persona_schema import PersonaCard


# --------------------------------------------------------------------------
# Public dispatcher — always builds the lean prompt
# --------------------------------------------------------------------------

def test_build_system_prompt_is_lean(monkeypatch):
    _patch_persona(monkeypatch, _ADVISORY_CARD)
    pb.build_system_prompt.cache_clear()
    out = pb.build_system_prompt("nephilim_test")
    assert out == pb._build_system_prompt_lean("nephilim_test")
    assert "<voice_examples>" in out


def test_cache_clear_present():
    assert hasattr(pb.build_system_prompt, "cache_clear")
    # Should not raise; clears the lean lru_cache.
    pb.build_system_prompt.cache_clear()


# --------------------------------------------------------------------------
# Lean block builders (pure — synthetic cards, no I/O)
# --------------------------------------------------------------------------

def test_voice_block_renders_signature():
    card = {
        "voice_signature": {
            "lexicon": ["triage", "objective", "discipline"],
            "cadence": "clipped declaratives, never trails off",
            "pattern": "always names the next single action",
            "anchor": "the 🛡️ before a hard call",
        }
    }
    block = pb._lean_voice_block(card)
    assert "triage" in block and "objective" in block
    assert "clipped declaratives" in block
    assert "next single action" in block
    assert "🛡️" in block


def test_voice_block_empty_without_signature():
    assert pb._lean_voice_block({}) == ""
    assert pb._lean_voice_block({"voice_signature": None}) == ""


def test_voice_examples_prefers_signature_exemplars():
    card = {
        "voice_signature": {
            "exemplars": [
                {"user": "hi", "response": "SIGNATURE REPLY"},
            ]
        },
        "example_dialogues": [
            {"user": "hi", "response": "LEGACY REPLY"},
        ],
    }
    block = pb._lean_voice_examples_block(card, "Tester")
    assert "SIGNATURE REPLY" in block
    assert "LEGACY REPLY" not in block


def test_voice_examples_falls_back_to_example_dialogues():
    card = {"example_dialogues": [{"user": "hi", "response": "FALLBACK REPLY"}]}
    block = pb._lean_voice_examples_block(card, "Tester")
    assert "FALLBACK REPLY" in block
    assert "match this voice exactly" in block


def test_voice_examples_caps_at_three():
    card = {"example_dialogues": [{"user": f"q{i}", "response": f"r{i}"} for i in range(8)]}
    block = pb._lean_voice_examples_block(card, "Tester")
    assert "r2" in block and "r3" not in block


def test_world_block_skips_non_nephilim():
    assert pb._lean_world_block({"key": "gojo"}) == ""


def test_world_block_no_wiki_dump():
    card = {
        "key": "nephilim_test",
        "title": "The Tester",
        "archetype": "The Probe",
        "domain": "testing",
        "nephilim_lore": {"origin": "x" * 500},
    }
    block = pb._lean_world_block(card)
    assert "Seeker" in block
    assert "The Tester" in block
    # The 500-char origin / wiki body must NOT be dumped into the lean prompt.
    assert "x" * 100 not in block
    assert "Extended Realm Context" not in block


def test_wallet_lean_block_keeps_hard_guards():
    block = pb._get_wallet_copilot_block_lean()
    assert "I cannot and will not" in block
    assert "Jupiter DEX" in block
    assert "Never invent" in block


# --------------------------------------------------------------------------
# Full lean prompt (hermetic via monkeypatch)
# --------------------------------------------------------------------------

def test_lean_prompt_keeps_guards_and_is_voice_last(monkeypatch):
    _patch_persona(monkeypatch, _ADVISORY_CARD)
    pb._build_system_prompt_lean.cache_clear()
    lean = pb._build_system_prompt_lean("nephilim_test")

    # Guards preserved.
    assert "first person" in lean.lower()
    assert "I cannot and will not" in lean
    assert "Seeker" in lean
    assert "<safety>" in lean and "<checklist>" in lean
    # Voice-last: exemplars are the final structured section.
    assert lean.index("<voice_examples>") > lean.index("<safety>")


def test_lean_prompt_drops_wiki_extended_context(monkeypatch):
    _patch_persona(monkeypatch, _ADVISORY_CARD)
    pb._build_system_prompt_lean.cache_clear()
    lean = pb._build_system_prompt_lean("nephilim_test")
    assert "Extended Realm Context" not in lean


def test_lean_prompt_wallet_section_only_for_wallet_personas(monkeypatch):
    _patch_persona(monkeypatch, {**_ADVISORY_CARD, "mcp_access": []})
    pb._build_system_prompt_lean.cache_clear()
    assert "<tools>" not in pb._build_system_prompt_lean("nephilim_test")

    _patch_persona(monkeypatch, {**_ADVISORY_CARD, "mcp_access": ["solana_wallet"]})
    pb._build_system_prompt_lean.cache_clear()
    assert "<tools>" in pb._build_system_prompt_lean("nephilim_test")


# --------------------------------------------------------------------------
# tool_intent injection (PERSONA_TOOL_INTENT_IN_PROMPT, default OFF)
# --------------------------------------------------------------------------

def _ti_card():
    return {**_ADVISORY_CARD,
            "escalation_policy": {"tool_intent": ["use brave_search for X", "use reasoning for Y"]}}


def test_tool_intent_absent_when_flag_off(monkeypatch):
    # Default OFF -> the dead field stays out of the prompt (byte-identical).
    _patch_persona(monkeypatch, _ti_card())
    pb._build_system_prompt_lean.cache_clear()
    out = pb._build_system_prompt_lean("nephilim_test")
    assert "Tool guidance:" not in out
    assert "use brave_search for X" not in out
    assert "<tools>" not in out  # advisory card has no wallet either


def test_tool_intent_present_when_flag_on(monkeypatch):
    monkeypatch.setattr(pb.get_settings().agent, "tool_intent_in_prompt", True)
    _patch_persona(monkeypatch, _ti_card())
    pb._build_system_prompt_lean.cache_clear()
    out = pb._build_system_prompt_lean("nephilim_test")
    assert "<tools>" in out
    assert "Tool guidance:" in out
    assert "use brave_search for X" in out
    assert "use reasoning for Y" in out
    pb._build_system_prompt_lean.cache_clear()  # don't leak flag-on prompt to next test


def test_tool_intent_merges_into_single_tools_block_with_wallet(monkeypatch):
    monkeypatch.setattr(pb.get_settings().agent, "tool_intent_in_prompt", True)
    card = {**_ti_card(), "mcp_access": ["solana_wallet"]}
    _patch_persona(monkeypatch, card)
    pb._build_system_prompt_lean.cache_clear()
    out = pb._build_system_prompt_lean("nephilim_test")
    assert out.count("<tools>") == 1  # merged, not two sections
    assert "oracle-advisor" in out  # wallet copilot text
    assert "use brave_search for X" in out  # tool_intent text
    pb._build_system_prompt_lean.cache_clear()


def test_tool_intent_empty_list_no_block(monkeypatch):
    monkeypatch.setattr(pb.get_settings().agent, "tool_intent_in_prompt", True)
    _patch_persona(monkeypatch, {**_ADVISORY_CARD, "escalation_policy": {"tool_intent": []}})
    pb._build_system_prompt_lean.cache_clear()
    out = pb._build_system_prompt_lean("nephilim_test")
    assert "Tool guidance:" not in out
    assert "<tools>" not in out
    pb._build_system_prompt_lean.cache_clear()


# --------------------------------------------------------------------------
# Helpers / fixtures
# --------------------------------------------------------------------------

_ADVISORY_CARD = {
    "key": "nephilim_test",
    "display_name": "Tester — The Probe",
    "style": "precise, calm",
    "title": "The Tester",
    "archetype": "The Probe",
    "domain": "testing",
    "mcp_access": [],
    "nephilim_lore": {"origin": "Born in the test harness.", "role_in_realm": "QA"},
    "behavior": {"traits": ["precise", "calm"], "pace": "moderate", "humor": "dry"},
    "emotional_profile": {"baseline": "steady and present"},
    "dialogue_prefs": {"reply_shape": "assess → answer"},
    "psychological_profile": {"contradiction_pairs": ["Confident | quietly unsure"]},
    "example_dialogues": [
        {"user": "Are you sure?", "response": "I am. Here is why."},
        {"user": "What now?", "response": "One step: start here."},
    ],
}


def _patch_persona(monkeypatch, card):
    monkeypatch.setattr(pb, "resolve_persona_to_card", lambda sel: card)
    monkeypatch.setattr(
        pb, "get_or_build_cv_summary",
        lambda sel, card=None: {"summary": "I am the Tester, a calm probe."}
    )


# --------------------------------------------------------------------------
# Milestone 2 — authored voice_signature data for the advisory cluster
# --------------------------------------------------------------------------

_PERSONA_DIR = Path(__file__).resolve().parents[3] / "personas"
_ADVISORY = ["nephilim_eeva", "nephilim_aegis", "nephilim_solace"]


def _load_card(key):
    return json.loads((_PERSONA_DIR / f"{key}.json").read_text())


@pytest.mark.parametrize("key", _ADVISORY)
def test_advisory_persona_has_valid_voice_signature(key):
    card = _load_card(key)
    parsed = PersonaCard(**card)  # schema must accept the new field
    vs = parsed.voice_signature
    assert vs is not None
    assert vs.lexicon and vs.cadence and vs.pattern and vs.anchor
    assert len(vs.exemplars) == 3  # first 3 are injected voice-last


def test_voice_signature_excluded_from_cv_fingerprint():
    """Adding/removing voice_signature must NOT change the CV-summary hash.

    Otherwise every cached summary goes stale and the <identity> text drifts on
    rebuild.
    """
    from src.coordinator.cv_summarizer import _fingerprint

    base = _load_card("nephilim_eeva")
    without = {k: v for k, v in base.items() if k != "voice_signature"}
    with_sig = {**without, "voice_signature": {"lexicon": ["x"], "cadence": "y"}}
    assert _fingerprint(without) == _fingerprint(with_sig)


def test_advisory_voice_blocks_are_distinct():
    """The three advisory personas must carry distinct anchors/diction."""
    blocks = {k: pb._lean_voice_block(_load_card(k)) for k in _ADVISORY}
    assert "Confluence" in blocks["nephilim_eeva"]
    assert "triage" in blocks["nephilim_aegis"]
    assert "Sanctuary" in blocks["nephilim_solace"] or "breath" in blocks["nephilim_solace"]
    # No two blocks identical.
    vals = list(blocks.values())
    assert len(set(vals)) == 3


def test_eeva_exemplars_no_longer_route_to_solace():
    """The de-collision fix: Eeva's injected exemplars stop deferring to Solace."""
    block = pb._lean_voice_examples_block(_load_card("nephilim_eeva"), "E.E.V.A.")
    assert "connect you with Solace" not in block
    assert "Confluence" in block  # her unique register instead


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# --------------------------------------------------------------------------
# Per-persona <format> override (2026-08-12)
#
# Root cause it addresses: LEAN_FORMAT ("reply like texting, not essays...
# 1-2 sentences") applies to every persona and caps analytical depth. Adding a
# counter-instruction to a persona card was MEASURED not to win — small models
# default to the dominant trained pattern when instructions conflict. So the
# block is swapped, never supplemented.
# --------------------------------------------------------------------------

from src.coordinator.config import get_settings  # noqa: E402


@pytest.fixture
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def format_override_on(monkeypatch, _fresh_settings):
    monkeypatch.setenv("PERSONA_FORMAT_OVERRIDE_ENABLED", "true")
    get_settings.cache_clear()
    return True


def _card(style=None):
    return {"dialogue_prefs": {"format_style": style}} if style else {"dialogue_prefs": {}}


def test_default_is_byte_identical_texting(_fresh_settings):
    """Flag off => every persona keeps LEAN_FORMAT, even one asking for analytical."""
    assert pb._resolve_format_block(_card()) == pb.LEAN_FORMAT
    assert pb._resolve_format_block(_card("analytical")) == pb.LEAN_FORMAT


def test_analytical_style_swaps_the_block_when_enabled(format_override_on):
    assert pb._resolve_format_block(_card("analytical")) == pb.LEAN_FORMAT_ANALYTICAL


def test_a_persona_that_declares_nothing_still_gets_texting(format_override_on):
    assert pb._resolve_format_block(_card()) == pb.LEAN_FORMAT
    assert pb._resolve_format_block({}) == pb.LEAN_FORMAT


def test_the_blocks_replace_each_other_and_never_coexist(format_override_on):
    """The load-bearing property. If both reached the prompt the model would
    face the exact contradiction ('texting, not essays' vs 'answer first, then
    explain') that small models resolve by defaulting to chat brevity — which
    is the bug this exists to fix."""
    analytical = pb._resolve_format_block(_card("analytical"))
    assert "texting" not in analytical.lower()
    assert "1-2 sentences" not in analytical
    texting = pb._resolve_format_block(_card("texting"))
    assert "Answer first" not in texting


def test_analytical_block_carries_the_anti_deferral_instruction(format_override_on):
    """The measured symptom was structure-then-defer: name the method, then ask
    a question instead of giving the finding."""
    a = pb._resolve_format_block(_card("analytical"))
    assert "Answer first" in a
    assert "only when you genuinely cannot answer" in a
    assert "never close on an offer to look something up" in a
    assert "<msg>" in a  # multi-message rendering must survive


def test_unknown_or_malformed_style_degrades_to_texting_never_raises(format_override_on):
    """A typo in a persona file must not take chat down."""
    assert pb._resolve_format_block(_card("ANALYTIC")) == pb.LEAN_FORMAT
    assert pb._resolve_format_block(_card("essays")) == pb.LEAN_FORMAT
    assert pb._resolve_format_block({"dialogue_prefs": {"format_style": 42}}) == pb.LEAN_FORMAT
    assert pb._resolve_format_block({"dialogue_prefs": "not-a-dict"}) == pb.LEAN_FORMAT
    assert pb._resolve_format_block({"dialogue_prefs": None}) == pb.LEAN_FORMAT


def test_style_matching_is_case_and_whitespace_tolerant(format_override_on):
    assert pb._resolve_format_block(_card("  Analytical ")) == pb.LEAN_FORMAT_ANALYTICAL


def test_no_shipped_persona_opts_in():
    """Regression guard: this feature must stay inert on prod by absence, not
    only by flag. If a persona ever declares format_style, that is a deliberate
    behavioural change and should fail here until the eval gate has run."""
    for p in Path("personas").glob("*.json"):
        card = json.loads(p.read_text())
        style = (card.get("dialogue_prefs") or {}).get("format_style")
        assert style is None, f"{p.name} declares format_style={style!r} — eval-gate it first"
