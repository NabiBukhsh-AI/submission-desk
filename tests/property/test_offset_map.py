"""The offset map, over text nobody thought to write down.

The map is what turns a character range into "page 2 of CV.pdf" for a reviewer.
A map that is subtly wrong sends them to the wrong paragraph, and nothing else
in the system would notice: the span still validates, the band is still
computed, and the citation still looks right until someone checks it.

Generated text rather than examples, because the failures live in the
combinations: a soft hyphen inside a ligature before an emoji, a right-to-left
mark between two dashes. Nobody writes those tests by hand.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from domain.provenance.normalization import (
    DASHES,
    INVISIBLE,
    LIGATURES,
    QUOTES,
    normalize,
    normalize_with_map,
)
from domain.provenance.offsets import (
    OffsetOutOfRange,
    covers,
    raw_span,
    to_norm,
    to_raw,
)

#: The characters that actually break offset maps: things that expand, things
#: that vanish, things that are several bytes, and things that reorder.
awkward = st.sampled_from(
    [
        *LIGATURES,
        *DASHES,
        *QUOTES,
        *INVISIBLE,
        "­",  # soft hyphen
        "한",  # CJK
        "日",
        "ع",  # RTL
        "א",
        "😀",  # multi-byte emoji
        "👨‍👩‍👧",  # emoji with joiners
        "\n",
        "\t",
        "  ",
        "-",
        "a",
        "Z",
        "0",
        "9",
        ".",
        "|",
    ]
)

text_with_awkward_characters = st.lists(awkward, min_size=1, max_size=60).map("".join)


@given(text=text_with_awkward_characters)
@settings(max_examples=400, deadline=None)
def test_the_map_covers_every_normalised_character(text: str) -> None:
    """A character with no entry has no route back to the document."""
    result = normalize_with_map(text)

    assert sum(run.length for run in result.runs) == len(result.text)


@given(text=text_with_awkward_characters)
@settings(max_examples=400, deadline=None)
def test_every_normalised_index_maps_into_the_original(text: str) -> None:
    """The round trip. Every position in the normalised text points at a real
    position in the text the reviewer will open."""
    result = normalize_with_map(text)

    for index in range(len(result.text)):
        raw = to_raw(index, result.runs)
        assert 0 <= raw < len(text), f"index {index} mapped outside the original"


@given(text=text_with_awkward_characters)
@settings(max_examples=400, deadline=None)
def test_the_runs_are_ordered_and_contiguous(text: str) -> None:
    result = normalize_with_map(text)

    expected = 0
    for run in result.runs:
        assert run.norm_start == expected
        assert run.length > 0
        expected += run.length


@given(text=text_with_awkward_characters)
@settings(max_examples=300, deadline=None)
def test_mapping_back_and_forth_is_stable(text: str) -> None:
    """The round trip, stated correctly for a profile that expands characters.

    One source character can become several: the ligature in "final" is a single
    code point that normalises to "fi". Both normalised positions therefore
    point at the same source index, and mapping that index forward returns the
    first of them rather than the one you started from.

    The property that actually matters for a highlight is that going back and
    forth again lands in the same place, so a highlight drawn from a mapped
    position covers the same source characters.
    """
    result = normalize_with_map(text)

    for index in range(len(result.text)):
        raw = to_raw(index, result.runs)
        assert to_raw(to_norm(raw, result.runs), result.runs) == raw


@given(text=text_with_awkward_characters)
@settings(max_examples=300, deadline=None)
def test_the_map_never_goes_backwards(text: str) -> None:
    """Reading order is preserved, so a highlight is a contiguous range in the
    original rather than two pieces from different places.

    Non-decreasing rather than strictly increasing, because an expanding
    substitution maps several normalised characters to one source character:
    "fi" from a single ligature is two positions pointing at the same index.
    """
    result = normalize_with_map(text)

    previous = -1
    for index in range(len(result.text)):
        raw = to_raw(index, result.runs)
        assert raw >= previous, "the map moved backwards through the original"
        previous = raw


@given(text=text_with_awkward_characters)
@settings(max_examples=300, deadline=None)
def test_normalisation_is_idempotent(text: str) -> None:
    """Or the validator's comparison would depend on how many times each side
    had been through the profile."""
    once = normalize(text)

    assert normalize(once) == once


@given(text=text_with_awkward_characters)
@settings(max_examples=200, deadline=None)
def test_a_span_maps_to_a_range_in_the_original(text: str) -> None:
    result = normalize_with_map(text)
    if len(result.text) < 2:
        return

    start, end = raw_span(0, len(result.text), result.runs)

    assert 0 <= start < end <= len(text)


@given(text=text_with_awkward_characters)
@settings(max_examples=200, deadline=None)
def test_an_index_past_the_end_is_an_error_not_a_guess(text: str) -> None:
    """Sending a reviewer to approximately the right place is worse than telling
    them the mapping failed."""
    import pytest

    result = normalize_with_map(text)

    with pytest.raises(OffsetOutOfRange):
        to_raw(len(result.text) + 5, result.runs)


@given(text=text_with_awkward_characters)
@settings(max_examples=200, deadline=None)
def test_coverage_agrees_with_the_mapping(text: str) -> None:
    result = normalize_with_map(text)

    for index in range(len(result.text)):
        assert covers(index, result.runs)
    assert not covers(len(result.text), result.runs)


def test_a_removed_character_has_no_normalised_counterpart() -> None:
    """A soft hyphen is deleted by the profile, so asking where it went in the
    normalised text is a question with no answer, and the map says so rather
    than returning the nearest survivor."""
    import pytest

    original = "produc­tion"
    result = normalize_with_map(original)

    with pytest.raises(OffsetOutOfRange, match="removed by the normalisation profile"):
        to_norm(original.index("­"), result.runs)


def test_an_empty_span_is_refused() -> None:
    import pytest

    result = normalize_with_map("some text")

    with pytest.raises(OffsetOutOfRange, match="at least one character"):
        raw_span(3, 3, result.runs)
