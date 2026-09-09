"""Every repository, written and read back.

A round trip is the only test that catches a column written in the wrong order,
an enum stored as a name instead of a value, or a Decimal quietly turned into a
float. Each one writes a contract, reads it, and asserts equality on the whole
object rather than on the fields someone remembered.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from domain.contracts import (
    Band,
    CalibrationCard,
    CostRecord,
    CriterionAssessment,
    CriterionState,
    DeliveryRecord,
    DeliveryStatus,
    ErrorRecord,
    ModelTier,
    Override,
    OverrideReason,
    Recommendation,
    ReviewAction,
    ReviewDecision,
    RunRecord,
    RunStatus,
)
from domain.contracts.recommendation import DerivationStep
from domain.ports.repositories import StaleRunVersion
from infrastructure.storage.sqlite.connection import close_thread_connection
from infrastructure.storage.sqlite.repositories import (
    SqliteCalibrationRepository,
    SqliteCandidateRepository,
    SqliteCostRepository,
    SqliteDeliveryRepository,
    SqliteErrorRepository,
    SqliteEventRepository,
    SqliteEvidenceRepository,
    SqliteLlmCacheRepository,
    SqliteReviewRepository,
    SqliteRunRepository,
)
from infrastructure.storage.sqlite.schema import migrate
from tests.builders import NOW, document, evidence

pytestmark = pytest.mark.usefixtures("database")


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "test.sqlite"
    migrate(path)
    yield path
    close_thread_connection(path)


@pytest.fixture
def database(db: Path) -> Path:
    return db


def make_run(**overrides: Any) -> RunRecord:
    fields: dict[str, Any] = {
        "run_id": uuid4(),
        "content_key": uuid4().hex,
        "candidate_id": "cand-0007",
        "role_id": "ai-engineer",
        "rubric_version": "1.0.0",
        "rubric_hash": "a" * 64,
        "prompt_bundle_hash": "b" * 64,
        "model_tier_bindings_hash": "c" * 64,
        "routing_policy_id": "routed",
        "blind_mode": True,
        "calibration_enabled": False,
        "calibration_status": "disabled",
        "pipeline_version": "1",
        "status": RunStatus.CREATED,
        "started_at": NOW,
    }
    fields.update(overrides)
    return RunRecord(**fields)


# --- runs --------------------------------------------------------------------


def test_a_run_round_trips(db: Path) -> None:
    runs = SqliteRunRepository(db)
    original = make_run()

    runs.create(original)
    stored = runs.get(original.run_id)

    assert stored == original


def test_an_unpriced_run_reads_back_as_null_not_zero(db: Path) -> None:
    """A zero would read as free, which is a claim nobody measured."""
    runs = SqliteRunRepository(db)
    original = make_run()
    runs.create(original)

    stored = runs.get(original.run_id)
    assert stored is not None
    assert stored.total_cost_usd is None


def test_a_cost_survives_as_a_decimal(db: Path) -> None:
    """Stored as text, because a float cannot hold a price exactly and a cost
    that drifts in the fourth decimal place is a cost nobody can reconcile."""
    runs = SqliteRunRepository(db)
    original = make_run(total_cost_usd=Decimal("0.001234"))
    runs.create(original)

    stored = runs.get(original.run_id)
    assert stored is not None
    assert stored.total_cost_usd == Decimal("0.001234")


def test_a_missing_run_reads_as_none(db: Path) -> None:
    assert SqliteRunRepository(db).get(uuid4()) is None


def test_a_run_is_found_by_content_key(db: Path) -> None:
    """This lookup is the run cache: the same documents and configuration return
    the existing run rather than billing for it again."""
    runs = SqliteRunRepository(db)
    original = make_run()
    runs.create(original)

    assert runs.find_by_content_key(original.content_key) == original


def test_two_runs_cannot_share_a_content_key(db: Path) -> None:
    import sqlite3

    runs = SqliteRunRepository(db)
    first = make_run(content_key="shared")
    runs.create(first)

    with pytest.raises(sqlite3.IntegrityError):
        runs.create(make_run(content_key="shared"))


def test_runs_are_listed_by_status(db: Path) -> None:
    runs = SqliteRunRepository(db)
    runs.create(make_run(status=RunStatus.READY_FOR_REVIEW))
    runs.create(make_run(status=RunStatus.READY_FOR_REVIEW))
    runs.create(make_run(status=RunStatus.DELIVERED))

    found = runs.list_by_status([RunStatus.READY_FOR_REVIEW])

    assert len(found) == 2
    assert all(run.status is RunStatus.READY_FOR_REVIEW for run in found)


def test_listing_no_statuses_returns_nothing(db: Path) -> None:
    SqliteRunRepository(db).create(make_run())
    assert SqliteRunRepository(db).list_by_status([]) == []


def test_an_update_advances_the_version(db: Path) -> None:
    runs = SqliteRunRepository(db)
    original = make_run()
    runs.create(original)

    updated = runs.update(
        original.model_copy(update={"status": RunStatus.EXTRACTED}), expected_version=0
    )

    assert updated.version == 1
    assert updated.status is RunStatus.EXTRACTED


def test_a_stale_write_is_refused(db: Path) -> None:
    """A reviewer whose page was open while the run changed is told to reload,
    rather than silently clobbering the decision someone else made."""
    runs = SqliteRunRepository(db)
    original = make_run()
    runs.create(original)
    runs.update(original, expected_version=0)

    with pytest.raises(StaleRunVersion, match="changed elsewhere"):
        runs.update(original, expected_version=0)


def test_completed_nodes_accumulate(db: Path) -> None:
    """Resumability rests on this: a restarted run skips what already committed."""
    runs = SqliteRunRepository(db)
    original = make_run()
    runs.create(original)

    runs.mark_node_complete(original.run_id, "INTAKE", RunStatus.INTAKE_OK)
    runs.mark_node_complete(original.run_id, "EXTRACT", RunStatus.EXTRACTED)
    runs.mark_node_complete(original.run_id, "EXTRACT", RunStatus.EXTRACTED)

    stored = runs.get(original.run_id)
    assert stored is not None
    assert stored.completed_nodes == ["INTAKE", "EXTRACT"]
    assert stored.status is RunStatus.EXTRACTED


# --- documents and extracted text --------------------------------------------


def test_a_document_round_trips(db: Path) -> None:
    runs, candidates = SqliteRunRepository(db), SqliteCandidateRepository(db)
    run = make_run()
    runs.create(run)
    original = document()

    candidates.add_document(original, run_id=run.run_id)

    assert candidates.documents_for_run(run.run_id) == [original]


def test_extracted_text_is_cached_independently_of_the_rubric(db: Path) -> None:
    """A rubric change must never re-OCR a document: the text of a PDF does not
    depend on what questions are asked of it."""
    from domain.contracts import ExtractionMethod, OffsetRun, PageSpan, SourceText

    candidates = SqliteCandidateRepository(db)
    text = "Shipped an evaluation suite."
    source = SourceText(
        document_id=uuid4(),
        raw_text=text,
        normalized_text=text,
        offset_runs=[OffsetRun(norm_start=0, raw_start=0, length=len(text))],
        pages=[
            PageSpan(
                page_number=1,
                norm_start=0,
                norm_end=len(text),
                extraction_method=ExtractionMethod.DIGITAL_PDF,
            )
        ],
        normalization_profile_id="np-v1",
        extraction_confidence=0.9,
        detected_languages=["en"],
    )

    candidates.put_source_text(source, document_sha256="d" * 64)

    assert candidates.get_source_text("d" * 64, "np-v1") == source
    assert candidates.get_source_text("d" * 64, "np-v2") is None


# --- evidence and assessments -------------------------------------------------


def test_evidence_round_trips(db: Path) -> None:
    runs, store = SqliteRunRepository(db), SqliteEvidenceRepository(db)
    run = make_run()
    runs.create(run)
    items = [evidence(criterion_id="alpha"), evidence(criterion_id="beta")]

    store.save_evidence(run.run_id, items, rejected=False)

    assert sorted(store.evidence_for_run(run.run_id), key=lambda i: i.criterion_id) == sorted(
        items, key=lambda i: i.criterion_id
    )


def test_rejected_evidence_is_kept_and_separable(db: Path) -> None:
    """A quotation the validator could not find is what the reviewer most needs
    to see, and what the hallucination metric counts."""
    from domain.contracts import SpanValidation

    runs, store = SqliteRunRepository(db), SqliteEvidenceRepository(db)
    run = make_run()
    runs.create(run)

    good = evidence(criterion_id="alpha")
    bad = evidence(criterion_id="alpha", span_validation=SpanValidation.INVALID_NOT_FOUND)
    store.save_evidence(run.run_id, [good], rejected=False)
    store.save_evidence(run.run_id, [bad], rejected=True)

    assert store.evidence_for_run(run.run_id, rejected=False) == [good]
    assert store.evidence_for_run(run.run_id, rejected=True) == [bad]
    assert len(store.evidence_for_run(run.run_id)) == 2


def test_an_assessment_round_trips_with_its_evidence(db: Path) -> None:
    runs, store = SqliteRunRepository(db), SqliteEvidenceRepository(db)
    run = make_run()
    runs.create(run)

    item = evidence(criterion_id="alpha")
    store.save_evidence(run.run_id, [item], rejected=False)
    assessment = CriterionAssessment(
        criterion_id="alpha",
        evidence=[item],
        resolved_state=CriterionState.MET,
        resolution_rule_id="R-MET",
        tier_used=ModelTier.CHEAP,
    )
    store.save_assessment(run.run_id, assessment)

    assert store.assessments_for_run(run.run_id) == [assessment]


def test_a_recommendation_round_trips(db: Path) -> None:
    runs, store = SqliteRunRepository(db), SqliteEvidenceRepository(db)
    run = make_run()
    runs.create(run)
    recommendation = Recommendation(
        run_id=run.run_id,
        band=Band.ADVANCE,
        score=0.81,
        coverage=0.9,
        criterion_states={"alpha": CriterionState.MET},
        derivation=[
            DerivationStep(rule_id="R-BAND", description="Score reaches Advance.", output="advance")
        ],
        rubric_version="1.0.0",
    )

    store.save_recommendation(run.run_id, recommendation)

    assert store.recommendation_for_run(run.run_id) == recommendation


def test_no_recommendation_reads_as_none(db: Path) -> None:
    runs = SqliteRunRepository(db)
    run = make_run()
    runs.create(run)
    assert SqliteEvidenceRepository(db).recommendation_for_run(run.run_id) is None


# --- reviews ------------------------------------------------------------------


def test_a_review_decision_round_trips_with_overrides(db: Path) -> None:
    runs, reviews = SqliteRunRepository(db), SqliteReviewRepository(db)
    run = make_run()
    runs.create(run)
    decision = ReviewDecision(
        decision_id=uuid4(),
        run_id=run.run_id,
        reviewer_id="recruiter-01",
        action=ReviewAction.APPROVE,
        overrides=[
            Override(
                criterion_id="alpha",
                previous_state=CriterionState.MET,
                new_state=CriterionState.PARTIAL,
                reason_code=OverrideReason.THRESHOLD_WRONG,
                reason_text="One example is not a practice.",
            )
        ],
        elapsed_seconds=96,
        trust_rating=4,
        decided_at=NOW,
        run_version=1,
    )

    reviews.save(decision)

    assert reviews.get_for_run(run.run_id) == decision


def test_the_decision_behind_an_approval_is_findable(db: Path) -> None:
    """Delivery adapters call this independently of the state machine, so an
    approved status with no decision row behind it still cannot send."""
    runs, reviews = SqliteRunRepository(db), SqliteReviewRepository(db)
    run = make_run(status=RunStatus.APPROVED)
    runs.create(run)

    assert reviews.get_for_run(run.run_id) is None


def test_overrides_are_queryable_across_runs(db: Path) -> None:
    """They are the raw material of the improvement loop, so they are a table
    rather than a JSON column."""
    runs, reviews = SqliteRunRepository(db), SqliteReviewRepository(db)
    run = make_run()
    runs.create(run)
    reviews.save(
        ReviewDecision(
            decision_id=uuid4(),
            run_id=run.run_id,
            reviewer_id="r",
            action=ReviewAction.APPROVE,
            overrides=[
                Override(
                    criterion_id="alpha",
                    previous_state=CriterionState.MET,
                    new_state=CriterionState.NOT_MET,
                    reason_code=OverrideReason.EVIDENCE_MISREAD,
                    reason_text="Wrong project.",
                )
            ],
            elapsed_seconds=10,
            decided_at=NOW,
            run_version=0,
        )
    )

    found = reviews.list_overrides(role_id="ai-engineer")
    assert [(row[1], row[4]) for row in found] == [("alpha", "evidence_misread")]


# --- cost, errors, delivery, calibration, cache, events ----------------------


def test_a_cost_record_round_trips(db: Path) -> None:
    runs, costs = SqliteRunRepository(db), SqliteCostRepository(db)
    run = make_run()
    runs.create(run)
    record = CostRecord(
        record_id=uuid4(),
        run_id=run.run_id,
        call_site="assess.criterion",
        model_tier=ModelTier.CHEAP,
        input_tokens=4210,
        output_tokens=318,
        latency_ms=1840,
        occurred_at=NOW,
    )

    costs.record(record)

    assert costs.for_run(run.run_id) == [record]


def test_token_totals_are_summed_not_estimated(db: Path) -> None:
    runs, costs = SqliteRunRepository(db), SqliteCostRepository(db)
    run = make_run()
    runs.create(run)
    for tokens in (100, 250):
        costs.record(
            CostRecord(
                record_id=uuid4(),
                run_id=run.run_id,
                call_site="assess.criterion",
                model_tier=ModelTier.CHEAP,
                input_tokens=tokens,
                output_tokens=10,
                latency_ms=1,
                occurred_at=NOW,
            )
        )

    assert costs.totals_for_run(run.run_id) == (350, 20, 0)


def test_totals_are_zero_for_a_run_with_no_calls(db: Path) -> None:
    runs = SqliteRunRepository(db)
    run = make_run()
    runs.create(run)
    assert SqliteCostRepository(db).totals_for_run(run.run_id) == (0, 0, 0)


def test_an_error_record_round_trips(db: Path) -> None:
    runs, errors = SqliteRunRepository(db), SqliteErrorRepository(db)
    run = make_run()
    runs.create(run)
    record = ErrorRecord(
        error_id=uuid4(),
        run_id=run.run_id,
        node="EXTRACT",
        error_code="UNSUPPORTED_TYPE",
        error_class="ExtractionError",
        message_redacted="could not open [redacted:filename]",
        retryable=False,
        attempt=1,
        resulting_state=RunStatus.FAILED_TERMINAL,
        occurred_at=NOW,
    )

    errors.record(record)

    assert errors.for_run(run.run_id) == [record]


def test_deliveries_round_trip_per_sink(db: Path) -> None:
    runs, deliveries = SqliteRunRepository(db), SqliteDeliveryRepository(db)
    run = make_run()
    runs.create(run)
    written = DeliveryRecord(
        delivery_id=uuid4(),
        run_id=run.run_id,
        sink_id="csv",
        status=DeliveryStatus.DELIVERED,
        attempts=1,
        delivered_at=NOW,
    )
    failed = DeliveryRecord(
        delivery_id=uuid4(),
        run_id=run.run_id,
        sink_id="sheets",
        status=DeliveryStatus.PENDING_RETRY,
        attempts=2,
        last_error="429 from the API",
    )

    deliveries.record(written)
    deliveries.record(failed)

    assert sorted(deliveries.for_run(run.run_id), key=lambda d: d.sink_id) == [written, failed]
    assert deliveries.pending_retries() == [failed]


def test_a_calibration_card_round_trips(db: Path) -> None:
    cards = SqliteCalibrationRepository(db)
    card = CalibrationCard(
        card_id=uuid4(),
        role_id="ai-engineer",
        rubric_version="1.0.0",
        anonymized_summary="Seven years backend, two shipped language-model features.",
        criterion_states={"alpha": CriterionState.MET},
        final_band=Band.ADVANCE,
        reviewer_reason_codes=[OverrideReason.EVIDENCE_MISSED],
        decided_at=NOW,
        embedding=b"\x00\x01\x02",
    )

    cards.add(card)

    assert cards.for_role("ai-engineer", "1.0.0") == [card]
    assert cards.for_role("ai-engineer", "2.0.0") == []


def test_the_llm_cache_round_trips(db: Path) -> None:
    cache = SqliteLlmCacheRepository(db)
    cache.put("key-1", '{"evidence": []}', '{"input_tokens": 10}')

    assert cache.get("key-1") == ('{"evidence": []}', '{"input_tokens": 10}')
    assert cache.get("missing") is None


def test_events_are_appended_in_order(db: Path) -> None:
    runs, events = SqliteRunRepository(db), SqliteEventRepository(db)
    run = make_run()
    runs.create(run)

    first = events.append(
        run.run_id, node="INTAKE", from_status=None, to_status=RunStatus.INTAKE_OK, node_status="ok"
    )
    second = events.append(
        run.run_id,
        node="EXTRACT",
        from_status=RunStatus.INTAKE_OK,
        to_status=RunStatus.EXTRACTED,
        node_status="ok",
    )

    assert (first, second) == (1, 2)
    assert [(row[0], row[1]) for row in events.for_run(run.run_id)] == [
        (1, "INTAKE"),
        (2, "EXTRACT"),
    ]


# --- concurrency and recovery -------------------------------------------------


def test_two_threads_write_without_corruption(db: Path) -> None:
    """WAL plus a process lock. Neither is sufficient alone: WAL allows
    concurrent readers, the lock serialises the writers."""
    runs = SqliteRunRepository(db)
    errors: list[BaseException] = []

    def write_ten(offset: int) -> None:
        try:
            for index in range(10):
                SqliteRunRepository(db).create(make_run(content_key=f"key-{offset}-{index}"))
        except BaseException as error:
            errors.append(error)
        finally:
            close_thread_connection(db)

    threads = [threading.Thread(target=write_ten, args=(offset,)) for offset in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(runs.list_by_status([RunStatus.CREATED], limit=200)) == 40


def test_a_stalled_run_is_findable(db: Path) -> None:
    """Startup reconciliation needs these, or a crashed run sits in the queue
    forever with no action that clears it."""
    runs = SqliteRunRepository(db)
    old = datetime(2020, 1, 1, tzinfo=UTC)
    runs.create(make_run(status=RunStatus.EXTRACTED, started_at=old))
    runs.create(make_run(status=RunStatus.READY_FOR_REVIEW, started_at=old))

    stale = runs.find_stale(older_than_minutes=15)

    assert [run.status for run in stale] == [RunStatus.EXTRACTED]


def test_a_recent_run_is_not_stale(db: Path) -> None:
    runs = SqliteRunRepository(db)
    runs.create(make_run(status=RunStatus.EXTRACTED, started_at=datetime.now(UTC)))

    assert runs.find_stale(older_than_minutes=15) == []
