"""SQLite implementations of the repository protocols.

Every method takes and returns contracts. No row, no dict, and no cursor leaves
this module, which is what makes a PostgreSQL implementation a day of work
rather than a rewrite: write six more classes, change one line in the factory,
run the same tests against both.

All SQL is parameterised. There is no string formatting anywhere near a query,
and a test asserts it, because "the input is a UUID so it is safe" is how the
habit erodes.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from domain.contracts import (
    Band,
    CalibrationCard,
    CandidateDocument,
    CostRecord,
    CriterionAssessment,
    CriterionState,
    DeliveryRecord,
    DeliveryStatus,
    DocumentRole,
    ErrorRecord,
    EscalationState,
    EvidenceItem,
    EvidenceState,
    IntegrityFinding,
    IntegrityFindingKind,
    IntegrityReport,
    IntegrityTier,
    ModelTier,
    Override,
    OverrideReason,
    Provenance,
    Recommendation,
    ReviewAction,
    ReviewDecision,
    RunRecord,
    RunStatus,
    Severity,
    SourceText,
    SpanValidation,
)
from domain.ports.repositories import StaleRunVersion
from infrastructure.storage.sqlite.connection import connect, write_transaction


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def _loads(value: str | None, fallback: Any) -> Any:
    return fallback if value is None else json.loads(value)


class SqliteRepository:
    """Shared connection handling. Not a base class with behaviour."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    @property
    def connection(self) -> sqlite3.Connection:
        return connect(self.db_path)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class SqliteRunRepository(SqliteRepository):
    def create(self, run: RunRecord) -> None:
        with write_transaction(self.connection) as write:
            write.execute(
                """
                INSERT INTO runs (
                    run_id, content_key, candidate_id, role_id, rubric_version, rubric_hash,
                    prompt_bundle_hash, prompt_versions, model_tier_bindings_hash,
                    routing_policy_id, blind_mode, calibration_enabled, calibration_status,
                    pipeline_version, status, completed_nodes, started_at, finished_at,
                    total_input_tokens, total_output_tokens, cached_input_tokens,
                    total_cost_usd, llm_call_count, retry_count, repair_count,
                    escalation_count, validation_failure_count, invalid_span_count,
                    integrity_tier, final_band, reviewer_action, override_count,
                    error_codes, version
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    str(run.run_id),
                    run.content_key,
                    run.candidate_id,
                    run.role_id,
                    run.rubric_version,
                    run.rubric_hash,
                    run.prompt_bundle_hash,
                    _dumps(run.prompt_versions),
                    run.model_tier_bindings_hash,
                    run.routing_policy_id,
                    int(run.blind_mode),
                    int(run.calibration_enabled),
                    run.calibration_status,
                    run.pipeline_version,
                    run.status.value,
                    _dumps(run.completed_nodes),
                    run.started_at.isoformat(),
                    run.finished_at.isoformat() if run.finished_at else None,
                    run.total_input_tokens,
                    run.total_output_tokens,
                    run.cached_input_tokens,
                    str(run.total_cost_usd) if run.total_cost_usd is not None else None,
                    run.llm_call_count,
                    run.retry_count,
                    run.repair_count,
                    run.escalation_count,
                    run.validation_failure_count,
                    run.invalid_span_count,
                    run.integrity_tier.value,
                    run.final_band.value if run.final_band else None,
                    run.reviewer_action.value if run.reviewer_action else None,
                    run.override_count,
                    _dumps(run.error_codes),
                    run.version,
                ),
            )

    def get(self, run_id: UUID) -> RunRecord | None:
        row = self.connection.execute(
            "SELECT * FROM runs WHERE run_id = ?", (str(run_id),)
        ).fetchone()
        return _run_from_row(row) if row else None

    def find_by_content_key(self, content_key: str) -> RunRecord | None:
        row = self.connection.execute(
            "SELECT * FROM runs WHERE content_key = ?", (content_key,)
        ).fetchone()
        return _run_from_row(row) if row else None

    def list_by_status(self, statuses: Sequence[RunStatus], limit: int = 100) -> list[RunRecord]:
        if not statuses:
            return []
        # The one formatted query in this module. The interpolated text is
        # "?,?,?" built from len(statuses); the values themselves are bound.
        # Marked here so the SQL-safety test can tell this apart from a mistake:
        # count only.
        placeholders = ",".join("?" for _ in statuses)
        rows = self.connection.execute(
            f"SELECT * FROM runs WHERE status IN ({placeholders}) ORDER BY started_at DESC LIMIT ?",
            (*[status.value for status in statuses], limit),
        ).fetchall()
        return [_run_from_row(row) for row in rows]

    def update(self, run: RunRecord, *, expected_version: int) -> RunRecord:
        """Write a run, or refuse because someone else already did.

        The version check and the write are one statement, so there is no window
        between reading the version and acting on it.
        """
        with write_transaction(self.connection) as write:
            cursor = write.execute(
                """
                UPDATE runs SET
                    status = ?, completed_nodes = ?, finished_at = ?,
                    total_input_tokens = ?, total_output_tokens = ?, cached_input_tokens = ?,
                    total_cost_usd = ?, llm_call_count = ?, retry_count = ?, repair_count = ?,
                    escalation_count = ?, validation_failure_count = ?, invalid_span_count = ?,
                    integrity_tier = ?, final_band = ?, reviewer_action = ?, override_count = ?,
                    error_codes = ?, version = version + 1
                WHERE run_id = ? AND version = ?
                """,
                (
                    run.status.value,
                    _dumps(run.completed_nodes),
                    run.finished_at.isoformat() if run.finished_at else None,
                    run.total_input_tokens,
                    run.total_output_tokens,
                    run.cached_input_tokens,
                    str(run.total_cost_usd) if run.total_cost_usd is not None else None,
                    run.llm_call_count,
                    run.retry_count,
                    run.repair_count,
                    run.escalation_count,
                    run.validation_failure_count,
                    run.invalid_span_count,
                    run.integrity_tier.value,
                    run.final_band.value if run.final_band else None,
                    run.reviewer_action.value if run.reviewer_action else None,
                    run.override_count,
                    _dumps(run.error_codes),
                    str(run.run_id),
                    expected_version,
                ),
            )
            if cursor.rowcount == 0:
                current = write.execute(
                    "SELECT version FROM runs WHERE run_id = ?", (str(run.run_id),)
                ).fetchone()
                raise StaleRunVersion(
                    run.run_id, expected_version, int(current["version"]) if current else -1
                )

        stored = self.get(run.run_id)
        assert stored is not None, "a run updated in this transaction must still exist"
        return stored

    def mark_node_complete(self, run_id: UUID, node: str, status: RunStatus) -> None:
        with write_transaction(self.connection) as write:
            row = write.execute(
                "SELECT completed_nodes FROM runs WHERE run_id = ?", (str(run_id),)
            ).fetchone()
            if row is None:
                return
            nodes: list[str] = _loads(row["completed_nodes"], [])
            if node not in nodes:
                nodes.append(node)
            write.execute(
                "UPDATE runs SET completed_nodes = ?, status = ?, version = version + 1 "
                "WHERE run_id = ?",
                (_dumps(nodes), status.value, str(run_id)),
            )

    def set_status(self, run_id: UUID, status: RunStatus) -> None:
        """Advance the status without adding to completed_nodes.

        Used when a node failed: the run moves to the node's failure state, but
        the node is not recorded as done, so a resume retries it.
        """
        with write_transaction(self.connection) as write:
            write.execute(
                "UPDATE runs SET status = ?, version = version + 1 WHERE run_id = ?",
                (status.value, str(run_id)),
            )

    def set_content_key(self, run_id: UUID, content_key: str) -> bool:
        """Claim the key, or report that another run already holds it.

        The unique index is the point: two concurrent starts on the same
        documents cannot both claim it, and the one that arrives second learns
        it is a repeat rather than silently producing a parallel result.

        A collision is not an error. The run was started deliberately — a
        forced re-run, or a second upload of the same file — so it keeps its
        placeholder key and continues. What it loses is the ability to be
        deduplicated against later, which is the right trade: the first run
        still holds the key that matters.
        """
        try:
            with write_transaction(self.connection) as write:
                write.execute(
                    "UPDATE runs SET content_key = ? WHERE run_id = ?",
                    (content_key, str(run_id)),
                )
        except sqlite3.IntegrityError:
            return False
        return True

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
        """Add one call to the run's totals, in SQL rather than read-modify-write.

        Criteria are assessed in parallel, so two threads can account for two
        calls at the same moment. An UPDATE that adds to the stored value is
        atomic; reading the row, adding in Python, and writing it back would
        lose one of them.

        Cost accumulates only when there is a figure. A NULL total stays NULL,
        because a run with one unpriced call has an unknown cost rather than a
        partial one, and a partial total presented as a whole one is the sort of
        number that ends up in a slide.
        """
        with write_transaction(self.connection) as write:
            write.execute(
                """
                UPDATE runs SET
                    total_input_tokens   = total_input_tokens + ?,
                    total_output_tokens  = total_output_tokens + ?,
                    cached_input_tokens  = cached_input_tokens + ?,
                    llm_call_count       = llm_call_count + 1,
                    escalation_count     = escalation_count + ?
                WHERE run_id = ?
                """,
                (
                    input_tokens,
                    output_tokens,
                    cached_input_tokens,
                    int(escalated),
                    str(run_id),
                ),
            )

            if cost_usd is not None:
                row = write.execute(
                    "SELECT total_cost_usd FROM runs WHERE run_id = ?", (str(run_id),)
                ).fetchone()
                current = (
                    Decimal(row["total_cost_usd"]) if row and row["total_cost_usd"] else Decimal(0)
                )
                write.execute(
                    "UPDATE runs SET total_cost_usd = ? WHERE run_id = ?",
                    (str(current + cost_usd), str(run_id)),
                )

    def find_stale(self, older_than_minutes: int) -> list[RunRecord]:
        """Runs that stopped mid-flight and never came back.

        Reconciled to INTERRUPTED at startup, from which they resume at the last
        committed node. A run left in PROCESSING forever is a queue entry nobody
        can clear.
        """
        cutoff = datetime.now(UTC).timestamp() - older_than_minutes * 60
        rows = self.connection.execute(
            """
            SELECT r.* FROM runs r
            WHERE r.finished_at IS NULL
              AND r.status NOT IN (
                'delivered', 'rejected', 'failed_terminal', 'ready_for_review',
                'needs_review', 'quarantined', 'needs_info', 'approved',
                'manual_review_required', 'interrupted'
              )
            """
        ).fetchall()

        stale: list[RunRecord] = []
        for row in rows:
            last = self.connection.execute(
                "SELECT occurred_at FROM run_events WHERE run_id = ? ORDER BY seq DESC LIMIT 1",
                (row["run_id"],),
            ).fetchone()
            marker = last["occurred_at"] if last else row["started_at"]
            if datetime.fromisoformat(marker).timestamp() < cutoff:
                stale.append(_run_from_row(row))
        return stale


def _run_from_row(row: sqlite3.Row) -> RunRecord:

    return RunRecord(
        run_id=UUID(row["run_id"]),
        content_key=row["content_key"],
        candidate_id=row["candidate_id"],
        role_id=row["role_id"],
        rubric_version=row["rubric_version"],
        rubric_hash=row["rubric_hash"],
        prompt_bundle_hash=row["prompt_bundle_hash"],
        prompt_versions=_loads(row["prompt_versions"], {}),
        model_tier_bindings_hash=row["model_tier_bindings_hash"],
        routing_policy_id=row["routing_policy_id"],
        blind_mode=bool(row["blind_mode"]),
        calibration_enabled=bool(row["calibration_enabled"]),
        calibration_status=row["calibration_status"],
        pipeline_version=row["pipeline_version"],
        status=RunStatus(row["status"]),
        completed_nodes=_loads(row["completed_nodes"], []),
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        total_input_tokens=row["total_input_tokens"],
        total_output_tokens=row["total_output_tokens"],
        cached_input_tokens=row["cached_input_tokens"],
        total_cost_usd=Decimal(row["total_cost_usd"]) if row["total_cost_usd"] else None,
        llm_call_count=row["llm_call_count"],
        retry_count=row["retry_count"],
        repair_count=row["repair_count"],
        escalation_count=row["escalation_count"],
        validation_failure_count=row["validation_failure_count"],
        invalid_span_count=row["invalid_span_count"],
        integrity_tier=IntegrityTier(row["integrity_tier"]),
        final_band=Band(row["final_band"]) if row["final_band"] else None,
        reviewer_action=ReviewAction(row["reviewer_action"]) if row["reviewer_action"] else None,
        override_count=row["override_count"],
        error_codes=_loads(row["error_codes"], []),
        version=row["version"],
    )


# ---------------------------------------------------------------------------
# Candidates, documents, extracted text
# ---------------------------------------------------------------------------


class SqliteCandidateRepository(SqliteRepository):
    def add_document(self, document: CandidateDocument, *, run_id: UUID | None = None) -> None:
        with write_transaction(self.connection) as write:
            write.execute(
                """
                INSERT OR REPLACE INTO documents (
                    document_id, run_id, candidate_id, original_filename, document_sha256,
                    mime_type, size_bytes, page_count, blob_path, doc_role,
                    detected_languages, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(document.document_id),
                    str(run_id) if run_id else None,
                    document.candidate_id,
                    document.original_filename,
                    document.document_sha256,
                    document.mime_type,
                    document.size_bytes,
                    document.page_count,
                    document.blob_path,
                    document.doc_role.value,
                    _dumps([]),
                    document.received_at.isoformat(),
                ),
            )

    def documents_for_run(self, run_id: UUID) -> list[CandidateDocument]:

        rows = self.connection.execute(
            "SELECT * FROM documents WHERE run_id = ? ORDER BY received_at", (str(run_id),)
        ).fetchall()
        return [
            CandidateDocument(
                document_id=UUID(row["document_id"]),
                candidate_id=row["candidate_id"],
                original_filename=row["original_filename"],
                document_sha256=row["document_sha256"],
                mime_type=row["mime_type"],
                size_bytes=row["size_bytes"],
                page_count=row["page_count"],
                blob_path=row["blob_path"],
                doc_role=DocumentRole(row["doc_role"]),
                received_at=datetime.fromisoformat(row["received_at"]),
            )
            for row in rows
        ]

    def get_source_text(self, document_sha256: str, profile_id: str) -> SourceText | None:
        row = self.connection.execute(
            "SELECT * FROM source_texts WHERE document_sha256 = ? AND normalization_profile_id = ?",
            (document_sha256, profile_id),
        ).fetchone()
        if row is None:
            return None

        return SourceText(
            document_id=UUID(row["document_id"]),
            raw_text=row["raw_text"],
            normalized_text=row["normalized_text"],
            offset_runs=_loads(row["offset_runs"], []),
            pages=_loads(row["pages"], []),
            normalization_profile_id=row["normalization_profile_id"],
            extraction_confidence=row["extraction_confidence"],
            detected_languages=_loads(row["detected_languages"], []),
            block_boundaries=[tuple(pair) for pair in _loads(row["block_boundaries"], [])],
        )

    def put_source_text(self, source: SourceText, *, document_sha256: str) -> None:
        with write_transaction(self.connection) as write:
            write.execute(
                """
                INSERT OR REPLACE INTO source_texts (
                    document_sha256, normalization_profile_id, document_id, raw_text,
                    normalized_text, offset_runs, pages, block_boundaries,
                    extraction_confidence, detected_languages, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_sha256,
                    source.normalization_profile_id,
                    str(source.document_id),
                    source.raw_text,
                    source.normalized_text,
                    _dumps([run.model_dump(mode="json") for run in source.offset_runs]),
                    _dumps([page.model_dump(mode="json") for page in source.pages]),
                    _dumps([list(pair) for pair in source.block_boundaries]),
                    source.extraction_confidence,
                    _dumps(source.detected_languages),
                    _now(),
                ),
            )

    # --- integrity ---------------------------------------------------------

    def save_integrity_report(self, report: IntegrityReport, *, run_id: UUID | None = None) -> None:
        """Store a scan, superseding any earlier one for the same document.

        The earlier row is marked rather than deleted. When a detector changes,
        the question "what did the scanner think in March" still has an answer,
        which is the difference between a security log and a status field.
        """
        now = _now()
        report_id = uuid4()

        with write_transaction(self.connection) as write:
            write.execute(
                """
                UPDATE integrity_reports SET superseded_at = ?
                WHERE document_id = ? AND superseded_at IS NULL
                """,
                (now, str(report.document_id)),
            )
            write.execute(
                """
                INSERT INTO integrity_reports (
                    report_id, document_id, run_id, tier, classifier_used,
                    classifier_unavailable, sanitized_char_count, threshold, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(report_id),
                    str(report.document_id),
                    str(run_id) if run_id else None,
                    report.tier.value,
                    int(report.classifier_used),
                    int(report.classifier_unavailable),
                    report.sanitized_char_count,
                    report.threshold,
                    now,
                ),
            )
            for finding in report.findings:
                write.execute(
                    """
                    INSERT INTO integrity_findings (
                        finding_id, report_id, document_id, kind, severity, detector,
                        detector_confidence, excerpt, provenance, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        str(report_id),
                        str(report.document_id),
                        finding.kind.value,
                        finding.severity.value,
                        finding.detector,
                        finding.detector_confidence,
                        finding.excerpt,
                        _dumps(finding.provenance.model_dump(mode="json"))
                        if finding.provenance
                        else None,
                        now,
                    ),
                )

    def integrity_report_for(self, document_id: UUID) -> IntegrityReport | None:
        """The current scan for one document."""
        row = self.connection.execute(
            """
            SELECT * FROM integrity_reports
            WHERE document_id = ? AND superseded_at IS NULL
            ORDER BY created_at DESC LIMIT 1
            """,
            (str(document_id),),
        ).fetchone()
        return None if row is None else self._report_from(row)

    def integrity_reports_for_run(self, run_id: UUID) -> list[IntegrityReport]:
        rows = self.connection.execute(
            """
            SELECT * FROM integrity_reports
            WHERE run_id = ? AND superseded_at IS NULL
            ORDER BY created_at
            """,
            (str(run_id),),
        ).fetchall()
        return [self._report_from(row) for row in rows]

    def _report_from(self, row: sqlite3.Row) -> IntegrityReport:
        findings = self.connection.execute(
            "SELECT * FROM integrity_findings WHERE report_id = ? ORDER BY created_at",
            (row["report_id"],),
        ).fetchall()

        return IntegrityReport(
            document_id=UUID(row["document_id"]),
            tier=IntegrityTier(row["tier"]),
            findings=[
                IntegrityFinding(
                    kind=IntegrityFindingKind(finding["kind"]),
                    severity=Severity(finding["severity"]),
                    detector=finding["detector"],
                    detector_confidence=finding["detector_confidence"],
                    excerpt=finding["excerpt"],
                    provenance=_loads(finding["provenance"], None),
                )
                for finding in findings
            ],
            classifier_used=bool(row["classifier_used"]),
            classifier_unavailable=bool(row["classifier_unavailable"]),
            sanitized_char_count=row["sanitized_char_count"],
            threshold=row["threshold"],
        )

    def detector_counts(self) -> list[tuple[str, str, int]]:
        """How often each detector fired, and at what severity.

        The question the stable detector id exists to answer. Used by the
        adversarial corpus report and by the operations page.
        """
        rows = self.connection.execute(
            """
            SELECT detector, severity, COUNT(*) AS n
            FROM integrity_findings
            GROUP BY detector, severity
            ORDER BY detector, severity
            """
        ).fetchall()
        return [(row["detector"], row["severity"], row["n"]) for row in rows]


# ---------------------------------------------------------------------------
# Evidence, assessments, recommendations
# ---------------------------------------------------------------------------


class SqliteEvidenceRepository(SqliteRepository):
    def save_evidence(self, run_id: UUID, items: Sequence[EvidenceItem], *, rejected: bool) -> None:
        with write_transaction(self.connection) as write:
            write.executemany(
                """
                INSERT OR REPLACE INTO evidence (
                    evidence_id, run_id, criterion_id, state, claim, verbatim_span,
                    provenance, confidence, model_tier, prompt_version, escalation_state,
                    span_validation, span_match_ratio, validated_norm_start, rejected
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(item.evidence_id),
                        str(run_id),
                        item.criterion_id,
                        item.state.value,
                        item.claim,
                        item.verbatim_span,
                        _dumps(item.provenance.model_dump(mode="json"))
                        if item.provenance
                        else None,
                        item.confidence,
                        item.model_tier.value,
                        item.prompt_version,
                        item.escalation_state.value,
                        item.span_validation.value,
                        item.span_match_ratio,
                        item.validated_norm_start,
                        int(rejected),
                    )
                    for item in items
                ],
            )

    def evidence_for_run(self, run_id: UUID, *, rejected: bool | None = None) -> list[EvidenceItem]:
        if rejected is None:
            rows = self.connection.execute(
                "SELECT * FROM evidence WHERE run_id = ? ORDER BY criterion_id", (str(run_id),)
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM evidence WHERE run_id = ? AND rejected = ? ORDER BY criterion_id",
                (str(run_id), int(rejected)),
            ).fetchall()
        return [_evidence_from_row(row) for row in rows]

    def save_assessment(self, run_id: UUID, assessment: CriterionAssessment) -> None:
        with write_transaction(self.connection) as write:
            write.execute(
                """
                INSERT OR REPLACE INTO assessments (
                    run_id, criterion_id, resolved_state, resolution_rule_id, tier_used,
                    escalated, escalation_trigger, pre_escalation_state,
                    escalation_changed_state, chunks_shown, unassessed_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    assessment.criterion_id,
                    assessment.resolved_state.value,
                    assessment.resolution_rule_id,
                    assessment.tier_used.value,
                    int(assessment.escalated),
                    assessment.escalation_trigger,
                    assessment.pre_escalation_state.value
                    if assessment.pre_escalation_state
                    else None,
                    None
                    if assessment.escalation_changed_state is None
                    else int(assessment.escalation_changed_state),
                    _dumps(assessment.chunks_shown) if assessment.chunks_shown else None,
                    assessment.unassessed_reason,
                ),
            )

    def assessments_for_run(self, run_id: UUID) -> list[CriterionAssessment]:

        rows = self.connection.execute(
            "SELECT * FROM assessments WHERE run_id = ? ORDER BY criterion_id", (str(run_id),)
        ).fetchall()

        by_criterion: dict[str, list[EvidenceItem]] = {}
        rejected_by_criterion: dict[str, list[EvidenceItem]] = {}
        for item in self.evidence_for_run(run_id, rejected=False):
            by_criterion.setdefault(item.criterion_id, []).append(item)
        for item in self.evidence_for_run(run_id, rejected=True):
            rejected_by_criterion.setdefault(item.criterion_id, []).append(item)

        return [
            CriterionAssessment(
                criterion_id=row["criterion_id"],
                evidence=by_criterion.get(row["criterion_id"], []),
                rejected_evidence=rejected_by_criterion.get(row["criterion_id"], []),
                resolved_state=CriterionState(row["resolved_state"]),
                resolution_rule_id=row["resolution_rule_id"],
                tier_used=ModelTier(row["tier_used"]),
                escalated=bool(row["escalated"]),
                escalation_trigger=row["escalation_trigger"],
                pre_escalation_state=CriterionState(row["pre_escalation_state"])
                if row["pre_escalation_state"]
                else None,
                escalation_changed_state=None
                if row["escalation_changed_state"] is None
                else bool(row["escalation_changed_state"]),
                chunks_shown=_loads(row["chunks_shown"], None),
                unassessed_reason=row["unassessed_reason"],
            )
            for row in rows
        ]

    def save_recommendation(self, run_id: UUID, recommendation: Recommendation) -> None:
        with write_transaction(self.connection) as write:
            write.execute(
                "UPDATE runs SET recommendation = ?, final_band = ?, score = ?, coverage = ? "
                "WHERE run_id = ?",
                (
                    _dumps(recommendation.model_dump(mode="json")),
                    recommendation.band.value,
                    recommendation.score,
                    recommendation.coverage,
                    str(run_id),
                ),
            )

    def recommendation_for_run(self, run_id: UUID) -> Recommendation | None:
        row = self.connection.execute(
            "SELECT recommendation FROM runs WHERE run_id = ?", (str(run_id),)
        ).fetchone()
        if row is None or row["recommendation"] is None:
            return None
        return Recommendation.model_validate(json.loads(row["recommendation"]))


def _evidence_from_row(row: sqlite3.Row) -> EvidenceItem:

    return EvidenceItem(
        evidence_id=UUID(row["evidence_id"]),
        criterion_id=row["criterion_id"],
        state=EvidenceState(row["state"]),
        claim=row["claim"],
        verbatim_span=row["verbatim_span"],
        provenance=Provenance.model_validate(json.loads(row["provenance"]))
        if row["provenance"]
        else None,
        confidence=row["confidence"],
        model_tier=ModelTier(row["model_tier"]),
        prompt_version=row["prompt_version"],
        escalation_state=EscalationState(row["escalation_state"]),
        span_validation=SpanValidation(row["span_validation"]),
        span_match_ratio=row["span_match_ratio"],
        validated_norm_start=row["validated_norm_start"],
    )


# ---------------------------------------------------------------------------
# Reviews and overrides
# ---------------------------------------------------------------------------


class SqliteReviewRepository(SqliteRepository):
    def save(self, decision: ReviewDecision) -> None:
        with write_transaction(self.connection) as write:
            write.execute(
                """
                INSERT OR REPLACE INTO reviews (
                    decision_id, run_id, reviewer_id, action, comments, elapsed_seconds,
                    trust_rating, post_override_band, decided_at, run_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(decision.decision_id),
                    str(decision.run_id),
                    decision.reviewer_id,
                    decision.action.value,
                    decision.comments,
                    decision.elapsed_seconds,
                    decision.trust_rating,
                    decision.post_override_band.value if decision.post_override_band else None,
                    decision.decided_at.isoformat(),
                    decision.run_version,
                ),
            )
            write.executemany(
                """
                INSERT INTO overrides (
                    override_id, decision_id, run_id, criterion_id,
                    previous_state, new_state, reason_code, reason_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(uuid4()),
                        str(decision.decision_id),
                        str(decision.run_id),
                        override.criterion_id,
                        override.previous_state.value,
                        override.new_state.value,
                        override.reason_code.value,
                        override.reason_text,
                    )
                    for override in decision.overrides
                ],
            )
            write.execute(
                "UPDATE runs SET reviewer_action = ?, override_count = ? WHERE run_id = ?",
                (decision.action.value, len(decision.overrides), str(decision.run_id)),
            )

    def get_for_run(self, run_id: UUID) -> ReviewDecision | None:

        row = self.connection.execute(
            "SELECT * FROM reviews WHERE run_id = ? ORDER BY decided_at DESC LIMIT 1",
            (str(run_id),),
        ).fetchone()
        if row is None:
            return None

        override_rows = self.connection.execute(
            "SELECT * FROM overrides WHERE decision_id = ? ORDER BY criterion_id",
            (row["decision_id"],),
        ).fetchall()

        return ReviewDecision(
            decision_id=UUID(row["decision_id"]),
            run_id=UUID(row["run_id"]),
            reviewer_id=row["reviewer_id"],
            action=ReviewAction(row["action"]),
            overrides=[
                Override(
                    criterion_id=item["criterion_id"],
                    previous_state=CriterionState(item["previous_state"]),
                    new_state=CriterionState(item["new_state"]),
                    reason_code=OverrideReason(item["reason_code"]),
                    reason_text=item["reason_text"],
                )
                for item in override_rows
            ],
            comments=row["comments"],
            elapsed_seconds=row["elapsed_seconds"],
            trust_rating=row["trust_rating"],
            post_override_band=Band(row["post_override_band"])
            if row["post_override_band"]
            else None,
            decided_at=datetime.fromisoformat(row["decided_at"]),
            run_version=row["run_version"],
        )

    def list_overrides(
        self, *, role_id: str | None = None, limit: int = 500
    ) -> list[tuple[UUID, str, str, str, str]]:
        if role_id is None:
            rows = self.connection.execute(
                "SELECT o.run_id, o.criterion_id, o.previous_state, o.new_state, o.reason_code "
                "FROM overrides o LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT o.run_id, o.criterion_id, o.previous_state, o.new_state, o.reason_code "
                "FROM overrides o JOIN runs r ON r.run_id = o.run_id "
                "WHERE r.role_id = ? LIMIT ?",
                (role_id, limit),
            ).fetchall()
        return [
            (
                UUID(row["run_id"]),
                row["criterion_id"],
                row["previous_state"],
                row["new_state"],
                row["reason_code"],
            )
            for row in rows
        ]


# ---------------------------------------------------------------------------
# Cost, errors, deliveries, calibration, cache, events
# ---------------------------------------------------------------------------


class SqliteCostRepository(SqliteRepository):
    def record(self, cost: CostRecord) -> None:
        with write_transaction(self.connection) as write:
            write.execute(
                """
                INSERT OR REPLACE INTO llm_calls (
                    call_id, run_id, call_site, criterion_id, model_tier, input_tokens,
                    output_tokens, cached_input_tokens, unit_price_source, cost_usd,
                    currency, latency_ms, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(cost.record_id),
                    str(cost.run_id),
                    cost.call_site,
                    None,
                    cost.model_tier.value,
                    cost.input_tokens,
                    cost.output_tokens,
                    cost.cached_input_tokens,
                    cost.unit_price_source,
                    str(cost.cost_usd) if cost.cost_usd is not None else None,
                    cost.currency,
                    cost.latency_ms,
                    cost.occurred_at.isoformat(),
                ),
            )

    def for_run(self, run_id: UUID) -> list[CostRecord]:

        rows = self.connection.execute(
            "SELECT * FROM llm_calls WHERE run_id = ? ORDER BY occurred_at", (str(run_id),)
        ).fetchall()
        return [
            CostRecord(
                record_id=UUID(row["call_id"]),
                run_id=UUID(row["run_id"]),
                call_site=row["call_site"],
                model_tier=ModelTier(row["model_tier"]),
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                cached_input_tokens=row["cached_input_tokens"],
                unit_price_source=row["unit_price_source"],
                cost_usd=Decimal(row["cost_usd"]) if row["cost_usd"] else None,
                currency=row["currency"],
                latency_ms=row["latency_ms"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
            )
            for row in rows
        ]

    def totals_for_run(self, run_id: UUID) -> tuple[int, int, int]:
        row = self.connection.execute(
            "SELECT COALESCE(SUM(input_tokens), 0) AS i, COALESCE(SUM(output_tokens), 0) AS o, "
            "COALESCE(SUM(cached_input_tokens), 0) AS c FROM llm_calls WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        return int(row["i"]), int(row["o"]), int(row["c"])


class SqliteErrorRepository(SqliteRepository):
    def record(self, error: ErrorRecord) -> None:
        with write_transaction(self.connection) as write:
            write.execute(
                """
                INSERT OR REPLACE INTO errors (
                    error_id, run_id, node, error_code, error_class, message_redacted,
                    retryable, attempt, resulting_state, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(error.error_id),
                    str(error.run_id) if error.run_id else None,
                    error.node,
                    error.error_code,
                    error.error_class,
                    error.message_redacted,
                    int(error.retryable),
                    error.attempt,
                    error.resulting_state.value if error.resulting_state else None,
                    error.occurred_at.isoformat(),
                ),
            )

    def for_run(self, run_id: UUID) -> list[ErrorRecord]:
        rows = self.connection.execute(
            "SELECT * FROM errors WHERE run_id = ? ORDER BY occurred_at", (str(run_id),)
        ).fetchall()
        return [
            ErrorRecord(
                error_id=UUID(row["error_id"]),
                run_id=UUID(row["run_id"]) if row["run_id"] else None,
                node=row["node"],
                error_code=row["error_code"],
                error_class=row["error_class"],
                message_redacted=row["message_redacted"],
                retryable=bool(row["retryable"]),
                attempt=row["attempt"],
                resulting_state=RunStatus(row["resulting_state"])
                if row["resulting_state"]
                else None,
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
            )
            for row in rows
        ]


class SqliteDeliveryRepository(SqliteRepository):
    def record(self, delivery: DeliveryRecord) -> None:
        with write_transaction(self.connection) as write:
            write.execute(
                """
                INSERT OR REPLACE INTO deliveries (
                    delivery_id, run_id, sink_id, status, attempts,
                    external_ref, last_error, delivered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(delivery.delivery_id),
                    str(delivery.run_id),
                    delivery.sink_id,
                    delivery.status.value,
                    delivery.attempts,
                    delivery.external_ref,
                    delivery.last_error,
                    delivery.delivered_at.isoformat() if delivery.delivered_at else None,
                ),
            )

    def for_run(self, run_id: UUID) -> list[DeliveryRecord]:
        rows = self.connection.execute(
            "SELECT * FROM deliveries WHERE run_id = ? ORDER BY sink_id", (str(run_id),)
        ).fetchall()
        return [_delivery_from_row(row) for row in rows]

    def pending_retries(self, limit: int = 100) -> list[DeliveryRecord]:
        rows = self.connection.execute(
            "SELECT * FROM deliveries WHERE status = ? LIMIT ?",
            (DeliveryStatus.PENDING_RETRY.value, limit),
        ).fetchall()
        return [_delivery_from_row(row) for row in rows]


def _delivery_from_row(row: sqlite3.Row) -> DeliveryRecord:
    return DeliveryRecord(
        delivery_id=UUID(row["delivery_id"]),
        run_id=UUID(row["run_id"]),
        sink_id=row["sink_id"],
        status=DeliveryStatus(row["status"]),
        attempts=row["attempts"],
        external_ref=row["external_ref"],
        last_error=row["last_error"],
        delivered_at=datetime.fromisoformat(row["delivered_at"]) if row["delivered_at"] else None,
    )


class SqliteCalibrationRepository(SqliteRepository):
    def add(self, card: CalibrationCard) -> None:
        with write_transaction(self.connection) as write:
            write.execute(
                """
                INSERT OR REPLACE INTO calibration_cards (
                    card_id, role_id, rubric_version, anonymized_summary, criterion_states,
                    final_band, reviewer_reason_codes, reviewer_reason_text_redacted,
                    decided_at, embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(card.card_id),
                    card.role_id,
                    card.rubric_version,
                    card.anonymized_summary,
                    _dumps({k: v.value for k, v in card.criterion_states.items()}),
                    card.final_band.value,
                    _dumps([code.value for code in card.reviewer_reason_codes]),
                    card.reviewer_reason_text_redacted,
                    card.decided_at.isoformat(),
                    card.embedding,
                ),
            )

    def for_role(
        self, role_id: str, rubric_version: str, limit: int = 200
    ) -> list[CalibrationCard]:

        rows = self.connection.execute(
            "SELECT * FROM calibration_cards WHERE role_id = ? AND rubric_version = ? "
            "ORDER BY decided_at DESC LIMIT ?",
            (role_id, rubric_version, limit),
        ).fetchall()
        return [
            CalibrationCard(
                card_id=UUID(row["card_id"]),
                role_id=row["role_id"],
                rubric_version=row["rubric_version"],
                anonymized_summary=row["anonymized_summary"],
                criterion_states={
                    k: CriterionState(v) for k, v in _loads(row["criterion_states"], {}).items()
                },
                final_band=Band(row["final_band"]),
                reviewer_reason_codes=[
                    OverrideReason(code) for code in _loads(row["reviewer_reason_codes"], [])
                ],
                reviewer_reason_text_redacted=row["reviewer_reason_text_redacted"],
                decided_at=datetime.fromisoformat(row["decided_at"]),
                embedding=bytes(row["embedding"]),
            )
            for row in rows
        ]


class SqliteLlmCacheRepository(SqliteRepository):
    def get(self, cache_key: str) -> tuple[str, str] | None:
        row = self.connection.execute(
            "SELECT response_json, usage_json FROM llm_cache WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        return (row["response_json"], row["usage_json"]) if row else None

    def put(self, cache_key: str, response_json: str, usage_json: str) -> None:
        with write_transaction(self.connection) as write:
            write.execute(
                "INSERT OR REPLACE INTO llm_cache (cache_key, response_json, usage_json, "
                "created_at) VALUES (?, ?, ?, ?)",
                (cache_key, response_json, usage_json, _now()),
            )


class SqliteEventRepository(SqliteRepository):
    def append(
        self,
        run_id: UUID,
        *,
        node: str,
        from_status: RunStatus | None,
        to_status: RunStatus | None,
        node_status: str,
        payload: dict[str, object] | None = None,
    ) -> int:
        with write_transaction(self.connection) as write:
            row = write.execute(
                "SELECT COALESCE(MAX(seq), 0) AS seq FROM run_events WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
            seq = int(row["seq"]) + 1
            write.execute(
                """
                INSERT INTO run_events (
                    run_id, seq, node, from_status, to_status, node_status, payload, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    seq,
                    node,
                    from_status.value if from_status else None,
                    to_status.value if to_status else None,
                    node_status,
                    _dumps(payload) if payload else None,
                    _now(),
                ),
            )
        return seq

    def for_run(self, run_id: UUID) -> list[tuple[int, str, str, str]]:
        rows = self.connection.execute(
            "SELECT seq, node, node_status, occurred_at FROM run_events WHERE run_id = ? "
            "ORDER BY seq",
            (str(run_id),),
        ).fetchall()
        return [(row["seq"], row["node"], row["node_status"], row["occurred_at"]) for row in rows]
