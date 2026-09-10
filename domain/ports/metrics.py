"""What the operations page and the evaluation both need to know.

One snapshot, taken in one pass. The shape is declared here so the application
layer can ask for it without knowing that the answers come out of SQLite, and so
the evaluation can produce the same figures for a report by asking the same
question.

Every field is a plain value. Nothing here is a query, a cursor, or a connection:
a reader that handed back something lazy would make "one pass" a claim rather
than a fact, and the page would issue a query per panel while rendering.

Cost figures may be absent. That is not a gap to fill with a zero — an
unconfigured price means the money is unknown, and the whole cost design exists
to keep that distinction visible this far out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True)
class PolicyCost:
    """What one routing policy spent, in tokens always and money sometimes."""

    candidates: int = 0
    total_usd: Decimal | None = None
    mean_per_candidate: Decimal | None = None
    per_hundred: Decimal | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    llm_calls: int = 0


@dataclass(frozen=True)
class MetricsSnapshot:
    """Everything the six panels show."""

    days: int = 7

    #: (status, count) — is anything stuck.
    runs_by_status: tuple[tuple[str, int], ...] = ()
    #: (run_id, candidate_id, status) for runs that stopped mid-flight.
    stuck: tuple[tuple[str, str, str], ...] = ()

    #: policy id -> what it spent.
    cost_by_policy: dict[str, PolicyCost] = field(default_factory=dict)

    #: count, p50, p95.
    run_latency: dict[str, float | None] = field(default_factory=dict)
    call_latency: dict[str, float | None] = field(default_factory=dict)

    escalation_rate: float | None = None
    #: (trigger, times fired, times it changed the answer, that as a fraction).
    escalation_by_trigger: tuple[tuple[str, int, int, float | None], ...] = ()

    failure_rates: dict[str, float | None] = field(default_factory=dict)
    error_codes: tuple[tuple[str, int], ...] = ()

    review_effort: dict[str, float | None] = field(default_factory=dict)
    override_rate: dict[str, float | None] = field(default_factory=dict)
    override_reasons: tuple[tuple[str, int], ...] = ()
    trust_distribution: tuple[tuple[int, int], ...] = ()

    @property
    def has_runs(self) -> bool:
        return bool(self.runs_by_status)


class MetricsReader(Protocol):
    """Reads the operational record.

    One method, because the page draws six panels at once and six round trips
    for one screen is six chances for the numbers to disagree with each other.
    """

    def snapshot(self, *, days: int = 7, stale_after_minutes: int = 15) -> MetricsSnapshot:
        """Every panel's figures, from one read of the record.

        Never raises for a database that predates a migration: a missing table
        yields empty rows, because an operations page that will not load is
        worse than one reporting nothing.
        """
        ...


def snapshot_of(reader: Any, *, days: int = 7, stale_after_minutes: int = 15) -> MetricsSnapshot:
    """The snapshot, or an empty one when no reader is configured.

    A deployment can run without metrics — a test, a script — and the caller
    should not have to branch on that.
    """
    if reader is None:
        return MetricsSnapshot(days=days)
    result: MetricsSnapshot = reader.snapshot(days=days, stale_after_minutes=stale_after_minutes)
    return result
