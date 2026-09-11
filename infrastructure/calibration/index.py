"""Finding past decisions like this one, with numpy and no database.

No vector database, no approximate-nearest-neighbour library, no service. A
recruiter's history is hundreds of decisions, not millions; a full cosine scan
over a few hundred 256-dimension vectors is a millisecond, and an index that
needs its own process to answer a millisecond question is a dependency bought
with nothing.

Two orderings matter here and both are deliberate.

Filters run before similarity. A search that ranked everything and then
discarded the wrong role would behave differently as the corpus grew — fine at
ten cards, returning nothing at ten thousand — and that is the worst kind of bug
because it appears in production and not in a test.

Diversity runs after ranking. If the three closest decisions all landed in the
same band, the third is swapped for the nearest card from a different one. Three
anchors that agree are not a reference; they are a suggestion, and a model shown
three "advance" cards has been told what answer is expected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

from domain.contracts.calibration import CalibrationCard
from domain.ports.calibration import (
    CalibrationIndex,
    CalibrationMatch,
    CalibrationQuery,
    CalibrationResult,
    CalibrationStatus,
    embedding_text,
)
from infrastructure.calibration.embedder import Embedder, LocalEmbedder, unpack

#: How many cards are scanned. A ceiling rather than a page size: past this, the
#: oldest are simply not considered, because a decision from two thousand
#: candidates ago is not an anchor.
MAX_CORPUS = 500

#: Below this, two summaries have nothing to do with each other and showing one
#: as an anchor would be noise presented as a reference.
MIN_SIMILARITY = 0.05


class NumpyCalibrationIndex(CalibrationIndex):
    """Cosine similarity over the cards for one role."""

    def __init__(
        self,
        repository: Any,
        embedder: Embedder | None = None,
        *,
        max_corpus: int = MAX_CORPUS,
        min_similarity: float = MIN_SIMILARITY,
    ) -> None:
        self.repository = repository
        self.embedder = embedder or LocalEmbedder()
        self.max_corpus = max_corpus
        self.min_similarity = min_similarity

    def search(self, query: CalibrationQuery) -> CalibrationResult:
        """The closest approved decisions for this role.

        Every path returns a status. Nothing here raises, because this is an
        optional aid and an exception would fail a run that could have completed
        without it.
        """
        try:
            cards = self.repository.for_role(
                query.role_id, query.rubric_version, limit=self.max_corpus
            )
        except Exception:
            return CalibrationResult(status=CalibrationStatus.INDEX_UNAVAILABLE)

        if not cards:
            return CalibrationResult(status=CalibrationStatus.EMPTY_CORPUS)

        kept, stale = apply_filters(cards, query)
        if not kept:
            return CalibrationResult(
                status=CalibrationStatus.NO_MATCHES,
                considered=len(cards),
                excluded_stale=stale,
            )

        vector = self.embedder.embed(embedding_text(query.summary, query.criterion_states))
        if vector is None:
            return CalibrationResult(
                status=CalibrationStatus.EMBEDDING_FAILED,
                considered=len(cards),
                excluded_stale=stale,
            )

        ranked = rank(vector, kept, min_similarity=self.min_similarity)
        if not ranked:
            return CalibrationResult(
                status=CalibrationStatus.NO_MATCHES,
                considered=len(cards),
                excluded_stale=stale,
            )

        chosen, diversified = diversify(ranked, query.top_k)

        return CalibrationResult(
            status=CalibrationStatus.APPLIED,
            matches=tuple(chosen),
            considered=len(cards),
            excluded_stale=stale,
            diversity_applied=diversified,
        )


def apply_filters(
    cards: list[CalibrationCard], query: CalibrationQuery
) -> tuple[list[CalibrationCard], int]:
    """Role, rubric, age, and never this run. Before any similarity is computed.

    The role and rubric filters are the correctness ones: an anchor from a
    different role is a decision made against different criteria, and one from a
    different major rubric version was made against a different bar.
    """
    cutoff = datetime.now(UTC) - timedelta(days=max(query.staleness_days, 1))
    major = major_version(query.rubric_version)

    kept: list[CalibrationCard] = []
    stale = 0

    for card in cards:
        if card.role_id != query.role_id:
            continue
        if major_version(card.rubric_version) != major:
            continue
        if query.exclude_run_id is not None and card.source_run_id == query.exclude_run_id:
            continue
        if card.decided_at < cutoff:
            stale += 1
            continue
        kept.append(card)

    return kept, stale


def major_version(version: str) -> str:
    """The part of a rubric version that changes what a decision meant.

    A patch release fixes a typo in a criterion's wording; a major release
    changes what is being asked. Anchoring across the first is fine and across
    the second is comparing answers to different questions.
    """
    return version.split(".", maxsplit=1)[0] if version else ""


def rank(
    vector: bytes, cards: list[CalibrationCard], *, min_similarity: float = MIN_SIMILARITY
) -> list[CalibrationMatch]:
    """Every card, scored, most similar first.

    One matrix multiplication. The vectors are unit length already, so a dot
    product is the cosine and there is nothing to divide by — which also means a
    zero vector cannot produce a division by zero.
    """
    query_vector = np.asarray(unpack(vector), dtype=np.float32)

    usable = [card for card in cards if card.embedding and len(card.embedding) == len(vector)]
    if not usable:
        return []

    matrix = np.asarray([unpack(card.embedding) for card in usable], dtype=np.float32)
    scores = matrix @ query_vector

    matches = [
        CalibrationMatch(card=card, similarity=float(score))
        for card, score in zip(usable, scores, strict=True)
        if float(score) >= min_similarity
    ]
    return sorted(matches, key=lambda match: match.similarity, reverse=True)


def diversify(ranked: list[CalibrationMatch], top_k: int) -> tuple[list[CalibrationMatch], bool]:
    """The closest few, unless they all agree.

    Three anchors in the same band are not a reference; they are a suggestion,
    and a model shown three "advance" cards has been told what answer is
    expected. So the last slot goes to the best-scoring card from a different
    band, when one exists.

    Only the last slot. Forcing more variety than that would start choosing
    anchors for their disagreement rather than their similarity, which is a
    different and equally bad thumb on the scale.
    """
    chosen = ranked[:top_k]

    if len(chosen) < top_k:
        return chosen, False

    bands = {match.card.final_band for match in chosen}
    if len(bands) > 1:
        return chosen, False

    # The highest-scoring dissenter, chosen by score rather than by position.
    # The input is sorted in practice, but a function whose promise depends on
    # its caller having sorted correctly is a promise that quietly stops being
    # true the first time somebody calls it from somewhere else.
    candidates = [match for match in ranked[top_k:] if match.card.final_band not in bands]
    if not candidates:
        return chosen, False

    alternative = max(candidates, key=lambda match: match.similarity)

    return [*chosen[:-1], alternative], True
