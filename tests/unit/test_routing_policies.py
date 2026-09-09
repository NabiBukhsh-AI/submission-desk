"""Three policies, and every trigger in the routed one.

Two of the three are controls. ``all_cheap`` bounds quality and cost from below,
``all_strong`` from above, and ``routed`` has to earn its place between them. The
comparison is the deliverable, so the policies must be genuinely different and
each trigger must be individually attributable in the trace.

The two orderings tested hardest are the ones that were decided rather than
inherited: budget beats quality, and a failed span beats low confidence.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from domain.contracts.enums import CriterionKind, EscalationState, ModelTier
from infrastructure.models.routing.policies import (
    TRIGGER_BUDGET_BLOCKED,
    TRIGGER_CAPPED,
    TRIGGER_HIGH_STAKES,
    TRIGGER_INITIAL,
    TRIGGER_INVALID_SPAN,
    TRIGGER_LOW_CONFIDENCE,
    TRIGGER_REPAIR_FAILED,
    AllCheapPolicy,
    AllStrongPolicy,
    BudgetState,
    RoutedPolicy,
    RoutingRequest,
    RoutingThresholds,
    escalation_state_for,
    policy_for,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def request(**overrides: object) -> RoutingRequest:
    fields: dict[str, object] = {
        "call_site": "assess.criterion",
        "criterion_id": "evaluation-practice",
        "criterion_kind": CriterionKind.STANDARD,
        "attempt_index": 0,
    }
    fields.update(overrides)
    return RoutingRequest(**fields)  # type: ignore[arg-type]


# --- the two controls ---------------------------------------------------------------


def test_all_cheap_never_uses_the_strong_tier() -> None:
    """The lower bound. If routing cannot beat this on quality, it buys nothing."""
    policy = AllCheapPolicy()

    for kind in CriterionKind:
        decision = policy.select(request(criterion_kind=kind))
        assert decision.selected_tier is ModelTier.CHEAP


def test_all_cheap_disables_escalation_entirely() -> None:
    assert AllCheapPolicy().may_escalate(request(prior_confidence=0.1)) is False


def test_all_strong_always_uses_the_strong_tier() -> None:
    """The upper bound on cost. If routed matches it for less, that is the
    finding the comparison exists to produce."""
    policy = AllStrongPolicy()

    for kind in CriterionKind:
        assert policy.select(request(criterion_kind=kind)).selected_tier is ModelTier.STRONG


def test_the_three_policies_disagree_on_the_same_input() -> None:
    """Three distinct traces on identical input, or the comparison is measuring
    nothing."""
    high_stakes = request(criterion_kind=CriterionKind.HIGH_STAKES)

    tiers = {
        policy.policy_id: policy.select(high_stakes).selected_tier
        for policy in (AllCheapPolicy(), AllStrongPolicy(), RoutedPolicy())
    }

    assert tiers == {
        "all_cheap": ModelTier.CHEAP,
        "all_strong": ModelTier.STRONG,
        "routed": ModelTier.STRONG,
    }


def test_the_policies_differ_on_an_ordinary_criterion() -> None:
    ordinary = request(criterion_kind=CriterionKind.STANDARD)

    assert AllCheapPolicy().select(ordinary).selected_tier is ModelTier.CHEAP
    assert AllStrongPolicy().select(ordinary).selected_tier is ModelTier.STRONG
    assert RoutedPolicy().select(ordinary).selected_tier is ModelTier.CHEAP


# --- trigger 0: budget beats quality ----------------------------------------------------


def test_a_run_near_its_token_ceiling_does_not_escalate() -> None:
    """Cost wins. A criterion that cannot be assessed within budget becomes
    insufficient evidence, which is honest; an unbudgeted escalation is a
    surprise on an invoice."""
    nearly_spent = BudgetState(tokens_used=95_000, token_ceiling=100_000)

    decision = RoutedPolicy().select(
        request(attempt_index=1, prior_confidence=0.1, budget=nearly_spent)
    )

    assert decision.selected_tier is ModelTier.CHEAP
    assert decision.trigger == TRIGGER_BUDGET_BLOCKED


def test_the_ceiling_holds_when_pricing_is_unconfigured() -> None:
    """The shipped state. The token headroom check works with no prices at all,
    which is what makes the ceiling real rather than aspirational."""
    nearly_spent = BudgetState(tokens_used=90_000, token_ceiling=100_000, remaining_usd=None)

    decision = RoutedPolicy().select(
        request(attempt_index=1, prior_invalid_span_count=3, budget=nearly_spent)
    )

    assert decision.trigger == TRIGGER_BUDGET_BLOCKED


def test_a_configured_money_floor_also_blocks() -> None:
    policy = RoutedPolicy(thresholds=RoutingThresholds(escalation_budget_floor_usd=Decimal("0.50")))
    low = BudgetState(tokens_used=10, token_ceiling=100_000, remaining_usd=Decimal("0.10"))

    decision = policy.select(request(attempt_index=1, prior_confidence=0.1, budget=low))

    assert decision.trigger == TRIGGER_BUDGET_BLOCKED


def test_the_budget_block_records_what_was_wanted() -> None:
    """requested and selected differ exactly when a policy intervened, which is
    the whole evidence base for the routing table."""
    nearly_spent = BudgetState(tokens_used=99_000, token_ceiling=100_000)

    decision = RoutedPolicy().select(
        request(attempt_index=1, prior_confidence=0.1, budget=nearly_spent)
    )

    assert decision.requested_tier is ModelTier.STRONG
    assert decision.selected_tier is ModelTier.CHEAP


# --- trigger 1: the cap -------------------------------------------------------------------


def test_escalations_are_capped_per_candidate() -> None:
    """Bounded worst case: one hard document cannot cost several times what a
    normal one does."""
    spent = BudgetState(escalations_used=3, token_ceiling=100_000)

    decision = RoutedPolicy().select(
        request(attempt_index=1, prior_invalid_span_count=2, budget=spent)
    )

    assert decision.trigger == TRIGGER_CAPPED
    assert decision.selected_tier is ModelTier.CHEAP


def test_below_the_cap_escalation_still_happens() -> None:
    almost = BudgetState(escalations_used=2, token_ceiling=100_000)

    decision = RoutedPolicy().select(
        request(attempt_index=1, prior_invalid_span_count=2, budget=almost)
    )

    assert decision.selected_tier is ModelTier.STRONG


def test_the_budget_block_outranks_the_cap() -> None:
    """Both apply; the message should be about money, because that is the one
    the operator can act on."""
    both = BudgetState(tokens_used=99_000, token_ceiling=100_000, escalations_used=5)

    decision = RoutedPolicy().select(request(attempt_index=1, prior_confidence=0.1, budget=both))

    assert decision.trigger == TRIGGER_BUDGET_BLOCKED


# --- trigger 2: the criteria that decide the outcome ---------------------------------------


@pytest.mark.parametrize("kind", [CriterionKind.HIGH_STAKES, CriterionKind.BLOCKER])
def test_deciding_criteria_start_strong(kind: CriterionKind) -> None:
    """From the outset, not as a repair. A blocker read wrongly at the cheap tier
    costs a candidate their place."""
    decision = RoutedPolicy().select(request(criterion_kind=kind))

    assert decision.selected_tier is ModelTier.STRONG
    assert decision.trigger == TRIGGER_HIGH_STAKES


def test_an_ordinary_criterion_starts_cheap() -> None:
    decision = RoutedPolicy().select(request(criterion_kind=CriterionKind.STANDARD))

    assert decision.selected_tier is ModelTier.CHEAP
    assert decision.trigger == TRIGGER_INITIAL


# --- triggers 3 to 5, and the ordering between them -----------------------------------------


def test_a_failed_span_escalates() -> None:
    """Mechanical evidence that the cheap tier is fabricating."""
    decision = RoutedPolicy().select(request(attempt_index=1, prior_invalid_span_count=1))

    assert decision.selected_tier is ModelTier.STRONG
    assert decision.trigger == TRIGGER_INVALID_SPAN


def test_low_confidence_escalates() -> None:
    decision = RoutedPolicy().select(request(attempt_index=1, prior_confidence=0.4))

    assert decision.trigger == TRIGGER_LOW_CONFIDENCE


def test_a_failed_span_outranks_low_confidence() -> None:
    """The ordering that was decided rather than inherited. A model is often
    confidently wrong and rarely uncertain about a right answer, so a
    non-self-reported signal wins."""
    decision = RoutedPolicy().select(
        request(attempt_index=1, prior_invalid_span_count=1, prior_confidence=0.4)
    )

    assert decision.trigger == TRIGGER_INVALID_SPAN


def test_high_confidence_does_not_escalate() -> None:
    decision = RoutedPolicy().select(request(attempt_index=1, prior_confidence=0.95))

    assert decision.selected_tier is ModelTier.CHEAP


def test_the_confidence_threshold_is_configurable() -> None:
    strict = RoutedPolicy(thresholds=RoutingThresholds(escalate_confidence_below=0.99))

    decision = strict.select(request(attempt_index=1, prior_confidence=0.95))

    assert decision.trigger == TRIGGER_LOW_CONFIDENCE


def test_a_repair_failure_escalates() -> None:
    """Schema trouble twice usually means the cheap tier is out of its depth."""
    decision = RoutedPolicy().select(request(attempt_index=1, prior_repair_failed=True))

    assert decision.trigger == TRIGGER_REPAIR_FAILED


def test_a_first_attempt_never_escalates_on_a_prior_signal() -> None:
    """There is no prior attempt to have a signal from. A first call that read
    the triggers would escalate on stale state from another criterion."""
    decision = RoutedPolicy().select(
        request(attempt_index=0, prior_confidence=0.1, prior_invalid_span_count=5)
    )

    assert decision.selected_tier is ModelTier.CHEAP
    assert decision.trigger == TRIGGER_INITIAL


# --- what the trace records ------------------------------------------------------------------


def test_every_decision_names_its_policy_and_trigger() -> None:
    """The routing table is grouped by these, so a decision without them is a
    row nobody can attribute."""
    for policy in (AllCheapPolicy(), AllStrongPolicy(), RoutedPolicy()):
        decision = policy.select(request())
        assert decision.policy_id == policy.policy_id
        assert decision.trigger


def test_a_decision_records_the_attempt_it_belongs_to() -> None:
    decision = RoutedPolicy().select(request(attempt_index=1, prior_confidence=0.2))

    assert decision.attempt_index == 1


def test_routing_does_not_depend_on_who_is_being_assessed() -> None:
    """A policy that could see the candidate could route differently for
    different people, which is exactly what the fairness work exists to
    prevent."""
    import inspect

    fields = set(inspect.signature(RoutingRequest).parameters)

    assert "candidate_id" not in fields
    assert "profile" not in fields
    assert "document" not in fields


@pytest.mark.parametrize(
    ("trigger", "expected"),
    [
        (TRIGGER_BUDGET_BLOCKED, EscalationState.ESCALATION_BUDGET_BLOCKED),
        (TRIGGER_CAPPED, EscalationState.ESCALATION_CAPPED),
        (TRIGGER_INVALID_SPAN, EscalationState.ESCALATED),
        (TRIGGER_LOW_CONFIDENCE, EscalationState.ESCALATED),
        (TRIGGER_REPAIR_FAILED, EscalationState.ESCALATED),
        (TRIGGER_INITIAL, EscalationState.NOT_ESCALATED),
        (TRIGGER_HIGH_STAKES, EscalationState.NOT_ESCALATED),
    ],
)
def test_the_trigger_maps_to_an_escalation_state(trigger: str, expected: EscalationState) -> None:
    """The state lands on the evidence, so the evaluation can group by why an
    escalation did or did not happen rather than only by whether it did."""
    assert escalation_state_for(trigger) is expected


# --- selection by name and the shipped configuration -------------------------------------------


@pytest.mark.parametrize("policy_id", ["all_cheap", "all_strong", "routed"])
def test_a_policy_can_be_chosen_by_name(policy_id: str) -> None:
    """Switching is one environment variable, which is what makes the three-way
    comparison cheap enough to run rather than describe."""
    assert policy_for(policy_id).policy_id == policy_id


def test_an_unknown_policy_says_what_is_known() -> None:
    with pytest.raises(KeyError, match="not a routing policy"):
        policy_for("adaptive")


def test_the_shipped_configuration_lists_all_three() -> None:
    config = yaml.safe_load((REPO_ROOT / "config" / "routing.yaml").read_text(encoding="utf-8"))

    assert set(config["policies"]) == {"all_cheap", "all_strong", "routed"}
    assert config["default"] == "routed"


def test_the_shipped_thresholds_match_the_defaults() -> None:
    """The file a person edits and the values the code uses must agree."""
    config = yaml.safe_load((REPO_ROOT / "config" / "routing.yaml").read_text(encoding="utf-8"))
    routed = config["routed"]
    defaults = RoutingThresholds()

    assert routed["max_escalations_per_candidate"] == defaults.max_escalations_per_candidate
    assert routed["escalate_confidence_below"] == defaults.escalate_confidence_below
    assert routed["token_ceiling_headroom"] == defaults.token_ceiling_headroom


def test_the_shipped_money_floor_is_unset() -> None:
    """Consistent with pricing shipping empty: a floor in dollars means nothing
    until somebody enters prices."""
    config = yaml.safe_load((REPO_ROOT / "config" / "routing.yaml").read_text(encoding="utf-8"))

    assert config["routed"]["escalation_budget_floor_usd"] is None
