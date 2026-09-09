"""The offset map is what makes a citation clickable.

If runs or pages do not tile the text, some character has no route back to the
original document, and a reviewer following a quotation lands on the wrong
words. These invariants are checked at construction because a broken map is
silent everywhere else.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain.contracts import ExtractionMethod, OffsetRun, PageSpan, SourceText

TEXT = "Shipped an evaluation suite. Ran it nightly."
PROFILE = "np-v1-nfkc-ws-dash-hyphen"


def page(number: int, start: int, end: int) -> PageSpan:
    return PageSpan(
        page_number=number,
        norm_start=start,
        norm_end=end,
        extraction_method=ExtractionMethod.DIGITAL_PDF,
    )


def source(**overrides: object) -> SourceText:
    fields: dict[str, object] = {
        "document_id": uuid4(),
        "raw_text": TEXT,
        "normalized_text": TEXT,
        "offset_runs": [OffsetRun(norm_start=0, raw_start=0, length=len(TEXT))],
        "pages": [page(1, 0, len(TEXT))],
        "normalization_profile_id": PROFILE,
        "extraction_confidence": 0.94,
        "detected_languages": ["en"],
    }
    fields.update(overrides)
    return SourceText(**fields)  # type: ignore[arg-type]


def test_a_whole_document_builds() -> None:
    built = source()
    assert built.extraction_confidence == 0.94
    assert built.detected_languages == ["en"]


def test_source_text_is_frozen() -> None:
    with pytest.raises(ValidationError):
        source().extraction_confidence = 0.1


def test_runs_split_across_the_text_are_accepted() -> None:
    built = source(
        offset_runs=[
            OffsetRun(norm_start=0, raw_start=0, length=28),
            OffsetRun(norm_start=28, raw_start=30, length=len(TEXT) - 28),
        ]
    )
    assert len(built.offset_runs) == 2


def test_a_gap_between_runs_is_rejected() -> None:
    with pytest.raises(ValidationError, match="without gaps or overlap"):
        source(
            offset_runs=[
                OffsetRun(norm_start=0, raw_start=0, length=10),
                OffsetRun(norm_start=20, raw_start=20, length=len(TEXT) - 20),
            ]
        )


def test_overlapping_runs_are_rejected() -> None:
    with pytest.raises(ValidationError, match="without gaps or overlap"):
        source(
            offset_runs=[
                OffsetRun(norm_start=0, raw_start=0, length=20),
                OffsetRun(norm_start=10, raw_start=10, length=len(TEXT) - 10),
            ]
        )


def test_runs_must_cover_the_whole_text() -> None:
    with pytest.raises(ValidationError, match="characters but normalized_text"):
        source(offset_runs=[OffsetRun(norm_start=0, raw_start=0, length=10)])


def test_pages_must_be_consecutive_from_one() -> None:
    with pytest.raises(ValidationError, match="consecutive from 1"):
        source(pages=[page(2, 0, len(TEXT))])


def test_a_gap_between_pages_is_rejected() -> None:
    with pytest.raises(ValidationError, match="expected 20"):
        source(pages=[page(1, 0, 20), page(2, 25, len(TEXT))])


def test_pages_must_reach_the_end_of_the_text() -> None:
    with pytest.raises(ValidationError, match="characters but normalized_text"):
        source(pages=[page(1, 0, 20)])


def test_two_pages_that_tile_the_text_are_accepted() -> None:
    built = source(pages=[page(1, 0, 28), page(2, 28, len(TEXT))])
    assert len(built.pages) == 2


def test_a_page_cannot_end_before_it_starts() -> None:
    with pytest.raises(ValidationError, match="starts after it ends"):
        page(1, 30, 10)


def test_ocr_confidence_is_a_fraction() -> None:
    with pytest.raises(ValidationError):
        PageSpan(
            page_number=1,
            norm_start=0,
            norm_end=10,
            extraction_method=ExtractionMethod.OCR,
            ocr_confidence=1.4,
        )


def test_a_run_must_have_length() -> None:
    with pytest.raises(ValidationError):
        OffsetRun(norm_start=0, raw_start=0, length=0)


def test_extraction_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        source(extraction_confidence=1.2)
