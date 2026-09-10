"""Reading a document the way an attacker wrote it.

The detectors are pure functions over text, spans and metadata. This module is
what produces those three things from bytes, which means it is the only part of
the security package that knows what a PDF is.

Keeping the split means every detector can be tested against literals, and the
one module that needs a real file is small enough to read in full.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pymupdf

from domain.contracts.integrity import IntegrityFinding
from domain.ports.extraction import OcrEngine
from domain.ports.security import ScanResult
from infrastructure.security import detectors, render_diff
from infrastructure.security import metadata as metadata_reader
from infrastructure.security.detectors import TextSpan

#: Colour a span is drawn on when the page does not say. White, because that is
#: what paper is and what every CV template assumes.
DEFAULT_BACKGROUND = (1.0, 1.0, 1.0)


@dataclass
class DocumentScan:
    """Everything the detectors need about one document."""

    findings: list[IntegrityFinding] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    span_count: int = 0
    pages_compared: int = 0
    render_diff_ran: bool = False
    render_diff_skipped_reason: str | None = None


def _colour_of(value: Any) -> tuple[float, float, float] | None:
    """A span's colour as three floats.

    PyMuPDF reports colour as a packed integer. Unpacked here rather than in the
    detector so the detector stays a function of numbers and can be tested
    without a PDF.
    """
    if not isinstance(value, int):
        return None
    return (
        ((value >> 16) & 0xFF) / 255.0,
        ((value >> 8) & 0xFF) / 255.0,
        (value & 0xFF) / 255.0,
    )


def _widened_read(page: pymupdf.Page) -> tuple[tuple[float, ...], dict[str, Any]]:
    """Read a page including anything cropped out of view, and say what is in view.

    This is the off-page attack, and it needs a deliberate step. A PDF carries
    two rectangles: the media box, which is the sheet, and the crop box, which
    is what a viewer displays. Text drawn between them is in the file, extracted
    by every parser, and printed by nothing.

    The library will not hand back text outside the crop box, whatever clip is
    asked for — so a scanner that simply read the page would see a clean
    document, which is exactly what the attack is for. The crop is lifted for
    the read and restored afterwards.

    Widening is skipped when the two boxes have different origins, because the
    coordinates a page reports are relative to the crop box: moving the origin
    would shift every bounding box, and comparing the shifted ones against the
    unshifted crop rectangle would flag the whole page. A missed detection is
    better than a detector that fires on everything.
    """
    media = page.mediabox
    crop = page.cropbox
    visible = tuple(crop)

    if tuple(crop) == tuple(media):
        return visible, page.get_text("dict")

    if (crop.x0, crop.y0) != (media.x0, media.y0):
        return visible, page.get_text("dict")

    page.set_cropbox(media)
    try:
        return visible, page.get_text("dict")
    finally:
        page.set_cropbox(crop)


def spans_of(document: pymupdf.Document, *, max_pages: int = 40) -> list[TextSpan]:
    """Every run of text with how it was drawn.

    Reads the dictionary form rather than the plain text, because the whole
    point is the attributes plain text discards: size, colour, and position.
    """
    spans: list[TextSpan] = []

    for index in range(min(document.page_count, max_pages)):
        page = document[index]

        try:
            visible, content = _widened_read(page)
        except Exception:
            continue

        for block in content.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if not text.strip():
                        continue

                    spans.append(
                        TextSpan(
                            text=text,
                            page_number=index + 1,
                            font_size=span.get("size"),
                            colour=_colour_of(span.get("color")),
                            background=DEFAULT_BACKGROUND,
                            alpha=span.get("alpha"),
                            bbox=tuple(span.get("bbox")) if span.get("bbox") else None,
                            media_box=visible,  # type: ignore[arg-type]
                        )
                    )

    return spans


def scan_pdf(  # noqa: PLR0913 - each argument is a separate control, and
    # collapsing them into a config object would hide which ones a caller set.
    data: bytes,
    normalized_text: str,
    *,
    raw_text: str | None = None,
    ocr: OcrEngine | None = None,
    render_diff_enabled: bool = True,
    render_diff_max_pages: int = render_diff.RENDER_DIFF_MAX_PAGES,
) -> DocumentScan:
    """Every detector, over one PDF.

    Two texts, and the distinction is not cosmetic. ``normalized_text`` is what
    the model will be shown, so the instruction detectors run over exactly the
    string that reaches it. ``raw_text`` is what was in the file, and the
    invisible-glyph detector needs that one: the normalization profile strips
    zero-width and bidi characters, so by the time text is normalized the
    evidence of them is gone. Scanning only the normalized text would report
    that no document has ever contained a zero-width space.
    """
    scan = DocumentScan()

    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        scan.findings += detectors.run_text_detectors(normalized_text, raw_text=raw_text)
        scan.render_diff_skipped_reason = "the document could not be opened for scanning"
        return scan

    try:
        scan.metadata = metadata_reader.from_pdf(document)
        spans = spans_of(document)
        scan.span_count = len(spans)

        scan.findings += detectors.run_text_detectors(normalized_text, raw_text=raw_text)
        scan.findings += detectors.run_span_detectors(spans)
        scan.findings += detectors.detect_metadata_instructions(scan.metadata)

        if not render_diff_enabled:
            scan.render_diff_skipped_reason = "the render comparison is switched off"
        elif ocr is None:
            scan.render_diff_skipped_reason = "no reader was available to look at the pages"
        elif document.page_count > render_diff_max_pages:
            scan.render_diff_skipped_reason = (
                f"the document has {document.page_count} pages, "
                f"more than the {render_diff_max_pages} this check reads"
            )
        else:
            comparisons = render_diff.compare_document(
                document, ocr, max_pages=render_diff_max_pages
            )
            scan.pages_compared = len(comparisons)
            scan.render_diff_ran = True
            scan.findings += render_diff.detect_render_diff(comparisons)
    finally:
        document.close()

    return scan


def scan_text(
    normalized_text: str,
    metadata: dict[str, str] | None = None,
    *,
    raw_text: str | None = None,
) -> DocumentScan:
    """The detectors that need only text.

    For DOCX and plain text, where there is no page to rasterise and no span
    colour to read. Narrower coverage, and the run records that: a text document
    that passes has passed fewer checks than a PDF that passes.
    """
    scan = DocumentScan(metadata=metadata or {})
    scan.findings += detectors.run_text_detectors(normalized_text, raw_text=raw_text)
    scan.findings += detectors.detect_metadata_instructions(scan.metadata)
    scan.render_diff_skipped_reason = "this document has no pages to compare"
    return scan


class Scanner:
    """The ``DocumentScanner`` the pipeline receives.

    A thin adapter over the module functions above. It exists so the node can
    hold a protocol rather than an import: a node that reached into this module
    directly could not be run against a fake, and the offline suite is what
    makes every security claim here checkable without a network.
    """

    def __init__(
        self,
        *,
        ocr: OcrEngine | None = None,
        render_diff_enabled: bool = True,
        render_diff_max_pages: int = render_diff.RENDER_DIFF_MAX_PAGES,
    ) -> None:
        self.ocr = ocr
        self.render_diff_enabled = render_diff_enabled
        self.render_diff_max_pages = render_diff_max_pages

    def scan(
        self,
        data: bytes,
        *,
        normalized_text: str,
        raw_text: str,
        mime_type: str,
    ) -> ScanResult:
        if mime_type == "application/pdf":
            found = scan_pdf(
                data,
                normalized_text,
                raw_text=raw_text,
                ocr=self.ocr,
                render_diff_enabled=self.render_diff_enabled,
                render_diff_max_pages=self.render_diff_max_pages,
            )
        else:
            found = scan_text(normalized_text, raw_text=raw_text)

        return ScanResult(
            findings=tuple(found.findings),
            metadata=found.metadata,
            render_diff_ran=found.render_diff_ran,
            render_diff_skipped_reason=found.render_diff_skipped_reason,
            pages_compared=found.pages_compared,
        )
