"""Public contract surface.

Import from ``domain.contracts``, never from a submodule path. The re-exports
here are the supported names; anything else is an implementation detail that may
move between modules without notice.
"""

from __future__ import annotations

from domain.contracts.assessment import CriterionAssessment
from domain.contracts.base import Contract
from domain.contracts.calibration import CalibrationCard
from domain.contracts.cost import CostRecord
from domain.contracts.delivery import DeliveryRecord
from domain.contracts.documents import CandidateDocument
from domain.contracts.enums import (
    VALID_SPAN_VALIDATIONS,
    Band,
    CriterionKind,
    CriterionState,
    DeliveryStatus,
    DocumentRole,
    EscalationState,
    EvidenceState,
    ExtractionMethod,
    IntegrityFindingKind,
    IntegrityTier,
    ModelTier,
    OverrideReason,
    ReviewAction,
    RunStatus,
    Severity,
    SpanValidation,
)
from domain.contracts.errors import ErrorRecord
from domain.contracts.evaluation import EvaluationCase, EvaluationResult, GoldLabel
from domain.contracts.evidence import EvidenceItem
from domain.contracts.integrity import IntegrityFinding, IntegrityReport
from domain.contracts.profile import CandidateProfile, EmploymentEntry, ProvenancedField
from domain.contracts.recommendation import SCORELESS_BANDS, DerivationStep, Recommendation
from domain.contracts.responses import (
    AssessmentResponse,
    ComposedQuestion,
    ComposedRequest,
    CompositionResponse,
    EmploymentResponse,
    EvidenceCandidate,
    InjectionVerdict,
    ProfileResponse,
    Quoted,
)
from domain.contracts.review import Override, ReviewDecision
from domain.contracts.routing import ModelRoutingDecision
from domain.contracts.rubric import (
    DEFAULT_FORBIDDEN_ATTRIBUTES,
    GATE_BANDS,
    SCORING_STATES,
    BandThreshold,
    Criterion,
    RoleRubric,
)
from domain.contracts.run import RunRecord
from domain.contracts.source_text import OffsetRun, PageSpan, Provenance, SourceText

#: Every model exported as JSON Schema by ``make schemas``, in export order.
EXPORTED_MODELS: tuple[type[Contract], ...] = (
    AssessmentResponse,
    BandThreshold,
    CalibrationCard,
    CandidateDocument,
    CandidateProfile,
    ComposedQuestion,
    ComposedRequest,
    CompositionResponse,
    CostRecord,
    Criterion,
    CriterionAssessment,
    DeliveryRecord,
    DerivationStep,
    EmploymentEntry,
    ErrorRecord,
    EvaluationCase,
    EvaluationResult,
    EvidenceCandidate,
    EvidenceItem,
    GoldLabel,
    InjectionVerdict,
    IntegrityFinding,
    IntegrityReport,
    ModelRoutingDecision,
    OffsetRun,
    Override,
    PageSpan,
    ProfileResponse,
    Provenance,
    Recommendation,
    ReviewDecision,
    RoleRubric,
    RunRecord,
    SourceText,
)

#: The schemas a language model fills. Nothing else may be sent to a provider
#: as a response format.
RESPONSE_MODELS: tuple[type[Contract], ...] = (
    AssessmentResponse,
    CompositionResponse,
    InjectionVerdict,
    ProfileResponse,
)

__all__ = [
    "DEFAULT_FORBIDDEN_ATTRIBUTES",
    "EXPORTED_MODELS",
    "GATE_BANDS",
    "RESPONSE_MODELS",
    "SCORELESS_BANDS",
    "SCORING_STATES",
    "VALID_SPAN_VALIDATIONS",
    "AssessmentResponse",
    "Band",
    "BandThreshold",
    "CalibrationCard",
    "CandidateDocument",
    "CandidateProfile",
    "ComposedQuestion",
    "ComposedRequest",
    "CompositionResponse",
    "Contract",
    "CostRecord",
    "Criterion",
    "CriterionAssessment",
    "CriterionKind",
    "CriterionState",
    "DeliveryRecord",
    "DeliveryStatus",
    "DerivationStep",
    "DocumentRole",
    "EmploymentEntry",
    "EmploymentResponse",
    "ErrorRecord",
    "EscalationState",
    "EvaluationCase",
    "EvaluationResult",
    "EvidenceCandidate",
    "EvidenceItem",
    "EvidenceState",
    "ExtractionMethod",
    "GoldLabel",
    "InjectionVerdict",
    "IntegrityFinding",
    "IntegrityFindingKind",
    "IntegrityReport",
    "IntegrityTier",
    "ModelRoutingDecision",
    "ModelTier",
    "OffsetRun",
    "Override",
    "OverrideReason",
    "PageSpan",
    "ProfileResponse",
    "Provenance",
    "ProvenancedField",
    "Quoted",
    "Recommendation",
    "ReviewAction",
    "ReviewDecision",
    "RoleRubric",
    "RunRecord",
    "RunStatus",
    "Severity",
    "SourceText",
    "SpanValidation",
]
