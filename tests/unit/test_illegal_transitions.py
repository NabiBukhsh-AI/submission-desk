"""Every row of the illegal-transition table, tested as raising.

Each case here is a specific way the approval gate could be stepped around, and
each one was written down because it is plausible rather than because it is
exotic.
"""

from __future__ import annotations

import pytest

from domain.contracts.enums import RunStatus
from domain.errors import IllegalTransition
from domain.state_machine import ALLOWED, DELIVERY_PREDECESSORS, transition


@pytest.mark.parametrize(
    "origin",
    sorted(set(RunStatus) - DELIVERY_PREDECESSORS),
)
def test_nothing_but_approval_leads_to_delivered(origin: RunStatus) -> None:
    """The approval gate, asserted one origin at a time.

    Delivery means something left the building: a spreadsheet row, a message, a
    file handed to someone. Only a persisted review decision opens that door.
    """
    with pytest.raises(IllegalTransition):
        transition(origin, RunStatus.DELIVERED)


def test_a_reviewable_run_cannot_skip_the_human() -> None:
    with pytest.raises(IllegalTransition):
        transition(RunStatus.READY_FOR_REVIEW, RunStatus.DELIVERED)


def test_a_quarantined_run_cannot_be_approved_directly() -> None:
    """It must pass through review, so the reviewer sees the integrity banner
    before deciding rather than after."""
    with pytest.raises(IllegalTransition):
        transition(RunStatus.QUARANTINED, RunStatus.APPROVED)


def test_assessment_cannot_skip_aggregation() -> None:
    """The recommendation is computed, never implied by the evidence looking good."""
    with pytest.raises(IllegalTransition):
        transition(RunStatus.ASSESSED, RunStatus.COMPOSED)


def test_aggregation_cannot_skip_composition() -> None:
    """Composition is what gives the reviewer questions and a missing-info draft,
    so a run that skipped it would present a band with nothing to act on."""
    with pytest.raises(IllegalTransition):
        transition(RunStatus.AGGREGATED, RunStatus.READY_FOR_REVIEW)


@pytest.mark.parametrize("target", sorted(RunStatus))
def test_a_terminal_failure_stays_terminal(target: RunStatus) -> None:
    with pytest.raises(IllegalTransition):
        transition(RunStatus.FAILED_TERMINAL, target)


BACKWARD_ATTEMPTS = [
    (RunStatus.EXTRACTED, RunStatus.INTAKE_OK),
    (RunStatus.SANITIZED, RunStatus.EXTRACTED),
    (RunStatus.STRUCTURED, RunStatus.SANITIZED),
    (RunStatus.ASSESSED, RunStatus.CALIBRATED),
    (RunStatus.AGGREGATED, RunStatus.ASSESSED),
    (RunStatus.COMPOSED, RunStatus.AGGREGATED),
    (RunStatus.APPROVED, RunStatus.READY_FOR_REVIEW),
    (RunStatus.DELIVERED, RunStatus.APPROVED),
]


@pytest.mark.parametrize(("current", "target"), BACKWARD_ATTEMPTS)
def test_the_pipeline_does_not_run_backwards(current: RunStatus, target: RunStatus) -> None:
    """Reprocessing is a new run with a new content key, not a rewind.

    A rewind would leave evidence, costs, and node records from two attempts
    interleaved on one row, and no reviewer could tell which pass produced what.
    """
    with pytest.raises(IllegalTransition):
        transition(current, target)


#: The forward spine of the pipeline. The off-ramps (quarantine, manual review,
#: needs-info) hang off it and are not ordered against it, so they are excluded
#: rather than compared by enum declaration order, which means nothing.
LINEAR_SPINE = [
    RunStatus.CREATED,
    RunStatus.INTAKE_OK,
    RunStatus.EXTRACTED,
    RunStatus.SANITIZED,
    RunStatus.STRUCTURED,
    RunStatus.CALIBRATED,
    RunStatus.ASSESSED,
    RunStatus.AGGREGATED,
    RunStatus.COMPOSED,
    RunStatus.READY_FOR_REVIEW,
    RunStatus.APPROVED,
    RunStatus.DELIVERED,
]


def test_the_spine_never_runs_backwards() -> None:
    """No stage of the forward pipeline re-enters an earlier stage."""
    backward = {
        (current, target)
        for current, onward in ALLOWED.items()
        for target in onward
        if current in LINEAR_SPINE
        and target in LINEAR_SPINE
        and LINEAR_SPINE.index(target) <= LINEAR_SPINE.index(current)
    }
    assert backward == set()


def test_only_two_documented_edges_restart_a_run() -> None:
    """NEEDS_INFO returns to CREATED when documents arrive. INTERRUPTED returns
    to CREATED when a crashed run is reconciled. Nothing else goes back."""
    restarts = {current for current, onward in ALLOWED.items() if RunStatus.CREATED in onward}
    assert restarts == {RunStatus.NEEDS_INFO, RunStatus.INTERRUPTED}


def test_a_run_cannot_be_approved_without_reaching_review() -> None:
    for origin in (RunStatus.CREATED, RunStatus.ASSESSED, RunStatus.AGGREGATED):
        with pytest.raises(IllegalTransition):
            transition(origin, RunStatus.APPROVED)


def test_delivery_retry_is_not_a_way_in() -> None:
    """DELIVERY_PENDING_RETRY is downstream of approval, so it cannot be entered
    from anywhere that has not already passed the gate."""
    for origin in set(RunStatus) - {RunStatus.APPROVED}:
        with pytest.raises(IllegalTransition):
            transition(origin, RunStatus.DELIVERY_PENDING_RETRY)


def test_transition_refuses_a_string() -> None:
    """A string would let a typo become a new state, silently."""
    with pytest.raises(IllegalTransition):
        transition(RunStatus.APPROVED, "delivered")  # type: ignore[arg-type]
