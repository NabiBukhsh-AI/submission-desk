"""One number a reviewer can read, composed deterministically.

Extraction confidence is not a model output and is never presented as one. It is
four measurable facts about how the reading went, weighted and summed, and the
formula lives here so a low number can be explained rather than merely shown.

Below the warning threshold the reviewer is told to check quotations against the
original, which is the honest thing to say about a poor scan.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.contracts.enums import ExtractionMethod
from domain.ports.extraction import PageText

#: Below this the reviewer is warned that quotations need checking by eye.
WARN_THRESHOLD = 0.6

#: Characters on a page below which the page yielded nothing worth counting.
MIN_CHARS_PER_PAGE = 50

#: Component scores below which the explanation names that component as a
#: reason the reading went poorly. Kept apart from WARN_THRESHOLD, which decides
#: whether to warn at all: these decide what the warning says.
COVERAGE_EXPLAIN_BELOW = 0.9
METHOD_EXPLAIN_BELOW = 0.8
STRUCTURE_EXPLAIN_BELOW = 0.8

#: What each method is worth before measured OCR confidence is considered.
#: DOCX scores below a digital PDF because flattening tables to rows loses
#: layout that occasionally matters to a quotation's meaning.
METHOD_SCORES = {
    ExtractionMethod.DIGITAL_PDF: 1.0,
    ExtractionMethod.PLAINTEXT: 1.0,
    ExtractionMethod.DOCX: 0.75,
    ExtractionMethod.OCR: 0.0,
}


@dataclass(frozen=True)
class ConfidenceBreakdown:
    """The four components, kept apart so a low score can be explained."""

    coverage: float
    method: float
    structure: float
    language: float

    @property
    def total(self) -> float:
        return min(
            1.0,
            0.4 * self.coverage + 0.3 * self.method + 0.2 * self.structure + 0.1 * self.language,
        )

    @property
    def is_poor(self) -> bool:
        return self.total < WARN_THRESHOLD

    def explain(self) -> str:
        """Why the number is what it is, in a sentence for the reviewer."""
        parts = []
        if self.coverage < COVERAGE_EXPLAIN_BELOW:
            parts.append("some pages yielded little or no text")
        if self.method < METHOD_EXPLAIN_BELOW:
            parts.append("some pages were read by optical character recognition")
        if self.structure < STRUCTURE_EXPLAIN_BELOW:
            parts.append("the page structure was hard to follow")
        if self.language < 1.0:
            parts.append("more than one language was present")
        return "; ".join(parts) or "the document was read cleanly"


def compute(pages: list[PageText], *, language_score: float = 1.0) -> ConfidenceBreakdown:
    """Score how well a document was read.

    Every component is measured. An empty document scores zero rather than
    defaulting to something comfortable.
    """
    if not pages:
        return ConfidenceBreakdown(coverage=0.0, method=0.0, structure=0.0, language=0.0)

    with_text = [page for page in pages if page.char_count >= MIN_CHARS_PER_PAGE]
    coverage = len(with_text) / len(pages)

    method_scores = [
        (page.ocr_confidence or 0.0)
        if page.method is ExtractionMethod.OCR
        else METHOD_SCORES[page.method]
        for page in pages
    ]
    method = sum(method_scores) / len(method_scores)

    structured = [page for page in pages if len(page.blocks) > 1]
    structure = len(structured) / len(pages)

    return ConfidenceBreakdown(
        coverage=coverage,
        method=method,
        structure=structure,
        language=language_score,
    )
