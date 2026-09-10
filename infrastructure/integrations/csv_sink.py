"""The sink that is always there.

Registered first, written first, and not removable. Every other integration is
optional; this one is the reason they can be. A spreadsheet that is down becomes
a row saying the spreadsheet is down, rather than a candidate whose result went
nowhere, because the file was already written.

It has no credentials, no network, and no rate limit, so the failure modes it has
are the ones a disk has. That is the point of putting it first: the least
sophisticated sink is the most reliable one, and reliability is what a delivery
guarantee is made of.

Append-only, one row per delivery. A run delivered twice appends twice, which is
honest — the file is a record of deliveries, not a table of candidates — and the
run id in the first column makes duplicates visible to anybody who looks.
"""

from __future__ import annotations

import csv
import time
from datetime import UTC, datetime
from pathlib import Path

from domain.ports.sinks import AdapterError, AdapterResult, DeliveryPayload

#: The columns, in order. Fixed rather than derived from the payload, so a field
#: added upstream does not silently shift every existing spreadsheet's columns.
COLUMNS: tuple[str, ...] = (
    "delivered_at",
    "run_id",
    "candidate_id",
    "role_id",
    "band",
    "score",
    "coverage",
    "reviewer_id",
    "reviewer_action",
    "decided_at",
    "override_count",
    "integrity_tier",
    "derivation",
    "criterion_states",
    "information_requests",
)

#: How a list becomes one cell. Newlines would break the file for anything that
#: reads it line by line, which is most things.
JOIN = " | "


class CsvSink:
    """Appends one row per delivered package."""

    sink_id = "csv"

    def __init__(self, path: Path | str = Path("data/deliveries/results.csv")) -> None:
        self.path = Path(path)

    def healthy(self) -> bool:
        """Always. A sink that could report itself unhealthy could be skipped,
        and this one is the guarantee that something was written."""
        return True

    def deliver(self, payload: DeliveryPayload) -> AdapterResult:
        started = time.monotonic()

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            is_new = not self.path.exists() or self.path.stat().st_size == 0

            with self.path.open("a", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
                if is_new:
                    writer.writeheader()
                writer.writerow(self._row(payload))

        except OSError as error:
            # A disk problem is a configuration problem in practice: the path is
            # wrong, or the volume is full. Neither is fixed by trying again in
            # thirty seconds.
            return AdapterResult.failed(
                AdapterError.CONFIG,
                f"The results file could not be written at {self.path}. "
                f"Check the path and the available space. ({error.strerror or error})",
                latency_ms=_elapsed(started),
            )

        return AdapterResult.succeeded(
            f"{self.path}:{self._line_count()}",
            latency_ms=_elapsed(started),
        )

    def _row(self, payload: DeliveryPayload) -> dict[str, object]:
        return {
            "delivered_at": datetime.now(UTC).isoformat(),
            "run_id": payload.run_id,
            "candidate_id": payload.candidate_id,
            "role_id": payload.role_id,
            "band": payload.band,
            # Empty rather than zero. A blank cell reads as "no score"; a zero
            # reads as "scored nothing", and those are different candidates.
            "score": "" if payload.score is None else f"{payload.score:.4f}",
            "coverage": f"{payload.coverage:.4f}",
            "reviewer_id": payload.reviewer_id,
            "reviewer_action": payload.reviewer_action,
            "decided_at": payload.decided_at,
            "override_count": payload.override_count,
            "integrity_tier": payload.integrity_tier,
            "derivation": JOIN.join(payload.derivation),
            "criterion_states": JOIN.join(
                f"{criterion}={state}" for criterion, state in payload.criterion_states.items()
            ),
            "information_requests": JOIN.join(payload.information_requests),
        }

    def _line_count(self) -> int:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                return sum(1 for _ in handle) - 1
        except OSError:
            return 0


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
