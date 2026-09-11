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

import streamlit as st

from app.passages import passage_around, source_for

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


def _escape(text: str) -> str:
    """Markdown characters in a candidate's CV are not formatting.

    A CV containing ``**`` or an underscore would otherwise render as bold or
    italic, which changes what the reviewer sees the document as saying.
    """
    for char in ("\\", "*", "_", "`", "[", "]", "#"):
        text = text.replace(char, "\\" + char)
    return text.replace("\n", " ")
