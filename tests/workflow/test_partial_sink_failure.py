"""An unapproved run cannot be delivered, even by calling the adapter directly.

This is the control that survives every other one being wrong. The state machine
forbids reaching DELIVERED except from APPROVED, and the runner stops before
delivery without a decision — but both of those are things the code does, and
both could be got wrong by an edit that looked reasonable.

So delivery checks the decision table itself. A status is a column anything with
write access can set; a decision is a row that ``submit_review`` had to create,
which means a person chose it. An APPROVED status with no decision behind it
still sends nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from application.deps import Deps
from application.use_cases.retry_deliveries import retry_deliveries
from application.use_cases.submit_review import submit_review
from domain.contracts.enums import ReviewAction, RunStatus
from domain.contracts.run_state import NodeStatus, RunState
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline.deliver import NOT_APPROVED, refuse_reason
from pipeline.deliver import node as deliver
from tests.fakes.fake_transports import RecordingSink
from tests.workflow.conftest_review import seed_run, wire


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "gate.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        reviewer_id="rec-014",
    )
    yield wire(build_deps(settings))
    close_thread_connection(settings.db_path)


def state_of(run) -> RunState:
    return RunState(
        run_id=run.run_id,
        candidate_id=run.candidate_id,
        role_id=run.role_id,
        status=run.status,
        started_at=run.started_at,
        integrity_tier=run.integrity_tier,
    )


def with_sink(deps: Deps, sink) -> Deps:
    return Deps(**{**deps.__dict__, "sinks": (sink,)})


# --- the re-assertion --------------------------------------------------------------


def test_an_unreviewed_run_is_refused(deps: Deps) -> None:
    """Called directly, bypassing the runner and the interface."""
    run = seed_run(deps)
    sink = RecordingSink()

    result = deliver(state_of(run), with_sink(deps, sink))

    assert result.status is NodeStatus.FAILED
    assert sink.delivered == []


def test_a_status_set_by_hand_is_not_enough(deps: Deps) -> None:
    """The failure this control exists for. Somebody, or something, sets the
    status to APPROVED without a decision behind it."""
    run = seed_run(deps)
    deps.runs.set_status(run.run_id, RunStatus.APPROVED)
    forged = deps.runs.get(run.run_id)
    sink = RecordingSink()

    result = deliver(state_of(forged), with_sink(deps, sink))

    assert result.status is NodeStatus.FAILED
    assert result.error.error_code == "NOT_APPROVED"
    assert sink.delivered == []


def test_a_rejected_run_is_not_delivered(deps: Deps) -> None:
    """A decision exists. It is the wrong one."""
    run = seed_run(deps)
    submit_review(run.run_id, ReviewAction.REJECT, deps)
    deps.runs.set_status(run.run_id, RunStatus.APPROVED)
    sink = RecordingSink()

    result = deliver(state_of(deps.runs.get(run.run_id)), with_sink(deps, sink))

    assert result.status is NodeStatus.FAILED
    assert sink.delivered == []


def test_a_request_for_information_is_not_an_approval(deps: Deps) -> None:
    run = seed_run(deps)
    submit_review(run.run_id, ReviewAction.REQUEST_INFO, deps)
    deps.runs.set_status(run.run_id, RunStatus.APPROVED)

    refusal = refuse_reason(state_of(deps.runs.get(run.run_id)), deps)

    assert refusal == NOT_APPROVED


def test_an_approved_run_is_delivered(deps: Deps) -> None:
    """The control. A gate that refused everything would pass every test above
    while being completely broken."""
    run = seed_run(deps)
    submit_review(run.run_id, ReviewAction.APPROVE, deps)
    sink = RecordingSink()

    result = deliver(state_of(deps.runs.get(run.run_id)), with_sink(deps, sink))

    assert result.next_status is RunStatus.DELIVERED
    assert sink.delivered


def test_a_pending_retry_is_still_deliverable(deps: Deps) -> None:
    """A half-delivered run has already been approved. Refusing the retry would
    strand it."""
    run = seed_run(deps)
    submit_review(run.run_id, ReviewAction.APPROVE, deps)
    deps.runs.set_status(run.run_id, RunStatus.DELIVERY_PENDING_RETRY)

    assert refuse_reason(state_of(deps.runs.get(run.run_id)), deps) is None


# --- the retry path checks too --------------------------------------------------------


def test_a_retry_re_asserts_approval(deps: Deps) -> None:
    """Retries run from a schedule and from a button, neither of which passes
    through the reviewer's page. The check has to be where the work is done."""
    run = seed_run(deps)
    deps.runs.set_status(run.run_id, RunStatus.DELIVERY_PENDING_RETRY)
    sink = RecordingSink()

    summary = retry_deliveries(with_sink(deps, sink))

    assert summary.refused
    assert sink.delivered == []


def test_a_retry_of_an_approved_run_proceeds(deps: Deps) -> None:
    run = seed_run(deps)
    submit_review(run.run_id, ReviewAction.APPROVE, deps)
    deps.runs.set_status(run.run_id, RunStatus.DELIVERY_PENDING_RETRY)
    sink = RecordingSink()

    summary = retry_deliveries(with_sink(deps, sink))

    assert summary.completed == [run.run_id]
    assert sink.delivered


# --- what the reviewer reads -------------------------------------------------------------


def test_the_refusal_says_what_to_do(deps: Deps) -> None:
    run = seed_run(deps)

    result = deliver(state_of(run), with_sink(deps, RecordingSink()))

    assert "not been approved" in result.error.message_redacted
    assert "decide first" in result.error.message_redacted


def test_a_refusal_is_not_retryable(deps: Deps) -> None:
    """Trying again does not make somebody approve it."""
    run = seed_run(deps)

    result = deliver(state_of(run), with_sink(deps, RecordingSink()))

    assert result.error.retryable is False


# --- the state machine agrees ----------------------------------------------------------------


def test_delivered_is_reachable_only_from_approval() -> None:
    """Read off the transition table, so the two controls are checked
    independently rather than one confirming the other."""
    from domain.state_machine import ALLOWED

    doors = {status for status, allowed in ALLOWED.items() if RunStatus.DELIVERED in allowed}

    assert doors == {RunStatus.APPROVED, RunStatus.DELIVERY_PENDING_RETRY}


def test_the_delivery_node_is_last() -> None:
    from pipeline.registry import NODE_NAMES

    assert NODE_NAMES[-1] == "DELIVER"
