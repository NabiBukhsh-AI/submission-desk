"""Evidence is the contract the whole system rests on.

Two rules matter more than the rest: a claim about a candidate must quote the
document, and absence of evidence must be expressible without inventing one.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain.contracts import EvidenceState, SpanValidation
from tests.builders import evidence, insufficient_evidence, provenance


def test_supported_evidence_builds_with_a_span() -> None:
    item = evidence()
    assert item.state is EvidenceState.SUPPORTED
    assert item.verbatim_span
    assert item.provenance is not None


def test_evidence_is_frozen() -> None:
    with pytest.raises(ValidationError):
        evidence().confidence = 0.1


@pytest.mark.parametrize("state", [EvidenceState.SUPPORTED, EvidenceState.CONTRADICTED])
def test_a_claim_without_a_span_is_rejected(state: EvidenceState) -> None:
    with pytest.raises(ValidationError, match="must cite a span"):
        evidence(state=state, verbatim_span=None, provenance=None)


@pytest.mark.parametrize("state", [EvidenceState.SUPPORTED, EvidenceState.CONTRADICTED])
def test_a_span_without_provenance_is_rejected(state: EvidenceState) -> None:
    """A quotation with no address cannot be checked, so it is not a citation."""
    with pytest.raises(ValidationError, match="must cite a span"):
        evidence(state=state, provenance=None)


def test_absence_of_evidence_is_representable() -> None:
    item = insufficient_evidence()
    assert item.state is EvidenceState.INSUFFICIENT_EVIDENCE
    assert item.verbatim_span is None
    assert item.span_validation is SpanValidation.NOT_APPLICABLE


def test_absence_may_not_smuggle_in_a_span() -> None:
    with pytest.raises(ValidationError, match="records absence"):
        insufficient_evidence(verbatim_span="something the document does not say at all")


def test_absence_must_not_claim_a_span_verdict() -> None:
    with pytest.raises(ValidationError, match="span_validation not_applicable"):
        evidence(
            state=EvidenceState.INSUFFICIENT_EVIDENCE,
            verbatim_span=None,
            provenance=None,
            span_validation=SpanValidation.VALID_EXACT,
        )


def test_a_cited_span_may_not_be_marked_not_applicable() -> None:
    """A quotation was either located in the source or it was not."""
    with pytest.raises(ValidationError, match="reserved for insufficient_evidence"):
        evidence(span_validation=SpanValidation.NOT_APPLICABLE)


@pytest.mark.parametrize("bad_confidence", [-0.01, 1.01])
def test_confidence_is_a_probability(bad_confidence: float) -> None:
    with pytest.raises(ValidationError):
        evidence(confidence=bad_confidence)


def test_a_claim_must_say_something() -> None:
    with pytest.raises(ValidationError):
        evidence(claim="too short")


def test_a_claim_cannot_be_an_essay() -> None:
    with pytest.raises(ValidationError):
        evidence(claim="x" * 401)


def test_a_span_must_be_long_enough_to_be_a_quotation() -> None:
    with pytest.raises(ValidationError):
        evidence(verbatim_span="short")


def test_a_span_is_bounded() -> None:
    """A span the length of the document is a copy, not a citation."""
    with pytest.raises(ValidationError):
        evidence(verbatim_span="x" * 601)


# --- what the rule engine is allowed to see ----------------------------------


@pytest.mark.parametrize(
    "validation",
    [SpanValidation.VALID_EXACT, SpanValidation.VALID_NORMALIZED, SpanValidation.VALID_FUZZY_OCR],
)
def test_located_spans_are_usable(validation: SpanValidation) -> None:
    assert evidence(span_validation=validation).is_usable


@pytest.mark.parametrize(
    "validation",
    [
        SpanValidation.INVALID_NOT_FOUND,
        SpanValidation.INVALID_OFFSET_MISMATCH,
        SpanValidation.INVALID_WRONG_DOCUMENT,
    ],
)
def test_unlocated_spans_are_not_usable(validation: SpanValidation) -> None:
    """The item is kept and shown to the reviewer, but it never reaches scoring."""
    assert not evidence(span_validation=validation).is_usable


def test_absence_is_usable_without_a_span() -> None:
    assert insufficient_evidence().is_usable


def test_provenance_rejects_an_empty_range() -> None:
    with pytest.raises(ValidationError, match="empty span is not evidence"):
        provenance(norm_start=100, norm_end=100)


def test_provenance_rejects_a_reversed_range() -> None:
    with pytest.raises(ValidationError, match="norm_start must be less"):
        provenance(norm_start=200, norm_end=100)


def test_provenance_rejects_reversed_pages() -> None:
    with pytest.raises(ValidationError, match="page_start must not exceed"):
        provenance(page_start=3, page_end=2)


def test_provenance_allows_a_span_across_a_page_break() -> None:
    """A sentence can straddle a page, and rejecting that would lose real evidence."""
    assert provenance(page_start=1, page_end=2).page_end == 2
