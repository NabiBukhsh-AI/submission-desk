"""Where a quotation actually sits in its document.

A card says "page 2"; this is what makes that checkable in one click. Offsets
are resolved through the offset map: a span is located in normalised text, and
the text a person reads is the raw text, so showing the normalised position
would point at the wrong characters in any document with a ligature or a curly
quote.

Shared by the Streamlit viewer and the HTTP API so both show the same passage.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

#: How much text around the quotation to show. Enough to see the sentence it
#: sits in, which is what tells a reviewer whether it was quoted fairly.
CONTEXT_CHARS = 240


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
    """Normalised offset to raw offset, through the offset map.

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
            return deps.candidates.get_source_text(document.document_sha256, deps.source_profile_id)
    return None
