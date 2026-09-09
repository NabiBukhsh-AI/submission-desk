"""The approval gate, proved over every path rather than every path someone
thought of.

The tests in ``test_illegal_transitions.py`` check single edges. These search
the whole graph, so an edge added three states away that opens a route around
the gate fails here even though every individual edge looks reasonable.

Do not weaken this module. It is the security guarantee, not a style check.
"""

from __future__ import annotations

import pytest

from domain.contracts.enums import RunStatus
from domain.state_machine import (
    ALLOWED,
    DELIVERY_PREDECESSORS,
    TERMINAL,
    paths_to,
    reachable_from,
)


def test_every_route_to_delivery_passes_through_approval() -> None:
    """The guarantee. Search every simple path from CREATED to DELIVERED and
    assert each one's second-to-last state is approval or its retry."""
    routes = paths_to(RunStatus.DELIVERED)
    assert routes, "no route to DELIVERED exists, so this test proves nothing"

    for route in routes:
        assert route[-2] in DELIVERY_PREDECESSORS, (
            f"a run reaches DELIVERED from {route[-2].value} without approval: "
            f"{' -> '.join(status.value for status in route)}"
        )


def test_every_route_to_delivery_passes_through_approved_itself() -> None:
    """DELIVERY_PENDING_RETRY is downstream of APPROVED, so APPROVED appears on
    every route regardless of which of the two doors was used."""
    for route in paths_to(RunStatus.DELIVERED):
        assert RunStatus.APPROVED in route, (
            f"route bypasses approval entirely: {' -> '.join(s.value for s in route)}"
        )


def test_delivery_retry_is_reachable_only_from_approval() -> None:
    origins = {
        current for current, onward in ALLOWED.items() if RunStatus.DELIVERY_PENDING_RETRY in onward
    }
    assert origins == {RunStatus.APPROVED}


def test_every_route_to_approval_passes_through_a_review_state() -> None:
    """Approval is a human act, so the run must have been in a state a reviewer
    could open before it can be approved."""
    routes = paths_to(RunStatus.APPROVED)
    assert routes

    for route in routes:
        assert route[-2] in (RunStatus.READY_FOR_REVIEW, RunStatus.NEEDS_REVIEW), (
            f"a run reaches APPROVED from {route[-2].value}: "
            f"{' -> '.join(status.value for status in route)}"
        )


def test_every_route_from_quarantine_to_a_decision_passes_through_review() -> None:
    """An adversarial document cannot be approved without the reviewer having
    been shown the integrity banner first."""
    for route in paths_to(RunStatus.APPROVED, start=RunStatus.QUARANTINED):
        assert RunStatus.NEEDS_REVIEW in route, (
            f"quarantine reaches approval without review: "
            f"{' -> '.join(status.value for status in route)}"
        )


def test_every_route_to_composition_passes_through_aggregation() -> None:
    """The recommendation is computed before it is packaged, always."""
    for route in paths_to(RunStatus.COMPOSED):
        assert RunStatus.AGGREGATED in route


def test_every_state_is_reachable_from_created_except_the_two_entry_points() -> None:
    """An unreachable state is dead code that still has to be maintained.

    INTERRUPTED is set by crash reconciliation rather than reached by a
    transition, so it is the one legitimate exception.
    """
    reachable = reachable_from(RunStatus.CREATED)
    unreachable = set(RunStatus) - reachable
    assert unreachable == {RunStatus.INTERRUPTED}


def test_every_state_can_reach_a_terminal_state() -> None:
    """A state that cannot end is a run stuck in the queue forever with no
    action that clears it."""
    for status in RunStatus:
        assert reachable_from(status) & TERMINAL, f"{status.value} can never conclude"


@pytest.mark.parametrize("status", sorted(TERMINAL))
def test_terminal_states_reach_only_themselves(status: RunStatus) -> None:
    assert reachable_from(status) == frozenset({status})


def test_the_gate_would_fail_if_an_edge_were_added(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard is proved capable of failing.

    Adding READY_FOR_REVIEW -> DELIVERED is the specific mistake the gate exists
    to catch, and it is exactly the shortcut that looks harmless in a hurry.
    """
    weakened = dict(ALLOWED)
    weakened[RunStatus.READY_FOR_REVIEW] = ALLOWED[RunStatus.READY_FOR_REVIEW] | {
        RunStatus.DELIVERED
    }
    monkeypatch.setattr("domain.state_machine.ALLOWED", weakened)

    offending = [
        route for route in paths_to(RunStatus.DELIVERED) if route[-2] not in DELIVERY_PREDECESSORS
    ]
    assert offending, "the reachability search did not notice a route around the approval gate"
