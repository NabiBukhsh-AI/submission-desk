"""Tolerating the reader's mistakes without tolerating the model's.

The scanned-document case lives or dies here. A quotation read off a scan will
differ from the source by exactly the characters a scanner confuses, and
rejecting it would report fabrication where there was none, destroying the
scanned-CV test case and the reviewer's trust with it.

The same tolerance applied to a digitally extracted page would be a hole: that
text was copied, so a quotation that differs from it by much was not copied from
it. Hence the looser threshold and the confusion map are applied only to pages
that genuinely went through OCR, and the resulting verdict is its own state so
the reviewer knows the citation is scanner-derived.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from domain.contracts.enums import ExtractionMethod, SpanValidation
from domain.contracts.source_text import OffsetRun, PageSpan, SourceText
from domain.provenance import ocr_confusions
from domain.provenance.validator import SpanThresholds, validate
from tests.builders import evidence, provenance

SCANNED_TEXT = (
    "Owned a multi-service payments backend and its on-call rotation for two years. "
    "Introduced a nightly evaluation suite that blocked releases below the agreed bar."
)


def scanned_source(text: str = SCANNED_TEXT, *, method: ExtractionMethod = ExtractionMethod.OCR):
    return SourceText(
        document_id=uuid4(),
        raw_text=text,
        normalized_text=text,
        offset_runs=[OffsetRun(norm_start=0, raw_start=0, length=len(text))],
        pages=[PageSpan(page_number=1, norm_start=0, norm_end=len(text), extraction_method=method)],
        normalization_profile_id="np-v1-nfkc-ws-dash-hyphen",
        extraction_confidence=0.7,
    )


def item_for(source: SourceText, span: str, *, start: int = 0):
    return evidence(
        verbatim_span=span,
        provenance=provenance(
            document_id=source.document_id, norm_start=start, norm_end=start + len(span)
        ),
    )


def check(
    source: SourceText, span: str, *, start: int = 0, thresholds: SpanThresholds | None = None
):
    return validate(item_for(source, span, start=start), {source.document_id: source}, thresholds)


# --- the confusion map itself -------------------------------------------------------


@pytest.mark.parametrize(
    ("scanned", "actual"),
    [
        ("rnulti", "multi"),
        ("0wned", "Owned"),
        ("evaIuation", "evaluation"),
        ("c1osed", "closed"),
        ("8ackend", "Backend"),
        ("clay", "day"),
    ],
)
def test_confusable_readings_fold_together(scanned: str, actual: str) -> None:
    assert ocr_confusions.fold(scanned) == ocr_confusions.fold(actual)


def test_the_map_is_symmetric() -> None:
    """Both directions of a confusion fold to the same representative, so
    neither reading is privileged over the other."""
    assert ocr_confusions.fold("rn") == ocr_confusions.fold("m")
    assert ocr_confusions.fold("m") == ocr_confusions.fold("rn")


def test_genuinely_different_words_do_not_fold_together() -> None:
    """Every pair in the map makes two different strings look alike, which is
    why the map is small. These must stay apart."""
    for left, right in [
        ("engineer", "manager"),
        ("shipped", "planned"),
        ("forty", "four"),
        ("production", "prototype"),
    ]:
        assert ocr_confusions.fold(left) != ocr_confusions.fold(right)


def test_spacing_is_not_evidence_after_a_scan() -> None:
    """Scanners insert and drop spaces around glyph boundaries unpredictably."""
    assert ocr_confusions.fold("on call") == ocr_confusions.fold("oncall")


# --- real evidence off a scan ---------------------------------------------------------


def test_a_quotation_with_two_confusions_is_accepted() -> None:
    """The acceptance criterion: real OCR evidence with two character errors
    validates, and is marked as scanner-derived rather than exact."""
    source = scanned_source()
    scanned_reading = "0wned a rnulti-service payments backend"

    result = check(source, scanned_reading)

    assert result.validation is SpanValidation.VALID_FUZZY_OCR
    assert result.ratio is not None and result.ratio >= 0.88


def test_the_verdict_says_the_citation_came_from_a_scan() -> None:
    """A distinct state, so the reviewer knows to check this one by eye."""
    source = scanned_source()

    result = check(source, "lntroduced a nightly evaIuation suite")

    assert result.validation is SpanValidation.VALID_FUZZY_OCR


def test_an_exact_quotation_from_a_scan_is_still_exact() -> None:
    """The tolerance is a fallback, not a replacement. A perfect match on a
    scanned page is reported as perfect."""
    source = scanned_source()

    result = check(source, "Owned a multi-service payments backend")

    assert result.validation is SpanValidation.VALID_EXACT


# --- the hole this must not open --------------------------------------------------------


def test_a_fabrication_is_still_rejected_on_a_scanned_page() -> None:
    """The tolerance rescues the reader's mistakes, not the model's inventions."""
    source = scanned_source()

    result = check(source, "Led a team of forty engineers across three continents")

    assert result.validation is SpanValidation.INVALID_NOT_FOUND


def test_the_tolerance_is_not_applied_to_a_digital_page() -> None:
    """That text was copied, so a quotation differing from it by six characters
    was not copied from it. Applying the scan tolerance here would be a hole."""
    digital = scanned_source(method=ExtractionMethod.DIGITAL_PDF)

    result = check(digital, "0wned a rnulti-service payrnents backend")

    assert result.validation is SpanValidation.INVALID_NOT_FOUND


def test_a_mixed_document_uses_the_right_threshold_per_page() -> None:
    """A quotation from the typed first page gets the strict threshold even
    though page two is a scan. The scan's presence must not loosen validation
    for the whole file."""
    text = "typed page content here. " + "scanned page content here."
    source = SourceText(
        document_id=uuid4(),
        raw_text=text,
        normalized_text=text,
        offset_runs=[OffsetRun(norm_start=0, raw_start=0, length=len(text))],
        pages=[
            PageSpan(
                page_number=1,
                norm_start=0,
                norm_end=25,
                extraction_method=ExtractionMethod.DIGITAL_PDF,
            ),
            PageSpan(
                page_number=2,
                norm_start=25,
                norm_end=len(text),
                extraction_method=ExtractionMethod.OCR,
            ),
        ],
        normalization_profile_id="np-v1-nfkc-ws-dash-hyphen",
        extraction_confidence=0.7,
    )

    from_typed = validate(
        item_for(source, "typecl page c0ntent here", start=0),
        {source.document_id: source},
    )

    assert from_typed.validation is SpanValidation.INVALID_NOT_FOUND


def test_the_ocr_threshold_is_looser_than_the_digital_one() -> None:
    """Stated as a property rather than left implicit in two numbers."""
    thresholds = SpanThresholds()

    assert thresholds.fuzzy_ocr_threshold < thresholds.fuzzy_threshold


def test_a_ratio_is_recorded_for_a_rejected_scan_quotation() -> None:
    """Tuning the threshold on measured data needs the measurement to exist for
    the rejections too."""
    source = scanned_source()

    result = check(source, "Led a team of forty engineers across three continents")

    assert result.ratio is not None
