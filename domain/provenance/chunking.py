"""Splitting documents that will not fit, and putting the pieces back.

Two stages can exceed a context window, and they need opposite treatments.

Structuring needs all of the document, so it is split into contiguous page
groups and the results are merged. The merge is deterministic Python, not a
model: asking a model to reconcile two partial profiles would be asking it to
decide which of two dates is right, which is a judgment nobody could check.

Assessment does not need all of the document, so it selects the passages most
likely to bear on one criterion. That is chunk *selection*, not summarisation,
because a summary is a paraphrase and a paraphrase cannot be quoted.

The honesty cost of selection is real and is recorded: an insufficient-evidence
result on a chunked document may mean "not in what we showed" rather than "not
in the CV", and the evaluation reports the two separately.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from domain.contracts.source_text import PageSpan, SourceText

#: Characters per token, roughly, for English prose. Used only to decide where
#: to split, never to report a token count: real usage comes from the provider.
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class Chunk:
    """A contiguous stretch of one document, with its coordinates kept.

    ``norm_start`` matters as much as the text. A span found inside a chunk has
    to be reported in the whole document's coordinates, or the reviewer's
    highlight lands in the wrong place.
    """

    text: str
    norm_start: int
    norm_end: int
    page_numbers: tuple[int, ...]

    @property
    def estimated_tokens(self) -> int:
        return math.ceil(len(self.text) / CHARS_PER_TOKEN)


def by_page_groups(source: SourceText, *, max_tokens: int) -> list[Chunk]:
    """Split into contiguous page groups that each fit.

    Pages are never split in the middle: a group is whole pages, so a quotation
    that runs to the end of a page is not truncated halfway through by an
    arbitrary character budget.

    A single page larger than the budget is returned whole rather than cut. It
    will be refused by the provider, which is a clearer failure than silently
    assessing three quarters of someone's CV.
    """
    if not source.pages:
        return [
            Chunk(
                text=source.normalized_text,
                norm_start=0,
                norm_end=len(source.normalized_text),
                page_numbers=(),
            )
        ]

    budget = max_tokens * CHARS_PER_TOKEN
    chunks: list[Chunk] = []
    group: list[PageSpan] = []

    for page in source.pages:
        prospective = [*group, page]
        span = prospective[-1].norm_end - prospective[0].norm_start

        if group and span > budget:
            chunks.append(_chunk_from(source, group))
            group = [page]
        else:
            group = prospective

    if group:
        chunks.append(_chunk_from(source, group))

    return chunks


def _chunk_from(source: SourceText, pages: list[PageSpan]) -> Chunk:
    start = pages[0].norm_start
    end = pages[-1].norm_end
    return Chunk(
        text=source.normalized_text[start:end],
        norm_start=start,
        norm_end=end,
        page_numbers=tuple(page.page_number for page in pages),
    )


def select_for_criterion(
    source: SourceText,
    *,
    query_terms: list[str],
    max_tokens: int,
    top_k: int = 6,
) -> tuple[list[Chunk], bool]:
    """The passages most likely to bear on one criterion.

    Returns the chunks and whether selection actually happened. That second
    value is not a detail: a criterion answered from selected chunks carries a
    different kind of "no evidence" than one answered from the whole document,
    and conflating them would overstate how often the system correctly found
    nothing.

    Scoring is term overlap, deliberately. A retrieval model here would add a
    dependency, a latency cost, and a second thing to evaluate, to choose
    between passages of a two-page CV.
    """
    whole = by_page_groups(source, max_tokens=max_tokens)
    if len(whole) <= 1:
        return whole, False

    scored = sorted(
        whole,
        key=lambda chunk: (-_overlap(chunk.text, query_terms), chunk.norm_start),
    )
    chosen = sorted(scored[:top_k], key=lambda chunk: chunk.norm_start)
    return chosen, True


def _overlap(text: str, terms: list[str]) -> int:
    """How many of the criterion's terms appear in this passage.

    Counted once per term rather than per occurrence, so a page repeating one
    word does not outrank a page covering several.
    """
    lowered = text.lower()
    words = set(re.findall(r"[a-z][a-z0-9+#.-]{2,}", lowered))

    hits = 0
    for term in terms:
        needle = term.lower().strip()
        if not needle:
            continue
        if " " in needle:
            hits += 1 if needle in lowered else 0
        else:
            hits += 1 if needle in words else 0
    return hits


def query_terms_for(label: str, question: str, positive_examples: list[str]) -> list[str]:
    """What to look for, from a criterion's own wording.

    Drawn from the rubric rather than from a fixed vocabulary, so a recruiter
    editing a criterion changes what gets retrieved for it without anyone
    touching code.
    """
    terms: list[str] = []
    for source_text in (label, question, *positive_examples):
        terms.extend(re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}", source_text))

    # Words that appear in every criterion carry no signal about which passage
    # is relevant, and leaving them in would make every chunk score the same.
    stopwords = {
        "does",
        "the",
        "document",
        "show",
        "this",
        "that",
        "person",
        "candidate",
        "their",
        "such",
        "with",
        "have",
        "has",
        "for",
        "and",
        "was",
        "were",
        "used",
        "using",
    }
    return [term for term in dict.fromkeys(terms) if term.lower() not in stopwords]
