"""Read the page the way a person does, and compare.

The strongest single control in the set, because it is mechanism-independent. It
does not ask *how* text was hidden — white ink, two-point type, off-page
coordinates, a covering image, something nobody has thought of yet. It rasterises
the page, reads the picture, and reports what the parser saw that the reader
would not.

That generality is bought with a rasterisation and an OCR pass per page, which
is why it is gated by configuration and by a page limit. It is on the cut list as
a performance concession and never as a correctness one: turning it off narrows
coverage to the mechanisms the other detectors name, and the run says so.

The comparison is deliberately forgiving. OCR misreads characters, drops
punctuation, and reorders columns; a strict diff would fire on every scanned
document. What survives that forgiveness is a long run of text present in the
extraction and absent from the picture, which has no innocent explanation.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from domain.contracts.enums import IntegrityFindingKind, Severity
from domain.contracts.integrity import EXCERPT_MAX_CHARS, IntegrityFinding
from domain.ports.extraction import OcrEngine

#: Pages beyond this are not rendered. A sixty-page document would take minutes
#: for a check that matters most on the first page of a CV.
RENDER_DIFF_MAX_PAGES = 10

#: Rendering resolution. Below 200 the OCR misses small type, which is precisely
#: the type this detector exists to find.
RENDER_DPI = 220

#: A run shorter than this is OCR noise. Longer, and somebody wrote it.
MIN_UNMATCHED_CHARS = 60

#: How much of a sentence must survive OCR before it counts as seen. Low,
#: because OCR is bad; a sentence recovered at 60% was on the page.
SEEN_RATIO = 0.6

#: Below this many words, OCR variance is the whole signal and a "missing"
#: fragment says nothing.
MIN_SENTENCE_WORDS = 4


@dataclass(frozen=True)
class RenderComparison:
    """What one page looked like from each side."""

    page_number: int
    extracted: str
    rendered: str
    unmatched: str


def _words(text: str) -> list[str]:
    """Lowercase alphanumeric words. Punctuation and case are where OCR fails
    first and where meaning survives, so both are discarded."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _sentences(text: str) -> list[str]:
    """Chunks big enough to judge. Split on sentence enders and line breaks,
    because a hidden instruction is usually its own line."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [part.strip() for part in parts if part.strip()]


def unmatched_text(extracted: str, rendered: str) -> str:
    """Text the parser found that the picture does not support.

    Word-set containment rather than sequence alignment: OCR reorders columns,
    and an alignment-based diff would report the reordering as missing text on
    every two-column CV.
    """
    seen = set(_words(rendered))
    if not seen:
        # Nothing was read from the picture at all. That is an OCR failure, not
        # a finding: reporting the whole page as hidden would fire on every
        # document whenever the OCR binary was missing.
        return ""

    missing = []
    for sentence in _sentences(extracted):
        words = _words(sentence)
        if len(words) < MIN_SENTENCE_WORDS:
            continue
        recovered = sum(1 for word in words if word in seen) / len(words)
        if recovered < SEEN_RATIO:
            missing.append(sentence)

    return " ".join(missing)


def compare_page(
    page: Any, ocr: OcrEngine, *, page_number: int, dpi: int = RENDER_DPI
) -> RenderComparison | None:
    """Rasterise one page, read the picture, and diff it against the text layer.

    Returns ``None`` when the page cannot be rendered or read. A detector that
    raised here would turn a missing OCR binary into a failed run, and the
    correct response to "this control could not run" is to say so, not to stop.
    """
    try:
        extracted = page.get_text()
        pixmap = page.get_pixmap(dpi=dpi)
        image = pixmap.tobytes("png")
    except Exception:
        return None

    if not extracted.strip():
        return None

    try:
        result = ocr.read(image, dpi=dpi)
    except Exception:
        return None

    return RenderComparison(
        page_number=page_number,
        extracted=extracted,
        rendered=result.text,
        unmatched=unmatched_text(extracted, result.text),
    )


def detect_render_diff(
    comparisons: Sequence[RenderComparison],
) -> list[IntegrityFinding]:
    """Findings from pages already compared.

    Split from the rendering so the rule can be tested against fixed pairs of
    strings with no PDF, no image, and no OCR binary anywhere in the test.
    """
    findings = []
    for comparison in comparisons:
        if len(comparison.unmatched) < MIN_UNMATCHED_CHARS:
            continue

        findings.append(
            IntegrityFinding(
                kind=IntegrityFindingKind.HIDDEN_TEXT,
                severity=Severity.HIGH,
                detector="D-RENDER-DIFF",
                # High but not certain: OCR can lose a whole block to a bad
                # scan. Above the quarantine threshold, because the alternative
                # is assessing a document with instructions nobody can see.
                detector_confidence=0.82,
                excerpt=_excerpt(f"page {comparison.page_number}: {comparison.unmatched}"),
            )
        )
    return findings


def _excerpt(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= EXCERPT_MAX_CHARS:
        return collapsed
    return collapsed[: EXCERPT_MAX_CHARS - 1] + "…"


def compare_document(
    document: Any,
    ocr: OcrEngine,
    *,
    max_pages: int = RENDER_DIFF_MAX_PAGES,
    dpi: int = RENDER_DPI,
) -> list[RenderComparison]:
    """Every page worth comparing, up to the limit."""
    comparisons = []
    for index in range(min(document.page_count, max_pages)):
        comparison = compare_page(document[index], ocr, page_number=index + 1, dpi=dpi)
        if comparison is not None:
            comparisons.append(comparison)
    return comparisons
