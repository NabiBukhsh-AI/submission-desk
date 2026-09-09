"""Valid objects to start from.

Each builder returns something that passes every validator, so a test that wants
to prove one rejection changes one field and nothing else. Without this, a test
asserting "a blocker with min_supported 0 is rejected" would also be asserting
that twelve unrelated fields are still spelled correctly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from domain.contracts import (
    Band,
    BandThreshold,
    CandidateDocument,
    Criterion,
    CriterionKind,
    CriterionState,
    DocumentRole,
    EscalationState,
    EvidenceItem,
    EvidenceState,
    ExtractionMethod,
    ModelTier,
    Provenance,
    RoleRubric,
    SpanValidation,
)

SHA = "a" * 64
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
PROFILE_ID = "np-v1-nfkc-ws-dash-hyphen"

FULL_POINTS: dict[CriterionState, float | None] = {
    CriterionState.MET: 1.0,
    CriterionState.PARTIAL: 0.5,
    CriterionState.NOT_MET: 0.0,
    CriterionState.CONTRADICTED: 0.0,
}


def criterion(**overrides: Any) -> Criterion:
    fields: dict[str, Any] = {
        "id": "evaluation-practice",
        "label": "Builds evaluation before scaling",
        "question": "Does the document show measurement of system quality?",
        "kind": CriterionKind.STANDARD,
        "weight": 4,
        "state_points": dict(FULL_POINTS),
    }
    fields.update(overrides)
    return Criterion(**fields)


def rubric(**overrides: Any) -> RoleRubric:
    fields: dict[str, Any] = {
        "role_id": "ai-engineer",
        "role_title": "AI Engineer",
        "version": "1.2.0",
        "criteria": [criterion()],
        "bands": [
            BandThreshold(band=Band.ADVANCE, min_score=0.75),
            BandThreshold(band=Band.HOLD, min_score=0.40),
            BandThreshold(band=Band.DECLINE, min_score=0.0),
        ],
    }
    fields.update(overrides)
    return RoleRubric(**fields)


def provenance(document_id: UUID | None = None, **overrides: Any) -> Provenance:
    fields: dict[str, Any] = {
        "document_id": document_id or uuid4(),
        "page_start": 1,
        "page_end": 1,
        "norm_start": 100,
        "norm_end": 180,
        "extraction_method": ExtractionMethod.DIGITAL_PDF,
        "normalization_profile_id": PROFILE_ID,
    }
    fields.update(overrides)
    return Provenance(**fields)


def evidence(**overrides: Any) -> EvidenceItem:
    fields: dict[str, Any] = {
        "evidence_id": uuid4(),
        "criterion_id": "evaluation-practice",
        "state": EvidenceState.SUPPORTED,
        "claim": "Built a regression suite that gated releases on measured quality.",
        "verbatim_span": "introduced a nightly evaluation suite; releases blocked below 0.8",
        "provenance": provenance(),
        "confidence": 0.82,
        "model_tier": ModelTier.CHEAP,
        "prompt_version": "assessment/criterion@3",
        "escalation_state": EscalationState.NOT_ESCALATED,
        "span_validation": SpanValidation.VALID_EXACT,
    }
    fields.update(overrides)
    return EvidenceItem(**fields)


def insufficient_evidence(**overrides: Any) -> EvidenceItem:
    fields: dict[str, Any] = {
        "state": EvidenceState.INSUFFICIENT_EVIDENCE,
        "verbatim_span": None,
        "provenance": None,
        "span_validation": SpanValidation.NOT_APPLICABLE,
        "claim": "The document does not address evaluation practice anywhere.",
    }
    fields.update(overrides)
    return evidence(**fields)


def document(**overrides: Any) -> CandidateDocument:
    fields: dict[str, Any] = {
        "document_id": uuid4(),
        "candidate_id": "cand-0007",
        "original_filename": "resume.pdf",
        "document_sha256": SHA,
        "mime_type": "application/pdf",
        "size_bytes": 84_213,
        "page_count": 2,
        "blob_path": f"data/blobs/{SHA[:2]}/{SHA}",
        "doc_role": DocumentRole.CV,
        "received_at": NOW,
    }
    fields.update(overrides)
    return CandidateDocument(**fields)
