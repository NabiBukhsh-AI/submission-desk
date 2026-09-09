"""Reconciling runs that stopped mid-flight.

A process killed between nodes leaves a run in a working state with no process
behind it. Without this, that run sits in the queue forever and no action a
recruiter can take will clear it.

Reconciliation is deliberately conservative. It marks a stalled run INTERRUPTED,
which transitions to CREATED and resumes from the last committed node. It never
deletes, never rewinds past a node boundary, and never touches a run a reviewer
could still act on.
"""

from __future__ import annotations

from dataclasses import dataclass

from application.deps import Deps
from domain.contracts.enums import RunStatus
from domain.contracts.run_state import RunState


@dataclass(frozen=True)
class Reconciliation:
    """What startup found and what it did about it."""

    interrupted: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.interrupted)


def reconcile(deps: Deps) -> Reconciliation:
    """Mark stalled runs INTERRUPTED so they can resume.

    Called at startup by both the command line and the interface. Running it
    twice is harmless: a run already INTERRUPTED is not stale, because the
    repository's search excludes that status.
    """
    stale = deps.runs.find_stale(deps.settings.stale_run_minutes)
    interrupted: list[str] = []

    for run in stale:
        deps.events.append(
            run.run_id,
            node="RECOVERY",
            from_status=run.status,
            to_status=RunStatus.INTERRUPTED,
            node_status="degraded",
            payload={
                "reason": "no progress since the last committed node",
                "completed_nodes": run.completed_nodes,
            },
        )
        deps.runs.mark_node_complete(run.run_id, "RECOVERY", RunStatus.INTERRUPTED)
        interrupted.append(str(run.run_id))

    return Reconciliation(interrupted=tuple(interrupted))


def state_for_resume(deps: Deps, run_id: object) -> RunState | None:
    """Rebuild the in-flight state of an interrupted run.

    Reads what was committed rather than replaying what was attempted, so a run
    resumes from the last node that actually finished.
    """
    record = deps.runs.get(run_id)  # type: ignore[arg-type]
    if record is None:
        return None

    status = RunStatus.CREATED if record.status is RunStatus.INTERRUPTED else record.status
    completed = tuple(node for node in record.completed_nodes if node != "RECOVERY")

    return RunState(
        run_id=record.run_id,
        candidate_id=record.candidate_id,
        role_id=record.role_id,
        status=status,
        completed_nodes=completed,
        started_at=record.started_at,
        rubric_hash=record.rubric_hash,
        content_key=record.content_key,
        blind_mode=record.blind_mode,
        calibration_enabled=record.calibration_enabled,
        integrity_tier=record.integrity_tier,
    )
