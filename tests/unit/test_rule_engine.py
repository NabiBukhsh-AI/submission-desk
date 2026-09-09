"""The rule engine, one row and one branch at a time.

Expected outputs are hand-computed and written as literals. Deriving them from
the code under test would prove only that the code agrees with itself.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from domain.contracts import (
    Band,
    BandThreshold,
    CriterionKind,
    CriterionState,
    EvidenceState,
    IntegrityTier,
    SpanValidation,
)
from domain.rules import AggregationFlags, aggregate, resolve_criterion
from domain.rules.aggregate import (
    REASON_BLOCKER_UNKNOWN,
    REASON_BUDGET,
    REASON_CONTRADICTED,
    REASON_COVERAGE,
    REASON_INTEGRITY,
    REASON_INVALID_SPAN,
    REASON_PARTIAL_PROFILE,
)
from domain.rules.resolve import CriterionResolution
from tests.builders import criterion, evidence, insufficient_evidence, rubric

RUN = uuid4()


def supported(**overrides: Any) -> Any:
    return evidence(state=EvidenceState.SUPPORTED, **overrides)


def contradicted(**overrides: Any) -> Any:
    return evidence(state=EvidenceState.CONTRADICTED, **overrides)


# --- stage one: the six-row resolution table --------------------------------


def test_r_unassessed_when_the_criterion_was_never_checked() -> None:
    """A budget ceiling is a fact about the run, not about the candidate."""
    resolution = resolve_criterion([], criterion(), unassessed_reason="budget_ceiling_reached")

    assert resolution.rule_id == "R-UNASSESSED"
    assert resolution.state is CriterionState.INSUFFICIENT_EVIDENCE
    assert resolution.requires_human is True
    assert resolution.unassessed_reason == "budget_ceiling_reached"


def test_r_conflict_never_resolves_itself() -> None:
    """Support and contradiction together is where a human is cheap and a model
    is dangerous, so it routes rather than averaging."""
    resolution = resolve_criterion([supported(), contradicted()], criterion())

    assert resolution.rule_id == "R-CONFLICT"
    assert resolution.state is CriterionState.CONTRADICTED
    assert resolution.requires_human is True


def test_r_met_when_support_reaches_the_threshold() -> None:
    resolution = resolve_criterion([supported(), supported()], criterion(min_supported=2))

    assert resolution.rule_id == "R-MET"
    assert resolution.state is CriterionState.MET


def test_r_partial_when_support_falls_short() -> None:
    resolution = resolve_criterion([supported()], criterion(min_supported=2))

    assert resolution.rule_id == "R-PARTIAL"
    assert resolution.state is CriterionState.PARTIAL


def test_r_not_met_when_only_contradicted() -> None:
    resolution = resolve_criterion([contradicted()], criterion())

    assert resolution.rule_id == "R-NOT-MET"
    assert resolution.state is CriterionState.NOT_MET


def test_r_insufficient_when_the_document_is_silent() -> None:
    """Absence of evidence is not evidence of absence. This is the distinction a
    naive screener collapses, and the one the recall metric measures."""
    resolution = resolve_criterion([insufficient_evidence()], criterion())

    assert resolution.rule_id == "R-INSUFFICIENT"
    assert resolution.state is CriterionState.INSUFFICIENT_EVIDENCE


def test_silence_never_becomes_met_even_at_min_supported_zero() -> None:
    """A criterion asking for zero support would otherwise be met by an empty
    document, which is exactly what non-negotiable rule 3 forbids."""
    resolution = resolve_criterion([], criterion(min_supported=0))

    assert resolution.state is CriterionState.INSUFFICIENT_EVIDENCE
    assert resolution.rule_id == "R-INSUFFICIENT"


def test_evidence_with_a_broken_span_does_not_count() -> None:
    """An unverifiable quotation carries no weight, however convincing it reads."""
    fabricated = supported(span_validation=SpanValidation.INVALID_NOT_FOUND)
    resolution = resolve_criterion([fabricated], criterion(min_supported=1))

    assert resolution.state is CriterionState.INSUFFICIENT_EVIDENCE
    assert resolution.supported_count == 0


def test_counts_are_reported_for_the_reviewer() -> None:
    resolution = resolve_criterion([supported(), supported(), contradicted()], criterion())

    assert (resolution.supported_count, resolution.contradicted_count) == (2, 1)


# --- stage two: aggregation --------------------------------------------------


def resolve_all(states: dict[str, CriterionState], role: Any) -> list[Any]:
    """Build resolutions directly, so aggregation is tested without stage one."""
    return [
        CriterionResolution(
            criterion_id=item.id,
            state=states.get(item.id, CriterionState.INSUFFICIENT_EVIDENCE),
            rule_id="R-MET",
            supported_count=1,
            contradicted_count=0,
        )
        for item in role.criteria
    ]


TWO_CRITERIA = rubric(
    criteria=[
        criterion(id="alpha", weight=3),
        criterion(id="beta", weight=1),
    ]
)


def test_score_divides_by_resolved_weight_not_total() -> None:
    """The candidate is not charged for the system's failure to find evidence.

    alpha met (weight 3, 1.0 points), beta unassessed. Resolved weight is 3, so
    the score is 3.0 / 3 = 1.0, not 3.0 / 4 = 0.75.
    """
    role = rubric(
        criteria=[criterion(id="alpha", weight=3), criterion(id="beta", weight=1)], min_coverage=0.0
    )
    resolutions = resolve_all({"alpha": CriterionState.MET}, role)

    result = aggregate(resolutions, role, run_id=RUN)

    assert result.score == pytest.approx(1.0)
    assert result.coverage == pytest.approx(0.75)


def test_partial_scores_half() -> None:
    role = rubric(criteria=[criterion(id="alpha", weight=2)], min_coverage=0.0)
    result = aggregate(resolve_all({"alpha": CriterionState.PARTIAL}, role), role, run_id=RUN)

    assert result.score == pytest.approx(0.5)


def test_the_coverage_gate_declines_to_score() -> None:
    """Upload a one-page CV against a rubric it cannot answer and the system
    says it does not know enough, rather than guessing low."""
    role = rubric(
        criteria=[criterion(id="alpha", weight=3), criterion(id="beta", weight=1)],
        min_coverage=0.8,
    )
    result = aggregate(resolve_all({"alpha": CriterionState.MET}, role), role, run_id=RUN)

    assert result.band is Band.INSUFFICIENT_INFORMATION
    assert result.score is None
    assert result.requires_human is True
    assert REASON_COVERAGE in result.requires_human_reasons
    assert [step.rule_id for step in result.derivation][-1] == "R-COVERAGE-GATE"


def test_the_coverage_gate_stops_before_banding() -> None:
    """Nothing after the gate runs, so a gated run cannot pick up a band."""
    role = rubric(
        criteria=[criterion(id="alpha", weight=1), criterion(id="beta", weight=1)], min_coverage=0.9
    )
    result = aggregate(resolve_all({"alpha": CriterionState.MET}, role), role, run_id=RUN)

    assert "R-BAND" not in [step.rule_id for step in result.derivation]


def test_a_failed_blocker_declines_whatever_the_score() -> None:
    role = rubric(
        criteria=[
            criterion(id="alpha", weight=9),
            criterion(id="gate", weight=1, kind=CriterionKind.BLOCKER),
        ],
        min_coverage=0.0,
    )
    result = aggregate(
        resolve_all({"alpha": CriterionState.MET, "gate": CriterionState.NOT_MET}, role),
        role,
        run_id=RUN,
    )

    assert result.band is Band.DECLINE
    assert result.blockers_fired == ["gate"]
    assert "R-BLOCKER-FAIL" in [step.rule_id for step in result.derivation]


def test_a_blocker_decline_keeps_its_score() -> None:
    """Hiding the score would hide that the candidate was strong everywhere else."""
    role = rubric(
        criteria=[
            criterion(id="alpha", weight=9),
            criterion(id="gate", weight=1, kind=CriterionKind.BLOCKER),
        ],
        min_coverage=0.0,
    )
    result = aggregate(
        resolve_all({"alpha": CriterionState.MET, "gate": CriterionState.NOT_MET}, role),
        role,
        run_id=RUN,
    )

    assert result.score is not None and result.score > 0.8


def test_an_unchecked_blocker_asks_rather_than_declines() -> None:
    """You may not decline someone because you failed to check."""
    role = rubric(
        criteria=[
            criterion(id="alpha", weight=3),
            criterion(id="gate", weight=1, kind=CriterionKind.BLOCKER),
        ],
        min_coverage=0.0,
    )
    result = aggregate(resolve_all({"alpha": CriterionState.MET}, role), role, run_id=RUN)

    assert result.band is Band.MANUAL_REVIEW_REQUIRED
    assert result.score is None
    assert REASON_BLOCKER_UNKNOWN in result.requires_human_reasons


def test_a_failed_blocker_outranks_an_unchecked_one() -> None:
    """A known failure is a cleaner answer than an unknown, so decline wins."""
    role = rubric(
        criteria=[
            criterion(id="alpha", weight=3),
            criterion(id="failed", weight=1, kind=CriterionKind.BLOCKER),
            criterion(id="unknown", weight=1, kind=CriterionKind.BLOCKER),
        ],
        min_coverage=0.0,
    )
    result = aggregate(
        resolve_all({"alpha": CriterionState.MET, "failed": CriterionState.NOT_MET}, role),
        role,
        run_id=RUN,
    )

    assert result.band is Band.DECLINE


@pytest.mark.parametrize(
    ("score_state", "weights", "expected"),
    [
        (CriterionState.MET, 4, Band.ADVANCE),
        (CriterionState.PARTIAL, 4, Band.HOLD),
        (CriterionState.NOT_MET, 4, Band.DECLINE),
    ],
)
def test_banding_maps_score_through_the_cutoffs(
    score_state: CriterionState, weights: int, expected: Band
) -> None:
    role = rubric(criteria=[criterion(id="alpha", weight=weights)], min_coverage=0.0)
    result = aggregate(resolve_all({"alpha": score_state}, role), role, run_id=RUN)

    assert result.band is expected


def test_a_score_lands_in_the_first_band_it_reaches() -> None:
    role = rubric(
        criteria=[criterion(id="alpha", weight=1)],
        min_coverage=0.0,
        bands=[
            BandThreshold(band=Band.ADVANCE, min_score=0.9),
            BandThreshold(band=Band.ADVANCE_WITH_RESERVATIONS, min_score=0.5),
            BandThreshold(band=Band.DECLINE, min_score=0.0),
        ],
    )
    result = aggregate(resolve_all({"alpha": CriterionState.PARTIAL}, role), role, run_id=RUN)

    assert result.band is Band.ADVANCE_WITH_RESERVATIONS


# --- the human-required overlay ---------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"integrity": IntegrityTier.SUSPECT}, REASON_INTEGRITY),
        ({"flags": AggregationFlags(invalid_span_present=True)}, REASON_INVALID_SPAN),
        ({"flags": AggregationFlags(partial_profile=True)}, REASON_PARTIAL_PROFILE),
        ({"flags": AggregationFlags(budget_capped=True)}, REASON_BUDGET),
    ],
)
def test_operational_problems_route_to_a_human(kwargs: dict[str, Any], reason: str) -> None:
    role = rubric(criteria=[criterion(id="alpha", weight=1)], min_coverage=0.0)
    result = aggregate(resolve_all({"alpha": CriterionState.MET}, role), role, run_id=RUN, **kwargs)

    assert result.requires_human is True
    assert reason in result.requires_human_reasons


@pytest.mark.parametrize(
    "kwargs",
    [
        {"integrity": IntegrityTier.SUSPECT},
        {"flags": AggregationFlags(invalid_span_present=True)},
        {"flags": AggregationFlags(partial_profile=True)},
        {"flags": AggregationFlags(budget_capped=True)},
    ],
)
def test_the_overlay_does_not_move_the_band(kwargs: dict[str, Any]) -> None:
    """Downgrading a band for an operational reason would put noise into the
    quality metrics. The run routes to review; the band still says what the
    evidence said."""
    role = rubric(criteria=[criterion(id="alpha", weight=1)], min_coverage=0.0)
    clean = aggregate(resolve_all({"alpha": CriterionState.MET}, role), role, run_id=RUN)
    flagged = aggregate(
        resolve_all({"alpha": CriterionState.MET}, role), role, run_id=RUN, **kwargs
    )

    assert flagged.band is clean.band
    assert flagged.score == clean.score


def test_a_contradiction_routes_to_a_human() -> None:
    role = rubric(criteria=[criterion(id="alpha", weight=1)], min_coverage=0.0)
    result = aggregate(resolve_all({"alpha": CriterionState.CONTRADICTED}, role), role, run_id=RUN)

    assert REASON_CONTRADICTED in result.requires_human_reasons


def test_a_clean_run_needs_no_human_reason() -> None:
    role = rubric(criteria=[criterion(id="alpha", weight=1)], min_coverage=0.0)
    result = aggregate(resolve_all({"alpha": CriterionState.MET}, role), role, run_id=RUN)

    assert result.requires_human is False
    assert result.requires_human_reasons == []


def test_reasons_are_not_repeated() -> None:
    role = rubric(
        criteria=[criterion(id="alpha", weight=1), criterion(id="beta", weight=1)],
        min_coverage=0.0,
    )
    result = aggregate(
        resolve_all(
            {"alpha": CriterionState.CONTRADICTED, "beta": CriterionState.CONTRADICTED}, role
        ),
        role,
        run_id=RUN,
    )

    assert result.requires_human_reasons.count(REASON_CONTRADICTED) == 1


# --- the derivation ----------------------------------------------------------


def test_the_derivation_is_ordered_and_complete() -> None:
    role = rubric(criteria=[criterion(id="alpha", weight=1)], min_coverage=0.0)
    result = aggregate(resolve_all({"alpha": CriterionState.MET}, role), role, run_id=RUN)

    assert [step.rule_id for step in result.derivation] == ["R-COVERAGE", "R-SCORE", "R-BAND"]


def test_every_step_carries_its_inputs_and_output() -> None:
    """The derivation is the observability for this subsystem, so a step without
    its inputs is a step nobody can check."""
    role = rubric(criteria=[criterion(id="alpha", weight=1)], min_coverage=0.0)
    result = aggregate(resolve_all({"alpha": CriterionState.MET}, role), role, run_id=RUN)

    for step in result.derivation:
        assert step.rule_id
        assert step.description
        assert step.output


def test_the_derivation_is_written_for_a_recruiter() -> None:
    """The reviewer reads these sentences verbatim."""
    role = rubric(criteria=[criterion(id="alpha", weight=1)], min_coverage=0.0)
    result = aggregate(resolve_all({"alpha": CriterionState.MET}, role), role, run_id=RUN)

    prose = " ".join(step.description for step in result.derivation).lower()
    for jargon in ("null", "none", "dict", "enum", "criterion_id", "traceback"):
        assert jargon not in prose


def test_every_rubric_criterion_appears_in_the_states() -> None:
    """A criterion missing from the output would silently vanish from the
    scorecard rather than showing as unassessed."""
    role = rubric(
        criteria=[criterion(id="alpha", weight=1), criterion(id="beta", weight=1)],
        min_coverage=0.0,
    )
    result = aggregate(resolve_all({"alpha": CriterionState.MET}, role), role, run_id=RUN)

    assert set(result.criterion_states) == {"alpha", "beta"}
    assert result.criterion_states["beta"] is CriterionState.INSUFFICIENT_EVIDENCE


def test_aggregation_is_deterministic() -> None:
    """Same inputs, same output, always. Every experiment downstream depends on
    this being true."""
    role = TWO_CRITERIA
    resolutions = resolve_all({"alpha": CriterionState.MET, "beta": CriterionState.PARTIAL}, role)

    first = aggregate(resolutions, role, run_id=RUN)
    second = aggregate(resolutions, role, run_id=RUN)

    assert first.model_dump() == second.model_dump()
