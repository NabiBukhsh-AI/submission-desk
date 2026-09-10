"""The mechanical check that turns a claim into a verified fact.

Every quotation a model returns is looked for in the document it says it came
from. If it is not there, the evidence is discarded from scoring, kept for the
reviewer, and counted. That single check is what makes "every claim is
traceable" a measured property rather than a promise.

The hard part is not rejecting fabrications. It is rejecting fabrications
*without* rejecting real evidence read off a scan, where the reader turned
"Owned" into "0wned". Getting that wrong in the strict direction destroys the
scanned-document case; getting it wrong in the lenient direction accepts
inventions. Hence four tiers, each narrower than the last, with the loosest one
applied only to pages that genuinely went through OCR.

Pure. No I/O, no clock, no model. The thresholds arrive as arguments so they can
be swept against real data rather than argued about.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from uuid import UUID

from domain.contracts.enums import EvidenceState, ExtractionMethod, SpanValidation
from domain.contracts.evidence import EvidenceItem
from domain.contracts.source_text import SourceText
from domain.provenance import ocr_confusions
from domain.provenance.normalization import normalize


@dataclass(frozen=True)
class SpanThresholds:
    """Starting values, not findings.

    ``scripts/tune_span_thresholds.py`` sweeps these against real OCR evidence
    and fabricated spans and reports the ROC. Until that has run they are
    untuned defaults, and the documentation says so.
    """

    offset_tolerance: int = 400
    fuzzy_search_radius: int = 1500
    #: Measured, not chosen. ``python -m scripts.tune_span_thresholds`` sweeps
    #: both over a corpus of really-present spans degraded the way each
    #: extraction path degrades them, and a corpus of fabrications close enough
    #: to the document to be hard.
    #:
    #: 0.92 for digital text is the lowest value on the plateau where every
    #: fabrication is caught: the sweep validated the value that was already
    #: here rather than changing it.
    #:
    #: 0.90 for OCR replaces a guessed 0.88. At 0.88 the sweep accepted 30% of
    #: fabrications; at 0.90 it accepts 20% and rejects one real span in
    #: sixteen. Neither error reaches zero at any threshold, which is why the
    #: schema, the human gate and the rejected-evidence panel are the other
    #: three controls rather than decoration.
    fuzzy_threshold: float = 0.92
    fuzzy_ocr_threshold: float = 0.90


@dataclass(frozen=True)
class SpanCheck:
    """What the validator concluded, and everything it measured on the way.

    The ratio and the corrected offset are recorded whatever the outcome, so a
    threshold can later be chosen from the distribution of real against
    fabricated spans rather than from an opinion.
    """

    validation: SpanValidation
    ratio: float | None = None
    validated_norm_start: int | None = None
    #: A span that crosses a layout block is the signature of a reading-order
    #: mistake rather than a fabrication, so it is flagged and shown rather
    #: than rejected.
    crosses_block: bool = False


def validate(
    item: EvidenceItem,
    sources: Mapping[UUID, SourceText],
    thresholds: SpanThresholds | None = None,
) -> SpanCheck:
    """Locate one quotation in its own document."""
    thresholds = thresholds or SpanThresholds()

    refusal = _precheck(item, sources)
    if refusal is not None:
        return refusal

    # _precheck established both of these.
    assert item.verbatim_span is not None
    assert item.provenance is not None
    source = sources[item.provenance.document_id]
    provenance = item.provenance

    needle = normalize(item.verbatim_span)
    haystack = source.normalized_text
    if not needle:
        return SpanCheck(validation=SpanValidation.INVALID_NOT_FOUND, ratio=0.0)

    claimed_start = provenance.norm_start
    crosses = _crosses_block(source, claimed_start, provenance.norm_end)

    # Step 2: the offsets are right and the text matches.
    if haystack[claimed_start : claimed_start + len(needle)] == needle:
        return SpanCheck(
            validation=SpanValidation.VALID_EXACT,
            ratio=1.0,
            validated_norm_start=claimed_start,
            crosses_block=crosses,
        )

    # Step 3: the quotation is right and the offsets are not.
    #
    # Models count characters poorly. A correct quotation with a wrong offset is
    # the common failure, and treating it as a fabrication would discard real
    # evidence for a clerical reason.
    relocated = _relocate(haystack, needle, claimed_start, thresholds.offset_tolerance)
    if relocated is not None:
        return SpanCheck(
            validation=relocated[0],
            ratio=1.0 if relocated[0] is SpanValidation.VALID_NORMALIZED else None,
            validated_norm_start=relocated[1],
            crosses_block=_crosses_block(source, relocated[1], relocated[1] + len(needle))
            if relocated[1] is not None
            else crosses,
        )

    # Step 4: near enough, judged by how the page was read.
    return _fuzzy(source, needle, claimed_start, thresholds, crosses)


def _precheck(item: EvidenceItem, sources: Mapping[UUID, SourceText]) -> SpanCheck | None:
    """Steps 0 and 1: is there anything to check, and is it ours to check.

    Returns the verdict when the answer is no, and None when validation should
    proceed.
    """
    # Step 0: absence of evidence has nothing to locate.
    if item.state is EvidenceState.INSUFFICIENT_EVIDENCE or not item.verbatim_span:
        return SpanCheck(validation=SpanValidation.NOT_APPLICABLE)

    if item.provenance is None:
        return SpanCheck(validation=SpanValidation.INVALID_NOT_FOUND, ratio=0.0)

    # Step 1: the document must be one of the candidate's own.
    #
    # This single line is what stops a model citing a calibration card. Those
    # cards are never in `sources`, so a quotation taken from one resolves to a
    # document that is not there and is rejected by name.
    if item.provenance.document_id not in sources:
        return SpanCheck(validation=SpanValidation.INVALID_WRONG_DOCUMENT, ratio=0.0)

    return None


def _relocate(
    haystack: str, needle: str, claimed_start: int, tolerance: int
) -> tuple[SpanValidation, int | None] | None:
    """Find the quotation verbatim, somewhere other than where it was claimed.

    One occurrence is unambiguous. Several are only unambiguous if one of them
    is near the claimed offset: a model citing the third of five identical
    bullets should not be credited with citing the first. Beyond the tolerance
    the citation is refused rather than resolved arbitrarily, because a citation
    nobody can locate is not a citation.
    """
    occurrences: list[int] = []
    position = haystack.find(needle)
    while position != -1:
        occurrences.append(position)
        position = haystack.find(needle, position + 1)

    if not occurrences:
        return None

    if len(occurrences) == 1:
        return SpanValidation.VALID_NORMALIZED, occurrences[0]

    nearest = min(occurrences, key=lambda start: abs(start - claimed_start))
    if abs(nearest - claimed_start) <= tolerance:
        return SpanValidation.VALID_NORMALIZED, nearest

    return SpanValidation.INVALID_OFFSET_MISMATCH, None


def _fuzzy(
    source: SourceText,
    needle: str,
    claimed_start: int,
    thresholds: SpanThresholds,
    crosses: bool,
) -> SpanCheck:
    """Accept a near match, at a threshold set by how the page was read.

    A digitally extracted page is held to a high bar: the text was copied, so a
    quotation that differs by much was not copied from it. A page that went
    through OCR is held to a lower one and compared through the confusion map,
    because the difference between "Owned" and "0wned" is the reader's mistake
    and not the model's invention.
    """
    haystack = source.normalized_text
    window = max(len(needle) * 2, thresholds.fuzzy_search_radius)
    start = max(0, claimed_start - window)
    near = haystack[start : claimed_start + window]

    is_ocr = _page_method(source, claimed_start) is ExtractionMethod.OCR

    if is_ocr:
        folded_needle = ocr_confusions.fold(needle)
        candidates = (
            (ocr_confusions.fold(near), start),
            (ocr_confusions.fold(haystack), 0),
        )
        threshold = thresholds.fuzzy_ocr_threshold
        verdict = SpanValidation.VALID_FUZZY_OCR
    else:
        folded_needle = needle
        candidates = ((near, start), (haystack, 0))
        threshold = thresholds.fuzzy_threshold
        verdict = SpanValidation.VALID_NORMALIZED

    best = 0.0
    for candidate, _ in candidates:
        if not candidate:
            continue
        best = max(best, partial_ratio(folded_needle, candidate))
        if best >= threshold:
            break

    if best >= threshold:
        return SpanCheck(
            validation=verdict,
            ratio=round(best, 4),
            validated_norm_start=claimed_start,
            crosses_block=crosses,
        )

    return SpanCheck(
        validation=SpanValidation.INVALID_NOT_FOUND,
        ratio=round(best, 4),
        validated_norm_start=None,
        crosses_block=crosses,
    )


def partial_ratio(needle: str, haystack: str) -> float:
    """How well the best window of the haystack matches the needle, 0 to 1.

    The standard library rather than a similarity package: domain code may
    import only the standard library and pydantic, and a dependency that
    duplicates the standard library is one the rules forbid. Sizes here are
    small enough that it does not matter, since a quotation is at most 600
    characters and this runs only after two exact checks have failed.

    Windows step by a quarter of the needle's length. Sliding one character at a
    time would be exact and quadratic; a quarter-length step finds the right
    neighbourhood and the matcher handles the alignment inside it.
    """
    if not needle or not haystack:
        return 0.0
    if len(needle) >= len(haystack):
        return SequenceMatcher(None, needle, haystack).ratio()

    span = len(needle)
    step = max(1, span // 4)
    best = 0.0

    for start in range(0, len(haystack) - span + 1, step):
        window = haystack[start : start + span]
        # real_quick_ratio is an upper bound and costs almost nothing, so a
        # window that cannot beat the best so far is skipped before the real
        # comparison runs.
        matcher = SequenceMatcher(None, needle, window)
        if matcher.real_quick_ratio() <= best:
            continue
        best = max(best, matcher.ratio())
        if best >= 1.0:
            break

    return best


def _page_method(source: SourceText, norm_index: int) -> ExtractionMethod | None:
    """How the page containing this offset was read.

    The OCR tolerance is applied per page, not per document. A quotation from
    the typed first page of a document whose third page is a scan gets the
    strict threshold, which is what stops the scan's presence from loosening
    validation for the whole file.
    """
    for page in source.pages:
        if page.norm_start <= norm_index < page.norm_end:
            return page.extraction_method
    return source.pages[0].extraction_method if source.pages else None


def _crosses_block(source: SourceText, start: int, end: int) -> bool:
    """Whether a span runs past the layout block it starts in.

    Text from two columns can sit next to each other in the buffer without ever
    having been adjacent on the page. A quotation spanning that seam exists in
    the extracted text but not in the document a person reads, so it is flagged
    for the reviewer rather than silently accepted or silently dropped.
    """
    if not source.block_boundaries:
        return False

    for block_start, block_end in source.block_boundaries:
        if block_start <= start < block_end:
            return end > block_end
    return False
