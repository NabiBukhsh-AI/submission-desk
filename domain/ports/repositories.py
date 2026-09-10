"""What storage looks like from the inside.

Protocols, never abstract base classes: an adapter satisfies one of these by
having the right methods, so a fake in a test is a small class rather than an
inheritance ceremony.

Every method accepts and returns contracts. No row, no dict, and no cursor
crosses this boundary, which is what makes the SQLite implementation replaceable
and what keeps the domain layer unaware that SQLite exists at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from domain.contracts import (
    CalibrationCard,
    CandidateDocument,
    CostRecord,
    CriterionAssessment,
    DeliveryRecord,
    ErrorRecord,
    EvidenceItem,
    IntegrityReport,
    Recommendation,
    ReviewDecision,
    RunRecord,
    RunStatus,
    SourceText,
)


class StaleRunVersion(Exception):
    """A write lost a race with another writer.

    Raised instead of overwriting, so a reviewer whose page was open while the
    run changed underneath is told to reload rather than silently clobbering
    someone else's decision.
    """

    error_code = "STALE_RUN_VERSION"

    def __init__(self, run_id: UUID, expected: int, actual: int) -> None:
        self.run_id = run_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"run {run_id} changed elsewhere: expected version {expected}, found {actual}"
        )


class RunRepository(Protocol):
    def create(self, run: RunRecord) -> None: ...

    def get(self, run_id: UUID) -> RunRecord | None: ...

    def find_by_content_key(self, content_key: str) -> RunRecord | None: ...

    def list_by_status(
        self, statuses: Sequence[RunStatus], limit: int = 100
    ) -> list[RunRecord]: ...

    def update(self, run: RunRecord, *, expected_version: int) -> RunRecord:
        """Persist a run, refusing if it changed elsewhere.

        Returns the stored record with its version advanced. Raises
        ``StaleRunVersion`` rather than winning the race by arriving last.
        """
        ...

    def mark_node_complete(self, run_id: UUID, node: str, status: RunStatus) -> None:
        """Record that a node finished successfully and advance the status.

        Only for a node that actually succeeded. A failed node written here
        would be skipped on resume, which turns a retryable failure into a
        silently missing step.
        """
        ...

    def set_status(self, run_id: UUID, status: RunStatus) -> None:
        """Advance the status without claiming a node completed."""
        ...

    def set_content_key(self, run_id: UUID, content_key: str) -> bool:
        """Replace the placeholder key once the documents have been hashed.

        A run starts with ``pending:<run_id>`` because the real key needs the
        document hashes and hashing them means fetching every file, which is the
        cost the key exists to avoid. Intake has them, so intake sets it. A run
        that reached a reviewer still carrying the placeholder could never be
        deduplicated against, which is idempotency quietly not working.
        """
        ...

    def add_usage(
        self,
        run_id: UUID,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        cost_usd: Decimal | None = None,
        escalated: bool = False,
    ) -> None:
        """Add one call's consumption to the run's running totals.

        Incremental rather than recomputed from the ledger, so the number is
        available to the budget guard before the next node starts rather than
        after a query. ``cost_usd`` of ``None`` leaves the total null: a run
        with one unpriced call has an unknown cost, not a partial one.
        """
        ...

    def find_stale(self, older_than_minutes: int) -> list[RunRecord]:
        """Runs that stopped mid-flight, for startup reconciliation."""
        ...


class CandidateRepository(Protocol):
    def add_document(self, document: CandidateDocument, *, run_id: UUID | None = None) -> None:
        """Record a document, optionally attached to the run that accepted it."""
        ...

    def documents_for_run(self, run_id: UUID) -> list[CandidateDocument]: ...

    def get_source_text(self, document_sha256: str, profile_id: str) -> SourceText | None:
        """Extraction cache, keyed independently of the rubric.

        A rubric change must never trigger a re-extraction, let alone a re-OCR:
        the text of a PDF does not depend on what questions are being asked of
        it.
        """
        ...

    def put_source_text(self, source: SourceText, *, document_sha256: str) -> None: ...

    def save_integrity_report(self, report: IntegrityReport, *, run_id: UUID | None = None) -> None:
        """Store what the scanner found, superseding any earlier scan.

        Never an update in place. A detector change must not rewrite history:
        what the scanner thought about a document at a point in time is the
        record a security incident is reconstructed from.
        """
        ...

    def integrity_report_for(self, document_id: UUID) -> IntegrityReport | None: ...

    def integrity_reports_for_run(self, run_id: UUID) -> list[IntegrityReport]: ...

    def detector_counts(self) -> list[tuple[str, str, int]]:
        """(detector, severity, count) across every scan.

        The reason each detector carries a stable id: precision is measured per
        detector, so a noisy pattern bank can be narrowed without touching the
        ones that work.
        """
        ...


class EvidenceRepository(Protocol):
    def save_evidence(self, run_id: UUID, items: Sequence[EvidenceItem], *, rejected: bool) -> None:
        """Store evidence, marking whether it reached scoring.

        Rejected items are persisted rather than dropped: a quotation the
        validator could not find is exactly what a reviewer needs to see, and
        what the hallucination-rate metric counts.
        """
        ...

    def evidence_for_run(
        self, run_id: UUID, *, rejected: bool | None = None
    ) -> list[EvidenceItem]: ...

    def save_assessment(self, run_id: UUID, assessment: CriterionAssessment) -> None: ...

    def assessments_for_run(self, run_id: UUID) -> list[CriterionAssessment]: ...

    def save_recommendation(self, run_id: UUID, recommendation: Recommendation) -> None: ...

    def recommendation_for_run(self, run_id: UUID) -> Recommendation | None: ...


class ReviewRepository(Protocol):
    def save(self, decision: ReviewDecision) -> None: ...

    def get_for_run(self, run_id: UUID) -> ReviewDecision | None:
        """The decision that authorises delivery.

        Delivery adapters call this independently of the state machine, so an
        approved status with no decision row behind it still cannot send.
        """
        ...

    def list_overrides(
        self, *, role_id: str | None = None, limit: int = 500
    ) -> list[tuple[UUID, str, str, str, str]]:
        """Overrides across runs, for the improvement loop.

        Returns (run_id, criterion_id, previous_state, new_state, reason_code).
        """
        ...


class CostRepository(Protocol):
    def record(self, cost: CostRecord) -> None: ...

    def for_run(self, run_id: UUID) -> list[CostRecord]: ...

    def totals_for_run(self, run_id: UUID) -> tuple[int, int, int]:
        """Input, output, and cached tokens. Never an estimate."""
        ...


class ErrorRepository(Protocol):
    def record(self, error: ErrorRecord) -> None: ...

    def for_run(self, run_id: UUID) -> list[ErrorRecord]: ...


class DeliveryRepository(Protocol):
    def record(self, delivery: DeliveryRecord) -> None: ...

    def for_run(self, run_id: UUID) -> list[DeliveryRecord]: ...

    def pending_retries(self, limit: int = 100) -> list[DeliveryRecord]: ...


class CalibrationRepository(Protocol):
    def add(self, card: CalibrationCard) -> None: ...

    def for_role(
        self, role_id: str, rubric_version: str, limit: int = 200
    ) -> list[CalibrationCard]: ...


class LlmCacheRepository(Protocol):
    """Responses keyed on everything that determines them.

    This is what makes an evaluation run reproducible and what stops a week of
    demos from re-billing the same prompts.
    """

    def get(self, cache_key: str) -> tuple[str, str] | None:
        """Returns (response_json, usage_json), or None."""
        ...

    def put(self, cache_key: str, response_json: str, usage_json: str) -> None: ...


class EventRepository(Protocol):
    """The append-only record of what the runner did.

    Written by the runner rather than by nodes, so a node cannot forget to be
    observed, and replayable so a run's history survives its final state.
    """

    def append(
        self,
        run_id: UUID,
        *,
        node: str,
        from_status: RunStatus | None,
        to_status: RunStatus | None,
        node_status: str,
        payload: dict[str, object] | None = None,
    ) -> int: ...

    def for_run(self, run_id: UUID) -> list[tuple[int, str, str, str]]:
        """Returns (seq, node, node_status, occurred_at) in order."""
        ...


class BlobStore(Protocol):
    """Where document bytes live, addressed by content hash.

    A port rather than a direct import, because a node performs I/O only through
    Deps. That is what keeps the pipeline runnable against fakes, and it is the
    rule the architecture test enforces.
    """

    def put(self, data: bytes) -> str:
        """Store bytes and return their hash. Writing an existing hash is a no-op."""
        ...

    def get(self, document_sha256: str) -> bytes: ...

    def exists(self, document_sha256: str) -> bool: ...

    def path_for(self, document_sha256: str) -> object:
        """The address of a hash. Never derived from a filename."""
        ...
