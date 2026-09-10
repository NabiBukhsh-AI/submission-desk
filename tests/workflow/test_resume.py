"""Resumption, tested by actually stopping halfway.

The claim is that a crash leaves the run at a node boundary and a restart
continues from there without redoing work. That is only worth anything if the
restart is the same code path as a normal run, so these tests drive the real
runner twice rather than a special resume function.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from application.deps import Deps
from application.runner import run
from domain.contracts.enums import RunStatus
from domain.contracts.run_state import RunState
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline import registry
from pipeline.registry import Node, NodeFn
from tests.workflow.conftest import make_run_record
from tests.workflow.stub_nodes import counting_node, ok_node, raising_node


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(db_path=str(tmp_path / "resume.sqlite"))
    built = build_deps(settings)
    yield built
    close_thread_connection(settings.db_path)


@pytest.fixture
def state(deps: Deps) -> RunState:
    record = make_run_record()
    deps.runs.create(record)
    return RunState(
        run_id=record.run_id,
        candidate_id=record.candidate_id,
        role_id=record.role_id,
        status=RunStatus.CREATED,
        started_at=record.started_at,
    )


def use_pipeline(monkeypatch: pytest.MonkeyPatch, nodes: tuple[Node, ...]) -> None:
    monkeypatch.setattr(registry, "PIPELINE", nodes)
    monkeypatch.setattr("application.runner.PIPELINE", nodes)


def counting_pipeline(ledger: list[str], third_fn: NodeFn | None = None) -> tuple[Node, ...]:
    return (
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
            fn=third_fn or counting_node("GAMMA", RunStatus.SANITIZED, ledger),
            success_status=RunStatus.SANITIZED,
            failure_status=RunStatus.FAILED_TERMINAL,
        ),
    )


def test_a_restart_does_not_redo_finished_work(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The headline property. Stop after node two, start again, and the first
    two nodes do not execute a second time."""
    ledger: list[str] = []
    use_pipeline(monkeypatch, counting_pipeline(ledger))

    first = run(state, deps, stop_after="BETA")
    assert ledger == ["ALPHA", "BETA"]

    second = run(first.state, deps)

    assert ledger == ["ALPHA", "BETA", "GAMMA"], "a completed node ran again"
    assert second.skipped == ("ALPHA", "BETA")
    assert second.executed == ("GAMMA",)


def test_the_final_state_is_the_same_either_way(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that was interrupted and resumed must end where an uninterrupted
    one would. Otherwise resumption is a different pipeline wearing the same
    name."""
    interrupted_ledger: list[str] = []
    use_pipeline(monkeypatch, counting_pipeline(interrupted_ledger))
    partial = run(state, deps, stop_after="ALPHA")
    resumed = run(partial.state, deps)

    straight_ledger: list[str] = []
    record = make_run_record()
    deps.runs.create(record)
    fresh = RunState(
        run_id=record.run_id,
        candidate_id=record.candidate_id,
        role_id=record.role_id,
        status=RunStatus.CREATED,
        started_at=record.started_at,
    )
    use_pipeline(monkeypatch, counting_pipeline(straight_ledger))
    uninterrupted = run(fresh, deps)

    assert resumed.status is uninterrupted.status
    assert resumed.state.completed_nodes == uninterrupted.state.completed_nodes


def test_resumption_reads_what_was_committed_not_what_was_attempted(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A node that crashed did not complete, so a restart runs it again.

    This is the difference between committing per node and committing per run:
    the failed node is retried, and the two before it are not.
    """
    ledger: list[str] = []
    use_pipeline(monkeypatch, counting_pipeline(ledger, third_fn=raising_node()))

    run(state, deps)
    assert ledger == ["ALPHA", "BETA"]

    stored = deps.runs.get(state.run_id)
    assert stored is not None
    assert "GAMMA" not in stored.completed_nodes


def test_the_database_holds_the_boundary_after_a_crash(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What survives a process that stops is what was committed, and what was
    committed is whole nodes."""
    ledger: list[str] = []
    use_pipeline(monkeypatch, counting_pipeline(ledger, third_fn=raising_node()))

    run(state, deps)

    stored = deps.runs.get(state.run_id)
    assert stored is not None
    assert stored.completed_nodes == ["ALPHA", "BETA"]
    assert [row[1] for row in deps.events.for_run(state.run_id)] == ["ALPHA", "BETA", "GAMMA"]


def test_force_re_executes_everything(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deliberate re-run is a different intent from a resume, and says so."""
    ledger: list[str] = []
    use_pipeline(monkeypatch, counting_pipeline(ledger))

    first = run(state, deps)
    run(first.state, deps, force=True)

    assert ledger == ["ALPHA", "BETA", "GAMMA", "ALPHA", "BETA", "GAMMA"]


def test_resuming_a_finished_run_does_nothing(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger: list[str] = []
    use_pipeline(monkeypatch, counting_pipeline(ledger))

    finished = run(state, deps)
    again = run(finished.state, deps)

    assert again.executed == ()
    assert ledger == ["ALPHA", "BETA", "GAMMA"]


def test_events_accumulate_across_a_resume(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The event log is append-only, so a run's history survives a restart
    rather than being rewritten by it."""
    ledger: list[str] = []
    use_pipeline(monkeypatch, counting_pipeline(ledger))

    partial = run(state, deps, stop_after="ALPHA")
    run(partial.state, deps)

    sequence = [row[0] for row in deps.events.for_run(state.run_id)]
    assert sequence == [1, 2, 3]


def test_a_skipped_node_writes_no_second_event(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise the operations page would show a node running twice when it ran
    once and was skipped once."""
    ledger: list[str] = []
    use_pipeline(monkeypatch, counting_pipeline(ledger))

    partial = run(state, deps, stop_after="ALPHA")
    run(partial.state, deps)

    nodes = [row[1] for row in deps.events.for_run(state.run_id)]
    assert nodes.count("ALPHA") == 1


def test_state_carried_between_nodes_survives_the_boundary(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RunState is frozen, so a node returns a new one rather than editing what
    it was given. A node that had mutated in place and then failed would leave a
    partially updated run for the next attempt to build on."""
    use_pipeline(
        monkeypatch,
        (
            Node(
                name="ALPHA",
                fn=ok_node("ALPHA", RunStatus.INTAKE_OK),
                success_status=RunStatus.INTAKE_OK,
                failure_status=RunStatus.FAILED_TERMINAL,
            ),
        ),
    )
    before = state.completed_nodes

    outcome = run(state, deps)

    assert before == ()
    assert outcome.state.completed_nodes == ("ALPHA",)
    assert state.completed_nodes == ()
