"""The characters optical character recognition mistakes for each other.

A restricted, deliberately small map. Applied only to pages that actually went
through OCR, and only after an exact and a normalised match have both failed.

Restricted because every pair added makes two genuinely different strings look
alike, and this map is the one place where a fabricated quotation could be
accepted by being close enough to a real one. The pairs here are ones a scanner
confuses often enough to be worth handling; anything more speculative costs more
in missed fabrications than it buys in rescued evidence.

**The order of the two passes is load-bearing.** Single characters fold first,
then pairs, and the pair rules are written in already-folded form. The two
classes interact: a scanner reading "closed" may produce "c1osed" by mistaking
the l for a 1, or "dosed" by merging the cl into a d. Folding pairs first would
turn "closed" into "dosed" while leaving "c1osed" alone, and the two readings of
one word would no longer match. Folding singles first turns both into "c105ed",
after which the pair rule maps both to "d05ed".
"""

from __future__ import annotations

#: Single characters a scanner confuses, collapsed to one representative.
#: Applied first, and after lowercasing, so case is never evidence here.
SINGLE_CLASSES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("0", "o"), "0"),
    (("1", "l", "i", "|"), "1"),
    (("5", "s"), "5"),
    (("8", "b"), "8"),
    (("2", "z"), "2"),
    (("6", "g"), "6"),
)

#: Pairs a scanner merges or splits, written in the form they take *after* the
#: single-character pass. "cl" appears here as "c1" for exactly that reason.
PAIR_CLASSES: tuple[tuple[str, str], ...] = (
    ("rn", "m"),
    ("c1", "d"),
    ("vv", "w"),
)


def fold(text: str) -> str:
    """Collapse confusable characters so two readings of one page agree."""
    folded = text.lower()

    for variants, canonical in SINGLE_CLASSES:
        for variant in variants:
            folded = folded.replace(variant, canonical)

    for pair, canonical in PAIR_CLASSES:
        folded = folded.replace(pair, canonical)

    # Scanners insert and drop spaces unpredictably around glyph boundaries, so
    # spacing is not evidence of anything at this stage.
    return "".join(folded.split())
