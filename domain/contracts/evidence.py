"""The most important contract in the system.

An evidence item is a quotation with an address. Everything downstream, the
criterion state, the score, the band, the recommendation, is computed from a
list of these, so an item that cannot be traced back to a character range in a
real document is worse than no item at all.

Two fields are set after the model has answered and never by it:
``span_validation`` and ``validated_norm_start`` are written by the span
validator. The response schema the model fills does not contain them, so there
is no path by which a model can mark its own quotation as verified.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, model_validator

from domain.contracts.base import Contract
from domain.contracts.enums import (
    VALID_SPAN_VALIDATIONS,
    EscalationState,
    EvidenceState,
    ModelTier,
    SpanValidation,
)
from domain.contracts.source_text import Provenance


class EvidenceItem(Contract):
    evidence_id: UUID
    criterion_id: str = Field(min_length=1)
    state: EvidenceState
    claim: str = Field(min_length=10, max_length=400)
    verbatim_span: str | None = Field(default=None, min_length=8, max_length=600)
    provenance: Provenance | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    model_tier: ModelTier
    prompt_version: str = Field(min_length=1)
    escalation_state: EscalationState
    span_validation: SpanValidation
    span_match_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    validated_norm_start: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _span_required_unless_insufficient(self) -> EvidenceItem:
        """A claim about the candidate must quote the document it came from.

        The single exception is absence: an item saying the document does not
        address the criterion has nothing to quote, and inventing something to
        put here is the failure this whole contract exists to prevent.
        """
        if self.state is not EvidenceState.INSUFFICIENT_EVIDENCE and (
            not self.verbatim_span or self.provenance is None
        ):
            raise ValueError("supported and contradicted evidence must cite a span")
        return self

    @model_validator(mode="after")
    def _absence_carries_no_span_verdict(self) -> EvidenceItem:
        """There is nothing to validate when nothing was quoted."""
        if self.state is EvidenceState.INSUFFICIENT_EVIDENCE:
            if self.verbatim_span is not None or self.provenance is not None:
                raise ValueError("insufficient_evidence must not carry a span; it records absence")
            if self.span_validation is not SpanValidation.NOT_APPLICABLE:
                raise ValueError("insufficient_evidence must have span_validation not_applicable")
        elif self.span_validation is SpanValidation.NOT_APPLICABLE:
            raise ValueError(
                "not_applicable is reserved for insufficient_evidence; a cited span "
                "was either found or it was not"
            )
        return self

    @property
    def is_usable(self) -> bool:
        """Whether the rule engine may see this item.

        An item whose span could not be located is persisted and shown to the
        reviewer as a hallucination flag, but it never reaches scoring.
        """
        if self.state is EvidenceState.INSUFFICIENT_EVIDENCE:
            return True
        return self.span_validation in VALID_SPAN_VALIDATIONS
