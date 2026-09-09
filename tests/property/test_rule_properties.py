"""The architectural claim, stated as properties over generated inputs.

These three are the formal version of "the model did not decide the outcome".
The table-driven tests check the rows someone wrote down; these check every
combination the generator can reach.

Do not weaken this module. If a property fails, the rule engine is wrong.
"""

from __future__ import annotations

from uuid import uuid4

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from domain.contracts import Band, BandThreshold, CriterionKind, CriterionState, EvidenceState
from domain.rules import AggregationFlags, aggregate, resolve_criterion
from domain.rules.resolve import CriterionResolution
from tests.builders import criterion, evidence, rubric

RUN = uuid4()

#: How states rank. Used only to assert that adding support never moves a
#: criterion down this ladder.
STATE_ORDER = {
    CriterionState.NOT_MET: 0,
    CriterionState.CONTRADICTED: 0,
    CriterionState.INSUFFICIENT_EVIDENCE: 1,
    CriterionState.PARTIAL: 2,
    CriterionState.MET: 3,
}

scoring_states = st.sampled_from(
    [
        CriterionState.MET,
        CriterionState.PARTIAL,
        CriterionState.NOT_MET,
        CriterionState.CONTRADICTED,
        CriterionState.INSUFFICIENT_EVIDENCE,
    ]
)


def resolution(criterion_id: str, state: CriterionState) -> CriterionResolution:
    return CriterionResolution(
        criterion_id=criterion_id,
        state=state,
        rule_id="R-MET",
        supported_count=1 if state is CriterionState.MET else 0,
        contradicted_count=1 if state is CriterionState.NOT_MET else 0,
    )


# --- property 1: monotonicity ------------------------------------------------


@given(
    supporting=st.integers(min_value=0, max_value=6),
    extra=st.integers(min_value=1, max_value=4),
    min_supported=st.integers(min_value=1, max_value=4),
)
@settings(max_examples=200, deadline=None)
def test_adding_support_never_lowers_a_criterion(
    supporting: int, extra: int, min_supported: int
) -> None:
    """More supporting evidence never makes a candidate look worse.

    A rule engine that could go down when evidence goes up would be unusable:
    a reviewer adding a second document could lower the band.
    """
    config = criterion(min_supported=min_supported)
    before = [evidence(state=EvidenceState.SUPPORTED) for _ in range(supporting)]
    after = [*before, *(evidence(state=EvidenceState.SUPPORTED) for _ in range(extra))]

    assert (
        STATE_ORDER[resolve_criterion(after, config).state]
        >= STATE_ORDER[resolve_criterion(before, config).state]
    )


@given(rest=st.lists(scoring_states, min_size=0, max_size=5))
@settings(max_examples=200, deadline=None)
def test_upgrading_one_criterion_never_lowers_the_score(rest: list[CriterionState]) -> None:
    """Moving a single criterion from partial to met cannot reduce the score.

    The first criterion is fixed as partial by construction rather than filtered
    for, so every generated example exercises the property instead of most of
    them being discarded.
    """
    states = [CriterionState.PARTIAL, *rest]

    role = rubric(
        criteria=[criterion(id=f"c-{i:02d}", weight=2) for i in range(len(states))],
        min_coverage=0.0,
    )
    before = [resolution(f"c-{i:02d}", state) for i, state in enumerate(states)]
    upgraded = [CriterionState.MET, *rest]
    after = [resolution(f"c-{i:02d}", state) for i, state in enumerate(upgraded)]

    first = aggregate(before, role, run_id=RUN)
    second = aggregate(after, role, run_id=RUN)

    if first.score is not None and second.score is not None:
        assert second.score >= first.score - 1e-9


# --- property 2: blocker dominance -------------------------------------------


@given(other_states=st.lists(scoring_states, min_size=1, max_size=6))
@settings(max_examples=200, deadline=None)
def test_a_failed_blocker_declines_for_every_possible_score(
    other_states: list[CriterionState],
) -> None:
    """No arrangement of the other criteria can rescue a failed requirement."""
    criteria = [criterion(id=f"c-{i:02d}", weight=3) for i in range(len(other_states))]
    criteria.append(criterion(id="gate", weight=1, kind=CriterionKind.BLOCKER))
    role = rubric(criteria=criteria, min_coverage=0.0)

    resolutions = [resolution(f"c-{i:02d}", state) for i, state in enumerate(other_states)]
    resolutions.append(resolution("gate", CriterionState.NOT_MET))

    result = aggregate(resolutions, role, run_id=RUN)

    assert result.band is Band.DECLINE
    assert "gate" in result.blockers_fired


@given(other_states=st.lists(scoring_states, min_size=1, max_size=6))
@settings(max_examples=200, deadline=None)
def test_an_unchecked_blocker_never_declines(other_states: list[CriterionState]) -> None:
    """You may not decline someone for a requirement you failed to check, no
    matter how the rest of the rubric came out."""
    criteria = [criterion(id=f"c-{i:02d}", weight=3) for i in range(len(other_states))]
    criteria.append(criterion(id="gate", weight=1, kind=CriterionKind.BLOCKER))
    role = rubric(criteria=criteria, min_coverage=0.0)

    resolutions = [resolution(f"c-{i:02d}", state) for i, state in enumerate(other_states)]
    resolutions.append(resolution("gate", CriterionState.INSUFFICIENT_EVIDENCE))

    result = aggregate(resolutions, role, run_id=RUN)

    # The property that matters: an unchecked requirement never declines anyone.
    assert result.band is not Band.DECLINE

    # When something else was assessed, the run asks about the requirement. When
    # nothing at all was assessed, the more informative answer wins: there was no
    # basis for a recommendation in the first place.
    anything_assessed = any(
        state is not CriterionState.INSUFFICIENT_EVIDENCE for state in other_states
    )
    expected = Band.MANUAL_REVIEW_REQUIRED if anything_assessed else Band.INSUFFICIENT_INFORMATION
    assert result.band is expected


# --- property 3: coverage dominance ------------------------------------------


@given(
    total=st.integers(min_value=2, max_value=8),
    assessed=st.integers(min_value=0, max_value=8),
    gate=st.floats(min_value=0.5, max_value=1.0),
    state=scoring_states,
)
@settings(max_examples=300, deadline=None)
def test_below_the_coverage_gate_the_band_is_always_insufficient_information(
    total: int, assessed: int, gate: float, state: CriterionState
) -> None:
    """When too little could be assessed, no arrangement of the assessed
    criteria produces a score. The system says it does not know."""
    assume(assessed <= total)
    assume(state is not CriterionState.INSUFFICIENT_EVIDENCE)

    role = rubric(
        criteria=[criterion(id=f"c-{i:02d}", weight=1) for i in range(total)],
        min_coverage=gate,
    )
    resolutions = [
        resolution(f"c-{i:02d}", state if i < assessed else CriterionState.INSUFFICIENT_EVIDENCE)
        for i in range(total)
    ]

    result = aggregate(resolutions, role, run_id=RUN)
    coverage = assessed / total

    if coverage < gate:
        assert result.band is Band.INSUFFICIENT_INFORMATION
        assert result.score is None
        assert result.requires_human is True
    else:
        assert result.band is not Band.INSUFFICIENT_INFORMATION


# --- invariants that hold across every generated case ------------------------


@given(
    states=st.lists(scoring_states, min_size=1, max_size=8),
    invalid_span=st.booleans(),
    partial=st.booleans(),
    capped=st.booleans(),
)
@settings(max_examples=300, deadline=None)
def test_the_score_never_leaves_the_unit_interval(
    states: list[CriterionState], invalid_span: bool, partial: bool, capped: bool
) -> None:
    role = rubric(
        criteria=[criterion(id=f"c-{i:02d}", weight=i % 5 + 1) for i in range(len(states))],
        min_coverage=0.0,
    )
    resolutions = [resolution(f"c-{i:02d}", state) for i, state in enumerate(states)]

    result = aggregate(
        resolutions,
        role,
        run_id=RUN,
        flags=AggregationFlags(
            invalid_span_present=invalid_span, partial_profile=partial, budget_capped=capped
        ),
    )

    assert result.score is None or 0.0 <= result.score <= 1.0
    assert 0.0 <= result.coverage <= 1.0


@given(states=st.lists(scoring_states, min_size=1, max_size=8))
@settings(max_examples=200, deadline=None)
def test_a_scoreless_band_always_explains_itself(states: list[CriterionState]) -> None:
    """A missing score is a stated outcome. Every path that produces one must
    also produce a reason a reviewer can read."""
    role = rubric(
        criteria=[criterion(id=f"c-{i:02d}", weight=2) for i in range(len(states))],
        min_coverage=0.5,
    )
    resolutions = [resolution(f"c-{i:02d}", state) for i, state in enumerate(states)]

    result = aggregate(resolutions, role, run_id=RUN)

    if result.score is None:
        assert result.band in (Band.INSUFFICIENT_INFORMATION, Band.MANUAL_REVIEW_REQUIRED)
        assert result.requires_human_reasons


@given(rest=st.lists(scoring_states, min_size=0, max_size=4))
@settings(max_examples=200, deadline=None)
def test_insufficient_evidence_is_never_scored_as_a_zero(rest: list[CriterionState]) -> None:
    """The candidate is never charged for the system's failure to find evidence.

    Replacing an unassessed criterion with an assessed zero-point one must not
    leave the score unchanged: if it did, absence and failure would be the same
    thing, which is the collapse this system exists to avoid.

    The list is built with one of each rather than filtered for, so every
    generated example exercises the property instead of most being discarded.
    """
    states = [CriterionState.INSUFFICIENT_EVIDENCE, CriterionState.MET, *rest]

    role = rubric(
        criteria=[criterion(id=f"c-{i:02d}", weight=2) for i in range(len(states))],
        min_coverage=0.0,
    )
    with_absence = aggregate(
        [resolution(f"c-{i:02d}", state) for i, state in enumerate(states)], role, run_id=RUN
    )
    as_failures = aggregate(
        [
            resolution(
                f"c-{i:02d}",
                CriterionState.NOT_MET if state is CriterionState.INSUFFICIENT_EVIDENCE else state,
            )
            for i, state in enumerate(states)
        ],
        role,
        run_id=RUN,
    )

    assert with_absence.score is not None
    assert as_failures.score is not None
    assert with_absence.score > as_failures.score


@given(states=st.lists(scoring_states, min_size=1, max_size=6))
@settings(max_examples=200, deadline=None)
def test_the_band_is_reproducible(states: list[CriterionState]) -> None:
    """Same inputs, same band. Every downstream experiment rests on this."""
    role = rubric(
        criteria=[criterion(id=f"c-{i:02d}", weight=2) for i in range(len(states))],
        min_coverage=0.0,
    )
    resolutions = [resolution(f"c-{i:02d}", state) for i, state in enumerate(states)]

    assert (
        aggregate(resolutions, role, run_id=RUN).band
        is aggregate(resolutions, role, run_id=RUN).band
    )


def test_a_rubric_whose_bands_stop_short_still_lands_somewhere() -> None:
    """Guards the fall-through in _band_for. The rubric validator makes this
    unreachable, so this asserts the defence rather than the path."""
    role = rubric(
        criteria=[criterion(id="alpha", weight=1)],
        min_coverage=0.0,
        bands=[BandThreshold(band=Band.HOLD, min_score=0.0)],
    )
    result = aggregate([resolution("alpha", CriterionState.NOT_MET)], role, run_id=RUN)

    assert result.band is Band.HOLD
