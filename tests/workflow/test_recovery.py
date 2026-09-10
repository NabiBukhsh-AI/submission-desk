"""Startup reconciliation.

A process killed between nodes leaves a run in a working state with no process
behind it. Without reconciliation that run sits in the queue forever and nothing
a recruiter can do will clear it, which is a worse failure than the crash.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from application.deps import Deps
from application.recovery import reconcile, state_for_resume
from application.runner import run
from domain.contracts.enums import RunStatus
from domain.contracts.run_state import RunState
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline import registry
from pipeline.registry import Node
from tests.workflow.conftest import make_run_record
from tests.workflow.stub_nodes import counting_node

LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(db_path=str(tmp_path / "recovery.sqlite"))
    built = build_deps(settings)
    yield built
    close_thread_connection(settings.db_path)


def use_pipeline(monkeypatch: pytest.MonkeyPatch, nodes: tuple[Node, ...]) -> None:
    monkeypatch.setattr(registry, "PIPELINE", nodes)
    monkeypatch.setattr("application.runner.PIPELINE", nodes)


def test_a_stalled_run_is_marked_interrupted(deps: Deps) -> None:
    record = make_run_record(status=RunStatus.EXTRACTED, started_at=LONG_AGO)
    deps.runs.create(record)

    result = reconcile(deps)

    assert result.count == 1
    stored = deps.runs.get(record.run_id)
    assert stored is not None
    assert stored.status is RunStatus.INTERRUPTED


def test_a_run_a_reviewer_could_act_on_is_left_alone(deps: Deps) -> None:
    """Reconciliation is conservative. A run waiting for a person is not stalled,
    however long it has been waiting."""
    for status in (
        RunStatus.READY_FOR_REVIEW,
        RunStatus.NEEDS_REVIEW,
        RunStatus.QUARANTINED,
        RunStatus.APPROVED,
    ):
        deps.runs.create(make_run_record(status=status, started_at=LONG_AGO))

    assert reconcile(deps).count == 0


def test_a_terminal_run_is_left_alone(deps: Deps) -> None:
    for status in (RunStatus.DELIVERED, RunStatus.REJECTED, RunStatus.FAILED_TERMINAL):
        deps.runs.create(make_run_record(status=status, started_at=LONG_AGO))

    assert reconcile(deps).count == 0


def test_a_recent_run_is_left_alone(deps: Deps) -> None:
    """A run that started thirty seconds ago is working, not stalled."""
    deps.runs.create(make_run_record(status=RunStatus.EXTRACTED, started_at=datetime.now(UTC)))

    assert reconcile(deps).count == 0


def test_reconciliation_is_safe_to_run_twice(deps: Deps) -> None:
    """Both the command line and the interface call it at startup."""
    deps.runs.create(make_run_record(status=RunStatus.EXTRACTED, started_at=LONG_AGO))

    assert reconcile(deps).count == 1
    assert reconcile(deps).count == 0


def test_the_reason_is_recorded_not_just_the_status(deps: Deps) -> None:
    """A run that changed state with no explanation is a run nobody can debug."""
    record = make_run_record(status=RunStatus.EXTRACTED, started_at=LONG_AGO)
    deps.runs.create(record)

    reconcile(deps)

    events = deps.events.for_run(record.run_id)
    assert [row[1] for row in events] == ["RECOVERY"]


def test_an_interrupted_run_resumes_from_where_it_stopped(
    deps: Deps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point. Reconcile, rebuild the state, and continue without
    redoing the nodes that already committed."""
    ledger: list[str] = []
    nodes = (
        Node(
            name="ALPHA",
            fn=counting_node("ALPHA", RunStatus.INTAKE_OK, ledger),
            success_status=RunStatus.INTAKE_OK,
            failure_status=RunStatus.FAILED_TERMINAL,
        ),
        Node(
            name="BETA",
            fn=counting_node("BETA", RunStatus.EXTRACTED, ledger),
            success_status=RunStatus.EXTRACTED,
            failure_status=RunStatus.FAILED_TERMINAL,
        ),
        Node(
            name="GAMMA",
            fn=counting_node("GAMMA", RunStatus.SANITIZED, ledger),
            success_status=RunStatus.SANITIZED,
            failure_status=RunStatus.FAILED_TERMINAL,
        ),
    )
    use_pipeline(monkeypatch, nodes)

    record = make_run_record(started_at=LONG_AGO)
    deps.runs.create(record)
    state = RunState(
        run_id=record.run_id,
        candidate_id=record.candidate_id,
        role_id=record.role_id,
        status=RunStatus.CREATED,
        started_at=record.started_at,
    )
    run(state, deps, stop_after="ALPHA")
    ledger.clear()

    reconcile(deps)
    resumed_state = state_for_resume(deps, record.run_id)
    assert resumed_state is not None

    outcome = run(resumed_state, deps)

    assert ledger == ["BETA", "GAMMA"], "reconciliation lost or repeated work"
    assert outcome.status is RunStatus.SANITIZED


def test_the_recovery_marker_does_not_count_as_a_pipeline_node(deps: Deps) -> None:
    """RECOVERY is bookkeeping, not a stage. If it stayed in completed_nodes the
    resumed run would look like it had finished a node that does not exist."""
    record = make_run_record(status=RunStatus.EXTRACTED, started_at=LONG_AGO)
    deps.runs.create(record)
    deps.runs.mark_node_complete(record.run_id, "CONFIG", RunStatus.CREATED)

    reconcile(deps)
    state = state_for_resume(deps, record.run_id)

    assert state is not None
    assert "RECOVERY" not in state.completed_nodes
    assert "CONFIG" in state.completed_nodes


def test_an_interrupted_run_resumes_as_created(deps: Deps) -> None:
    """INTERRUPTED is not a state the pipeline runs from; it transitions to
    CREATED, which is the one backward edge the state machine allows."""
    record = make_run_record(status=RunStatus.EXTRACTED, started_at=LONG_AGO)
    deps.runs.create(record)
    reconcile(deps)

    state = state_for_resume(deps, record.run_id)

    assert state is not None
    assert state.status is RunStatus.CREATED


def test_rebuilding_an_unknown_run_returns_nothing(deps: Deps) -> None:
    from uuid import uuid4

    assert state_for_resume(deps, uuid4()) is None


def test_reconciliation_reports_what_it_touched(deps: Deps) -> None:
    """Startup prints this. A silent recovery is a recovery nobody noticed."""
    first = make_run_record(status=RunStatus.EXTRACTED, started_at=LONG_AGO)
    second = make_run_record(status=RunStatus.ASSESSED, started_at=LONG_AGO)
    deps.runs.create(first)
    deps.runs.create(second)

    result = reconcile(deps)

    assert set(result.interrupted) == {str(first.run_id), str(second.run_id)}
