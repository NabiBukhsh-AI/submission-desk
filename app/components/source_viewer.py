"""Showing a reviewer where a quotation actually is.

The whole evidence claim ends here. A card says "page 2" and this is what makes
that checkable in one click rather than by opening the PDF and scrolling.

Two modes, and the fallback is not a failure. Rendering the page as an image
with the span highlighted is better; page number plus quotation is enough. The
architecture puts page rendering on the cut list, so the fallback is the
supported path and not an apology.

Offsets are resolved through the offset map. A span is located in normalized
text, and the document a person reads is the raw text, so showing the normalized
position would point at the wrong characters in any document containing a
ligature or a curly quote.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import streamlit as st

#: How much text around the quotation to show in the fallback. Enough to see the
#: sentence it sits in, which is what tells a reviewer whether it was quoted
#: fairly.
CONTEXT_CHARS = 240

#: Rendering a page costs a rasterisation. Above this many, the viewer stays in
#: fallback mode rather than making the reviewer wait.
MAX_RENDER_PAGES = 40


def render(item: Any, deps: Any, documents: list[Any], *, debug: bool = False) -> None:
    """Show where this quotation sits in its document.

    ``documents`` is passed in rather than looked up per card: a review page
    draws twenty cards, and twenty queries for one answer is twenty round trips.
    """
    provenance = getattr(item, "provenance", None)
    if provenance is None or not item.verbatim_span:
        st.caption("This item did not quote the document, so there is nothing to show.")
        return

    source = source_for(deps, documents, provenance.document_id)
    if source is None:
        st.caption(
            f"Page {provenance.page_start}. The document's text is no longer stored, "
            "so the surrounding passage cannot be shown."
        )
        return

    before, quoted, after = passage_around(source, item, provenance)

    st.markdown(f"**Page {provenance.page_start}**")
    st.markdown(
        f"…{_escape(before)}**:blue[{_escape(quoted)}]**{_escape(after)}…",
    )

    if debug:
        st.caption(
            f"normalized {provenance.norm_start}–{provenance.norm_end}, "
            f"validated at {getattr(item, 'validated_norm_start', None)}"
        )


def passage_around(source: Any, item: Any, provenance: Any) -> tuple[str, str, str]:
    """The quotation with its surroundings, in the text a person would read.

    The validated offset is preferred over the reported one. A model reports
    where it thinks the span is; the validator finds where it actually is, and
    the second number is the one that points at the right characters.
    """
    validated = getattr(item, "validated_norm_start", None)
    start = validated if validated is not None else provenance.norm_start
    length = len(item.verbatim_span)

    raw_start = _to_raw(source, start)
    raw_end = _to_raw(source, start + length)

    text = source.raw_text
    quoted = text[raw_start:raw_end]

    # If the offsets do not land on the quotation — a stored span from an older
    # extraction, a document re-parsed under a different profile — fall back to
    # searching for it. Showing the wrong passage would be worse than showing a
    # slightly different one.
    if item.verbatim_span not in quoted:
        found = text.find(item.verbatim_span)
        if found >= 0:
            raw_start, raw_end = found, found + length
            quoted = text[raw_start:raw_end]

    return (
        text[max(0, raw_start - CONTEXT_CHARS) : raw_start],
        quoted or item.verbatim_span,
        text[raw_end : raw_end + CONTEXT_CHARS],
    )


def _to_raw(source: Any, norm_index: int) -> int:
    """Normalized offset to raw offset, through the offset map.

    The map is a list of runs rather than a per-character table, because a
    per-character table for a forty-page CV is most of a megabyte and the runs
    are usually a handful.
    """
    runs = getattr(source, "offset_runs", None) or []
    for run in runs:
        if run.norm_start <= norm_index < run.norm_start + run.length:
            return run.raw_start + (norm_index - run.norm_start)

    # Past the last run: clamp to the end rather than raising. A viewer that
    # crashed on an out-of-range offset would take the whole review page with it.
    if runs:
        last = runs[-1]
        return min(last.raw_start + last.length, len(source.raw_text))
    return min(norm_index, len(source.raw_text))


def source_for(deps: Any, documents: list[Any], document_id: UUID) -> Any:
    """The stored text for one document, or None if it is no longer held."""
    for document in documents:
        if document.document_id == document_id:
            return deps.candidates.get_source_text(
                document.document_sha256, deps.extractor.profile_id
            )
    return None


def _escape(text: str) -> str:
    """Markdown characters in a candidate's CV are not formatting.

    A CV containing ``**`` or an underscore would otherwise render as bold or
    italic, which changes what the reviewer sees the document as saying.
    """
    for char in ("\\", "*", "_", "`", "[", "]", "#"):
        text = text.replace(char, "\\" + char)
    return text.replace("\n", " ")
