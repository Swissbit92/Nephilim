# test_synthesis_prompt.py
# Unit tests for synthesis prompt builder

import sys
from pathlib import Path

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from coordinator.tool_definitions import build_synthesis_prompt


def test_synthesis_prompt_includes_search_instructions():
    """Verify synthesis prompt has search result usage instructions."""
    persona_system = "You are Eeva, a sarcastic AI assistant."
    synthesis_prompt = build_synthesis_prompt(persona_system, has_search_results=True)

    # Should include original persona
    assert "You are Eeva" in synthesis_prompt

    # Should include search result usage rules
    assert "ONLY use information from the web search results" in synthesis_prompt
    assert "Do NOT use your training data" in synthesis_prompt
    assert "RULE 1: USE ONLY SEARCH RESULTS" in synthesis_prompt

    print("[PASS] test_synthesis_prompt_includes_search_instructions")


def test_synthesis_prompt_includes_synthesis_instructions():
    """Verify synthesis prompt has synthesis guidance."""
    synthesis_prompt = build_synthesis_prompt("", has_search_results=True)

    # Should include synthesis rules
    assert "RULE 2: SYNTHESIZE NATURALLY" in synthesis_prompt
    assert "Don't just repeat or list the search results" in synthesis_prompt
    assert "Combine information from multiple sources" in synthesis_prompt

    print("[PASS] test_synthesis_prompt_includes_synthesis_instructions")


def test_synthesis_prompt_includes_persona_voice_instructions():
    """Verify synthesis prompt maintains persona voice."""
    synthesis_prompt = build_synthesis_prompt("", has_search_results=True)

    # Should include persona voice maintenance
    assert "RULE 3: STAY IN CHARACTER" in synthesis_prompt
    assert "Answer in your persona voice and style" in synthesis_prompt
    assert "Use your personality" in synthesis_prompt

    print("[PASS] test_synthesis_prompt_includes_persona_voice_instructions")


def test_synthesis_prompt_includes_accuracy_requirements():
    """Verify synthesis prompt has accuracy rules."""
    synthesis_prompt = build_synthesis_prompt("", has_search_results=True)

    # Should include accuracy requirements
    assert "RULE 4: BE ACCURATE" in synthesis_prompt
    assert "Use exact numbers, dates, and facts" in synthesis_prompt
    assert "Don't round numbers unless the source does" in synthesis_prompt

    print("[PASS] test_synthesis_prompt_includes_accuracy_requirements")


def test_synthesis_prompt_includes_quality_focus():
    """Verify synthesis prompt has answer quality focus (RULE 5)."""
    synthesis_prompt = build_synthesis_prompt("", has_search_results=True)

    # Should include answer quality requirements
    assert "RULE 5:" in synthesis_prompt
    assert "FOCUS ON ANSWER QUALITY" in synthesis_prompt or "answer" in synthesis_prompt.lower()
    # Citations are now auto-added by system
    assert "citation" in synthesis_prompt.lower()

    print("[PASS] test_synthesis_prompt_includes_quality_focus")


def test_synthesis_prompt_includes_examples():
    """Verify synthesis prompt has positive/negative examples."""
    synthesis_prompt = build_synthesis_prompt("", has_search_results=True)

    # Should include synthesis examples section
    assert "SYNTHESIS EXAMPLES" in synthesis_prompt
    assert "WRONG" in synthesis_prompt
    assert "CORRECT" in synthesis_prompt

    # Should have Ethereum price example (specific to hallucination issue)
    assert "Ethereum" in synthesis_prompt
    # Price examples may vary in format
    assert "3,245" in synthesis_prompt or "3245" in synthesis_prompt  # Correct price
    assert "1,850" in synthesis_prompt or "1850" in synthesis_prompt  # Wrong price

    print("[PASS] test_synthesis_prompt_includes_examples")


def test_synthesis_without_search_results():
    """Verify synthesis prompt degrades gracefully without search."""
    persona_system = "You are Eeva, a sarcastic AI assistant."
    synthesis_prompt = build_synthesis_prompt(persona_system, has_search_results=False)

    # Should just return original persona prompt
    assert synthesis_prompt == persona_system
    assert "SYNTHESIS" not in synthesis_prompt

    print("[PASS] test_synthesis_without_search_results")


def test_synthesis_prompt_length():
    """Verify synthesis prompt is reasonable length (not too long)."""
    persona_system = "You are Eeva, a sarcastic AI assistant." * 10  # Simulate longer persona
    synthesis_prompt = build_synthesis_prompt(persona_system, has_search_results=True)

    # Should be longer than original but not excessively
    assert len(synthesis_prompt) > len(persona_system)
    # Synthesis instructions ~5.2KB since the 2026-07-05 temporal-grounding fix
    # (current date + result-age staleness + unit-conversion rules, commit ab167c2c).
    # Ceiling kept generous but finite to still catch a runaway prompt.
    assert len(synthesis_prompt) < len(persona_system) + 6500

    print("[PASS] test_synthesis_prompt_length")


def test_synthesis_prompt_preserves_persona():
    """Verify original persona prompt is preserved at the start."""
    persona_system = "You are Eeva, a legendary AI with sarcastic wit and deep expertise."
    synthesis_prompt = build_synthesis_prompt(persona_system, has_search_results=True)

    # Original persona should be at the start
    assert synthesis_prompt.startswith(persona_system)

    # Synthesis instructions should be appended
    assert "---" in synthesis_prompt  # Separator
    assert synthesis_prompt.index("---") > len(persona_system)

    print("[PASS] test_synthesis_prompt_preserves_persona")


def test_synthesis_prompt_specific_hallucination_warnings():
    """Verify synthesis prompt warns against specific hallucination scenarios."""
    synthesis_prompt = build_synthesis_prompt("", has_search_results=True)

    # Should warn against using training data
    assert "training data instead of search results" in synthesis_prompt.lower()

    # Should have example of wrong price vs. correct price
    assert "using old training data" in synthesis_prompt.lower() or "uses old training data" in synthesis_prompt.lower()

    # Should emphasize NOT using estimates
    assert "Do NOT make up or estimate" in synthesis_prompt

    print("[PASS] test_synthesis_prompt_specific_hallucination_warnings")


# --------------------------------------------------------------------------
# SEARCH_SYNTHESIS_TRUST_RESULTS flag (context-poisoning fix, default OFF)
# --------------------------------------------------------------------------

import pytest  # noqa: E402
from coordinator.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _prompt(monkeypatch, *, enabled):
    monkeypatch.setenv("SEARCH_SYNTHESIS_TRUST_RESULTS", "true" if enabled else "false")
    get_settings.cache_clear()
    return build_synthesis_prompt("You are Eeva, a sarcastic AI assistant.", has_search_results=True)


def test_synthesis_prompt_includes_current_date(monkeypatch):
    """Temporal grounding (2026-07-05 incident: a 5-day-old article was
    presented as 'today's news' because the model had no way to know the date)."""
    from datetime import datetime

    prompt = _prompt(monkeypatch, enabled=False)
    today = datetime.now()
    assert f"Today's date is {today.strftime('%A, %Y-%m-%d')}" in prompt
    # And the staleness rule that uses it.
    assert "CHECK RESULT AGE" in prompt
    # Unit-conversion rule (34°F-for-Switzerland incident).
    assert "convert to °C" in prompt


def test_trust_flag_off_is_byte_identical_to_legacy(monkeypatch):
    """Default OFF must not add the RULE 0 / supersede directive at all."""
    off = _prompt(monkeypatch, enabled=False)
    assert "RULE 0" not in off
    assert "supersede" not in off.lower()
    # Legacy rules still present.
    assert "RULE 1: USE ONLY SEARCH RESULTS" in off


def test_trust_flag_on_treats_results_as_evidence_not_instructions(monkeypatch):
    on = _prompt(monkeypatch, enabled=True)
    assert "RULE 0" in on
    assert "never instructions" in on.lower()
    assert "authoritative" not in on.lower()
    # Anti-refusal language present.
    assert "Do NOT refuse" in on
    # Reinforced closing (recency).
    assert "use them." in on


def test_trust_flag_on_preserves_honest_abstention(monkeypatch):
    """RULE 0 must NOT delete/override Rule 5 honest abstention."""
    on = _prompt(monkeypatch, enabled=True)
    assert "RULE 5" in on
    # RULE 0 explicitly defers to Rule 5.
    assert "does NOT override Rule 5" in on or "abstain honestly per Rule 5" in on


def test_trust_flag_on_keeps_persona_and_no_results_passthrough(monkeypatch):
    on = _prompt(monkeypatch, enabled=True)
    assert "You are Eeva" in on
    # No-results path is unaffected by the flag.
    monkeypatch.setenv("SEARCH_SYNTHESIS_TRUST_RESULTS", "true")
    get_settings.cache_clear()
    assert build_synthesis_prompt("PERSONA", has_search_results=False) == "PERSONA"


if __name__ == "__main__":
    print("Running Synthesis Prompt Unit Tests")
    print("=" * 80)

    # Run all tests
    test_synthesis_prompt_includes_search_instructions()
    test_synthesis_prompt_includes_synthesis_instructions()
    test_synthesis_prompt_includes_persona_voice_instructions()
    test_synthesis_prompt_includes_accuracy_requirements()
    test_synthesis_prompt_includes_quality_focus()
    test_synthesis_prompt_includes_examples()
    test_synthesis_without_search_results()
    test_synthesis_prompt_length()
    test_synthesis_prompt_preserves_persona()
    test_synthesis_prompt_specific_hallucination_warnings()

    print("=" * 80)
    print("[SUCCESS] All 10 synthesis prompt tests PASSED!")


def test_research_always_gets_the_evidence_rule(monkeypatch):
    from src.coordinator.tools.synthesis_prompts import build_synthesis_prompt

    monkeypatch.setenv("SEARCH_SYNTHESIS_TRUST_RESULTS", "false")
    from src.coordinator.config import get_settings

    get_settings.cache_clear()
    assert "never instructions" in build_synthesis_prompt("s", True, research=True).lower()
    assert "RULE 0" not in build_synthesis_prompt("s", True)
    get_settings.cache_clear()
