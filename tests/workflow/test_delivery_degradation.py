"""One destination fails and nothing is lost.

The claim every optional integration rests on. A spreadsheet down for ten
minutes must not cost a reviewer their afternoon's decisions, and it does not,
because the CSV was written first and the decision was recorded before either.

The state that expresses this is DELIVERY_PENDING_RETRY. Not DELIVERED, because
some of it did not arrive; not FAILED, because most of it did. A per-sink record
is what makes the difference visible rather than reduced to one word.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from application.deps import Deps
from application.use_cases.retry_deliveries import retry_deliveries
from application.use_cases.submit_review import submit_review
from domain.contracts.enums import DeliveryStatus, ReviewAction, RunStatus
from domain.contracts.run_state import RunState
from domain.ports.sinks import AdapterError
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.integrations.csv_sink import CsvSink
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline.deliver import node as deliver
from tests.fakes.fake_transports import RecordingSink
from tests.workflow.conftest_review import seed_run, wire


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "delivery.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        reviewer_id="rec-014",
    )
    yield wire(build_deps(settings))
    close_thread_connection(settings.db_path)


def approved(deps: Deps):
    """A run a person actually approved."""
    run = seed_run(deps)
    submit_review(run.run_id, ReviewAction.APPROVE, deps)
    stored = deps.runs.get(run.run_id)
    assert stored is not None
    return stored


def state_of(run) -> RunState:
    return RunState(
        run_id=run.run_id,
        candidate_id=run.candidate_id,
        role_id=run.role_id,
        status=run.status,
        started_at=run.started_at,
        integrity_tier=run.integrity_tier,
    )


def with_sinks(deps: Deps, *sinks) -> Deps:
    return Deps(**{**deps.__dict__, "sinks": tuple(sinks)})


# --- everything works ---------------------------------------------------------------


def test_a_full_delivery_reaches_delivered(deps: Deps, tmp_path: Path) -> None:
    run = approved(deps)
    csv = CsvSink(tmp_path / "results.csv")
    sheets = RecordingSink(sink_id="sheets")

    result = deliver(state_of(run), with_sinks(deps, csv, sheets))

    assert result.next_status is RunStatus.DELIVERED
    assert csv.path.exists()
    assert sheets.delivered


def test_every_sink_gets_its_own_record(deps: Deps, tmp_path: Path) -> None:
    """One row per sink, which is what makes partial delivery representable."""
    run = approved(deps)
    wired = with_sinks(deps, CsvSink(tmp_path / "results.csv"), RecordingSink(sink_id="sheets"))

    deliver(state_of(run), wired)

    records = wired.deliveries.for_run(run.run_id)
    assert {record.sink_id for record in records} == {"csv", "sheets"}


# --- one destination fails -------------------------------------------------------------


def test_a_failed_sink_leaves_the_run_pending(deps: Deps, tmp_path: Path) -> None:
    """Not delivered, because some of it did not arrive. Not failed, because
    most of it did."""
    run = approved(deps)
    wired = with_sinks(
        deps,
        CsvSink(tmp_path / "results.csv"),
        RecordingSink(sink_id="sheets", fail_with=AdapterError.TRANSIENT),
    )

    result = deliver(state_of(run), wired)

    assert result.next_status is RunStatus.DELIVERY_PENDING_RETRY
    assert result.status.value == "degraded"


def test_the_csv_still_holds_the_result(deps: Deps, tmp_path: Path) -> None:
    """The guarantee. The least sophisticated sink is the most reliable one."""
    run = approved(deps)
    csv = CsvSink(tmp_path / "results.csv")
    wired = with_sinks(deps, csv, RecordingSink(sink_id="sheets", fail_with=AdapterError.TRANSIENT))

    deliver(state_of(run), wired)

    assert run.candidate_id in csv.path.read_text(encoding="utf-8")


def test_the_review_is_untouched(deps: Deps, tmp_path: Path) -> None:
    """A spreadsheet being down must not cost somebody their decision."""
    run = approved(deps)
    wired = with_sinks(
        deps,
        CsvSink(tmp_path / "results.csv"),
        RecordingSink(sink_id="sheets", fail_with=AdapterError.TRANSIENT),
    )

    deliver(state_of(run), wired)

    decision = wired.reviews.get_for_run(run.run_id)
    assert decision is not None
    assert decision.action is ReviewAction.APPROVE


def test_the_failure_is_recorded_per_sink(deps: Deps, tmp_path: Path) -> None:
    run = approved(deps)
    wired = with_sinks(
        deps,
        CsvSink(tmp_path / "results.csv"),
        RecordingSink(sink_id="sheets", fail_with=AdapterError.TRANSIENT),
    )

    deliver(state_of(run), wired)

    records = {record.sink_id: record for record in wired.deliveries.for_run(run.run_id)}
    assert records["csv"].status is DeliveryStatus.DELIVERED
    assert records["sheets"].status is DeliveryStatus.FAILED
    assert records["sheets"].last_error


def test_the_degradation_is_an_event(deps: Deps, tmp_path: Path) -> None:
    run = approved(deps)
    wired = with_sinks(
        deps,
        CsvSink(tmp_path / "results.csv"),
        RecordingSink(sink_id="sheets", fail_with=AdapterError.RATE_LIMITED),
    )

    result = deliver(state_of(run), wired)

    event = next(item for item in result.events if item.name == "deliver.degraded")
    assert event.payload["delivered"] == ["csv"]
    assert event.payload["failed"] == ["sheets"]


def test_a_disabled_sink_is_not_attempted(deps: Deps, tmp_path: Path) -> None:
    """One message about a broken credential, not one per candidate."""
    run = approved(deps)
    broken = RecordingSink(sink_id="sheets", is_healthy=False)
    wired = with_sinks(deps, CsvSink(tmp_path / "results.csv"), broken)

    deliver(state_of(run), wired)

    assert broken.delivered == []


# --- everything fails -----------------------------------------------------------------


def test_a_total_failure_is_retryable(deps: Deps) -> None:
    run = approved(deps)
    wired = with_sinks(deps, RecordingSink(fail_with=AdapterError.TRANSIENT))

    result = deliver(state_of(run), wired)

    assert result.error.error_code == "DELIVERY_FAILED"
    assert result.error.retryable is True
    assert "nothing has been lost" in result.error.message_redacted


# --- retrying -----------------------------------------------------------------------------


def test_a_retry_completes_the_delivery(deps: Deps, tmp_path: Path) -> None:
    """The whole point. Ten minutes later, the spreadsheet is back."""
    run = approved(deps)
    csv = CsvSink(tmp_path / "results.csv")
    sheets = RecordingSink(sink_id="sheets", fail_with=AdapterError.TRANSIENT)
    wired = with_sinks(deps, csv, sheets)

    deliver(state_of(run), wired)
    wired.runs.set_status(run.run_id, RunStatus.DELIVERY_PENDING_RETRY)

    sheets.fail_with = None
    summary = retry_deliveries(wired)

    assert summary.completed == [run.run_id]
    assert wired.runs.get(run.run_id).status is RunStatus.DELIVERED


def test_a_retry_does_not_resend_what_worked(deps: Deps, tmp_path: Path) -> None:
    """Re-sending to a sink that already succeeded would append a second row for
    one decision, and a recruiter would have to work out which is real."""
    run = approved(deps)
    csv = CsvSink(tmp_path / "results.csv")
    sheets = RecordingSink(sink_id="sheets", fail_with=AdapterError.TRANSIENT)
    wired = with_sinks(deps, csv, sheets)

    deliver(state_of(run), wired)
    wired.runs.set_status(run.run_id, RunStatus.DELIVERY_PENDING_RETRY)
    rows_before = csv.path.read_text(encoding="utf-8").count("\n")

    sheets.fail_with = None
    retry_deliveries(wired)

    assert csv.path.read_text(encoding="utf-8").count("\n") == rows_before


def test_a_retry_that_still_fails_says_so(deps: Deps, tmp_path: Path) -> None:
    run = approved(deps)
    wired = with_sinks(
        deps,
        CsvSink(tmp_path / "results.csv"),
        RecordingSink(sink_id="sheets", fail_with=AdapterError.TRANSIENT),
    )

    deliver(state_of(run), wired)
    wired.runs.set_status(run.run_id, RunStatus.DELIVERY_PENDING_RETRY)
    summary = retry_deliveries(wired)

    assert summary.still_failing
    assert wired.runs.get(run.run_id).status is RunStatus.DELIVERY_PENDING_RETRY


def test_nothing_pending_is_not_an_error(deps: Deps) -> None:
    summary = retry_deliveries(deps)

    assert summary.attempted == 0
    assert "Nothing was waiting" in summary.sentence()


def test_the_summary_reads_as_a_sentence(deps: Deps, tmp_path: Path) -> None:
    run = approved(deps)
    wired = with_sinks(deps, CsvSink(tmp_path / "results.csv"))
    deliver(state_of(run), wired)
    wired.runs.set_status(run.run_id, RunStatus.DELIVERY_PENDING_RETRY)

    summary = retry_deliveries(wired)

    assert summary.sentence().endswith(".")
    assert "completed" in summary.sentence()
