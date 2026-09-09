"""Turning bytes into text that a quotation can be checked against.

``SourceText`` produced here is the sole ground truth for span validation.
Everything downstream, every quotation a reviewer clicks, resolves to a
character range in this object, so what it records about *how* each page was
read matters as much as the text itself.

Per-page extraction method is the reason. A quotation from a digitally
extracted page must match exactly; a quotation from a page that went through
OCR is compared with a tolerance for the characters OCR confuses. Without the
method recorded per page, either real evidence gets thrown away or fabricated
evidence gets accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from domain.contracts.documents import CandidateDocument
from domain.contracts.enums import ExtractionMethod
from domain.contracts.source_text import SourceText
from domain.errors import SubmissionDeskError


class ExtractionFailed(SubmissionDeskError):
    """A document could not be read.

    Carries a sentence for the recruiter. The distinction between "this file is
    broken" and "this file has no text in it" is theirs to act on, so it is made
    here rather than left to a stack trace.
    """

    error_code = "EXTRACTION_FAILED"

    def __init__(self, message: str, *, error_code: str = "EXTRACTION_FAILED") -> None:
        self.error_code = error_code
        super().__init__(message)


@dataclass(frozen=True)
class PageText:
    """One page, as read, before any normalisation.

    ``blocks`` carries the geometry the column heuristic needs: each entry is
    (x0, y0, x1, y1, text). Kept as plain tuples rather than a model because
    thousands of them exist per document and none of them are persisted.
    """

    page_number: int
    text: str
    method: ExtractionMethod
    blocks: tuple[tuple[float, float, float, float, str], ...] = ()
    ocr_confidence: float | None = None
    width: float = 0.0
    height: float = 0.0

    @property
    def char_count(self) -> int:
        return len(self.text.strip())


@dataclass(frozen=True)
class OcrResult:
    """What an OCR engine returned, including how sure it was."""

    text: str
    mean_confidence: float
    available: bool = True
    reason: str | None = None


class OcrEngine(Protocol):
    """Reads text from an image of a page.

    A port because the engine is a system binary that may not be installed. An
    absent engine is reported, not silently treated as an empty page: a scan
    that produced nothing because nobody installed the reader looks exactly like
    a blank page, and those two must not be confused.
    """

    available: bool

    def read(self, image_bytes: bytes, *, dpi: int = 300) -> OcrResult: ...


class DocumentExtractor(Protocol):
    """Bytes to text, with provenance.

    ``profile_id`` names how the text was assembled: the cell delimiter, the
    page separator, and the reading order. It is part of the extraction cache
    key, because a span validated against one assembly will not match another.
    """

    profile_id: str

    def extract(self, document: CandidateDocument, data: bytes, *, profile_id: str) -> SourceText:
        """Read one document.

        Raises ``ExtractionFailed`` with a recruiter-readable message when the
        document cannot be read at all. A partially readable document is not a
        failure: it returns with a lower confidence and the pages that could not
        be read marked, because half a CV assessed honestly beats none.
        """
        ...
