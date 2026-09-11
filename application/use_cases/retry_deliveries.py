"""Finishing a delivery that only half happened.

A spreadsheet being down for ten minutes should not cost a reviewer their
afternoon's decisions. The CSV was written, the decision is recorded, and this
completes the rest when the far side comes back.

Only the sinks that failed are retried. Re-sending to one that already succeeded
would append a second row for one decision, which is the duplicate a recruiter
then has to work out the truth of — and the reason the sheets adapter
pre-reads its key column even so.

Approval is re-asserted here as well as in the node. This runs on a schedule and
from a button, neither of which passes through the reviewer's page, so the check
that a decision exists has to be made where the work is done rather than where
it was requested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from application.deps import Deps
from domain.contracts.delivery import DeliveryRecord
from domain.contracts.enums import DeliveryStatus, RunStatus
from domain.contracts.run_state import RunState
from domain.ports.sinks import AdapterResult


@dataclass
class RetrySummary:
    """What a retry pass achieved."""

    attempted: int = 0
    completed: list[UUID] = field(default_factory=list)
    still_failing: list[tuple[UUID, str]] = field(default_factory=list)
    refused: list[tuple[UUID, str]] = field(default_factory=list)

    def sentence(self) -> str:
        if not self.attempted:
            return "Nothing was waiting to be sent."

        parts = [f"{len(self.completed)} completed"]
        if self.still_failing:
            parts.append(f"{len(self.still_failing)} still failing")
        if self.refused:
            parts.append(f"{len(self.refused)} not approved")

        return f"Retried {self.attempted} delivery(s): " + ", ".join(parts) + "."


def retry_deliveries(deps: Deps, *, limit: int = 50) -> RetrySummary:
    """Try again for every run whose delivery is incomplete."""
    # Imported here rather than at module scope: pipeline sits above
    # application, so a top-level import would invert the dependency the
    # architecture test enforces. Two functions, called once.
    from pipeline.deliver import DEMO_MODE, build_payload, refuse_reason  # noqa: PLC0415

    summary = RetrySummary()
    pending = deps.runs.list_by_status((RunStatus.DELIVERY_PENDING_RETRY,), limit=limit)

    for run in pending:
        summary.attempted += 1
        state = _state_of(run)

        refusal = refuse_reason(state, deps)
        if refusal is not None:
            summary.refused.append((run.run_id, refusal))
            continue

        if deps.settings.demo_mode:
            # With no sinks configured, "nothing outstanding" below would read
            # as "everything delivered". In demo mode nothing is delivered.
            summary.refused.append((run.run_id, DEMO_MODE))
            continue

        done = {
            record.sink_id
            for record in deps.deliveries.for_run(run.run_id)
            if record.status is DeliveryStatus.DELIVERED
        }
        outstanding = [sink for sink in (deps.sinks or ()) if sink.sink_id not in done]

        if not outstanding:
            _mark_delivered(deps, run.run_id)
            summary.completed.append(run.run_id)
            continue

        payload = build_payload(state, deps)
        failures: list[str] = []

        for sink in outstanding:
            if not sink.healthy():
                failures.append(sink.sink_id)
                continue

            result = sink.deliver(payload)
            deps.deliveries.record(_record_for(run.run_id, sink.sink_id, result))
            if not result.ok:
                failures.append(sink.sink_id)

        if failures:
            summary.still_failing.append((run.run_id, ", ".join(failures)))
        else:
            _mark_delivered(deps, run.run_id)
            summary.completed.append(run.run_id)

    return summary


def _state_of(run: object) -> RunState:
    """The minimum a delivery needs to know about a run.

    Rebuilt rather than reloaded from a snapshot, because a delivery does not
    need the evidence or the profile — only who this was, what was decided, and
    whether the documents were clean.
    """
    return RunState(
        run_id=run.run_id,  # type: ignore[attr-defined]
        candidate_id=run.candidate_id,  # type: ignore[attr-defined]
        role_id=run.role_id,  # type: ignore[attr-defined]
        status=run.status,  # type: ignore[attr-defined]
        started_at=run.started_at,  # type: ignore[attr-defined]
        integrity_tier=run.integrity_tier,  # type: ignore[attr-defined]
    )


def _record_for(run_id: UUID, sink_id: str, result: AdapterResult) -> DeliveryRecord:
    return DeliveryRecord(
        delivery_id=uuid4(),
        run_id=run_id,
        sink_id=sink_id,
        status=DeliveryStatus.DELIVERED if result.ok else DeliveryStatus.FAILED,
        attempts=result.attempts,
        external_ref=result.external_ref,
        last_error=None if result.ok else result.message,
        delivered_at=datetime.now(UTC) if result.ok else None,
    )


def _mark_delivered(deps: Deps, run_id: UUID) -> None:
    deps.runs.set_status(run_id, RunStatus.DELIVERED)
