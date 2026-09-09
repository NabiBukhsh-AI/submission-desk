"""The bridge between normalised coordinates and the original page.

Every claim in the system ends at a character range in one of these objects. If
the offset map is wrong, a reviewer clicking a quotation lands on the wrong
words and the audit trail is decorative, so the invariants here are checked at
construction rather than trusted.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, model_validator

from domain.contracts.base import Contract
from domain.contracts.enums import ExtractionMethod


class OffsetRun(Contract):
    """One contiguous stretch where normalised and raw text stay in step."""

    norm_start: int = Field(ge=0)
    raw_start: int = Field(ge=0)
    length: int = Field(gt=0)


class PageSpan(Contract):
    """Where one page begins and ends in normalised coordinates."""

    page_number: int = Field(ge=1)
    norm_start: int = Field(ge=0)
    norm_end: int = Field(ge=0)
    extraction_method: ExtractionMethod
    ocr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> PageSpan:
        if self.norm_start > self.norm_end:
            raise ValueError("page span starts after it ends")
        return self


class SourceText(Contract):
    """The text of one document, in both coordinate systems."""

    document_id: UUID
    raw_text: str
    normalized_text: str
    offset_runs: list[OffsetRun]
    pages: list[PageSpan]
    normalization_profile_id: str = Field(min_length=1)
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    detected_languages: list[str] = Field(default_factory=list)
    block_boundaries: list[tuple[int, int]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _offset_runs_tile_the_text(self) -> SourceText:
        """Runs are sorted, non-overlapping, and cover the normalised text.

        A gap means some character has no route back to the original document,
        which would make a span citing it unverifiable.
        """
        expected_start = 0
        for run in self.offset_runs:
            if run.norm_start != expected_start:
                raise ValueError(
                    f"offset runs must tile normalized_text without gaps or overlap; "
                    f"expected a run at {expected_start}, found one at {run.norm_start}"
                )
            expected_start = run.norm_start + run.length
        if expected_start != len(self.normalized_text):
            raise ValueError(
                f"offset runs cover {expected_start} characters but normalized_text "
                f"is {len(self.normalized_text)} long"
            )
        return self

    @model_validator(mode="after")
    def _pages_partition_the_text(self) -> SourceText:
        """Pages partition the normalised text, in order, with no gaps."""
        expected_start = 0
        expected_page = 1
        for page in self.pages:
            if page.page_number != expected_page:
                raise ValueError(
                    f"pages must be consecutive from 1; expected page {expected_page}, "
                    f"found {page.page_number}"
                )
            if page.norm_start != expected_start:
                raise ValueError(
                    f"page {page.page_number} starts at {page.norm_start}, "
                    f"expected {expected_start}"
                )
            expected_start = page.norm_end
            expected_page += 1
        if self.pages and expected_start != len(self.normalized_text):
            raise ValueError(
                f"pages cover {expected_start} characters but normalized_text "
                f"is {len(self.normalized_text)} long"
            )
        return self


class Provenance(Contract):
    """Where a quoted span sits in one document.

    Cross-page spans are allowed, because a sentence can straddle a page break.
    Cross-document spans are not: a single quotation drawn from two files is not
    a quotation, and the span validator rejects one that resolves elsewhere.
    """

    document_id: UUID
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    norm_start: int = Field(ge=0)
    norm_end: int = Field(ge=0)
    extraction_method: ExtractionMethod
    normalization_profile_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _ranges_are_ordered(self) -> Provenance:
        if self.norm_start >= self.norm_end:
            raise ValueError("norm_start must be less than norm_end; an empty span is not evidence")
        if self.page_start > self.page_end:
            raise ValueError("page_start must not exceed page_end")
        return self
