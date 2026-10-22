"""The extraction gate counts USER messages, after the turn.

🔴 THE DEFECT, measured on a real teaching session 2026-10-22. Three facts were
stated to her. ZERO reached any store. The gate was
`len(state.db_messages) % 10 == 0`, and two things were wrong with it:

  1. `db_messages` counts EVERY row, and she replies in multi-message bubbles,
     so the counter advances by 2 or 3 unpredictably. Hitting `% 10 == 0` was
     luck, not cadence.
  2. `db_messages` is populated in `_build_turn_prompt` BEFORE the turn is
     persisted, so even when it fired the window excluded the newest turn — the
     one most likely to carry a new fact.

Enumerating the pre-turn counts across six user turns gives 0, 3, 6, 9, 12, 16.
The gate fired EXACTLY ONCE, at count 0, on an EMPTY transcript.

⚠️ NOT the extractor's fault and NOT temperature. Re-run on the same transcript
at the live 0.3 it captures 7 of 9 across 3 reps. My temperature hypothesis was
wrong and is recorded as wrong. This also explains the historical 12 July rows
that describe the conversation rather than the operator — the rare firings
caught arbitrary windows.
"""
from __future__ import annotations

import pytest


def _user_turns(roles: list[str]) -> int:
    """The gate's arithmetic, extracted so it can be tested without a turn."""
    return sum(1 for r in roles if r == "user") + 1


class TestTheOldGateWasSkippable:
    def test_the_measured_session_fired_only_on_an_empty_transcript(self):
        """The real role sequence from the teaching session."""
        # 6 user turns, she replies in 2-3 bubbles -> counts 0, 3, 6, 9, 12, 16
        pre_turn_counts = [0, 3, 6, 9, 12, 16]
        fired = [n for n in pre_turn_counts if n % 10 == 0]
        assert fired == [0], (
            "the old gate fired once, on an empty transcript, and skipped every "
            f"turn that carried a fact; fired at {fired}"
        )

    def test_a_multi_message_reply_makes_the_old_step_irregular(self):
        """Why it was luck: the step is the reply's bubble count, not 2."""
        roles, counts = [], []
        for _ in range(6):
            counts.append(len(roles))
            roles.append("user")
            roles.extend(["assistant"] * 2)   # she replies in 2-3 bubbles
        assert len(set(b - a for a, b in zip(counts, counts[1:]))) == 1
        # ...and with a VARYING bubble count the step is not constant at all:
        roles, counts = [], []
        for bubbles in (2, 3, 2, 3, 1, 2):
            counts.append(len(roles))
            roles.append("user")
            roles.extend(["assistant"] * bubbles)
        steps = {b - a for a, b in zip(counts, counts[1:])}
        assert len(steps) > 1, f"expected an irregular step, got {steps}"


class TestTheNewGateCannotBeSkipped:
    def test_the_step_is_exactly_one_per_user_turn(self):
        roles, seen = [], []
        for bubbles in (2, 3, 1, 3, 2, 2, 1, 3, 2, 2):
            seen.append(_user_turns(roles))
            roles.append("user")
            roles.extend(["assistant"] * bubbles)
        assert seen == list(range(1, 11)), (
            f"user-turn count must advance by exactly 1; got {seen}"
        )

    def test_it_fires_every_interval_user_turns(self):
        roles, fired = [], []
        for turn in range(1, 31):
            n = _user_turns(roles)
            if n % 10 == 0:
                fired.append(n)
            roles.append("user")
            roles.extend(["assistant"] * ((turn % 3) + 1))
        assert fired == [10, 20, 30], f"expected regular firing; got {fired}"

    def test_it_never_fires_on_an_empty_transcript(self):
        """The old gate's one firing was the useless one."""
        assert _user_turns([]) == 1
        assert _user_turns([]) % 10 != 0

    def test_the_plus_one_includes_this_turn(self):
        """Removes the off-by-one without a second DB read before the persist."""
        roles = ["user", "assistant", "assistant"] * 9   # 9 user turns persisted
        assert _user_turns(roles) == 10, (
            "the 10th user turn must see 10, not 9 — otherwise the window that "
            "fires excludes the turn that triggered it"
        )

    def test_the_live_gate_uses_this_arithmetic(self):
        """A source pin: the behaviour above must be what production runs."""
        import ast
        import inspect
        import textwrap

        from coordinator.services import chat_session_service as css

        # 🔴 PARSED, NOT STRING-MATCHED. The first version of this assertion
        # grepped for "len(state.db_messages) % fact_interval" and FAILED —
        # because that string appears in the explanatory COMMENT above the fixed
        # gate, describing the defect. Fourth time in this repo that a test
        # matched prose instead of code. Comments are not in the AST, so parsing
        # cannot make that mistake.
        tree = ast.parse(textwrap.dedent(inspect.getsource(css._apply_post_turn_updates)))

        # The assignment that computes the count must iterate on role == "user".
        assigns = [n for n in ast.walk(tree)
                   if isinstance(n, ast.Assign)
                   and any(getattr(t, "id", None) == "user_turns" for t in n.targets)]
        assert assigns, "the gate must compute a user_turns count"
        src_of_assign = ast.unparse(assigns[0])
        assert "'user'" in src_of_assign or '"user"' in src_of_assign, (
            f"the count must filter on the user role; got {src_of_assign}"
        )

        # No live BoolOp may still test a modulo of the whole-row count.
        bad = []
        for n in ast.walk(tree):
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod):
                left = ast.unparse(n.left)
                if "db_messages" in left:
                    bad.append(ast.unparse(n))
        assert not bad, (
            f"the all-rows modulo is the defect and must not run: {bad}"
        )
