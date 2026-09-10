"""A run sitting in front of a reviewer, built without running the pipeline.

The review tests are about what happens after a run reaches a person, so they
start from that state directly. Driving a document through eleven nodes first
would make each of these tests four seconds slower and would couple them to
extraction, which they are not about.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from application.deps import Deps
from domain.contracts.assessment import CriterionAssessment
from domain.contracts.enums import (
    CriterionState,
    EscalationState,
    EvidenceState,
    ExtractionMethod,
    IntegrityTier,
    ModelTier,
    RunStatus,
    SpanValidation,
)
from domain.contracts.evidence import EvidenceItem
from domain.contracts.recommendation import Recommendation
from domain.contracts.run import RunRecord
from domain.contracts.source_text import Provenance
from domain.rules import AggregationFlags, aggregate
from domain.rules.resolve import CriterionResolution
from tests.builders import criterion, rubric

RUBRIC = rubric(
    criteria=[
        criterion(id="evaluation-practice", label="Measures quality before scaling", weight=5),
        criterion(id="production-engineering", label="Runs what they build", weight=4),
        criterion(id="python-depth", label="Substantial Python experience", weight=3),
        criterion(id="cost-awareness", label="Treats cost as a constraint", weight=2),
    ],
    min_coverage=0.5,
)

#: A candidate the system scored as advancing, so an override has somewhere to
#: move it in either direction.
DEFAULT_STATES: dict[str, CriterionState] = {
    "evaluation-practice": CriterionState.MET,
    "production-engineering": CriterionState.MET,
    "python-depth": CriterionState.MET,
    "cost-awareness": CriterionState.PARTIAL,
}

RULE_FOR: dict[CriterionState, str] = {
    CriterionState.MET: "R-MET",
    CriterionState.PARTIAL: "R-PARTIAL",
    CriterionState.NOT_MET: "R-NOT-MET",
    CriterionState.CONTRADICTED: "R-CONTRADICTED",
    CriterionState.INSUFFICIENT_EVIDENCE: "R-INSUFFICIENT",
}


def load_rubric(_role_id: str):
    return RUBRIC


#: One document, shared by every fixture. Provenance has to point somewhere,
#: and pointing every item at the same file is closer to a real submission than
#: minting a new document id per quotation.
DOCUMENT_ID = UUID("11111111-2222-3333-4444-555555555555")


def evidence_for(criterion_id: str, state: EvidenceState) -> EvidenceItem:
    """One item, with a located quotation where the contract requires one.

    Supported and contradicted evidence must cite a span — that is the headline
    invariant of the whole system — so these fixtures carry a real provenance
    rather than a validation exemption.
    """
    if state is EvidenceState.INSUFFICIENT_EVIDENCE:
        return EvidenceItem(
            evidence_id=uuid4(),
            criterion_id=criterion_id,
            state=state,
            claim=f"The documents do not address {criterion_id}.",
            confidence=0.9,
            model_tier=ModelTier.CHEAP,
            prompt_version="assessment/criterion@1",
            escalation_state=EscalationState.NOT_ESCALATED,
            span_validation=SpanValidation.NOT_APPLICABLE,
        )

    span = f"a sentence in the document about {criterion_id}"
    return EvidenceItem(
        evidence_id=uuid4(),
        criterion_id=criterion_id,
        state=state,
        claim=f"The document shows something about {criterion_id}.",
        verbatim_span=span,
        provenance=Provenance(
            document_id=DOCUMENT_ID,
            page_start=1,
            page_end=1,
            norm_start=0,
            norm_end=len(span),
            extraction_method=ExtractionMethod.DIGITAL_PDF,
            normalization_profile_id="np-v1-nfkc-ws-dash-hyphen",
        ),
        confidence=0.9,
        model_tier=ModelTier.CHEAP,
        prompt_version="assessment/criterion@1",
        escalation_state=EscalationState.NOT_ESCALATED,
        span_validation=SpanValidation.VALID_EXACT,
    )


def assessment_for(criterion_id: str, state: CriterionState) -> CriterionAssessment:
    supported = 2 if state is CriterionState.MET else 1 if state is CriterionState.PARTIAL else 0
    contradicted = 1 if state is CriterionState.CONTRADICTED else 0

    return CriterionAssessment(
        criterion_id=criterion_id,
        resolved_state=state,
        resolution_rule_id=RULE_FOR[state],
        evidence=[
            *(evidence_for(criterion_id, EvidenceState.SUPPORTED) for _ in range(supported)),
            *(evidence_for(criterion_id, EvidenceState.CONTRADICTED) for _ in range(contradicted)),
        ],
        rejected_evidence=[],
        tier_used=ModelTier.CHEAP,
    )


def recommendation_for(
    run_id: UUID, states: dict[str, CriterionState] | None = None
) -> Recommendation:
    resolved = states or DEFAULT_STATES
    return aggregate(
        [
            CriterionResolution(
                criterion_id=criterion_id,
                state=state,
                rule_id=RULE_FOR[state],
                supported_count=2 if state is CriterionState.MET else 0,
                contradicted_count=1 if state is CriterionState.CONTRADICTED else 0,
                requires_human=state is CriterionState.CONTRADICTED,
            )
            for criterion_id, state in resolved.items()
        ],
        RUBRIC,
        run_id=run_id,
        flags=AggregationFlags(),
    )


def seed_run(
    deps: Deps,
    *,
    status: RunStatus = RunStatus.READY_FOR_REVIEW,
    states: dict[str, CriterionState] | None = None,
    integrity: IntegrityTier = IntegrityTier.CLEAN,
) -> RunRecord:
    """A run in front of a reviewer, with assessments and a recommendation."""
    run_id = uuid4()
    resolved = states or DEFAULT_STATES
    recommendation = recommendation_for(run_id, resolved)

    record = RunRecord(
        run_id=run_id,
        content_key=f"key-{run_id}",
        candidate_id="cand-0007",
        role_id="ai-engineer",
        rubric_version=RUBRIC.version,
        rubric_hash="rubric-hash",
        prompt_bundle_hash="prompt-hash",
        model_tier_bindings_hash="bindings-hash",
        routing_policy_id="routed",
        blind_mode=False,
        calibration_enabled=False,
        calibration_status="disabled",
        pipeline_version="1",
        status=status,
        started_at=datetime.now(UTC),
        integrity_tier=integrity,
        final_band=recommendation.band,
    )
    deps.runs.create(record)

    for criterion_id, state in resolved.items():
        deps.evidence.save_assessment(run_id, assessment_for(criterion_id, state))
    deps.evidence.save_recommendation(run_id, recommendation)

    stored = deps.runs.get(run_id)
    assert stored is not None
    return stored


def wire(deps: Deps, **overrides: object) -> Deps:
    return Deps(**{**deps.__dict__, "rubric_loader": load_rubric, **overrides})
