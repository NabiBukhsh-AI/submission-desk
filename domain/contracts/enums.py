"""Every enumeration in the system.

All are ``(str, Enum)`` so they serialise readably into JSONL and SQLite, and so
a stored value can be read by a person opening the database with any browser.
"""

from __future__ import annotations

from enum import Enum


class EvidenceState(str, Enum):
    """What one piece of evidence says about a criterion.

    There is no "probably" and no numeric grade. Absence of evidence is its own
    state rather than a low score, because "the document does not say" and "the
    document says no" are different facts about a candidate.
    """

    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class CriterionState(str, Enum):
    """What the rule engine concluded for a criterion, from its evidence."""

    MET = "met"
    PARTIAL = "partial"
    NOT_MET = "not_met"
    CONTRADICTED = "contradicted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Band(str, Enum):
    """The recommendation. Computed by ``aggregate``, never emitted by a model."""

    ADVANCE = "advance"
    ADVANCE_WITH_RESERVATIONS = "advance_with_reservations"
    HOLD = "hold"
    DECLINE = "decline"
    # Not score-derived: these two are produced by gates, so they carry no score.
    INSUFFICIENT_INFORMATION = "insufficient_information"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class SpanValidation(str, Enum):
    """The verdict of the mechanical check that a quoted span exists.

    Assigned only by the span validator. No model ever fills this in.
    """

    VALID_EXACT = "valid_exact"
    VALID_NORMALIZED = "valid_normalized"
    VALID_FUZZY_OCR = "valid_fuzzy_ocr"
    INVALID_NOT_FOUND = "invalid_not_found"
    INVALID_OFFSET_MISMATCH = "invalid_offset_mismatch"
    INVALID_WRONG_DOCUMENT = "invalid_wrong_document"
    NOT_APPLICABLE = "not_applicable"


#: The only span verdicts that may reach the rule engine.
VALID_SPAN_VALIDATIONS = frozenset(
    {
        SpanValidation.VALID_EXACT,
        SpanValidation.VALID_NORMALIZED,
        SpanValidation.VALID_FUZZY_OCR,
    }
)


class ModelTier(str, Enum):
    """Model identity by role, never by provider name.

    The binding from tier to a provider model identifier lives in
    ``config/models.yaml`` and nowhere else, so a model swap is a configuration
    change and no report, chart, or log line has to be rewritten.
    """

    CHEAP = "tier_cheap"
    STRONG = "tier_strong"


class EscalationState(str, Enum):
    NOT_ESCALATED = "not_escalated"
    ESCALATED = "escalated"
    ESCALATION_CAPPED = "escalation_capped"
    ESCALATION_BUDGET_BLOCKED = "escalation_budget_blocked"


class IntegrityTier(str, Enum):
    CLEAN = "clean"
    SUSPECT = "suspect"
    QUARANTINE = "quarantine"


class CriterionKind(str, Enum):
    STANDARD = "standard"
    HIGH_STAKES = "high_stakes"
    BLOCKER = "blocker"


class ExtractionMethod(str, Enum):
    DIGITAL_PDF = "digital_pdf"
    OCR = "ocr"
    DOCX = "docx"
    PLAINTEXT = "plaintext"


class ReviewAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_INFO = "request_info"


class OverrideReason(str, Enum):
    """Why a reviewer disagreed.

    This taxonomy is what turns an override from a complaint into an
    improvement loop: each value routes to a different remediation.
    ``EVIDENCE_MISSED`` and ``SPAN_WRONG`` point at prompts. ``RUBRIC_WRONG``
    and ``THRESHOLD_WRONG`` point at the rubric. ``CONTEXT_MODEL_LACKS`` points
    at a genuine capability limit that belongs in LIMITATIONS.md.
    """

    EVIDENCE_MISREAD = "evidence_misread"
    EVIDENCE_MISSED = "evidence_missed"
    SPAN_WRONG = "span_wrong"
    CRITERION_AMBIGUOUS = "criterion_ambiguous"
    RUBRIC_WRONG = "rubric_wrong"
    CONTEXT_MODEL_LACKS = "context_model_lacks"
    THRESHOLD_WRONG = "threshold_wrong"
    OTHER = "other"


class IntegrityFindingKind(str, Enum):
    HIDDEN_TEXT = "hidden_text"
    INVISIBLE_GLYPHS = "invisible_glyphs"
    INSTRUCTION_PATTERN = "instruction_pattern"
    METADATA_INSTRUCTION = "metadata_instruction"
    ENCODING_OBFUSCATION = "encoding_obfuscation"
    EXCESSIVE_REPETITION = "excessive_repetition"
    FABRICATED_SPAN = "fabricated_span"
    FORBIDDEN_ATTRIBUTE_LEAK = "forbidden_attribute_leak"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RunStatus(str, Enum):
    """Every state a run can occupy.

    The transition graph and the illegal-transition list live in
    ``domain/state_machine.py``; this enum is only the vocabulary.
    """

    CREATED = "created"
    INTAKE_OK = "intake_ok"
    EXTRACTED = "extracted"
    SANITIZED = "sanitized"
    STRUCTURED = "structured"
    CALIBRATED = "calibrated"
    ASSESSED = "assessed"
    AGGREGATED = "aggregated"
    COMPOSED = "composed"
    READY_FOR_REVIEW = "ready_for_review"
    NEEDS_REVIEW = "needs_review"
    QUARANTINED = "quarantined"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_INFO = "needs_info"
    DELIVERY_PENDING_RETRY = "delivery_pending_retry"
    DELIVERED = "delivered"
    FAILED_TERMINAL = "failed_terminal"
    INTERRUPTED = "interrupted"


class DeliveryStatus(str, Enum):
    DELIVERED = "delivered"
    PENDING_RETRY = "pending_retry"
    FAILED = "failed"


class DocumentRole(str, Enum):
    CV = "cv"
    COVER_LETTER = "cover_letter"
    PORTFOLIO = "portfolio"
    OTHER = "other"
