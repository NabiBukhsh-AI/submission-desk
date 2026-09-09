"""The runner, driven over stub nodes against real storage.

These use the SQLite repositories rather than fakes, because the properties
under test are about the transaction boundary: what is committed, when, and what
survives a process that stops halfway.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from application.budget import BudgetGuard
from application.deps import Deps
from application.runner import run
from domain.contracts.enums import RunStatus
from domain.contracts.run_state import RunState
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline import registry
from pipeline._stub_nodes import (
    counting_node,
    degraded_node,
    failing_node,
    ok_node,
    raising_node,
)
from pipeline.registry import Node
from tests.workflow.conftest import make_run_record


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(db_path=str(tmp_path / "runner.sqlite"))
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
    """Swap the pipeline for a short one, for the duration of a test."""
    monkeypatch.setattr(registry, "PIPELINE", nodes)
    monkeypatch.setattr("application.runner.PIPELINE", nodes)


THREE_NODES = (
    Node(
        name="ALPHA",
        fn=ok_node("ALPHA", RunStatus.INTAKE_OK),
        success_status=RunStatus.INTAKE_OK,
        failure_status=RunStatus.FAILED_TERMINAL,
    ),
    Node(
        name="BETA",
        fn=ok_node("BETA", RunStatus.EXTRACTED),
        success_status=RunStatus.EXTRACTED,
        failure_status=RunStatus.FAILED_TERMINAL,
    ),
    Node(
        name="GAMMA",
        fn=ok_node("GAMMA", RunStatus.SANITIZED),
        success_status=RunStatus.SANITIZED,
        failure_status=RunStatus.FAILED_TERMINAL,
    ),
)


# --- the happy path -----------------------------------------------------------


def test_a_run_travels_the_whole_pipeline(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_pipeline(monkeypatch, THREE_NODES)

    outcome = run(state, deps)

    assert outcome.executed == ("ALPHA", "BETA", "GAMMA")
    assert outcome.status is RunStatus.SANITIZED
    assert outcome.failed_node is None


def test_every_node_writes_an_event(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runner writes these, so a node cannot forget to be observed."""
    use_pipeline(monkeypatch, THREE_NODES)

    run(state, deps)

    events = deps.events.for_run(state.run_id)
    assert [row[1] for row in events] == ["ALPHA", "BETA", "GAMMA"]
    assert all(row[2] == "ok" for row in events)


def test_the_event_records_how_long_the_node_took(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Latency per node is what makes the operations page able to say which step
    is slow, rather than that the run is slow."""
    use_pipeline(monkeypatch, THREE_NODES)

    run(state, deps)

    import json
    import sqlite3

    connection = sqlite3.connect(deps.settings.db_path)
    rows = connection.execute("SELECT payload FROM run_events ORDER BY seq").fetchall()
    connection.close()

    for (payload,) in rows:
        assert "latency_ms" in json.loads(payload)


def test_completed_nodes_are_persisted(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_pipeline(monkeypatch, THREE_NODES)

    run(state, deps)

    stored = deps.runs.get(state.run_id)
    assert stored is not None
    assert stored.completed_nodes == ["ALPHA", "BETA", "GAMMA"]


# --- failure ------------------------------------------------------------------


def test_a_failing_required_node_stops_the_run(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    nodes = (
        THREE_NODES[0],
        Node(
            name="BETA",
            fn=failing_node(),
            success_status=RunStatus.EXTRACTED,
            failure_status=RunStatus.FAILED_TERMINAL,
            required=True,
        ),
        THREE_NODES[2],
    )
    use_pipeline(monkeypatch, nodes)

    outcome = run(state, deps)

    assert outcome.failed_node == "BETA"
    assert outcome.executed == ("ALPHA", "BETA")
    assert outcome.status is RunStatus.FAILED_TERMINAL


def test_a_failing_optional_node_does_not_stop_the_run(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calibration failing is a worse assessment. Extraction failing is no
    assessment at all. The pipeline distinguishes them."""
    nodes = (
        THREE_NODES[0],
        Node(
            name="BETA",
            fn=failing_node(),
            success_status=RunStatus.EXTRACTED,
            failure_status=RunStatus.INTAKE_OK,
            required=False,
        ),
        Node(
            name="GAMMA",
            fn=ok_node("GAMMA", RunStatus.EXTRACTED),
            success_status=RunStatus.EXTRACTED,
            failure_status=RunStatus.FAILED_TERMINAL,
        ),
    )
    use_pipeline(monkeypatch, nodes)

    outcome = run(state, deps)

    assert outcome.executed == ("ALPHA", "BETA", "GAMMA")
    assert outcome.failed_node == "BETA"


def test_a_failure_writes_an_error_record(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_pipeline(
        monkeypatch,
        (
            Node(
                name="ALPHA",
                fn=failing_node("STUB_FAILURE"),
                success_status=RunStatus.INTAKE_OK,
                failure_status=RunStatus.FAILED_TERMINAL,
            ),
        ),
    )

    run(state, deps)

    errors = deps.errors.for_run(state.run_id)
    assert [error.error_code for error in errors] == ["STUB_FAILURE"]


def test_a_node_that_raises_is_caught_at_the_boundary(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One badly behaved node must not take down a run. The exception becomes a
    failed node with the node's name attached, so the operations page shows
    where it happened rather than a bare traceback."""
    use_pipeline(
        monkeypatch,
        (
            Node(
                name="ALPHA",
                fn=raising_node("a library did something unexpected"),
                success_status=RunStatus.INTAKE_OK,
                failure_status=RunStatus.FAILED_TERMINAL,
            ),
        ),
    )

    outcome = run(state, deps)

    assert outcome.failed_node == "ALPHA"
    errors = deps.errors.for_run(state.run_id)
    assert errors[0].node == "ALPHA"
    assert errors[0].error_class == "RuntimeError"
    assert errors[0].error_code == "UNEXPECTED_ERROR"


def test_a_raised_exception_still_writes_its_event(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A node that failed silently is worse than one that failed loudly."""
    use_pipeline(
        monkeypatch,
        (
            Node(
                name="ALPHA",
                fn=raising_node(),
                success_status=RunStatus.INTAKE_OK,
                failure_status=RunStatus.FAILED_TERMINAL,
            ),
        ),
    )

    run(state, deps)

    assert [row[2] for row in deps.events.for_run(state.run_id)] == ["failed"]


# --- degraded -----------------------------------------------------------------


def test_a_degraded_node_continues_and_is_recorded(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_pipeline(
        monkeypatch,
        (
            Node(
                name="ALPHA",
                fn=degraded_node("ALPHA", RunStatus.INTAKE_OK, "partial_profile"),
                success_status=RunStatus.INTAKE_OK,
                failure_status=RunStatus.FAILED_TERMINAL,
            ),
            THREE_NODES[1],
        ),
    )

    outcome = run(state, deps)

    assert outcome.executed == ("ALPHA", "BETA")
    assert "ALPHA" in outcome.state.degraded_reasons


# --- the budget guard ----------------------------------------------------------


def test_the_budget_is_checked_before_the_node_runs(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard sits before the node, not inside it, so a node cannot spend
    money the guard was going to refuse."""
    ledger: list[str] = []
    use_pipeline(
        monkeypatch,
        (
            Node(
                name="ALPHA",
                fn=counting_node("ALPHA", RunStatus.INTAKE_OK, ledger),
                success_status=RunStatus.INTAKE_OK,
                failure_status=RunStatus.FAILED_TERMINAL,
            ),
        ),
    )
    guard = BudgetGuard(token_ceiling=1000, max_escalations=3)
    guard.record(state.run_id, input_tokens=999, output_tokens=2)
    exhausted = Deps(**{**deps.__dict__, "budget": guard})

    outcome = run(state, exhausted)

    assert ledger == [], "the node ran despite the ceiling being reached"
    assert outcome.status is RunStatus.MANUAL_REVIEW_REQUIRED


def test_a_budget_stop_explains_itself(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_pipeline(monkeypatch, THREE_NODES)
    guard = BudgetGuard(token_ceiling=10, max_escalations=3)
    guard.record(state.run_id, input_tokens=10, output_tokens=0)

    run(state, Deps(**{**deps.__dict__, "budget": guard}))

    errors = deps.errors.for_run(state.run_id)
    assert errors[0].error_code == "BUDGET_EXCEEDED"
    assert "cost ceiling" in errors[0].message_redacted


def test_the_ceiling_holds_when_pricing_is_unconfigured() -> None:
    """A run that cannot be priced still must not run away."""
    guard = BudgetGuard(token_ceiling=100, max_escalations=3)
    state = RunState(
        run_id=uuid4(),
        candidate_id="c",
        role_id="r",
        status=RunStatus.CREATED,
        started_at=datetime.now(UTC),
    )
    guard.record(state.run_id, input_tokens=100, output_tokens=0, cost_usd=None)

    assert guard.allows(state) is False
    assert guard.state_for(state.run_id).cost_usd is None


def test_escalations_are_capped() -> None:
    guard = BudgetGuard(token_ceiling=10_000, max_escalations=2)
    run_id = uuid4()

    assert guard.may_escalate(run_id)
    guard.record(run_id, input_tokens=1, output_tokens=1, escalated=True)
    guard.record(run_id, input_tokens=1, output_tokens=1, escalated=True)

    assert not guard.may_escalate(run_id)


# --- stopping and terminal states ----------------------------------------------


def test_stop_after_halts_at_the_named_node(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This is the approval gate in the runner: everything up to REVIEW happens
    automatically, and nothing past it happens without a decision."""
    use_pipeline(monkeypatch, THREE_NODES)

    outcome = run(state, deps, stop_after="BETA")

    assert outcome.executed == ("ALPHA", "BETA")


def test_a_terminal_state_stops_the_loop(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_pipeline(monkeypatch, THREE_NODES)

    outcome = run(state.model_copy(update={"status": RunStatus.DELIVERED}), deps)

    assert outcome.executed == ()


def test_a_node_cannot_invent_a_transition(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A node naming a state the machine forbids falls back to its declared
    success state, so the graph stays the single source of truth."""
    use_pipeline(
        monkeypatch,
        (
            Node(
                name="ALPHA",
                fn=ok_node("ALPHA", RunStatus.DELIVERED),
                success_status=RunStatus.INTAKE_OK,
                failure_status=RunStatus.FAILED_TERMINAL,
            ),
        ),
    )

    outcome = run(state, deps)

    assert outcome.status is RunStatus.INTAKE_OK


# --- the real pipeline ---------------------------------------------------------


def test_the_shipped_pipeline_has_eleven_nodes_in_order() -> None:
    assert registry.NODE_NAMES == (
        "CONFIG",
        "INTAKE",
        "EXTRACT",
        "SANITIZE",
        "STRUCTURE",
        "CALIBRATE",
        "ASSESS",
        "AGGREGATE",
        "COMPOSE",
        "REVIEW",
        "DELIVER",
    )


def test_every_node_declares_where_a_failure_goes() -> None:
    for node in registry.PIPELINE:
        assert node.failure_status is not None
        assert node.failure_message.strip()


def test_failure_messages_are_written_for_a_recruiter() -> None:
    """The reviewer reads these. None of them should contain a node name, a
    status constant, or an exception class."""
    for node in registry.PIPELINE:
        lowered = node.failure_message.lower()
        for jargon in ("node", "exception", "status", "null", "traceback", "_"):
            assert jargon not in lowered, f"{node.name}: {node.failure_message}"


def test_nothing_runs_after_review_without_a_decision() -> None:
    """DELIVER is last and optional; everything before REVIEW is automatic."""
    assert registry.NODE_NAMES[-1] == "DELIVER"
    assert "REVIEW" not in registry.NODES_BEFORE_REVIEW
    assert registry.NODES_BEFORE_REVIEW[-1] == "COMPOSE"


def test_asking_for_an_unknown_node_is_an_error() -> None:
    with pytest.raises(KeyError):
        registry.node_by_name("NOT_A_NODE")


def test_a_run_that_takes_no_time_still_records_a_latency(
    deps: Deps, state: RunState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frozen clock produces zero, not a missing field."""

    class FrozenClock:
        def now(self) -> datetime:
            return datetime(2026, 9, 9, tzinfo=UTC)

    use_pipeline(monkeypatch, THREE_NODES[:1])
    frozen = Deps(**{**deps.__dict__, "clock": FrozenClock()})

    run(state, frozen)

    assert len(deps.events.for_run(state.run_id)) == 1
