"""What happens after approval in demo mode, how approved runs get sent, and
what retention removes.

Three use cases that share a fixture: an approved run in a real database.

Demo mode: the run stays approved, says why nothing was sent, and is never
queued for a retry — because with no sinks configured, "nothing outstanding"
would otherwise read as "everything delivered".

Deliver: approval is a decision; sending is a separate act on it, and it goes
through the same DELIVER node as everything else, so the approval gate is
checked in one place.

Purge: finished runs older than the retention period go, with their rows and
their bytes — except bytes another run still refers to.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from application.deps import Deps
from application.use_cases.deliver_approved import deliver_approved
from application.use_cases.purge import purge
from application.use_cases.retry_deliveries import retry_deliveries
from application.use_cases.submit_review import submit_review
from domain.contracts.documents import CandidateDocument
from domain.contracts.enums import DocumentRole, ReviewAction, RunStatus
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.integrations.csv_sink import CsvSink
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline.deliver import DEMO_MODE
from tests.workflow.conftest_review import seed_run, wire


class FrozenClock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


def _deps(tmp_path: Path, **overrides: object) -> Deps:
    settings = settings_from_env(
        db_path=str(tmp_path / "flow.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        reviewer_id="rec-014",
        **overrides,
    )
    return wire(build_deps(settings))


@pytest.fixture
def real(tmp_path: Path) -> Iterator[Deps]:
    deps = _deps(tmp_path)
    yield Deps(**{**deps.__dict__, "sinks": (CsvSink(tmp_path / "results.csv"),)})
    close_thread_connection(deps.settings.db_path)


@pytest.fixture
def demo(tmp_path: Path) -> Iterator[Deps]:
    deps = _deps(tmp_path, demo_mode=True)
    yield deps
    close_thread_connection(deps.settings.db_path)


def approved(deps: Deps):
    run = seed_run(deps)
    submit_review(run.run_id, ReviewAction.APPROVE, deps)
    return run.run_id


# --- demo mode after approval ---------------------------------------------------------


def test_an_approved_run_in_demo_mode_stays_approved(demo: Deps) -> None:
    run_id = approved(demo)

    summary = deliver_approved(demo)

    assert demo.runs.get(run_id).status is RunStatus.APPROVED
    assert summary.delivered == []
    assert summary.pending_retry == []


def test_demo_mode_says_why_nothing_was_sent(demo: Deps) -> None:
    run_id = approved(demo)

    summary = deliver_approved(demo)

    assert [reason for _, reason in summary.skipped] == [
        "Demo mode is on, so nothing is sent anywhere."
    ]
    assert run_id in [run for run, _ in summary.skipped]


def test_demo_mode_writes_no_delivery_record(demo: Deps) -> None:
    """Not a failed delivery. Nothing was attempted, so nothing is recorded as
    attempted — a failure row would put a retry in the queue."""
    run_id = approved(demo)

    deliver_approved(demo)

    assert demo.deliveries.for_run(run_id) == []


def test_demo_mode_leaves_a_trace_in_the_events(demo: Deps) -> None:
    """The run's own record says delivery was skipped and why."""
    run_id = approved(demo)

    deliver_approved(demo)

    nodes = [row[1] for row in demo.events.for_run(run_id)]
    assert "DELIVER" in nodes


def test_a_retry_in_demo_mode_never_marks_delivered(demo: Deps) -> None:
    """The bug this guards: with no sinks, "no sink outstanding" would read as
    "all sinks done" and a retry would mark a run delivered that was never
    sent anywhere."""
    run_id = approved(demo)
    demo.runs.set_status(run_id, RunStatus.DELIVERY_PENDING_RETRY)

    summary = retry_deliveries(demo)

    assert demo.runs.get(run_id).status is RunStatus.DELIVERY_PENDING_RETRY
    assert summary.completed == []
    assert [reason for _, reason in summary.refused] == [DEMO_MODE]


# --- deliver approved -----------------------------------------------------------------------


def test_an_approved_run_is_delivered(real: Deps, tmp_path: Path) -> None:
    run_id = approved(real)

    summary = deliver_approved(real)

    assert summary.delivered == [run_id]
    assert real.runs.get(run_id).status is RunStatus.DELIVERED
    assert (tmp_path / "results.csv").exists()


def test_only_approved_runs_are_considered(real: Deps) -> None:
    """A run waiting for review is not sent, whatever else is true of it."""
    waiting = seed_run(real)

    summary = deliver_approved(real)

    assert summary.attempted == 0
    assert real.runs.get(waiting.run_id).status is RunStatus.READY_FOR_REVIEW


def test_a_rejected_run_is_never_sent(real: Deps) -> None:
    run = seed_run(real)
    submit_review(run.run_id, ReviewAction.REJECT, real)

    summary = deliver_approved(real)

    assert summary.attempted == 0
    assert real.runs.get(run.run_id).status is RunStatus.REJECTED


def test_delivery_is_idempotent(real: Deps) -> None:
    """Running it twice sends once."""
    approved(real)
    deliver_approved(real)

    second = deliver_approved(real)

    assert second.attempted == 0


def test_the_summary_is_a_sentence(real: Deps) -> None:
    approved(real)

    sentence = deliver_approved(real).sentence()

    assert sentence.endswith(".")
    assert "1 sent" in sentence


# --- purge ---------------------------------------------------------------------------------------


def _finished(deps: Deps, *, days_ago: int, sha: str) -> object:
    """A finished run with one document, finished ``days_ago`` days ago."""
    then = datetime.now(UTC) - timedelta(days=days_ago)
    run = seed_run(deps, started_at=then - timedelta(minutes=5))
    submit_review(run.run_id, ReviewAction.REJECT, deps)
    finished = deps.runs.get(run.run_id)
    deps.runs.update(
        finished.model_copy(update={"finished_at": then}), expected_version=finished.version
    )
    deps.candidates.add_document(
        CandidateDocument(
            document_id=uuid4(),
            candidate_id=run.candidate_id,
            original_filename="cv.txt",
            document_sha256=sha,
            mime_type="text/plain",
            size_bytes=3,
            page_count=1,
            blob_path=str(deps.blobs.path_for(sha)),
            doc_role=DocumentRole.CV,
            received_at=datetime.now(UTC),
        ),
        run_id=run.run_id,
    )
    return run.run_id


def test_a_run_past_retention_is_removed_with_its_bytes(real: Deps) -> None:
    sha = real.blobs.put(b"old")
    run_id = _finished(real, days_ago=45, sha=sha)

    summary = purge(real, retention_days=30, dry_run=False)

    assert summary.runs == [run_id]
    assert real.runs.get(run_id) is None
    assert not real.blobs.exists(sha)
    assert summary.blobs_removed == 1


def test_a_recent_run_is_kept(real: Deps) -> None:
    sha = real.blobs.put(b"new")
    run_id = _finished(real, days_ago=3, sha=sha)

    summary = purge(real, retention_days=30, dry_run=False)

    assert summary.runs == []
    assert real.runs.get(run_id) is not None
    assert real.blobs.exists(sha)


def test_an_open_run_is_never_purged(real: Deps) -> None:
    """Old is about finishing, not starting. A run somebody is still looking
    at is not old, whenever it began."""
    waiting = seed_run(real)
    old = waiting.model_copy(update={"started_at": datetime.now(UTC) - timedelta(days=400)})
    real.runs.update(old, expected_version=waiting.version)

    purge(real, retention_days=30, dry_run=False)

    assert real.runs.get(waiting.run_id) is not None


def test_bytes_another_run_still_uses_are_kept(real: Deps) -> None:
    """The same CV uploaded for two roles is stored once. Purging the older run
    must not remove the newer run's document."""
    sha = real.blobs.put(b"shared")
    old = _finished(real, days_ago=60, sha=sha)
    recent = _finished(real, days_ago=1, sha=sha)

    summary = purge(real, retention_days=30, dry_run=False)

    assert summary.runs == [old]
    assert real.runs.get(recent) is not None
    assert real.blobs.exists(sha)
    assert summary.blobs_kept == 1


def test_dry_run_removes_nothing(real: Deps) -> None:
    sha = real.blobs.put(b"old")
    run_id = _finished(real, days_ago=45, sha=sha)

    summary = purge(real, retention_days=30, dry_run=True)

    assert summary.runs == [run_id]
    assert real.runs.get(run_id) is not None
    assert real.blobs.exists(sha)
    assert "Would remove" in summary.sentence()


def test_dependent_rows_go_with_the_run(real: Deps) -> None:
    """Evidence, the decision, the assessments. One statement, cascading, so a
    purge does not have to remember every table."""
    sha = real.blobs.put(b"old")
    run_id = _finished(real, days_ago=45, sha=sha)
    assert real.reviews.get_for_run(run_id) is not None

    purge(real, retention_days=30, dry_run=False)

    assert real.reviews.get_for_run(run_id) is None
    assert real.evidence.assessments_for_run(run_id) == []
    assert real.candidates.documents_for_run(run_id) == []


def test_a_zero_retention_is_refused(real: Deps) -> None:
    """Retention of zero days would purge everything that ever finished, and
    it is exactly the typo somebody makes."""
    with pytest.raises(ValueError):
        purge(real, retention_days=0, dry_run=False)


def test_the_clock_is_the_injected_one(tmp_path: Path) -> None:
    """Retention is measured against the system's clock, not the wall's, so a
    test can move time and an operator's timezone cannot move a cutoff."""
    deps = _deps(tmp_path)
    try:
        sha = deps.blobs.put(b"x")
        run_id = _finished(deps, days_ago=10, sha=sha)
        later = Deps(
            **{**deps.__dict__, "clock": FrozenClock(datetime.now(UTC) + timedelta(days=25))}
        )

        summary = purge(later, retention_days=30, dry_run=True)

        assert summary.runs == [run_id]
    finally:
        close_thread_connection(deps.settings.db_path)
