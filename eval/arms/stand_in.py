"""The deterministic stand-in, as arm C uses it.

The client itself lives in ``infrastructure/models/stand_in.py`` because it is a
model client and the composition root wires it in for the offline demo. The
harness imports it from here so that what arm C measured is named in the
evaluation package, next to the arms it is compared against.

What it is for, restated because it is the easiest thing in the repository to
misread: arm C measures the *pipeline* — span validation, the rule engine, the
coverage gate, abstention, the integrity path. With this stand-in, band accuracy
measures whether the rule engine turns evidence into the right band, not whether
a language model can read a CV. Every report produced with it says so at the
top.

A known limitation, recorded rather than tuned away: it matches on shared words
and does not stem, so a document saying "I am eligible to work in the United
Kingdom" does not match a criterion asking about "eligibility". On the benchmark
that costs one band, and the case that loses it is dev-001. Adding a stemmer
would raise the number without making the harness measure anything more, which
is the wrong direction to move a benchmark in.
"""

from __future__ import annotations

from infrastructure.models.stand_in import (
    MATCH_THRESHOLD,
    MIN_PHRASE_TOKENS,
    MIN_SENTENCE_CHARS,
    NOISE,
    QUESTION_NOISE,
    StandInModelClient,
    for_rubric,
    for_rubrics,
)

__all__ = [
    "MATCH_THRESHOLD",
    "MIN_PHRASE_TOKENS",
    "MIN_SENTENCE_CHARS",
    "NOISE",
    "QUESTION_NOISE",
    "StandInModelClient",
    "for_rubric",
    "for_rubrics",
]
