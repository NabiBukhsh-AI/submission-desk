"""One piece of evidence, as a reviewer sees it.

The card is the product. A band without the quotations behind it is an opinion,
and a quotation without a link to the page it came from is a claim the reviewer
has to take on trust — which is the thing this whole system exists not to ask
for.

So every card shows four things: what the document was read as saying, the
words it actually used, where those words are, and how the system checked them.
The fourth is the one people leave out.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from domain.contracts.enums import EvidenceState, ModelTier, SpanValidation

#: What each state is called on a card. The enum values read as jargon.
STATE_LABELS: dict[EvidenceState, tuple[str, str]] = {
    EvidenceState.SUPPORTED: ("Supports", "✅"),
    EvidenceState.CONTRADICTED: ("Contradicts", "⚠️"),
    EvidenceState.INSUFFICIENT_EVIDENCE: ("Not addressed", "—"),
}

#: How the quotation was matched, in words. A reviewer deciding whether to trust
#: a card needs to know the difference between "found exactly" and "found after
#: allowing for scanner errors".
VALIDATION_LABELS: dict[SpanValidation, str] = {
    SpanValidation.VALID_EXACT: "Found in the document, character for character.",
    SpanValidation.VALID_NORMALIZED: (
        "Found in the document, allowing for spacing and punctuation differences."
    ),
    SpanValidation.VALID_FUZZY_OCR: (
        "Found in the document, allowing for scanner misreadings on a scanned page."
    ),
    SpanValidation.INVALID_NOT_FOUND: "Not found in the document. Excluded from the score.",
    SpanValidation.INVALID_WRONG_DOCUMENT: (
        "Found somewhere other than the document it was attributed to. Excluded."
    ),
    SpanValidation.INVALID_OFFSET_MISMATCH: (
        "Found in the document, but not where it was said to be. Excluded."
    ),
    SpanValidation.NOT_APPLICABLE: "Nothing to check: no quotation was offered.",
}

#: Where a self-reported confidence stops being described as high.
#:
#: Shown as a word rather than a number on purpose. A model's certainty is
#: weakly calibrated, and "0.87" invites a reader to treat it as a probability
#: when it is closer to a tone of voice.
HIGH_CONFIDENCE = 0.85
MODERATE_CONFIDENCE = 0.6

TIER_LABELS: dict[ModelTier, str] = {
    ModelTier.CHEAP: "standard read",
    ModelTier.STRONG: "second read",
}


def render(item: Any, *, rejected: bool = False, debug: bool = False) -> None:
    """Draw one evidence card.

    ``rejected`` changes the framing rather than hiding anything. A quotation
    the validator could not find is the single most useful thing a reviewer can
    see, because it is the measurable form of the failure everybody worries
    about.
    """
    label, icon = STATE_LABELS.get(item.state, (item.state.value, "•"))

    with st.container(border=True):
        header, meta = st.columns([3, 2])
        with header:
            st.markdown(f"**{icon} {label}**")
            st.write(item.claim)
        with meta:
            if rejected:
                st.markdown(":red[Excluded from the score]")
            st.caption(_confidence_line(item))

        if item.verbatim_span:
            st.markdown("> " + item.verbatim_span.replace("\n", " "))
            st.caption(_where(item))

        st.caption(VALIDATION_LABELS.get(item.span_validation, item.span_validation.value))

        if debug:
            # Off unless ?debug=1. A recruiter who found this would reasonably
            # conclude the rest of the interface had been hiding something.
            st.json(item.model_dump(mode="json"))


def _confidence_line(item: Any) -> str:
    """Confidence and which read produced it.

    Confidence is shown as a word rather than a number on purpose: a model's
    self-reported certainty is weakly calibrated, and "0.87" invites a reader to
    treat it as a probability.
    """
    tier = TIER_LABELS.get(item.model_tier, "read")
    return f"{_confidence_word(item.confidence)} confidence, {tier}"


def _confidence_word(value: float | None) -> str:
    if value is None:
        return "Unstated"
    if value >= HIGH_CONFIDENCE:
        return "High"
    if value >= MODERATE_CONFIDENCE:
        return "Moderate"
    return "Low"


def _where(item: Any) -> str:
    """Page and position, for a reviewer who wants to look."""
    provenance = item.provenance
    if provenance is None:
        return "No location was recorded."

    if provenance.page_start == provenance.page_end:
        return f"Page {provenance.page_start}"
    return f"Pages {provenance.page_start} to {provenance.page_end}"


def render_group(items: list[Any], *, rejected: bool = False, debug: bool = False) -> None:
    """A list of cards, or a sentence saying there are none.

    An empty area with no explanation reads as a broken page. "Nothing was
    excluded" is information.
    """
    if not items:
        st.caption(
            "Nothing was excluded from this candidate's assessment."
            if rejected
            else "No evidence was found for this point."
        )
        return

    for item in items:
        render(item, rejected=rejected, debug=debug)
