"""The three routing policies.

Two are controls: ``all_cheap`` and ``all_strong`` bound quality and cost, and
``routed`` has to earn its place between them. Switching is one environment
variable, which is what makes the three-way comparison cheap enough to actually
run rather than describe.

The value types and the protocol live in ``domain/ports/routing.py``. These are
the implementations, and they are here because which one is in use is a
configuration choice the pipeline must not see.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.contracts.enums import ModelTier
from domain.contracts.routing import ModelRoutingDecision
from domain.ports.routing import (
    TRIGGER_BUDGET_BLOCKED,
    TRIGGER_CAPPED,
    TRIGGER_HIGH_STAKES,
    TRIGGER_INITIAL,
    TRIGGER_INVALID_SPAN,
    TRIGGER_LOW_CONFIDENCE,
    TRIGGER_REPAIR_FAILED,
    BudgetState,
    RoutingPolicy,
    RoutingRequest,
    RoutingThresholds,
    build_decision,
    escalation_state_for,
)


@dataclass(frozen=True)
class AllCheapPolicy:
    """Configuration A. The cheap tier, always, with escalation disabled.

    A control, and the lower bound on both cost and quality. If the routed
    policy cannot beat this on quality, routing is buying nothing.
    """

    policy_id: str = "all_cheap"
    thresholds: RoutingThresholds = field(default_factory=RoutingThresholds)

    def select(self, request: RoutingRequest) -> ModelRoutingDecision:
        return build_decision(
            request,
            policy_id=self.policy_id,
            selected=ModelTier.CHEAP,
            trigger=TRIGGER_INITIAL if request.attempt_index == 0 else TRIGGER_CAPPED,
        )

    def may_escalate(self, request: RoutingRequest) -> bool:
        return False


@dataclass(frozen=True)
class AllStrongPolicy:
    """Configuration B. The strong tier, always.

    The other control, and the upper bound on cost. If the routed policy matches
    this on quality for less money, that is the finding the comparison exists to
    produce.
    """

    policy_id: str = "all_strong"
    thresholds: RoutingThresholds = field(default_factory=RoutingThresholds)

    def select(self, request: RoutingRequest) -> ModelRoutingDecision:
        return build_decision(
            request,
            policy_id=self.policy_id,
            selected=ModelTier.STRONG,
            trigger=TRIGGER_INITIAL,
        )

    def may_escalate(self, request: RoutingRequest) -> bool:
        return False


@dataclass(frozen=True)
class RoutedPolicy:
    """Configuration C. Cheap by default, stronger where it is worth paying for.

    Triggers are evaluated in the order below and the first match wins. The
    order is the design: budget before cap, cap before difficulty, mechanical
    evidence before self-reported confidence.
    """

    policy_id: str = "routed"
    thresholds: RoutingThresholds = field(default_factory=RoutingThresholds)

    def select(self, request: RoutingRequest) -> ModelRoutingDecision:
        budget = request.budget
        limits = self.thresholds

        # Trigger 0: cost ceiling wins over quality.
        if request.attempt_index > 0 and self._budget_blocked(budget):
            return build_decision(
                request,
                policy_id=self.policy_id,
                selected=ModelTier.CHEAP,
                requested=ModelTier.STRONG,
                trigger=TRIGGER_BUDGET_BLOCKED,
            )

        # Trigger 1: bounded worst case per candidate.
        if (
            request.attempt_index > 0
            and budget.escalations_used >= limits.max_escalations_per_candidate
        ):
            return build_decision(
                request,
                policy_id=self.policy_id,
                selected=ModelTier.CHEAP,
                requested=ModelTier.STRONG,
                trigger=TRIGGER_CAPPED,
            )

        # Trigger 2: the criteria that decide the outcome start strong, because
        # a blocker read wrongly at the cheap tier costs a candidate their place.
        if request.attempt_index == 0 and request.criterion_kind in limits.start_strong_for:
            return build_decision(
                request,
                policy_id=self.policy_id,
                selected=ModelTier.STRONG,
                trigger=TRIGGER_HIGH_STAKES,
            )

        if request.attempt_index > 0:
            # Trigger 3: a span that could not be located is mechanical evidence
            # of fabrication, ranked above anything the model says about itself.
            if limits.escalate_on_invalid_span and (request.prior_invalid_span_count or 0) > 0:
                return build_decision(
                    request,
                    policy_id=self.policy_id,
                    selected=ModelTier.STRONG,
                    requested=ModelTier.CHEAP,
                    trigger=TRIGGER_INVALID_SPAN,
                )

            # Trigger 4: kept because the brief asks for it, ranked below the
            # span signal because self-reported confidence is weakly calibrated.
            if (
                request.prior_confidence is not None
                and request.prior_confidence < limits.escalate_confidence_below
            ):
                return build_decision(
                    request,
                    policy_id=self.policy_id,
                    selected=ModelTier.STRONG,
                    requested=ModelTier.CHEAP,
                    trigger=TRIGGER_LOW_CONFIDENCE,
                )

            # Trigger 5: schema trouble twice usually means the cheap tier is out
            # of its depth on this document.
            if limits.escalate_on_repair_failure and request.prior_repair_failed:
                return build_decision(
                    request,
                    policy_id=self.policy_id,
                    selected=ModelTier.STRONG,
                    requested=ModelTier.CHEAP,
                    trigger=TRIGGER_REPAIR_FAILED,
                )

        # Trigger 6: the default.
        return build_decision(
            request,
            policy_id=self.policy_id,
            selected=ModelTier.CHEAP,
            trigger=TRIGGER_INITIAL,
        )

    def may_escalate(self, request: RoutingRequest) -> bool:
        """Whether a second attempt at a stronger tier is worth requesting."""
        decision = self.select(
            RoutingRequest(
                call_site=request.call_site,
                criterion_id=request.criterion_id,
                criterion_kind=request.criterion_kind,
                attempt_index=1,
                prior_confidence=request.prior_confidence,
                prior_invalid_span_count=request.prior_invalid_span_count,
                prior_repair_failed=request.prior_repair_failed,
                budget=request.budget,
            )
        )
        return decision.selected_tier is ModelTier.STRONG

    def _budget_blocked(self, budget: BudgetState) -> bool:
        """Whether spending more is refused.

        The token headroom check works whether or not pricing is configured,
        which matters: the ceiling has to hold on a system where nobody has
        entered a price yet, and that is the shipped state.
        """
        if budget.token_headroom < self.thresholds.token_ceiling_headroom:
            return True
        floor = self.thresholds.escalation_budget_floor_usd
        return (
            floor is not None and budget.remaining_usd is not None and budget.remaining_usd < floor
        )


POLICIES: dict[str, type] = {
    "all_cheap": AllCheapPolicy,
    "all_strong": AllStrongPolicy,
    "routed": RoutedPolicy,
}


def policy_for(policy_id: str, thresholds: RoutingThresholds | None = None) -> RoutingPolicy:
    """Build a policy by name, or say which names exist."""
    try:
        cls = POLICIES[policy_id]
    except KeyError:
        raise KeyError(
            f"{policy_id!r} is not a routing policy. Known: {', '.join(sorted(POLICIES))}"
        ) from None
    built: RoutingPolicy = cls(thresholds=thresholds or RoutingThresholds())
    return built


#: Re-exported so a caller holding a policy can map its trigger without
#: reaching past it into the port.
__all__ = [
    "POLICIES",
    "AllCheapPolicy",
    "AllStrongPolicy",
    "RoutedPolicy",
    "escalation_state_for",
    "policy_for",
]
