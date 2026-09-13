"""What has been sent, as the destinations saw it.

The same table a recruiter finds in the spreadsheet — one row per approved
candidate, the outcome and the reasoning, never the evidence — with, for each
destination, whether it arrived and where. Built from the same payload the
sinks were given, so the page and the sheet cannot disagree.
"""

from __future__ import annotations

from dataclasses import dataclass

from application.deps import Deps
from application.recovery import state_for_resume
from domain.contracts.enums import RunStatus
from domain.ports.sinks import DeliveryPayload

#: A run that has been through DELIVER at least once.
SENT_STATUSES = (RunStatus.DELIVERED, RunStatus.DELIVERY_PENDING_RETRY)


@dataclass(frozen=True)
class Destination:
    sink_id: str
    status: str
    #: What the far side calls the place it wrote: a row range, a file path.
    reference: str
    attempts: int
    error: str
    delivered_at: str


@dataclass(frozen=True)
class SentRow:
    payload: DeliveryPayload
    status: RunStatus
    destinations: tuple[Destination, ...]


def list_deliveries(deps: Deps, *, limit: int = 100) -> list[SentRow]:
    """Every run that reached delivery, newest first, with its destinations."""
    # Imported here rather than at module scope, for the reason
    # retry_deliveries gives: the payload builder lives in the DELIVER node.
    from pipeline.deliver import build_payload  # noqa: PLC0415

    rows: list[SentRow] = []
    for record in deps.runs.list_by_status(SENT_STATUSES, limit=limit):
        state = state_for_resume(deps, record.run_id)
        if state is None:
            continue
        rows.append(
            SentRow(
                payload=build_payload(state, deps),
                status=record.status,
                destinations=tuple(
                    Destination(
                        sink_id=item.sink_id,
                        status=item.status.value,
                        reference=item.external_ref or "",
                        attempts=item.attempts,
                        error=item.last_error or "",
                        delivered_at=item.delivered_at.isoformat() if item.delivered_at else "",
                    )
                    for item in deps.deliveries.for_run(record.run_id)
                ),
            )
        )
    return rows
