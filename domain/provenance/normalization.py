"""Normalisation, with the offset map as a by-product.

Aggressive normalisation makes span validation robust. Unrecorded normalisation
makes provenance a lie. The resolution is that every step is a pure function
returning text *and* the runs describing where each character moved, composed
into a profile that carries a version id.

The map is produced as the text is transformed, never reconstructed afterwards.
Reconstruction would mean inferring where characters went, and an offset map
that is inferred is one that is subtly wrong in exactly the cases nobody tests.

What is deliberately not done: case is preserved, digits are never altered, and
nothing here fixes spelling. Fixing spelling would let a fabricated span match a
real one, which is the failure this whole subsystem exists to catch.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from domain.contracts.source_text import OffsetRun, SourceText

#: The shipped profile. Any change to the steps or their order changes this id,
#: which invalidates the extraction cache rather than silently mismatching spans
#: validated under the old rules.
PROFILE_ID = "np-v1-nfkc-ws-dash-hyphen"

#: Ligatures NFKC leaves alone or that appear in PDFs from older typesetters.
LIGATURES = {
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬀ": "ff",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
    "Æ": "AE",
    "æ": "ae",
    "Œ": "OE",
    "œ": "oe",
}

#: Every dash a word processor might produce, mapped to the ASCII hyphen.
DASHES = {
    "‐": "-",  # hyphen
    "‑": "-",  # non-breaking hyphen
    "‒": "-",  # figure dash
    "–": "-",  # en dash
    "—": "-",  # em dash
    "―": "-",  # horizontal bar
    "−": "-",  # minus sign
    "－": "-",  # fullwidth hyphen-minus
}

#: Curly quotes and primes, mapped to their ASCII equivalents. A curly quote in
#: the source and a straight one in a model's output is the single most common
#: reason a correct quotation fails to match.
QUOTES = {
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "′": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "″": '"',
    "«": '"',
    "»": '"',
}

#: Zero-width and bidirectional control characters. Removing these doubles as an
#: injection control: hidden text is frequently smuggled in exactly these code
#: points, and a span containing one could never be matched by a reader.
INVISIBLE = {
    "\u00ad",  # soft hyphen
    "\u200b",  # zero-width space
    "\u200c",  # zero-width non-joiner
    "\u200d",  # zero-width joiner
    "\u2060",  # word joiner
    "\ufeff",  # byte order mark
    "\u200e",  # left-to-right mark
    "\u200f",  # right-to-left mark
    "\u202a",  # left-to-right embedding
    "\u202b",  # right-to-left embedding
    "\u202c",  # pop directional formatting
    "\u202d",  # left-to-right override
    "\u202e",  # right-to-left override
    "\u2066",  # left-to-right isolate
    "\u2067",  # right-to-left isolate
    "\u2068",  # first strong isolate
    "\u2069",  # pop directional isolate
}


@dataclass(frozen=True)
class Normalized:
    """Text after a step, with where every character came from."""

    text: str
    #: One run per contiguous stretch that kept its alignment. Together they
    #: cover the output completely, which the SourceText contract checks.
    runs: tuple[OffsetRun, ...]


#: A step maps text to text, reporting for each output character the index it
#: came from in the input. Returning the mapping rather than deriving it is what
#: makes the composed map exact.
Step = Callable[[str], tuple[str, list[int]]]


def _identity_mapping(text: str) -> list[int]:
    return list(range(len(text)))


def nfkc(text: str) -> tuple[str, list[int]]:
    """Unicode compatibility composition, character by character.

    Applied per character rather than to the whole string, because NFKC on a
    whole string can merge across boundaries and there would be no way to say
    which input index an output character came from.
    """
    output: list[str] = []
    mapping: list[int] = []
    for index, character in enumerate(text):
        composed = unicodedata.normalize("NFKC", character)
        output.append(composed)
        mapping.extend([index] * len(composed))
    return "".join(output), mapping


def _substitute(table: dict[str, str]) -> Step:
    def step(text: str) -> tuple[str, list[int]]:
        output: list[str] = []
        mapping: list[int] = []
        for index, character in enumerate(text):
            replacement = table.get(character, character)
            output.append(replacement)
            mapping.extend([index] * len(replacement))
        return "".join(output), mapping

    return step


expand_ligatures = _substitute(LIGATURES)
unify_dashes = _substitute(DASHES)
unify_quotes = _substitute(QUOTES)


def remove_invisible(text: str) -> tuple[str, list[int]]:
    """Drop zero-width and bidi control characters."""
    output: list[str] = []
    mapping: list[int] = []
    for index, character in enumerate(text):
        if character in INVISIBLE:
            continue
        output.append(character)
        mapping.append(index)
    return "".join(output), mapping


def dehyphenate(text: str) -> tuple[str, list[int]]:
    """Join words broken across a line by a hyphen.

    "evalu-\\nation" becomes "evaluation". Only when a lowercase letter follows,
    so a genuine hyphenated compound at a line end ("multi-\\nService") and a
    list item ("- item") are both left alone.
    """
    output: list[str] = []
    mapping: list[int] = []
    index = 0

    while index < len(text):
        character = text[index]
        if character == "-" and index + 2 < len(text):
            after = index + 1
            while after < len(text) and text[after] in " \t":
                after += 1
            if after < len(text) and text[after] == "\n":
                following = after + 1
                while following < len(text) and text[following] in " \t":
                    following += 1
                if following < len(text) and text[following].islower():
                    index = following
                    continue
        output.append(character)
        mapping.append(index)
        index += 1

    return "".join(output), mapping


def collapse_whitespace(text: str) -> tuple[str, list[int]]:
    """Runs of whitespace become a single space; the text is stripped.

    This is what lets a model's single-line quotation match text that was
    wrapped across three lines in the original. Without it, every multi-line
    quotation would fail validation and the system would report fabrication
    where there was none.
    """
    output: list[str] = []
    mapping: list[int] = []
    in_whitespace = False

    for index, character in enumerate(text):
        if character.isspace():
            if not in_whitespace and output:
                output.append(" ")
                mapping.append(index)
            in_whitespace = True
            continue
        in_whitespace = False
        output.append(character)
        mapping.append(index)

    while output and output[-1] == " ":
        output.pop()
        mapping.pop()

    return "".join(output), mapping


#: The profile, in order. Order matters: ligature expansion after NFKC because
#: NFKC handles some of them, de-hyphenation before whitespace collapse because
#: it needs the newline it is looking for.
PROFILE_STEPS: tuple[Step, ...] = (
    nfkc,
    expand_ligatures,
    unify_dashes,
    unify_quotes,
    remove_invisible,
    dehyphenate,
    collapse_whitespace,
)


def _runs_from_mapping(mapping: list[int]) -> tuple[OffsetRun, ...]:
    """Turn a per-character source index into contiguous runs.

    A run is a stretch where output and input advance together. Storing runs
    rather than one entry per character keeps a sixty-page document's map small
    enough to hold in a JSON column.
    """
    if not mapping:
        return ()

    runs: list[OffsetRun] = []
    norm_start = 0
    raw_start = mapping[0]
    length = 1

    for position in range(1, len(mapping)):
        if mapping[position] == mapping[position - 1] + 1:
            length += 1
            continue
        runs.append(OffsetRun(norm_start=norm_start, raw_start=raw_start, length=length))
        norm_start = position
        raw_start = mapping[position]
        length = 1

    runs.append(OffsetRun(norm_start=norm_start, raw_start=raw_start, length=length))
    return tuple(runs)


def compose_profile(steps: tuple[Step, ...] = PROFILE_STEPS) -> Callable[[str], Normalized]:
    """Build a normaliser that also reports where every character came from.

    Each step's mapping is composed with the one before it, so the final map
    points from the normalised text all the way back to the original bytes
    however many transformations happened in between.
    """

    def normalise(text: str) -> Normalized:
        current = text
        composed = _identity_mapping(text)

        for step in steps:
            current, mapping = step(current)
            composed = [composed[index] for index in mapping]

        return Normalized(text=current, runs=_runs_from_mapping(composed))

    return normalise


#: The shipped normaliser.
normalize_with_map = compose_profile()


def normalize(text: str) -> str:
    """Normalised text alone, for comparing two strings.

    Used by the span validator on the model's quotation, where the offsets of
    the quotation itself are of no interest: only whether it appears in the
    source once the same rules have been applied to both sides.
    """
    return normalize_with_map(text).text


def normalize_source(source: SourceText) -> SourceText:
    """Apply the profile to an extracted document.

    The extractor hands over raw text with an identity map; this is where the
    normalised text, the real offset map and the composed profile id are
    produced. Page and block boundaries are carried through the map, so a
    boundary that fell on collapsed whitespace moves to the next character
    rather than being lost. Applied once, at extraction; the span validator
    applies the same steps to every quotation it checks.
    """
    normalized = normalize_with_map(source.raw_text)
    runs = list(normalized.runs)
    total = len(normalized.text)
    starts = [_norm_at(page.norm_start, runs, total) for page in source.pages]
    ends = [*starts[1:], total]
    blocks = [
        (_norm_at(start, runs, total), _norm_at(end, runs, total))
        for start, end in source.block_boundaries
    ]
    return source.model_copy(
        update={
            "normalized_text": normalized.text,
            "offset_runs": runs,
            "pages": [
                page.model_copy(update={"norm_start": start, "norm_end": end})
                for page, start, end in zip(source.pages, starts, ends, strict=True)
            ],
            "normalization_profile_id": f"{source.normalization_profile_id}+{PROFILE_ID}",
            "block_boundaries": [(start, end) for start, end in blocks if start < end],
        }
    )


def _norm_at(raw_index: int, runs: list[OffsetRun], total: int) -> int:
    """The normalised index of the first surviving character at or after
    ``raw_index``; the end of the text when nothing survives past it."""
    for run in runs:
        if raw_index < run.raw_start + run.length:
            return run.norm_start + max(raw_index - run.raw_start, 0)
    return total
