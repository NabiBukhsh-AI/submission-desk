"""Every edge in the state diagram, tested as allowed."""

from __future__ import annotations

import pytest

from domain.contracts.enums import RunStatus
from domain.errors import IllegalTransition
from domain.state_machine import (
    ALLOWED,
    TERMINAL,
    can_transition,
    is_reviewable,
    is_terminal,
    requires_review,
    transition,
)

#: The diagram from the architecture, transcribed edge by edge. Written out in
#: full rather than derived from ALLOWED, because a test that reads its
#: expectations from the thing it is testing proves only that it can read.
DIAGRAM_EDGES = [
    (RunStatus.CREATED, RunStatus.INTAKE_OK),
    (RunStatus.CREATED, RunStatus.FAILED_TERMINAL),
    (RunStatus.INTAKE_OK, RunStatus.EXTRACTED),
    (RunStatus.INTAKE_OK, RunStatus.FAILED_TERMINAL),
    (RunStatus.EXTRACTED, RunStatus.SANITIZED),
    (RunStatus.EXTRACTED, RunStatus.FAILED_TERMINAL),
    (RunStatus.SANITIZED, RunStatus.STRUCTURED),
    (RunStatus.SANITIZED, RunStatus.QUARANTINED),
    (RunStatus.STRUCTURED, RunStatus.CALIBRATED),
    (RunStatus.CALIBRATED, RunStatus.ASSESSED),
    (RunStatus.ASSESSED, RunStatus.AGGREGATED),
    (RunStatus.ASSESSED, RunStatus.MANUAL_REVIEW_REQUIRED),
    (RunStatus.AGGREGATED, RunStatus.COMPOSED),
    (RunStatus.COMPOSED, RunStatus.READY_FOR_REVIEW),
    (RunStatus.COMPOSED, RunStatus.NEEDS_REVIEW),
    (RunStatus.READY_FOR_REVIEW, RunStatus.APPROVED),
    (RunStatus.READY_FOR_REVIEW, RunStatus.REJECTED),
    (RunStatus.READY_FOR_REVIEW, RunStatus.NEEDS_INFO),
    (RunStatus.NEEDS_REVIEW, RunStatus.APPROVED),
    (RunStatus.NEEDS_REVIEW, RunStatus.REJECTED),
    (RunStatus.NEEDS_REVIEW, RunStatus.NEEDS_INFO),
    (RunStatus.QUARANTINED, RunStatus.NEEDS_REVIEW),
    (RunStatus.QUARANTINED, RunStatus.REJECTED),
    (RunStatus.MANUAL_REVIEW_REQUIRED, RunStatus.NEEDS_REVIEW),
    (RunStatus.NEEDS_INFO, RunStatus.CREATED),
    (RunStatus.APPROVED, RunStatus.DELIVERED),
    (RunStatus.APPROVED, RunStatus.DELIVERY_PENDING_RETRY),
    (RunStatus.DELIVERY_PENDING_RETRY, RunStatus.DELIVERED),
    (RunStatus.INTERRUPTED, RunStatus.CREATED),
]


@pytest.mark.parametrize(("current", "target"), DIAGRAM_EDGES)
def test_every_diagram_edge_is_permitted(current: RunStatus, target: RunStatus) -> None:
    assert transition(current, target) is target
    assert can_transition(current, target)


def test_the_graph_holds_exactly_the_diagram_edges() -> None:
    """No edge exists that the diagram does not show.

    This is the half that catches an edge added quietly: the parametrised test
    above proves the diagram is implemented, and this proves nothing else is.
    """
    implemented = {(current, target) for current, onward in ALLOWED.items() for target in onward}
    assert implemented == set(DIAGRAM_EDGES)


def test_every_status_appears_in_the_graph() -> None:
    """A status with no entry would silently permit nothing, which reads as a
    dead end rather than as the omission it is."""
    assert set(ALLOWED) == set(RunStatus)


def test_terminal_states_are_the_three_ends() -> None:
    assert (
        frozenset({RunStatus.REJECTED, RunStatus.DELIVERED, RunStatus.FAILED_TERMINAL}) == TERMINAL
    )


@pytest.mark.parametrize("status", sorted(TERMINAL))
def test_nothing_proceeds_from_a_terminal_state(status: RunStatus) -> None:
    assert is_terminal(status)
    for target in RunStatus:
        with pytest.raises(IllegalTransition):
            transition(status, target)


def test_the_error_names_both_states() -> None:
    """A message naming only one state sends the reader to the wrong place."""
    with pytest.raises(IllegalTransition, match="cannot move from delivered to approved"):
        transition(RunStatus.DELIVERED, RunStatus.APPROVED)


def test_the_error_carries_a_code() -> None:
    with pytest.raises(IllegalTransition) as caught:
        transition(RunStatus.CREATED, RunStatus.DELIVERED)
    assert caught.value.error_code == "ILLEGAL_TRANSITION"
    assert caught.value.retryable is False


# --- helpers the interface uses ---------------------------------------------


@pytest.mark.parametrize(
    "status",
    [RunStatus.READY_FOR_REVIEW, RunStatus.NEEDS_REVIEW, RunStatus.QUARANTINED],
)
def test_reviewable_states_can_be_opened(status: RunStatus) -> None:
    assert is_reviewable(status)


@pytest.mark.parametrize(
    "status",
    [RunStatus.CREATED, RunStatus.ASSESSED, RunStatus.DELIVERED, RunStatus.FAILED_TERMINAL],
)
def test_other_states_are_not_reviewable(status: RunStatus) -> None:
    assert not is_reviewable(status)


@pytest.mark.parametrize(
    "status",
    [RunStatus.NEEDS_REVIEW, RunStatus.QUARANTINED, RunStatus.MANUAL_REVIEW_REQUIRED],
)
def test_attention_states_are_flagged(status: RunStatus) -> None:
    assert requires_review(status)


def test_a_clean_run_does_not_require_attention() -> None:
    """READY_FOR_REVIEW is the happy path: a reviewer still approves it, but
    nothing went wrong, and the queue should not colour it as though it did."""
    assert not requires_review(RunStatus.READY_FOR_REVIEW)


def test_a_state_cannot_transition_to_itself() -> None:
    for status in RunStatus:
        assert not can_transition(status, status)
