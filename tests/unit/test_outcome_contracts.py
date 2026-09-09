"""What the system concluded, and what the human did about it.

The rules under test are the ones that keep the deterministic-scoring contract
intact: evidence with a broken span never reaches scoring, a missing score is
always explained, and a reviewer changes states rather than typing a band.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain.contracts import (
    Band,
    CriterionAssessment,
    CriterionState,
    DerivationStep,
    ModelTier,
    Override,
    OverrideReason,
    Recommendation,
    ReviewAction,
    ReviewDecision,
    SpanValidation,
)
from tests.builders import NOW, evidence

STEP = DerivationStep(
    rule_id="band-cutoff",
    description="Weighted score 0.81 is at or above the Advance cutoff of 0.75.",
    inputs={"score": 0.81},
    output="advance",
)


def assessment(**overrides: Any) -> CriterionAssessment:
    fields: dict[str, Any] = {
        "criterion_id": "evaluation-practice",
        "evidence": [evidence()],
        "resolved_state": CriterionState.MET,
        "resolution_rule_id": "met-when-supported-at-or-above-min",
        "tier_used": ModelTier.CHEAP,
    }
    fields.update(overrides)
    return CriterionAssessment(**fields)


def recommendation(**overrides: Any) -> Recommendation:
    fields: dict[str, Any] = {
        "run_id": uuid4(),
        "band": Band.ADVANCE,
        "score": 0.81,
        "coverage": 0.9,
        "criterion_states": {"evaluation-practice": CriterionState.MET},
        "derivation": [STEP],
        "rubric_version": "1.2.0",
    }
    fields.update(overrides)
    return Recommendation(**fields)


def decision(**overrides: Any) -> ReviewDecision:
    fields: dict[str, Any] = {
        "decision_id": uuid4(),
        "run_id": uuid4(),
        "reviewer_id": "recruiter-01",
        "action": ReviewAction.APPROVE,
        "elapsed_seconds": 96,
        "decided_at": NOW,
        "run_version": 3,
    }
    fields.update(overrides)
    return ReviewDecision(**fields)


# --- assessment --------------------------------------------------------------


def test_an_assessment_builds() -> None:
    assert assessment().resolved_state is CriterionState.MET


def test_evidence_with_a_broken_span_cannot_reach_scoring() -> None:
    """This is the rule that turns the span check into a guarantee."""
    with pytest.raises(ValidationError, match="invalid span reached the scoring list"):
        assessment(evidence=[evidence(span_validation=SpanValidation.INVALID_NOT_FOUND)])


def test_rejected_evidence_is_kept_for_the_reviewer() -> None:
    rejected = evidence(span_validation=SpanValidation.INVALID_NOT_FOUND)
    built = assessment(rejected_evidence=[rejected])
    assert built.rejected_evidence[0].span_validation is SpanValidation.INVALID_NOT_FOUND


def test_an_item_cannot_be_both_used_and_rejected() -> None:
    item = evidence()
    with pytest.raises(ValidationError, match="both used and rejected"):
        assessment(evidence=[item], rejected_evidence=[item])


def test_an_escalation_must_record_what_it_changed_from() -> None:
    """Otherwise the value of escalating cannot be measured after the fact."""
    with pytest.raises(ValidationError, match="must record pre_escalation_state"):
        assessment(escalated=True)


def test_escalation_outcome_without_an_escalation_is_rejected() -> None:
    with pytest.raises(ValidationError, match="meaningless without an escalation"):
        assessment(escalated=False, escalation_changed_state=True)


# --- recommendation ----------------------------------------------------------


def test_a_recommendation_builds() -> None:
    assert recommendation().band is Band.ADVANCE


@pytest.mark.parametrize("band", [Band.INSUFFICIENT_INFORMATION, Band.MANUAL_REVIEW_REQUIRED])
def test_gate_bands_may_omit_a_score(band: Band) -> None:
    assert recommendation(band=band, score=None).score is None


@pytest.mark.parametrize("band", [Band.ADVANCE, Band.HOLD, Band.DECLINE])
def test_a_scored_band_must_carry_its_score(band: Band) -> None:
    with pytest.raises(ValidationError, match="requires a score"):
        recommendation(band=band, score=None)


def test_a_blocker_may_decline_with_a_good_score() -> None:
    """Hiding the score would hide that the candidate was strong elsewhere."""
    built = recommendation(band=Band.DECLINE, score=0.88, blockers_fired=["evaluation-practice"])
    assert built.score == 0.88


def test_requiring_a_human_means_saying_why() -> None:
    with pytest.raises(ValidationError, match="must name its reasons"):
        recommendation(requires_human=True)


def test_a_blocker_must_name_a_real_criterion() -> None:
    with pytest.raises(ValidationError, match="absent from the rubric"):
        recommendation(blockers_fired=["not-a-criterion"])


def test_a_recommendation_must_show_its_working() -> None:
    with pytest.raises(ValidationError):
        recommendation(derivation=[])


def test_coverage_is_a_fraction() -> None:
    with pytest.raises(ValidationError):
        recommendation(coverage=1.4)


# --- review ------------------------------------------------------------------


def test_a_decision_builds() -> None:
    assert decision().action is ReviewAction.APPROVE


def test_an_override_must_change_something() -> None:
    with pytest.raises(ValidationError, match="changes nothing"):
        Override(
            criterion_id="evaluation-practice",
            previous_state=CriterionState.MET,
            new_state=CriterionState.MET,
            reason_code=OverrideReason.EVIDENCE_MISREAD,
            reason_text="The quotation was about a different project.",
        )


def test_an_override_must_give_a_reason() -> None:
    """The reason text is what makes the override taxonomy usable later."""
    with pytest.raises(ValidationError):
        Override(
            criterion_id="evaluation-practice",
            previous_state=CriterionState.MET,
            new_state=CriterionState.PARTIAL,
            reason_code=OverrideReason.EVIDENCE_MISREAD,
            reason_text="no",
        )


def test_a_criterion_may_be_overridden_only_once_per_decision() -> None:
    change = Override(
        criterion_id="evaluation-practice",
        previous_state=CriterionState.MET,
        new_state=CriterionState.PARTIAL,
        reason_code=OverrideReason.THRESHOLD_WRONG,
        reason_text="One example is not a practice.",
    )
    with pytest.raises(ValidationError, match="overridden once"):
        decision(overrides=[change, change])


def test_trust_rating_is_a_five_point_scale() -> None:
    assert decision(trust_rating=4).trust_rating == 4
    with pytest.raises(ValidationError):
        decision(trust_rating=6)


def test_elapsed_time_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        decision(elapsed_seconds=-1)


def test_a_naive_decision_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        decision(decided_at=datetime(2026, 9, 9, 12, 0))


def test_a_decision_is_frozen() -> None:
    with pytest.raises(ValidationError):
        decision().action = ReviewAction.REJECT


def test_the_reviewer_does_not_type_a_band() -> None:
    """post_override_band is recomputed by the rule engine; it defaults to absent
    rather than to a value a reviewer could have supplied."""
    assert decision().post_override_band is None
