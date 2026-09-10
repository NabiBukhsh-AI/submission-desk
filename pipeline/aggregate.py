"""The AGGREGATE node.

Zero I/O, zero model calls. This node reads the assessments the previous stage
produced, filters to the evidence whose quotations were actually located, and
hands the result to two pure functions that decide the band.

The whole architectural claim lives in that sentence. Everything a model
contributed stops at the boundary of this node; what leaves it is a number
computed by code a recruiter can read, with a derivation listing every rule that
fired.

The module imports nothing from infrastructure, and the architecture test
enforces it. That is not tidiness: it is what makes the scoring logic testable
without a network and reproducible on any machine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from application.deps import Deps
from domain.contracts.assessment import CriterionAssessment
from domain.contracts.enums import CriterionState, RunStatus
from domain.contracts.errors import ErrorRecord
from domain.contracts.run_state import DomainEvent, NodeResult, NodeStatus, RunState
from domain.rules.aggregate import AggregationFlags
from domain.rules.aggregate import aggregate as aggregate_rules
from domain.rules.resolve import CriterionResolution


def node(state: RunState, deps: Deps) -> NodeResult:
    """Turn assessed criteria into a recommendation."""
    rubric = state.rubric
    if rubric is None:
        return _failed(state, "No role definition was loaded for this run.", "NO_RUBRIC")

    assessments: tuple[CriterionAssessment, ...] = state.assessments
    if not assessments:
        return _failed(state, "No criteria were assessed for this candidate.", "NO_ASSESSMENTS")

    resolutions = [_resolution_from(assessment) for assessment in assessments]

    recommendation = aggregate_rules(
        resolutions,
        rubric,
        run_id=state.run_id,
        integrity=state.integrity_tier,
        flags=AggregationFlags(
            invalid_span_present=state.invalid_span_present,
            partial_profile=state.profile_partial,
            budget_capped=state.budget_capped,
        ),
    )

    deps.evidence.save_recommendation(state.run_id, recommendation)

    events = [
        DomainEvent(
            name="aggregate.recommendation",
            payload={
                "band": recommendation.band.value,
                "score": recommendation.score,
                "coverage": round(recommendation.coverage, 4),
                "blockers_fired": recommendation.blockers_fired,
                "requires_human": recommendation.requires_human,
                "reasons": recommendation.requires_human_reasons,
                "rules_fired": [step.rule_id for step in recommendation.derivation],
            },
        )
    ]

    return NodeResult(
        state=state.model_copy(update={"recommendation": recommendation}),
        status=NodeStatus.OK,
        events=tuple(events),
        next_status=RunStatus.AGGREGATED,
    )


def _resolution_from(assessment: CriterionAssessment) -> CriterionResolution:
    """Rebuild the rule engine's view of one criterion.

    The state was computed by ``resolve_criterion`` during assessment and is
    carried on the assessment. It is reconstructed rather than recomputed here
    because the evidence list on the assessment has already been filtered to
    what validated, and recomputing from the unfiltered list would quietly
    re-admit the items the span validator rejected.
    """
    return CriterionResolution(
        criterion_id=assessment.criterion_id,
        state=assessment.resolved_state,
        rule_id=assessment.resolution_rule_id,
        supported_count=sum(1 for item in assessment.evidence if item.state.value == "supported"),
        contradicted_count=sum(
            1 for item in assessment.evidence if item.state.value == "contradicted"
        ),
        requires_human=assessment.resolved_state is CriterionState.CONTRADICTED,
        unassessed_reason=assessment.unassessed_reason,
    )


def _failed(state: RunState, message: str, error_code: str) -> NodeResult:
    return NodeResult(
        state=state,
        status=NodeStatus.FAILED,
        events=(DomainEvent(name="aggregate.failed", payload={"reason": error_code}),),
        error=ErrorRecord(
            error_id=uuid4(),
            run_id=state.run_id,
            node="AGGREGATE",
            error_code=error_code,
            error_class="AggregateFailed",
            message_redacted=message,
            retryable=False,
            attempt=1,
            resulting_state=RunStatus.MANUAL_REVIEW_REQUIRED,
            occurred_at=datetime.now(UTC),
        ),
    )
