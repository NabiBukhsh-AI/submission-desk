"""The rubric is the one file a recruiter edits, so its validators are the
difference between a typo and a wrong hiring recommendation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain.contracts import (
    Band,
    BandThreshold,
    Criterion,
    CriterionKind,
    CriterionState,
)
from domain.contracts.rubric import HIGH_STAKES_MIN_SUPPORTED
from tests.builders import FULL_POINTS, criterion, rubric


def test_a_valid_rubric_builds() -> None:
    built = rubric()
    assert built.role_id == "ai-engineer"
    assert built.min_coverage == 0.70
    assert built.blind_mode_default is True
    assert "nationality" in built.forbidden_attributes


def test_a_rubric_is_frozen() -> None:
    with pytest.raises(ValidationError):
        rubric().min_coverage = 0.9


def test_an_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        rubric(auto_reject_below=0.3)


# --- criterion ---------------------------------------------------------------


@pytest.mark.parametrize("bad_id", ["", "ab", "Has_Underscores", "Upper", "a" * 65])
def test_criterion_id_must_be_a_slug(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        criterion(id=bad_id)


@pytest.mark.parametrize("bad_weight", [0, -1, 11])
def test_criterion_weight_is_bounded(bad_weight: int) -> None:
    with pytest.raises(ValidationError):
        criterion(weight=bad_weight)


def test_high_stakes_criteria_ask_for_corroboration_by_default() -> None:
    assert criterion(kind=CriterionKind.HIGH_STAKES).min_supported == HIGH_STAKES_MIN_SUPPORTED


def test_a_recruiter_may_override_the_high_stakes_default() -> None:
    assert criterion(kind=CriterionKind.HIGH_STAKES, min_supported=1).min_supported == 1


def test_a_blocker_may_not_fire_on_silence() -> None:
    with pytest.raises(ValidationError, match="at least one supporting item"):
        criterion(kind=CriterionKind.BLOCKER, min_supported=0)


def test_every_scoring_state_needs_points() -> None:
    incomplete = {k: v for k, v in FULL_POINTS.items() if k is not CriterionState.NOT_MET}
    with pytest.raises(ValidationError, match="missing not_met"):
        criterion(state_points=incomplete)


@pytest.mark.parametrize("bad_points", [1.5, -0.1])
def test_points_are_bounded(bad_points: float) -> None:
    with pytest.raises(ValidationError, match="between 0 and 1"):
        criterion(state_points={**FULL_POINTS, CriterionState.PARTIAL: bad_points})


def test_insufficient_evidence_may_not_carry_points() -> None:
    """Scoring absence as zero punishes a candidate for a document's silence."""
    with pytest.raises(ValidationError, match="must map to null"):
        criterion(state_points={**FULL_POINTS, CriterionState.INSUFFICIENT_EVIDENCE: 0.0})


def test_insufficient_evidence_may_be_listed_as_null() -> None:
    built = criterion(state_points={**FULL_POINTS, CriterionState.INSUFFICIENT_EVIDENCE: None})
    assert built.state_points[CriterionState.INSUFFICIENT_EVIDENCE] is None


# --- bands -------------------------------------------------------------------


@pytest.mark.parametrize("gate_band", [Band.INSUFFICIENT_INFORMATION, Band.MANUAL_REVIEW_REQUIRED])
def test_gate_bands_cannot_be_given_a_cutoff(gate_band: Band) -> None:
    """These two say why no score exists, so a score cutoff cannot produce them."""
    with pytest.raises(ValidationError, match="produced by a gate"):
        BandThreshold(band=gate_band, min_score=0.5)


def test_band_cutoffs_must_descend() -> None:
    with pytest.raises(ValidationError, match="strictly decreasing"):
        rubric(
            bands=[
                BandThreshold(band=Band.HOLD, min_score=0.40),
                BandThreshold(band=Band.ADVANCE, min_score=0.75),
                BandThreshold(band=Band.DECLINE, min_score=0.0),
            ]
        )


def test_band_cutoffs_may_not_repeat_a_score() -> None:
    with pytest.raises(ValidationError, match="strictly decreasing"):
        rubric(
            bands=[
                BandThreshold(band=Band.ADVANCE, min_score=0.5),
                BandThreshold(band=Band.HOLD, min_score=0.5),
                BandThreshold(band=Band.DECLINE, min_score=0.0),
            ]
        )


def test_a_band_may_not_appear_twice() -> None:
    with pytest.raises(ValidationError, match="at most once"):
        rubric(
            bands=[
                BandThreshold(band=Band.HOLD, min_score=0.6),
                BandThreshold(band=Band.HOLD, min_score=0.3),
                BandThreshold(band=Band.DECLINE, min_score=0.0),
            ]
        )


def test_the_lowest_band_must_reach_zero() -> None:
    """Otherwise a score below the last cutoff falls into no band at all."""
    with pytest.raises(ValidationError, match=r"must start at 0\.0"):
        rubric(
            bands=[
                BandThreshold(band=Band.ADVANCE, min_score=0.75),
                BandThreshold(band=Band.HOLD, min_score=0.40),
            ]
        )


# --- rubric invariants -------------------------------------------------------


def test_criterion_ids_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="duplicate criterion ids"):
        rubric(criteria=[criterion(), criterion()])


def test_a_rubric_of_blockers_alone_is_rejected() -> None:
    """Such a rubric can only decline, which is a scoring system with no scores."""
    with pytest.raises(ValidationError, match="at least one criterion"):
        rubric(criteria=[criterion(id="work-eligibility", kind=CriterionKind.BLOCKER)])


def test_a_rubric_needs_at_least_one_criterion() -> None:
    with pytest.raises(ValidationError):
        rubric(criteria=[])


def test_a_rubric_caps_at_thirty_criteria() -> None:
    many = [criterion(id=f"criterion-{index:03d}") for index in range(31)]
    with pytest.raises(ValidationError):
        rubric(criteria=many)


@pytest.mark.parametrize("bad_version", ["1.2", "v1.2.0", "latest", ""])
def test_version_must_be_semver(bad_version: str) -> None:
    with pytest.raises(ValidationError, match="semver"):
        rubric(version=bad_version)


@pytest.mark.parametrize("bad_coverage", [-0.1, 1.1])
def test_min_coverage_is_a_fraction(bad_coverage: float) -> None:
    with pytest.raises(ValidationError):
        rubric(min_coverage=bad_coverage)


def test_forbidden_attributes_cannot_be_emptied() -> None:
    """Emptying the list would quietly permit exactly what it exists to prevent."""
    with pytest.raises(ValidationError):
        rubric(forbidden_attributes=[])


def test_the_shipped_defaults_cover_the_protected_attributes() -> None:
    built = rubric()
    for attribute in ("age", "gender", "ethnicity", "religion", "disability", "culture_fit"):
        assert attribute in built.forbidden_attributes


def test_criterion_question_is_required() -> None:
    """A criterion without a question cannot be asked, only guessed at."""
    with pytest.raises(ValidationError):
        # The omission is the point of the test, so the type error is expected.
        Criterion(  # type: ignore[call-arg]
            id="work-eligibility",
            label="Meets the work authorisation requirement",
            kind=CriterionKind.BLOCKER,
            weight=1,
            state_points=dict(FULL_POINTS),
        )
