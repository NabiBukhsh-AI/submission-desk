"""The run state graph, as data.

One source of truth that the command line and the interface both call, so
neither can reach a state the other could not. The graph is a dictionary rather
than a chain of conditionals because a conditional chain is where an exception
gets added quietly at four in the afternoon on day four.

The guarantee this module exists to provide: nothing reaches ``DELIVERED``
except from ``APPROVED`` or ``DELIVERY_PENDING_RETRY``. A breadth-first search
over ``ALLOWED`` proves it across every path, not just the ones someone thought
to test.
"""

from __future__ import annotations

from domain.contracts.enums import RunStatus
from domain.errors import IllegalTransition

#: The permitted moves. Forward-only, with two deliberate exceptions:
#: NEEDS_INFO returns to CREATED when documents arrive, and INTERRUPTED returns
#: to CREATED when a crashed run is reconciled on startup. Reprocessing is
#: otherwise a new run with a new content key, never a rewind of this one.
ALLOWED: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.INTAKE_OK, RunStatus.FAILED_TERMINAL}),
    RunStatus.INTAKE_OK: frozenset({RunStatus.EXTRACTED, RunStatus.FAILED_TERMINAL}),
    RunStatus.EXTRACTED: frozenset({RunStatus.SANITIZED, RunStatus.FAILED_TERMINAL}),
    RunStatus.SANITIZED: frozenset({RunStatus.STRUCTURED, RunStatus.QUARANTINED}),
    RunStatus.STRUCTURED: frozenset({RunStatus.CALIBRATED}),
    RunStatus.CALIBRATED: frozenset({RunStatus.ASSESSED}),
    RunStatus.ASSESSED: frozenset({RunStatus.AGGREGATED, RunStatus.MANUAL_REVIEW_REQUIRED}),
    RunStatus.AGGREGATED: frozenset({RunStatus.COMPOSED}),
    RunStatus.COMPOSED: frozenset({RunStatus.READY_FOR_REVIEW, RunStatus.NEEDS_REVIEW}),
    RunStatus.READY_FOR_REVIEW: frozenset(
        {RunStatus.APPROVED, RunStatus.REJECTED, RunStatus.NEEDS_INFO}
    ),
    RunStatus.NEEDS_REVIEW: frozenset(
        {RunStatus.APPROVED, RunStatus.REJECTED, RunStatus.NEEDS_INFO}
    ),
    # A quarantined document reaches a decision only through review, so the
    # reviewer always sees the integrity banner before anything else happens.
    RunStatus.QUARANTINED: frozenset({RunStatus.NEEDS_REVIEW, RunStatus.REJECTED}),
    RunStatus.MANUAL_REVIEW_REQUIRED: frozenset({RunStatus.NEEDS_REVIEW}),
    RunStatus.NEEDS_INFO: frozenset({RunStatus.CREATED}),
    # The approval gate. These are the only two doors into DELIVERED.
    RunStatus.APPROVED: frozenset({RunStatus.DELIVERED, RunStatus.DELIVERY_PENDING_RETRY}),
    RunStatus.DELIVERY_PENDING_RETRY: frozenset({RunStatus.DELIVERED}),
    RunStatus.INTERRUPTED: frozenset({RunStatus.CREATED}),
    RunStatus.REJECTED: frozenset(),
    RunStatus.DELIVERED: frozenset(),
    RunStatus.FAILED_TERMINAL: frozenset(),
}

#: States from which nothing proceeds. Terminal means terminal: a reviewer
#: starts a new run rather than reviving this one.
TERMINAL: frozenset[RunStatus] = frozenset(
    status for status, onward in ALLOWED.items() if not onward
)

#: The only states from which a run may enter DELIVERED.
DELIVERY_PREDECESSORS: frozenset[RunStatus] = frozenset(
    {RunStatus.APPROVED, RunStatus.DELIVERY_PENDING_RETRY}
)

#: States a reviewer can open and act on.
REVIEWABLE: frozenset[RunStatus] = frozenset(
    {RunStatus.READY_FOR_REVIEW, RunStatus.NEEDS_REVIEW, RunStatus.QUARANTINED}
)

#: States that reached a reviewer because something needs a person, not because
#: the pipeline finished cleanly.
NEEDS_ATTENTION: frozenset[RunStatus] = frozenset(
    {RunStatus.NEEDS_REVIEW, RunStatus.QUARANTINED, RunStatus.MANUAL_REVIEW_REQUIRED}
)


def transition(current: RunStatus, target: RunStatus) -> RunStatus:
    """Move a run from one state to another, or refuse.

    Takes enums, never strings: a string would let a typo become a new state.
    There is no force parameter and no administrative override, because an
    escape hatch in this function is an escape hatch around the approval gate.
    """
    # A str enum compares equal to its own value, so "delivered" would satisfy
    # a membership test against frozenset({RunStatus.DELIVERED}) and slip
    # straight through. The rule that this takes enums needs an explicit guard.
    if not isinstance(current, RunStatus) or not isinstance(target, RunStatus):
        raise IllegalTransition(
            current, target, reason="transition takes RunStatus members, not strings"
        )
    if target not in ALLOWED.get(current, frozenset()):
        raise IllegalTransition(current, target)
    return target


def can_transition(current: RunStatus, target: RunStatus) -> bool:
    """Whether a move is permitted, for rendering a button as disabled.

    Applies the same type guard as ``transition``. If this helper were laxer,
    the interface would offer a move the enforcer then refuses, which is the
    divergence this module exists to prevent.
    """
    return (
        isinstance(current, RunStatus)
        and isinstance(target, RunStatus)
        and target in ALLOWED.get(current, frozenset())
    )


def is_terminal(status: RunStatus) -> bool:
    return status in TERMINAL


def is_reviewable(status: RunStatus) -> bool:
    """Whether a reviewer can open this run and act on it."""
    return status in REVIEWABLE


def requires_review(status: RunStatus) -> bool:
    """Whether this run reached the queue because something needs a person."""
    return status in NEEDS_ATTENTION


def reachable_from(start: RunStatus) -> frozenset[RunStatus]:
    """Every state reachable from ``start``, by breadth-first search.

    Used by the property tests to make claims about all paths rather than about
    the paths someone remembered to write down.
    """
    seen: set[RunStatus] = {start}
    queue = [start]
    while queue:
        for onward in ALLOWED.get(queue.pop(), frozenset()):
            if onward not in seen:
                seen.add(onward)
                queue.append(onward)
    return frozenset(seen)


def paths_to(target: RunStatus, start: RunStatus = RunStatus.CREATED) -> list[list[RunStatus]]:
    """Every simple path from ``start`` to ``target``.

    Simple meaning no repeated state, which keeps the search finite in a graph
    that has cycles through NEEDS_INFO. Used to assert what every route into a
    state must pass through.
    """
    found: list[list[RunStatus]] = []

    def walk(current: RunStatus, path: list[RunStatus]) -> None:
        if current is target and len(path) > 1:
            found.append(path)
            return
        for onward in sorted(ALLOWED.get(current, frozenset())):
            if onward not in path:
                walk(onward, [*path, onward])

    walk(start, [start])
    return found
