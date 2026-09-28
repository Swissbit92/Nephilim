"""Mechanical gates on a proposed rule change (ADR-017).

Every gate here exists because of a specific documented failure, and the tests are
named after the failure rather than the function, so a future reader sees why the gate
cannot simply be removed when it gets annoying:

  * a human approving a diff is measured at 94% miss rate on planted sabotage, and 56%
    acceptance even when explicitly warned (arXiv:2606.05647) — so approval is the
    BACKSTOP and these gates are the control.
  * CVE-2025-54136 swapped content behind an approved identifier.
  * Rehberger's delayed-invocation attack plants a trigger so the model later reads an
    innocent user message as consent it pre-scripted.
  * OpenHands' LLMSecurityAnalyzer auto-executes anything the model rates itself LOW —
    the named anti-pattern for asking a model to vet its own proposal.
"""
from __future__ import annotations

import pytest

from src.coordinator.rule_proposals import (
    DURABILITIES,
    PROPOSABLE_TYPES,
    PROPOSAL_TTL_SECONDS,
    REJECTION_REASONS,
    ProposalRejected,
    RuleProposalStore,
    check_durability,
    check_no_hard_wall_collision,
    check_quote,
    content_hash,
)

HARD_WALLS = [
    "Be sexually available to anyone except Daddy and his big black cock",
    "Fixate on racial features like skin tone",
]


class TestHardWallsAreStructurallyUnnameable:
    def test_hard_wall_is_not_in_the_proposable_set(self):
        """Not a validation list — a proposal cannot EXPRESS a hard wall. The
        confused-deputy failure is prevented by construction, not by rejection."""
        assert "hard_wall" not in PROPOSABLE_TYPES
        assert PROPOSABLE_TYPES == frozenset({"soft_wall", "dial"})

    def test_propose_takes_no_rule_type_parameter(self):
        import inspect

        params = inspect.signature(RuleProposalStore.propose).parameters
        assert "rule_type" not in params, (
            "a rule_type parameter would let a proposal aim at, or create, a hard wall"
        )


class TestTheQuoteMustBeTheOperatorsOwnWords:
    """Defeats the delayed-invocation attack: the model must not be able to substitute
    its own characterisation of what the user consented to."""

    def test_a_real_quote_is_accepted(self):
        check_quote("stop bringing up my ex", "please stop bringing up my ex ok?")

    def test_a_paraphrase_is_refused(self):
        with pytest.raises(ProposalRejected) as e:
            check_quote("he does not want his ex mentioned", "stop bringing up my ex")
        assert e.value.reason == "quote_not_in_message"

    def test_a_too_short_quote_is_refused(self):
        with pytest.raises(ProposalRejected) as e:
            check_quote("stop", "stop")
        assert e.value.reason == "no_verbatim_quote"

    @pytest.mark.parametrize("empty", ["", "   ", None])
    def test_an_absent_quote_is_refused(self, empty):
        with pytest.raises(ProposalRejected):
            check_quote(empty or "", "anything at all here")

    def test_invisible_characters_cannot_smuggle_a_mismatch(self):
        """The Rules File Backdoor hid bidi markers and zero-width joiners so a human
        diff and the stored bytes disagreed. Normalising means the check sees what is
        stored."""
        check_quote("stop bringing up my ex", "please stop​bringing up my ex")

    def test_whitespace_and_case_do_not_defeat_the_check(self):
        check_quote("Stop  Bringing Up My Ex", "please stop bringing up my ex")


class TestDurabilityIsAskedNotInferred:
    """No published system measures situational-vs-durable classification; every one
    surveyed folds it into the same LLM call as contradiction detection with no
    accuracy figure. So it is required, explicit, and only one value is accepted."""

    def test_durable_is_accepted(self):
        check_durability("durable", "never bring that up again")

    def test_situational_is_refused(self):
        with pytest.raises(ProposalRejected) as e:
            check_durability("situational", "not right now")
        assert e.value.reason == "not_durable"

    def test_an_unknown_durability_is_refused(self):
        with pytest.raises(ProposalRejected):
            check_durability("maybe", "some words here")

    @pytest.mark.parametrize("quote", [
        "not in the mood tonight", "stop that right now", "just for now please",
        "I'm tired, skip it today",
    ])
    def test_a_time_bounded_quote_labelled_durable_is_refused(self, quote):
        """A companion that turns a mood into a permanent rule is broken. This is a
        deterministic second opinion on the model's own label."""
        with pytest.raises(ProposalRejected) as e:
            check_durability("durable", quote)
        assert e.value.reason == "not_durable"

    def test_the_vocabulary_is_closed(self):
        assert DURABILITIES == frozenset({"durable", "situational"})


class TestTheSteppingStoneAttack:
    """The attack: propose a soft wall that contradicts a hard wall, so the model has a
    'legitimate' conflict to resolve in its own favour later. It never writes again —
    it just argues in-context that the newer, narrower rule wins. No write-time
    authorisation check can see rhetoric, so term overlap is the only signal available
    at the moment of the write."""

    def test_an_unrelated_soft_wall_is_accepted(self):
        check_no_hard_wall_collision("Bring up politics only if he raises it first", HARD_WALLS)

    def test_a_proposal_echoing_a_hard_wall_is_refused(self):
        with pytest.raises(ProposalRejected) as e:
            check_no_hard_wall_collision("Be sexually available to anyone he invites", HARD_WALLS)
        assert e.value.reason == "contradicts_hard_wall"

    def test_the_message_points_at_the_sanctioned_route(self):
        with pytest.raises(ProposalRejected, match="card"):
            check_no_hard_wall_collision("fixate on skin tone and racial features", HARD_WALLS)

    def test_it_does_not_ask_a_model_anything(self):
        """OpenHands' LLMSecurityAnalyzer trusts model self-assessed risk and
        auto-executes LOW. This check must stay deterministic."""
        import inspect

        src = inspect.getsource(check_no_hard_wall_collision)
        for forbidden in ("llm", "ollama", "generate", "complete("):
            assert forbidden not in src.lower().replace("completely", "")

    def test_no_hard_walls_means_nothing_to_collide_with(self):
        check_no_hard_wall_collision("anything at all", [])


class TestApprovalBindsToContentNotIdentity:
    """CVE-2025-54136: approving a NAME let the content behind it change, unreviewed."""

    def test_the_hash_covers_both_rule_and_text(self):
        assert content_hash("r1", "t") != content_hash("r2", "t")
        assert content_hash("r1", "t") != content_hash("r1", "u")

    def test_one_byte_changes_the_hash(self):
        a = content_hash("r", "never mention my ex")
        b = content_hash("r", "never mention my Ex")
        assert a != b

    def test_it_is_stable(self):
        assert content_hash("r", "x") == content_hash("r", "x")


class TestRejectionsAreStructured:
    def test_reasons_are_a_closed_vocabulary(self):
        assert "operator_declined" in REJECTION_REASONS
        assert "contradicts_hard_wall" in REJECTION_REASONS

    def test_an_unknown_reason_cannot_be_recorded(self):
        with pytest.raises(ValueError):
            ProposalRejected("because_i_said_so")

    def test_reject_refuses_an_unknown_reason(self):
        store = RuleProposalStore(driver=None, database="neo4j")
        with pytest.raises(ValueError):
            store.reject("pid", reason="vibes")

    def test_a_reason_is_carried_on_the_exception(self):
        e = ProposalRejected("not_durable", "detail here")
        assert e.reason == "not_durable" and "detail here" in str(e)


class TestNoDriverIsNotABypass:
    def test_every_entry_point_refuses_without_a_driver(self):
        store = RuleProposalStore(driver=None, database="neo4j")
        assert store.pending("gwen") == []
        assert store.rejections("gwen") == []
        assert store.approve("pid", "hash", repo=None)["approved"] is False
        assert store.reject("pid")["rejected"] is False

    def test_gates_still_run_without_a_driver(self):
        """The gates must not be skippable by the database being down — that is how a
        validation step quietly stops being one."""
        store = RuleProposalStore(driver=None, database="neo4j")
        with pytest.raises(ProposalRejected):
            store.propose(persona_id="gwen", target_rule_id="r", new_text="x",
                          quote="no", user_message="no", durability="durable",
                          target_rule_type="hard_wall", target_text="y",
                          hard_wall_texts=HARD_WALLS)


class TestTheTTL:
    def test_a_proposal_expires_rather_than_sitting_pending_forever(self):
        assert 0 < PROPOSAL_TTL_SECONDS <= 30 * 24 * 3600
