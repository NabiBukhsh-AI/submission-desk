"""Choosing a reader, and assembling the result.

The one place that turns a document into ``SourceText``. It picks a reader by
MIME type, joins the pages into a single string, and records where each page
begins and ends so that a character offset can be turned back into "page 2 of
CV.pdf" for the reviewer.

The text returned here is raw, with an identity offset map. The EXTRACT node
applies the domain's normalisation profile (``domain.provenance.normalization``)
and produces the real map, page spans and block boundaries; keeping the two
steps apart is what lets the profile change without a reader changing. A
wrong offset map is invisible until a reviewer clicks a quotation and lands in
the wrong paragraph, which is why both halves are tested end to end.
"""

from __future__ import annotations

from domain.contracts.documents import CandidateDocument
from domain.contracts.enums import ExtractionMethod
from domain.contracts.source_text import OffsetRun, PageSpan, SourceText
from domain.ports.extraction import ExtractionFailed, OcrEngine, PageText
from infrastructure.extraction import confidence, docx, language, layout, pdf
from infrastructure.extraction.docx import CELL_DELIMITER

#: Separates pages in the assembled text. A single newline would let the last
#: line of one page and the first of the next form a sentence that exists in no
#: document, and a quotation spanning that seam would validate against text
#: nobody wrote.
PAGE_SEPARATOR = "\n\n"

#: Identifies how the text was assembled. Any change to the delimiter, the page
#: separator, or the reading order changes this, because a span validated
#: against one assembly will not match another.
PROFILE_ID = "np-v1-raw-blocks-pipe-cells"


class Extractor:
    """Reads a document and returns the ground truth for span validation."""

    profile_id = PROFILE_ID

    def __init__(self, ocr: OcrEngine | None = None) -> None:
        self.ocr = ocr
        #: Counts real parses, so a test can prove the cache avoided one.
        self.parse_count = 0

    def extract(
        self, document: CandidateDocument, data: bytes, *, profile_id: str = PROFILE_ID
    ) -> SourceText:
        self.parse_count += 1
        pages = self._read(document, data)

        if not pages or not any(page.text.strip() for page in pages):
            raise ExtractionFailed(
                "No readable text could be taken from this document. It may be a scan that "
                "could not be read, or a blank file.",
                error_code="EMPTY_DOCUMENT",
            )

        report = language.detect([page.text[:1500] for page in pages])
        breakdown = confidence.compute(pages, language_score=report.score)

        text, spans, boundaries = _assemble(pages)

        return SourceText(
            document_id=document.document_id,
            raw_text=text,
            normalized_text=text,
            offset_runs=[OffsetRun(norm_start=0, raw_start=0, length=len(text))] if text else [],
            pages=spans,
            normalization_profile_id=profile_id,
            extraction_confidence=round(breakdown.total, 4),
            detected_languages=list(report.languages),
            block_boundaries=boundaries,
        )

    def _read(self, document: CandidateDocument, data: bytes) -> list[PageText]:
        mime = document.mime_type
        if mime == "application/pdf":
            return pdf.extract_pages(data, ocr=self.ocr)
        if mime.endswith("wordprocessingml.document"):
            return docx.extract_pages(data)
        if mime.startswith("text/"):
            return _plaintext_pages(data)
        raise ExtractionFailed(
            "This file type cannot be read. Supported types are PDF, Word, and plain text.",
            error_code="UNSUPPORTED_TYPE",
        )


def _plaintext_pages(data: bytes) -> list[PageText]:
    """Plain text and Markdown.

    Decoded permissively: a CV saved from a word processor often carries a
    stray byte, and refusing the whole document over one character would be a
    worse outcome than a single replacement character in the middle of it.
    """
    text = data.decode("utf-8", errors="replace")
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    blocks = tuple(
        (0.0, float(index), 1.0, float(index) + 1.0, part) for index, part in enumerate(paragraphs)
    )
    return [
        PageText(
            page_number=1,
            text="\n".join(paragraphs),
            method=ExtractionMethod.PLAINTEXT,
            blocks=blocks,
            width=1.0,
            height=float(len(paragraphs) or 1),
        )
    ]


def _assemble(pages: list[PageText]) -> tuple[str, list[PageSpan], list[tuple[int, int]]]:
    """Join pages into one string, recording where each begins and ends.

    Page spans must tile the text with no gaps, which the SourceText contract
    checks. The separator between pages therefore belongs to the page before it,
    so every character has a page.
    """
    chunks: list[str] = []
    spans: list[PageSpan] = []
    boundaries: list[tuple[int, int]] = []
    cursor = 0

    for index, page in enumerate(pages):
        body = page.text
        is_last = index == len(pages) - 1
        chunk = body if is_last else body + PAGE_SEPARATOR

        boundaries.extend(layout.block_boundaries(page.blocks, page.width, cursor))

        chunks.append(chunk)
        spans.append(
            PageSpan(
                page_number=page.page_number,
                norm_start=cursor,
                norm_end=cursor + len(chunk),
                extraction_method=page.method,
                ocr_confidence=page.ocr_confidence,
            )
        )
        cursor += len(chunk)

    return "".join(chunks), spans, boundaries


def profile_id_for(delimiter: str = CELL_DELIMITER) -> str:
    """The profile id, so a caller can record what produced a span."""
    return PROFILE_ID if delimiter == CELL_DELIMITER else f"np-v1-raw-blocks-{delimiter!r}-cells"
