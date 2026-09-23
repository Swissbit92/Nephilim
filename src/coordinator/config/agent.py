from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class AgentSettings(BaseSettings):
    """Deterministic tool-call safety configuration.

    Originally the ADR-004 two-stage agentic pipeline's config; that pipeline was
    retired once the ADR-008 single-model tool brain superseded it. What remains
    are the middleware flags that outlived it: the argument allowlist (enforced
    by ``ToolCallInterceptor`` on every tool-brain call) and the memory-write
    sanitizer flag, plus the unrelated lean-prompt ``tool_intent_in_prompt``.
    """

    argument_allowlist: bool = Field(
        default=True,
        description=(
            "Enforce the per-tool argument-level allowlist in the tool-call "
            "interceptor. Default ON — disabling drops to mcp_access checks only "
            "(removes the argument schema validation layer)."
        ),
        alias="AGENTIC_ARGUMENT_ALLOWLIST",
    )
    injection_guard: bool = Field(
        default=True,
        description=(
            "Sanitize memory writes before they are indexed into RAG (strips "
            "instruction-like content so retrieved memories cannot issue "
            "directives on a later turn). Default ON. Scoped to the memory-write "
            "path in chat_session_service; the tool-trigger-source and escalation "
            "checks this flag also used to gate were removed with the ADR-004 "
            "pipeline that was their only caller."
        ),
        alias="AGENTIC_INJECTION_GUARD",
    )
    tool_intent_in_prompt: bool = Field(
        default=False,
        description=(
            "Inject each persona's escalation_policy.tool_intent lines as a "
            "<tools> guidance block in the lean system prompt. Default OFF = "
            "byte-identical (the field is otherwise dead data). Behavioral: adds "
            "prompt content to every persona that has tool_intent, so it is "
            "eval-gated (ADR-005 distinctiveness) before any flip. Set "
            "PERSONA_TOOL_INTENT_IN_PROMPT=true to enable."
        ),
        alias="PERSONA_TOOL_INTENT_IN_PROMPT",
    )
    persona_format_override: bool = Field(
        default=False,
        description=(
            "Let a persona choose its <format> block via "
            "dialogue_prefs.format_style ('texting' | 'analytical') instead of "
            "always receiving LEAN_FORMAT. Default OFF = byte-identical; and no "
            "shipped persona declares a format_style, so the feature is inert by "
            "absence too — the flag is a single-env-var kill switch. WHY A SWAP "
            "AND NOT AN ADDITION: measured 2026-08-12, adding 'finish the "
            "analysis' to a persona card while LEAN_FORMAT still said 'reply "
            "like texting, not essays' barely moved the output. Small models do "
            "not arbitrate conflicting instructions — they fall back on the "
            "pattern that is stronger in training, and instruction position does "
            "not decide it. Behavioral for any persona that opts in, so it is "
            "eval-gated (analytical depth AND voice) before a flip. Set "
            "PERSONA_FORMAT_OVERRIDE_ENABLED=true to enable."
        ),
        alias="PERSONA_FORMAT_OVERRIDE_ENABLED",
    )
    constraints_in_prompt: bool = Field(
        default=False,
        description=(
            "Render each persona's behavioural constraints — do / dont / "
            "boundaries.ethics / user_relationship / "
            "escalation_policy.when_to_decline — as a <constraints> block, and "
            "re-state the hardest of them just before the latest user turn. "
            "Default OFF = byte-identical. WHY THIS EXISTS: none of those fields "
            "had a single reader anywhere in src/; persona_schema.py even "
            "documents the workaround ('the lean prompt omits do/dont'). A "
            "persona could declare exclusivity and the model was never told — "
            "the constraint violation observed 2026-08-23. WHY NEGATIONS ARE "
            "REFRAMED: open models violate negated instructions 77-100% of the "
            "time versus affirmative framing (arXiv 'When Prohibitions Become "
            "Permissions'), so the dont list is anchored to the affirmative "
            "identity rather than emitted as a bare prohibition list. WHY "
            "LOW-DEPTH RE-INJECTION: recall is worst in the middle of a long "
            "context (arXiv:2307.03172), so a rule stated once at position 0 is "
            "the least-attended part of the prompt by turn 80. Behavioral — adds "
            "~120-180 tokens for a persona that has these fields, and this repo "
            "has twice measured a prompt-content addition as neutral-or-negative, "
            "so it is eval-gated before any flip. Set "
            "PERSONA_CONSTRAINTS_IN_PROMPT=true to enable."
        ),
        alias="PERSONA_CONSTRAINTS_IN_PROMPT",
    )
    unpin_on_depth: bool = Field(
        default=False,
        description=(
            "Once a session has real history, stop force-feeding it the two "
            "permanently-pinned blocks: the 3 cached voice_examples and the "
            "first 3 messages of the session. Default OFF = today's behaviour. "
            "WHY: both are fixed text that reaches the model on EVERY turn "
            "forever, formatted identically to real dialogue, which is a "
            "standing invitation to reproduce them (arXiv:2402.09954 — few-shot "
            "examples resembling the live context induce verbatim copying, and "
            "the effect grows with the number of similar examples). "
            "SillyTavern's own guidance is that example dialogues should be "
            "evicted once real history establishes the voice. Behavioral for "
            "any session past the threshold, so eval-gated before a flip. Set "
            "PERSONA_UNPIN_ON_DEPTH=true to enable."
        ),
        alias="PERSONA_UNPIN_ON_DEPTH",
    )
    unpin_depth_turns: int = Field(
        default=6,
        ge=1,
        description=(
            "How many real turns count as 'the voice is established' before "
            "voice_examples stop being injected. Only consulted when "
            "PERSONA_UNPIN_ON_DEPTH is on."
        ),
        alias="PERSONA_UNPIN_DEPTH_TURNS",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }


class ToolBrainSettings(BaseSettings):
    """ADR-008 P1 single-model native tool-brain loop configuration.

    The daily-driver model (abliterated Mistral-Small-24B) decides + fills tool
    calls natively; deterministic middleware (ADR-004 interceptor + injection
    guard) gates execution; the same model synthesizes in-voice. When the model
    emits no native call, the loop falls back to the existing deterministic
    intent-router / force-search floor (the spike found native calling is
    phrasing-sensitive: explicit phrasings trigger calls, colloquial ones miss).

    ``enabled`` default **False** = byte-identical to the legacy force-search
    chat path; the whole loop is bypassed. Flip TOOL_BRAIN_ENABLED=true only
    after the TB4 red-team/eval gate.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Route non-wallet-flow chat turns through the native tool-brain loop "
            "(model-decided tool calls + deterministic fallback). False (default) "
            "= byte-identical legacy force-search path. Set TOOL_BRAIN_ENABLED=true."
        ),
        alias="TOOL_BRAIN_ENABLED",
    )
    max_iterations: int = Field(
        default=3,
        ge=1,
        le=6,
        description=(
            "Max native tool-call round-trips per turn before forcing synthesis. "
            "Bounds latency + injection-compounding on a local model. 3 covers "
            "search->fetch->answer; the reads-only MVP rarely needs more."
        ),
        alias="TOOL_BRAIN_MAX_ITERATIONS",
    )
    deterministic_fallback: bool = Field(
        default=True,
        description=(
            "When the model emits NO native tool call, fall back to the "
            "deterministic intent router / force-search (the reliability floor "
            "for colloquial phrasings the model misses). Default ON. OFF = pure "
            "native (research/debug only — will silently skip tools on ~40% of "
            "colloquial queries per the TB0 spike)."
        ),
        alias="TOOL_BRAIN_DETERMINISTIC_FALLBACK",
    )
    ungated_web: bool = Field(
        default=False,
        description=(
            "Offer the persona's WEB tools on NEEDS_NEITHER turns too, letting "
            "the model decide (Hermes-Agent-style), instead of requiring the "
            "bge-m3 router to first classify the turn as NEEDS_WEB_SEARCH. "
            "WALLET is NEVER ungated — it keeps its deterministic gate, which is "
            "the part TB5 proved must stay classifier-scoped. "
            "Motivation: the tool-firing eval measured the router silently "
            "blocking real web queries (\"who is the current chancellor of "
            "Germany?\" scores 0.56 vs the 0.66 threshold), so the model never "
            "saw a tool at all. Ungating converts an invisible routing miss into "
            "a visible model choice. "
            "COST: tool schemas then sit in the prompt on chitchat turns too, and "
            "prompt bloat has twice measured as flattening persona voice — so "
            "this is eval-gated on the ADR-005 attribution eval, not just the "
            "tool-firing eval. Default OFF = byte-identical TB5 behaviour."
        ),
        alias="TOOL_BRAIN_UNGATED_WEB",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }
