"""The schemas a model is allowed to fill.

Kept apart from the storage contracts on purpose. These are the only shapes that
cross the boundary from a language model into this system, and the guarantee
that matters is structural: there is no field here for a score, a rating, a
band, a verdict, or a hiring recommendation, so a model cannot express one even
if a document instructs it to.

That is a stronger control than any instruction in a prompt, because it does not
depend on the model complying. ``tests/unit/test_schemas_forbid_scores.py``
holds the line.

Two fields present on the stored ``EvidenceItem`` are deliberately absent here:
``span_validation`` and ``validated_norm_start`` are written by the span
validator afterwards, so nothing a model returns can mark its own quotation
as verified.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, model_validator

from domain.contracts.base import Contract
from domain.contracts.enums import EvidenceState, Severity


class EvidenceCandidate(Contract):
    """One piece of evidence as the model reports it.

    The offsets are a claim, not a fact. Models count characters poorly, so the
    validator relocates the quotation in the source text and corrects them; what
    matters is that the quoted string appears in the document at all.
    """

    state: EvidenceState
    claim: str = Field(min_length=10, max_length=400)
    verbatim_span: str | None = Field(default=None, min_length=8, max_length=600)
    document_id: UUID | None = None
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    norm_start: int | None = Field(default=None, ge=0)
    norm_end: int | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _span_required_unless_insufficient(self) -> EvidenceCandidate:
        if self.state is not EvidenceState.INSUFFICIENT_EVIDENCE and (
            not self.verbatim_span or self.document_id is None
        ):
            raise ValueError(
                "supported and contradicted evidence must quote the document it came from"
            )
        return self


class AssessmentResponse(Contract):
    """The answer to one criterion, for one candidate."""

    criterion_id: str = Field(min_length=1)
    evidence: list[EvidenceCandidate] = Field(default_factory=list)


class ComposedQuestion(Contract):
    criterion_id: str = Field(min_length=1)
    question: str = Field(min_length=10, max_length=500)


class ComposedRequest(Contract):
    criterion_id: str = Field(min_length=1)
    request: str = Field(min_length=10, max_length=500)


class CompositionResponse(Contract):
    """Gaps, phrased as something a recruiter can send.

    Which gaps exist is decided deterministically from the criterion states
    before this call is made. The model is asked only to write the sentences, so
    it cannot invent a gap that the assessment did not find.
    """

    interview_questions: list[ComposedQuestion] = Field(default_factory=list)
    information_requests: list[ComposedRequest] = Field(default_factory=list)


class InjectionVerdict(Contract):
    """A second opinion on one suspicious excerpt.

    Consulted only where the deterministic detectors are ambiguous, and its
    answer may only raise a severity, never lower one. A model persuaded by the
    text it is examining can therefore make the system more cautious and never
    less.
    """

    is_instruction: bool
    severity: Severity
    rationale: str = Field(min_length=3, max_length=500)
