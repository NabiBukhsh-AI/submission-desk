"""What a routing decision depends on, and the shape of the thing that makes one.

The values here are pure: a request describes a criterion's difficulty and a
run's remaining budget, and nothing else. The concrete policies live in
``infrastructure/models/routing/``, because swapping one is a configuration
choice and the pipeline must not know which is in use.

What is deliberately absent from a routing request is as important as what is
present. There is no candidate id, no profile, and no document text, so a
policy cannot route differently for different people, which is exactly what the
fairness work exists to prevent.

Original note on the three policies:

Three policies. Two are controls: ``all_cheap`` and ``all_strong`` bound the
quality and the cost, and ``routed`` has to earn its place between them.
Switching is one environment variable, which is what makes the three-way
comparison cheap enough to actually run rather than describe.

The architecture does not assume routing helps. Every escalation records the
criterion state before and after it, so the evaluation can report per trigger
how often escalating changed an answer and what each changed answer cost. A
trigger that changes nothing across the benchmark is a finding, and switching it
off is a result worth writing down.

Two orderings in ``routed`` are deliberate and worth reading twice.

Cost beats quality. A criterion that cannot be assessed within budget becomes
insufficient evidence, which is honest. An unbudgeted escalation is a surprise
on an invoice.

A failed span beats low confidence. A quotation that could not be found in the
document is mechanical evidence that the cheap tier is fabricating.
Self-reported confidence is weakly calibrated: a model is often confidently
wrong and rarely uncertain about a right answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from domain.contracts.enums import CriterionKind, EscalationState, ModelTier
from domain.contracts.routing import ModelRoutingDecision

#: Trigger names, as they land in the decision record and the evaluation table.
TRIGGER_INITIAL = "initial"
TRIGGER_HIGH_STAKES = "high_stakes"
TRIGGER_INVALID_SPAN = "invalid_span"
TRIGGER_LOW_CONFIDENCE = "low_confidence"
TRIGGER_REPAIR_FAILED = "repair_failed"
TRIGGER_BUDGET_BLOCKED = "budget_blocked"
TRIGGER_CAPPED = "escalation_capped"


@dataclass(frozen=True)
class BudgetState:
    """What the run has spent and what remains, as the router sees it."""

    tokens_used: int = 0
    token_ceiling: int = 120_000
    remaining_usd: Decimal | None = None
    escalations_used: int = 0

    @property
    def token_headroom(self) -> float:
        """Fraction of the ceiling still available."""
        if self.token_ceiling <= 0:
            return 0.0
        return max(0.0, 1.0 - self.tokens_used / self.token_ceiling)


@dataclass(frozen=True)
class RoutingRequest:
    """Everything a routing decision may depend on.

    Notably absent: the criterion's text, the candidate, and any prior
    criterion's result. Routing is about difficulty and budget, not about who is
    being assessed.
    """

    call_site: str
    criterion_id: str | None = None
    criterion_kind: CriterionKind | None = None
    attempt_index: int = 0
    prior_confidence: float | None = None
    prior_invalid_span_count: int | None = None
    prior_repair_failed: bool = False
    budget: BudgetState = field(default_factory=BudgetState)


@dataclass(frozen=True)
class RoutingThresholds:
    """From config/routing.yaml. Starting values; the sweep that would tune them
    is `make tune-thresholds` for spans and the routing experiment for the rest."""

    max_escalations_per_candidate: int = 3
    token_ceiling_headroom: float = 0.15
    escalation_budget_floor_usd: Decimal | None = None
    start_strong_for: tuple[CriterionKind, ...] = (
        CriterionKind.HIGH_STAKES,
        CriterionKind.BLOCKER,
    )
    escalate_on_invalid_span: bool = True
    escalate_confidence_below: float = 0.60
    escalate_on_repair_failure: bool = True


class RoutingPolicy(Protocol):
    """Chooses a tier and says why."""

    policy_id: str

    def select(self, request: RoutingRequest) -> ModelRoutingDecision: ...


def build_decision(
    request: RoutingRequest,
    *,
    policy_id: str,
    selected: ModelTier,
    trigger: str,
    requested: ModelTier | None = None,
) -> ModelRoutingDecision:
    return ModelRoutingDecision(
        call_site=request.call_site,
        criterion_id=request.criterion_id,
        attempt_index=request.attempt_index,
        requested_tier=requested or selected,
        selected_tier=selected,
        policy_id=policy_id,
        trigger=trigger,
        budget_remaining_usd=request.budget.remaining_usd,
        decided_at=datetime.now(UTC),
    )


#: Escalation states, from a decision's trigger. The state lands on the evidence
#: item so the evaluation can group by why an escalation did or did not happen.
def escalation_state_for(trigger: str) -> EscalationState:
    if trigger == TRIGGER_BUDGET_BLOCKED:
        return EscalationState.ESCALATION_BUDGET_BLOCKED
    if trigger == TRIGGER_CAPPED:
        return EscalationState.ESCALATION_CAPPED
    if trigger in (TRIGGER_INVALID_SPAN, TRIGGER_LOW_CONFIDENCE, TRIGGER_REPAIR_FAILED):
        return EscalationState.ESCALATED
    return EscalationState.NOT_ESCALATED
