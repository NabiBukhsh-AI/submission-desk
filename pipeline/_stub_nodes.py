"""Nodes that exercise the runner before the real ones exist.

Temporary. Each is replaced as its phase lands, and this module is deleted once
the last real node arrives in Phase 13. It is here so that resumption, the event
log, and failure handling can be tested end to end today rather than being
assumed to work until the pipeline is complete.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from application.deps import Deps
from domain.contracts.enums import RunStatus
from domain.contracts.errors import ErrorRecord
from domain.contracts.run_state import DomainEvent, NodeResult, NodeStatus, RunState
from pipeline.registry import NodeFn


def ok_node(name: str, status: RunStatus) -> NodeFn:
    """A node that succeeds and records that it did."""

    def node(state: RunState, deps: Deps) -> NodeResult:
        return NodeResult(
            state=state,
            status=NodeStatus.OK,
            events=(DomainEvent(name=f"{name.lower()}_completed"),),
            next_status=status,
        )

    return node


def degraded_node(name: str, status: RunStatus, reason: str) -> NodeFn:
    """A node that produced something usable but incomplete.

    Neither a success to be ignored nor a failure to route around: the run
    continues and the reviewer is told.
    """

    def node(state: RunState, deps: Deps) -> NodeResult:
        return NodeResult(
            state=state,
            status=NodeStatus.DEGRADED,
            events=(DomainEvent(name=f"{name.lower()}_degraded", payload={"reason": reason}),),
            next_status=status,
        )

    return node


def failing_node(error_code: str = "STUB_FAILURE") -> NodeFn:
    """A node that fails properly: it returns, it does not raise."""

    def node(state: RunState, deps: Deps) -> NodeResult:
        return NodeResult(
            state=state,
            status=NodeStatus.FAILED,
            error=ErrorRecord(
                error_id=uuid4(),
                run_id=state.run_id,
                node="STUB",
                error_code=error_code,
                error_class="StubFailure",
                message_redacted="the stub node was asked to fail",
                retryable=False,
                attempt=1,
                occurred_at=datetime.now(UTC),
            ),
        )

    return node


def raising_node(message: str = "something nobody anticipated") -> NodeFn:
    """A node that breaks its contract by raising.

    The runner is expected to catch this and convert it, so that one badly
    behaved node cannot take down a run.
    """

    def node(state: RunState, deps: Deps) -> NodeResult:
        raise RuntimeError(message)

    return node


def counting_node(name: str, status: RunStatus, ledger: list[str]) -> NodeFn:
    """Records each execution, so a test can prove a node did not run twice."""

    def node(state: RunState, deps: Deps) -> NodeResult:
        ledger.append(name)
        return NodeResult(state=state, status=NodeStatus.OK, next_status=status)

    return node
