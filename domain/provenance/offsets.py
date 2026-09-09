"""Translating between normalised and original coordinates.

Every span the reviewer clicks resolves through here. The runs come from the
normalisation profile, so this module does arithmetic rather than guessing, and
an index outside the map is an error rather than a nearby guess: sending a
reviewer to approximately the right place is worse than telling them the
mapping failed.
"""

from __future__ import annotations

from collections.abc import Sequence

from domain.contracts.source_text import OffsetRun
from domain.errors import SubmissionDeskError


class OffsetOutOfRange(SubmissionDeskError):
    """An index that the offset map does not cover."""

    error_code = "OFFSET_OUT_OF_RANGE"


def to_raw(norm_index: int, runs: Sequence[OffsetRun]) -> int:
    """Where a normalised index sits in the original text."""
    for run in runs:
        if run.norm_start <= norm_index < run.norm_start + run.length:
            return run.raw_start + (norm_index - run.norm_start)
    raise OffsetOutOfRange(f"normalised index {norm_index} is outside the offset map")


def to_norm(raw_index: int, runs: Sequence[OffsetRun]) -> int:
    """Where an original index sits in the normalised text.

    Not every original index has a normalised counterpart: characters the
    profile removed have none. Such an index raises rather than returning the
    nearest survivor, because a highlight that silently shifts is worse than one
    that reports it cannot be drawn.
    """
    for run in runs:
        if run.raw_start <= raw_index < run.raw_start + run.length:
            return run.norm_start + (raw_index - run.raw_start)
    raise OffsetOutOfRange(
        f"original index {raw_index} has no normalised counterpart; "
        "it was removed by the normalisation profile"
    )


def raw_span(norm_start: int, norm_end: int, runs: Sequence[OffsetRun]) -> tuple[int, int]:
    """The original range covering a normalised range.

    The end is exclusive and is computed from the last included character, so a
    span ending at a collapsed run of whitespace does not swallow the words
    after it.
    """
    if norm_start >= norm_end:
        raise OffsetOutOfRange("a span must cover at least one character")
    return to_raw(norm_start, runs), to_raw(norm_end - 1, runs) + 1


def covers(norm_index: int, runs: Sequence[OffsetRun]) -> bool:
    """Whether the map has an entry for this index."""
    return any(run.norm_start <= norm_index < run.norm_start + run.length for run in runs)
