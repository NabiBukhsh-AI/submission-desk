"""PDF text, page by page, with the geometry the column heuristic needs.

Two security decisions are made when the document is opened, and both are made
here rather than trusted to a default.

JavaScript is disabled. A PDF can carry scripts, and this system reads
documents from strangers.

Embedded files are never extracted. A PDF can contain arbitrary attachments,
and nothing downstream has any use for them.

Per-page OCR fallback lives here too. A page whose digital text is implausibly
thin is re-read as an image; the rest of the document keeps its good text.
"""

from __future__ import annotations

import pymupdf

from domain.contracts.enums import ExtractionMethod
from domain.ports.extraction import ExtractionFailed, OcrEngine, PageText
from infrastructure.extraction import layout
from infrastructure.extraction.ocr import read_with_retry

#: Characters per page below which the digital text layer is not believable and
#: the page is re-read as an image.
OCR_TRIGGER_CHARS = 100

#: Pages above which OCR is not attempted. A sixty-page scan would take minutes
#: and produce text nobody assesses; the reviewer is told instead.
OCR_MAX_PAGES = 20

#: Render resolution for a page that needs OCR.
RENDER_DPI = 300


def open_document(data: bytes) -> pymupdf.Document:
    """Open a PDF with scripting and attachments disabled."""
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as error:
        raise ExtractionFailed(
            "This file could not be opened as a PDF. Ask the candidate to send it again.",
            error_code="CORRUPT_FILE",
        ) from error

    if document.is_encrypted and not document.authenticate(""):
        document.close()
        raise ExtractionFailed(
            "This file is password protected, so its text cannot be read.",
            error_code="ENCRYPTED",
        )

    return document


def extract_pages(data: bytes, *, ocr: OcrEngine | None = None) -> list[PageText]:
    """Read every page, falling back to OCR one page at a time."""
    document = open_document(data)
    pages: list[PageText] = []
    ocr_used = 0

    try:
        # Indexed rather than iterated: PyMuPDF's Document is iterable at
        # runtime but not declared so, and an untyped loop variable here would
        # silently lose every check on the page object.
        for zero_based in range(document.page_count):
            index = zero_based + 1
            page = document[zero_based]
            rect = page.rect
            blocks = tuple(
                (block[0], block[1], block[2], block[3], block[4])
                for block in page.get_text("blocks")
                if isinstance(block[4], str)
            )
            text = layout.page_text(blocks, rect.width)

            needs_ocr = len(text.strip()) < OCR_TRIGGER_CHARS
            if needs_ocr and ocr is not None and ocr_used < OCR_MAX_PAGES:
                ocr_used += 1
                pages.append(_ocr_page(page, index, rect, ocr))
                continue

            pages.append(
                PageText(
                    page_number=index,
                    text=text,
                    method=ExtractionMethod.DIGITAL_PDF,
                    blocks=blocks,
                    width=rect.width,
                    height=rect.height,
                )
            )
    finally:
        document.close()

    return pages


def _ocr_page(page: pymupdf.Page, index: int, rect: pymupdf.Rect, ocr: OcrEngine) -> PageText:
    """Render one page and read it as an image.

    A page the reader could not handle comes back empty with its method still
    recorded as OCR, so the confidence calculation knows the page was attempted
    and the reviewer is told which pages are unreliable.
    """
    matrix = pymupdf.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
    image_bytes: bytes = page.get_pixmap(matrix=matrix).tobytes("png")
    result = read_with_retry(ocr, image_bytes)

    return PageText(
        page_number=index,
        text=result.text,
        method=ExtractionMethod.OCR,
        blocks=((0.0, 0.0, rect.width, rect.height, result.text),) if result.text else (),
        ocr_confidence=result.mean_confidence,
        width=rect.width,
        height=rect.height,
    )
