"""The exception taxonomy.

Every failure in the system raises from here. Nothing raises a bare
``Exception``, and nothing catches one except the runner boundary, where it is
logged with the node name and converted into a failed node result.

Each exception carries an ``error_code`` because that code is what lands in an
``ErrorRecord``, what the operations page groups by, and what the runbook is
indexed on. A message is for a person; a code is for a query.

Classes are added here as the phases that need them arrive. Adding one is
ordinary; widening the base class is not.
"""

from __future__ import annotations


class SubmissionDeskError(Exception):
    """Base for every error this system raises deliberately."""

    error_code = "UNCLASSIFIED"
    retryable = False


class IllegalTransition(SubmissionDeskError):
    """A run was asked to move to a state the graph does not permit.

    Raised by ``domain.state_machine.transition``. Both the interface and the
    command line call that one function, so neither can produce a state the
    other could not, and the approval gate cannot be stepped around by picking a
    different entry point.
    """

    error_code = "ILLEGAL_TRANSITION"

    def __init__(self, current: object, target: object, reason: str | None = None) -> None:
        self.current = current
        self.target = target
        self.reason = reason
        message = (
            f"cannot move from {getattr(current, 'value', current)} to "
            f"{getattr(target, 'value', target)}"
        )
        super().__init__(f"{message}: {reason}" if reason else message)
