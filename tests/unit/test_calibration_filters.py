"""Which past decisions may be shown, and which may not.

Filters run before similarity, and the ordering is the interesting part. A
search that ranked everything and then discarded the wrong role would work
perfectly at ten cards and return nothing at ten thousand, which is the worst
kind of bug: it appears in production and never in a test.

The diversity rule is the other half. Three anchors in the same band are not a
reference; they are a suggestion, and a model shown three "advance" cards has
been told what answer is expected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from domain.contracts.calibration import CalibrationCard
from domain.contracts.enums import Band, CriterionState
from domain.ports.calibration import (
    DEFAULT_STALENESS_DAYS,
    DEFAULT_TOP_K,
    CalibrationMatch,
    CalibrationQuery,
    CalibrationStatus,
)
from infrastructure.calibration.embedder import LocalEmbedder
from infrastructure.calibration.index import (
    NumpyCalibrationIndex,
    apply_filters,
    diversify,
    major_version,
    rank,
)

EMBEDDER = LocalEmbedder()


def card(
    *,
    role_id: str = "ai-engineer",
    rubric_version: str = "1.0.0",
    band: Band = Band.ADVANCE,
    days_ago: int = 3,
    summary: str = "Lead Engineer, 2021 to present. Technologies: python, kubernetes",
    source_run_id: UUID | None = None,
) -> CalibrationCard:
    states = {"python-depth": CriterionState.MET}
    return CalibrationCard(
        card_id=uuid4(),
        role_id=role_id,
        rubric_version=rubric_version,
        anonymized_summary=summary,
        criterion_states=states,
        final_band=band,
        decided_at=datetime.now(UTC) - timedelta(days=days_ago),
        embedding=EMBEDDER.embed(summary) or b"",
        source_run_id=source_run_id,
    )


def query(**kwargs) -> CalibrationQuery:
    return CalibrationQuery(
        **{
            "role_id": "ai-engineer",
            "rubric_version": "1.0.0",
            "summary": "Lead Engineer. Technologies: python, kubernetes",
            "criterion_states": {},
            **kwargs,
        }
    )


class _Repository:
    def __init__(self, cards: list[CalibrationCard], *, fail: bool = False) -> None:
        self.cards = cards
        self.fail = fail

    def for_role(self, role_id: str, rubric_version: str, limit: int = 200):
        if self.fail:
            raise RuntimeError("the index is unreachable")
        return list(self.cards)


def index(cards: list[CalibrationCard], **kwargs) -> NumpyCalibrationIndex:
    return NumpyCalibrationIndex(_Repository(cards), EMBEDDER, **kwargs)


# --- role scoping -------------------------------------------------------------------


def test_a_card_from_another_role_is_removed() -> None:
    """A decision made against different criteria is worse than no anchor."""
    kept, _ = apply_filters([card(role_id="data-scientist")], query())

    assert kept == []


def test_a_card_from_this_role_is_kept() -> None:
    assert len(apply_filters([card()], query())[0]) == 1


def test_the_role_filter_runs_before_similarity() -> None:
    """The ordering. A search that ranked first would behave differently as the
    corpus grew."""
    result = index([card(role_id="data-scientist")]).search(query())

    assert result.status is CalibrationStatus.NO_MATCHES
    assert result.matches == ()


# --- rubric version ------------------------------------------------------------------


def test_a_different_major_version_is_removed() -> None:
    """A major release changes what is being asked, so anchoring across one is
    comparing answers to different questions."""
    kept, _ = apply_filters([card(rubric_version="2.0.0")], query())

    assert kept == []


def test_a_different_patch_version_is_kept() -> None:
    """A patch fixes a typo in a criterion's wording. The bar did not move."""
    kept, _ = apply_filters([card(rubric_version="1.0.4")], query())

    assert len(kept) == 1


def test_a_different_minor_version_is_kept() -> None:
    kept, _ = apply_filters([card(rubric_version="1.3.0")], query())

    assert len(kept) == 1


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.0.0", "1"), ("2.11.3", "2"), ("1", "1"), ("", "")],
)
def test_the_major_version_is_read_correctly(version: str, expected: str) -> None:
    assert major_version(version) == expected


# --- staleness ------------------------------------------------------------------------


def test_an_old_decision_is_removed() -> None:
    """Roles drift and rubrics change. Last year's decision met a different
    bar."""
    kept, stale = apply_filters([card(days_ago=400)], query(staleness_days=180))

    assert kept == []
    assert stale == 1


def test_a_recent_decision_is_kept() -> None:
    kept, stale = apply_filters([card(days_ago=10)], query(staleness_days=180))

    assert len(kept) == 1
    assert stale == 0


def test_the_stale_count_is_reported() -> None:
    """ "No matches" and "everything was too old" look the same to a reader and
    mean different things to somebody deciding whether this is worth keeping."""
    result = index([card(days_ago=400), card(days_ago=500)]).search(query())

    assert result.excluded_stale == 2
    assert result.considered == 2


def test_the_default_window_is_six_months() -> None:
    assert DEFAULT_STALENESS_DAYS == 180


# --- never itself -----------------------------------------------------------------------


def test_a_run_never_anchors_against_its_own_card() -> None:
    """A re-run finding its own earlier decision would be treating one judgement
    as two."""
    run_id = uuid4()
    kept, _ = apply_filters([card(source_run_id=run_id)], query(exclude_run_id=run_id))

    assert kept == []


def test_another_run_card_is_kept() -> None:
    kept, _ = apply_filters([card(source_run_id=uuid4())], query(exclude_run_id=uuid4()))

    assert len(kept) == 1


def test_a_card_with_no_run_recorded_is_kept() -> None:
    """Cards written before the field existed have no run to name, and
    discarding a recruiter's history to fix a filter would be the wrong trade."""
    kept, _ = apply_filters([card(source_run_id=None)], query(exclude_run_id=uuid4()))

    assert len(kept) == 1


# --- diversity -----------------------------------------------------------------------------


def matches(*bands: Band) -> list[CalibrationMatch]:
    return [
        CalibrationMatch(card=card(band=band), similarity=1.0 - index_ * 0.1)
        for index_, band in enumerate(bands)
    ]


def test_three_agreeing_anchors_get_one_dissenter() -> None:
    """The headline. A model shown three "advance" cards has been told what
    answer is expected."""
    ranked = matches(Band.ADVANCE, Band.ADVANCE, Band.ADVANCE, Band.HOLD)

    chosen, applied = diversify(ranked, 3)

    assert applied is True
    assert {match.card.final_band for match in chosen} == {Band.ADVANCE, Band.HOLD}


def test_the_dissenter_replaces_only_the_last_slot() -> None:
    """Forcing more variety would start choosing anchors for their disagreement
    rather than their similarity, which is a different thumb on the scale."""
    ranked = matches(Band.ADVANCE, Band.ADVANCE, Band.ADVANCE, Band.HOLD)

    chosen, _ = diversify(ranked, 3)

    assert chosen[0].card.final_band is Band.ADVANCE
    assert chosen[1].card.final_band is Band.ADVANCE
    assert chosen[2].card.final_band is Band.HOLD


def test_the_dissenter_is_the_best_scoring_one() -> None:
    ranked = [
        *matches(Band.ADVANCE, Band.ADVANCE, Band.ADVANCE),
        CalibrationMatch(card=card(band=Band.DECLINE), similarity=0.2),
        CalibrationMatch(card=card(band=Band.HOLD), similarity=0.5),
    ]

    chosen, _ = diversify(ranked, 3)

    assert chosen[2].card.final_band is Band.HOLD


def test_already_varied_anchors_are_left_alone() -> None:
    ranked = matches(Band.ADVANCE, Band.HOLD, Band.ADVANCE, Band.DECLINE)

    chosen, applied = diversify(ranked, 3)

    assert applied is False
    assert [match.card.final_band for match in chosen] == [
        Band.ADVANCE,
        Band.HOLD,
        Band.ADVANCE,
    ]


def test_nothing_to_swap_leaves_the_anchors_as_they_are() -> None:
    """Every card in the corpus agreed. That is a fact about the corpus, and
    inventing variety would be worse than reporting it."""
    ranked = matches(Band.ADVANCE, Band.ADVANCE, Band.ADVANCE)

    chosen, applied = diversify(ranked, 3)

    assert applied is False
    assert len(chosen) == 3


def test_fewer_anchors_than_asked_for_is_not_diversified() -> None:
    ranked = matches(Band.ADVANCE, Band.ADVANCE)

    chosen, applied = diversify(ranked, 3)

    assert applied is False
    assert len(chosen) == 2


def test_three_is_the_default() -> None:
    """Two is not a range; four starts to read like a pattern to match."""
    assert DEFAULT_TOP_K == 3


# --- every failure path proceeds -------------------------------------------------------------


def test_an_empty_corpus_is_a_status_not_an_error() -> None:
    result = index([]).search(query())

    assert result.status is CalibrationStatus.EMPTY_CORPUS
    assert result.matches == ()


def test_an_unreachable_index_is_a_status_not_an_error() -> None:
    """An aid that could fail an assessment would be worse than no aid."""
    reader = NumpyCalibrationIndex(_Repository([], fail=True), EMBEDDER)

    result = reader.search(query())

    assert result.status is CalibrationStatus.INDEX_UNAVAILABLE


def test_a_failed_embedding_is_a_status_not_an_error() -> None:
    class _Broken:
        embedder_id = "broken"

        def embed(self, text: str) -> bytes | None:
            return None

    reader = NumpyCalibrationIndex(_Repository([card()]), _Broken())

    result = reader.search(query())

    assert result.status is CalibrationStatus.EMBEDDING_FAILED


def test_nothing_similar_enough_is_a_status_not_an_error() -> None:
    unrelated = card(summary="Pastry chef, sourdough, viennoiserie, patisserie")

    result = index([unrelated], min_similarity=0.9).search(query())

    assert result.status is CalibrationStatus.NO_MATCHES


def test_a_status_is_never_anchored_unless_it_applied() -> None:
    """The property the run record depends on: only APPLIED means the model saw
    anchors."""
    for status in CalibrationStatus:
        assert status.anchored is (status is CalibrationStatus.APPLIED)


# --- ranking -----------------------------------------------------------------------------------


def test_a_similar_card_scores_higher_than_an_unrelated_one() -> None:
    similar = card(summary="Lead Engineer. Technologies: python, kubernetes")
    unrelated = card(summary="Pastry chef, sourdough, viennoiserie")
    vector = EMBEDDER.embed(query().summary)

    ranked = rank(vector, [unrelated, similar], min_similarity=-1.0)

    assert ranked[0].card.anonymized_summary == similar.anonymized_summary


def test_a_card_with_a_mismatched_vector_is_skipped() -> None:
    """A card embedded by a different embedder cannot be compared, and comparing
    it anyway would produce a number that means nothing."""
    wrong = card()
    wrong = wrong.model_copy(update={"embedding": b"\x00" * 8})

    assert rank(EMBEDDER.embed(query().summary), [wrong], min_similarity=-1.0) == []


def test_an_empty_corpus_ranks_to_nothing() -> None:
    assert rank(EMBEDDER.embed("anything"), [], min_similarity=-1.0) == []
