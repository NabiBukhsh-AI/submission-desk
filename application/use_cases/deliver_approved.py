"""Send every approved package, and nothing else.

Approval and delivery are two steps on purpose. A reviewer approving a candidate
is deciding; the system sending the package is acting on that decision, and the
gap between them is where a wrong click gets caught. So the review page records
the decision and this use case, run from the queue or the command line, does the
sending.

It does not deliver. It hands each approved run back to the runner, which
executes the one node left — DELIVER — with the same refusal checks, the same
per-sink records and the same state transitions a first-time run would get. A
second delivery path would be a second place for the approval gate to be wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from application.deps import Deps
from application.recovery import state_for_resume
from application.runner import run
from domain.contracts.enums import RunStatus
from pipeline.registry import PIPELINE

#: Everything before DELIVER. An APPROVED run has, by the state machine's own
#: rules, passed through every one of these; marking them complete on the
#: rebuilt state says so explicitly rather than trusting a column, so an
#: approved run can never be re-extracted or re-assessed on its way out.
NODES_BEFORE_DELIVERY = tuple(node.name for node in PIPELINE if node.name != "DELIVER")


@dataclass
class DeliverySummary:
    """What happened to each approved run."""

    delivered: list[UUID] = field(default_factory=list)
    pending_retry: list[UUID] = field(default_factory=list)
    skipped: list[tuple[UUID, str]] = field(default_factory=list)

    @property
    def attempted(self) -> int:
        return len(self.delivered) + len(self.pending_retry) + len(self.skipped)

    def sentence(self) -> str:
        if not self.attempted:
            return "There is nothing approved and waiting to be sent."

        parts = [f"{len(self.delivered)} sent"]
        if self.pending_retry:
            parts.append(f"{len(self.pending_retry)} will be retried")
        if self.skipped:
            parts.append(f"{len(self.skipped)} not sent")
        return f"Processed {self.attempted} approved candidate(s): " + ", ".join(parts) + "."


def deliver_approved(deps: Deps, *, limit: int = 50) -> DeliverySummary:
    """Take each APPROVED run through DELIVER."""
    summary = DeliverySummary()

    for record in deps.runs.list_by_status((RunStatus.APPROVED,), limit=limit):
        state = state_for_resume(deps, record.run_id)
        if state is None:
            continue

        outcome = run(state.model_copy(update={"completed_nodes": NODES_BEFORE_DELIVERY}), deps)

        if outcome.status is RunStatus.DELIVERED:
            summary.delivered.append(record.run_id)
        elif outcome.status is RunStatus.DELIVERY_PENDING_RETRY:
            summary.pending_retry.append(record.run_id)
        else:
            summary.skipped.append((record.run_id, _reason(outcome, deps)))

    return summary


def _reason(outcome: object, deps: Deps) -> str:
    if deps.settings.demo_mode:
        return "Demo mode is on, so nothing is sent anywhere."
    failed = getattr(outcome, "failed_node", None)
    return f"Stopped at {failed}." if failed else "The run did not reach delivery."
