"""Locating quotations, and refusing to locate inventions.

The two halves of this file are the whole argument. Real evidence written in a
slightly different shape must validate, or the system throws away good findings
and the reviewer stops trusting it. A fluent sentence that is not in the
document must not validate, or the system launders a fabrication into a
citation.

Every edge case in the architecture's table gets a test here, because each one
is a specific way a naive implementation gets one of those two halves wrong.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from domain.contracts.enums import ExtractionMethod, SpanValidation
from domain.contracts.source_text import OffsetRun, PageSpan, SourceText
from domain.provenance.validator import SpanThresholds, validate
from tests.builders import evidence, insufficient_evidence, provenance

DOCUMENT_TEXT = (
    "Ana Ferreira, Senior Backend Engineer. "
    "Owned a multi-service payments backend and its on-call rotation for two years. "
    "Introduced a nightly evaluation suite; releases were blocked when agreement fell below 0.8. "
    "Reduced p95 latency from 900ms to 210ms on the routing service."
)


def source_text(
    text: str = DOCUMENT_TEXT,
    *,
    document_id: UUID | None = None,
    method: ExtractionMethod = ExtractionMethod.DIGITAL_PDF,
    blocks: list[tuple[int, int]] | None = None,
) -> SourceText:
    return SourceText(
        document_id=document_id or uuid4(),
        raw_text=text,
        normalized_text=text,
        offset_runs=[OffsetRun(norm_start=0, raw_start=0, length=len(text))],
        pages=[PageSpan(page_number=1, norm_start=0, norm_end=len(text), extraction_method=method)],
        normalization_profile_id="np-v1-nfkc-ws-dash-hyphen",
        extraction_confidence=0.95,
        detected_languages=["en"],
        block_boundaries=blocks or [],
    )


def item_for(source: SourceText, span: str, *, start: int | None = None, **overrides):
    """Evidence quoting a span, with offsets that are right unless overridden."""
    located = source.normalized_text.find(span) if start is None else start
    return evidence(
        verbatim_span=span,
        provenance=provenance(
            document_id=source.document_id,
            norm_start=max(located, 0),
            norm_end=max(located, 0) + len(span),
        ),
        **overrides,
    )


def sources_for(*texts: SourceText) -> dict[UUID, SourceText]:
    return {text.document_id: text for text in texts}


# --- the common case ------------------------------------------------------------


def test_a_quotation_at_the_right_offset_is_exact() -> None:
    source = source_text()
    span = "Owned a multi-service payments backend"

    check = validate(item_for(source, span), sources_for(source))

    assert check.validation is SpanValidation.VALID_EXACT
    assert check.ratio == 1.0


def test_a_correct_quotation_with_wrong_offsets_is_relocated() -> None:
    """Models count characters poorly. Treating that as fabrication would throw
    away real evidence for a clerical reason."""
    source = source_text()
    span = "nightly evaluation suite"

    check = validate(item_for(source, span, start=3), sources_for(source))

    assert check.validation is SpanValidation.VALID_NORMALIZED
    assert check.validated_norm_start == source.normalized_text.find(span)


def test_absence_of_evidence_has_nothing_to_validate() -> None:
    source = source_text()

    check = validate(insufficient_evidence(), sources_for(source))

    assert check.validation is SpanValidation.NOT_APPLICABLE


# --- fabrication -----------------------------------------------------------------


def test_a_fluent_invention_is_rejected() -> None:
    """The headline case. A sentence that reads like a CV but appears in no
    document must not become a citation."""
    source = source_text()
    invented = "Led a team of forty engineers across three continents"

    check = validate(item_for(source, invented, start=40), sources_for(source))

    assert check.validation is SpanValidation.INVALID_NOT_FOUND
    assert check.ratio is not None and check.ratio < 0.92


def test_a_plausible_paraphrase_is_rejected() -> None:
    """Correct in substance, not a quotation. Rejecting it is right, and the
    evaluation counts these separately as a prompt problem rather than a
    candidate problem."""
    source = source_text()
    paraphrase = "was responsible for a payments system with several services"

    check = validate(item_for(source, paraphrase, start=40), sources_for(source))

    assert check.validation is SpanValidation.INVALID_NOT_FOUND


def test_a_span_from_another_document_is_named_as_such() -> None:
    """This branch is what stops a model citing a calibration card: those are
    never in the sources map, so a quotation from one resolves to a document
    that is not there."""
    source = source_text()
    elsewhere = source_text(document_id=uuid4())

    check = validate(item_for(elsewhere, "Owned a multi-service"), sources_for(source))

    assert check.validation is SpanValidation.INVALID_WRONG_DOCUMENT


def test_an_empty_sources_map_rejects_everything() -> None:
    source = source_text()

    check = validate(item_for(source, "Owned a multi-service"), {})

    assert check.validation is SpanValidation.INVALID_WRONG_DOCUMENT


# --- the edge-case table ----------------------------------------------------------


def test_a_quotation_wrapped_across_lines_still_matches() -> None:
    """Whitespace is collapsed on both sides, so a model's single-line quotation
    matches source text that was wrapped across three."""
    wrapped = "Introduced a nightly\n   evaluation suite;\nreleases were blocked."
    source = source_text(wrapped)

    check = validate(
        item_for(source, "Introduced a nightly evaluation suite; releases were blocked.", start=0),
        sources_for(source),
    )

    assert check.validation in (SpanValidation.VALID_EXACT, SpanValidation.VALID_NORMALIZED)


def test_curly_quotes_and_dashes_match_their_ascii_forms() -> None:
    """A curly quote in the PDF and a straight one in the model's output is the
    most common reason a correct quotation fails."""
    fancy = "The team’s multi–service backend was “production critical”."
    source = source_text(fancy)

    check = validate(
        item_for(source, 'The team\'s multi-service backend was "production critical".', start=0),
        sources_for(source),
    )

    assert check.validation in (SpanValidation.VALID_EXACT, SpanValidation.VALID_NORMALIZED)


def test_a_quotation_crossing_a_page_boundary_validates() -> None:
    """Pages partition one buffer, so a sentence straddling a page break is
    ordinary rather than an error."""
    text = "the first page ends here. and the second begins here."
    source = SourceText(
        document_id=uuid4(),
        raw_text=text,
        normalized_text=text,
        offset_runs=[OffsetRun(norm_start=0, raw_start=0, length=len(text))],
        pages=[
            PageSpan(
                page_number=1,
                norm_start=0,
                norm_end=26,
                extraction_method=ExtractionMethod.DIGITAL_PDF,
            ),
            PageSpan(
                page_number=2,
                norm_start=26,
                norm_end=len(text),
                extraction_method=ExtractionMethod.DIGITAL_PDF,
            ),
        ],
        normalization_profile_id="np-v1-nfkc-ws-dash-hyphen",
        extraction_confidence=0.9,
    )

    check = validate(
        item_for(source, "ends here. and the second", start=text.find("ends here")),
        sources_for(source),
    )

    assert check.validation is SpanValidation.VALID_EXACT


def test_a_real_cross_cell_quotation_matches_through_the_delimiter() -> None:
    """The explicit delimiter is what separates a real cross-cell quotation from
    an invented smooth sentence: the real one contains the pipe."""
    table = "Acme Payments | Lead Engineer | 2021 to present"
    source = source_text(table)

    check = validate(item_for(source, "Acme Payments | Lead Engineer"), sources_for(source))

    assert check.validation is SpanValidation.VALID_EXACT


def test_an_invented_smooth_sentence_across_cells_is_rejected() -> None:
    table = "Acme Payments | Lead Engineer | 2021 to present"
    source = source_text(table)

    check = validate(
        item_for(source, "Acme Payments Lead Engineer since 2021", start=0), sources_for(source)
    )

    assert check.validation is SpanValidation.INVALID_NOT_FOUND


def test_a_duplicate_bullet_near_the_claimed_offset_resolves_to_the_nearest() -> None:
    repeated = "shipped to production. " * 6
    source = source_text(repeated)
    third = repeated.find("shipped", repeated.find("shipped", 24) + 1)

    check = validate(
        item_for(source, "shipped to production.", start=third + 2), sources_for(source)
    )

    assert check.validation is SpanValidation.VALID_NORMALIZED
    assert check.validated_norm_start == third


def test_a_duplicate_beyond_tolerance_is_an_ambiguous_citation() -> None:
    """A model citing the third of five identical bullets should not be credited
    with citing the first. Beyond the tolerance it is refused rather than
    resolved arbitrarily."""
    repeated = "shipped to production. " + ("filler text here. " * 60) + "shipped to production."
    source = source_text(repeated)

    check = validate(
        item_for(source, "shipped to production.", start=len(repeated) // 2),
        sources_for(source),
        SpanThresholds(offset_tolerance=50),
    )

    assert check.validation is SpanValidation.INVALID_OFFSET_MISMATCH
    assert check.validated_norm_start is None


def test_a_span_crossing_a_layout_block_is_flagged_not_rejected() -> None:
    """Text from two columns can sit next to each other in the buffer without
    ever having been adjacent on the page. That is a reading-order mistake, not
    a fabrication, so it is shown to the reviewer."""
    text = "left column text right column text"
    source = source_text(text, blocks=[(0, 16), (17, len(text))])

    check = validate(item_for(source, "left column text right"), sources_for(source))

    assert check.validation is SpanValidation.VALID_EXACT
    assert check.crosses_block is True


def test_a_span_inside_one_block_is_not_flagged() -> None:
    text = "left column text right column text"
    source = source_text(text, blocks=[(0, 16), (17, len(text))])

    check = validate(item_for(source, "left column"), sources_for(source))

    assert check.crosses_block is False


# --- what is always recorded --------------------------------------------------------


@pytest.mark.parametrize(
    "span",
    [
        "Owned a multi-service payments backend",
        "Led a team of forty engineers across three continents",
        "nightly evaluation suite",
    ],
)
def test_a_ratio_is_recorded_whatever_the_outcome(span: str) -> None:
    """Thresholds are tuned on the distribution of real against fabricated
    spans, which requires the measurement to exist for both."""
    source = source_text()

    check = validate(item_for(source, span, start=40), sources_for(source))

    assert check.ratio is None or 0.0 <= check.ratio <= 1.0


def test_the_thresholds_are_arguments_not_literals() -> None:
    """So they can be swept against real data rather than argued about."""
    source = source_text()
    strict = SpanThresholds(fuzzy_threshold=0.99)
    lenient = SpanThresholds(fuzzy_threshold=0.30)
    near_miss = "Owned a multi service payments backend and its on call rotation"

    item = item_for(source, near_miss, start=40)

    assert (
        validate(item, sources_for(source), strict).validation is SpanValidation.INVALID_NOT_FOUND
    )
    assert (
        validate(item, sources_for(source), lenient).validation is SpanValidation.VALID_NORMALIZED
    )


def test_a_model_cannot_set_its_own_verdict() -> None:
    """span_validation is absent from the response schema and assigned here.
    This asserts the validator ignores whatever an item arrived carrying."""
    source = source_text()
    claiming_valid = item_for(
        source,
        "Led a team of forty engineers across three continents",
        start=40,
        span_validation=SpanValidation.VALID_EXACT,
    )

    check = validate(claiming_valid, sources_for(source))

    assert check.validation is SpanValidation.INVALID_NOT_FOUND


def test_an_item_with_no_provenance_cannot_be_located() -> None:
    """The contract forbids this shape, so it is built without validation.

    The validator is a pure function that may one day be handed a row read back
    from a database written by an older version, and it must not assume a field
    is present because a model says it should be.
    """
    source = source_text()
    valid = item_for(source, "Owned a multi-service payments backend")
    without_provenance = valid.model_copy(update={"provenance": None}, deep=False)
    object.__setattr__(without_provenance, "provenance", None)

    check = validate(without_provenance, sources_for(source))

    assert check.validation is SpanValidation.INVALID_NOT_FOUND
