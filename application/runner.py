"""The loop.

Short on purpose. Every node has the same signature, so the runner does not know
what any of them do: it skips what already completed, checks the budget, times
the call, records what happened, commits, and moves on.

Three properties come out of that uniformity, and each is worth more than the
brevity.

*Observability is not a node's job.* The runner writes the event row, so a node
cannot forget to be observed, and adding a node adds its telemetry for free.

*Durability is not a node's job.* The runner commits after each node, so a crash
leaves the run at a node boundary and resumption skips what already finished.
A node that committed for itself could leave the run half-written.

*Failure is not an exception.* Nodes return ``FAILED`` with an error record. The
one place a raised exception is caught is here, at the boundary, where it is
logged with the node's name and converted into the same shape.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from application.deps import Deps
from domain.contracts.enums import IntegrityTier, RunStatus
from domain.contracts.errors import ErrorRecord
from domain.contracts.run_state import NodeResult, NodeStatus, RunState
from domain.state_machine import TERMINAL, can_transition
from pipeline.registry import PIPELINE, Node


@dataclass(frozen=True)
class RunOutcome:
    """What the runner did, for the caller and for the tests."""

    state: RunState
    executed: tuple[str, ...]
    skipped: tuple[str, ...]
    failed_node: str | None = None

    @property
    def status(self) -> RunStatus:
        return self.state.status


class BudgetExceeded(Exception):
    """The run would cross its ceiling. Converted to a state, never a crash."""

    error_code = "BUDGET_EXCEEDED"


def run(
    state: RunState,
    deps: Deps,
    *,
    force: bool = False,
    stop_after: str | None = None,
) -> RunOutcome:
    """Drive a run through the pipeline.

    ``force`` re-executes nodes that already completed, for a deliberate re-run.
    ``stop_after`` halts once the named node has run, which is how the reviewer
    gate works: everything up to REVIEW happens automatically, and nothing past
    it happens without a persisted decision.
    """
    executed: list[str] = []
    skipped: list[str] = []
    failed_node: str | None = None

    for node in PIPELINE:
        if state.status in TERMINAL:
            break

        if node.name in state.completed_nodes and not force:
            skipped.append(node.name)
            continue

        # The circuit breaker sits before the node, not inside it, so a node
        # cannot spend money the guard was going to refuse.
        if deps.budget is not None and not deps.budget.allows(state):
            state = _record_budget_stop(state, node, deps)
            failed_node = node.name
            break

        started = deps.clock.now()
        tier_before = state.integrity_tier
        result = _execute(node, state, deps)
        latency_ms = int((deps.clock.now() - started).total_seconds() * 1000)

        state = _commit_node(node, result, deps, latency_ms=latency_ms, tier_before=tier_before)
        executed.append(node.name)

        if result.failed:
            failed_node = node.name
            if node.required:
                break

        if stop_after is not None and node.name == stop_after:
            break

    return RunOutcome(
        state=state,
        executed=tuple(executed),
        skipped=tuple(skipped),
        failed_node=failed_node,
    )


def _execute(node: Node, state: RunState, deps: Deps) -> NodeResult:
    """Call one node, converting a raised exception into a failure.

    This is the single place in the system that catches a broad exception, and
    it does so to keep its promise: a node never takes down a run. Anything
    unexpected becomes a failed node with the node's name attached, so the
    operations page shows where it happened rather than a bare traceback.
    """
    try:
        return node.fn(state, deps)
    except Exception as error:
        return NodeResult(
            state=state,
            status=NodeStatus.FAILED,
            error=ErrorRecord(
                error_id=uuid4(),
                run_id=state.run_id,
                node=node.name,
                error_code=getattr(error, "error_code", "UNEXPECTED_ERROR"),
                error_class=type(error).__name__,
                message_redacted=_redact(str(error), deps.redactor),
                retryable=bool(getattr(error, "retryable", False)),
                attempt=1,
                resulting_state=node.failure_status,
                occurred_at=datetime.now(UTC),
            ),
        )


def _redact(message: str, redactor: Callable[[str], str] | None) -> str:
    """An error message fit to store: through the injected redactor, then bounded.

    Truncation alone is not redaction. Without a redactor wired (a bare test
    container) the message is only bounded, and a stored error may then carry
    whatever the exception carried; the composition root always wires one.
    """
    return (redactor(message) if redactor else message)[:500]


def _commit_node(
    node: Node,
    result: NodeResult,
    deps: Deps,
    *,
    latency_ms: int,
    tier_before: IntegrityTier,
) -> RunState:
    """Record what the node did and advance the run. One transaction.

    The event row is written whatever the outcome, including failure, because a
    node that failed silently is worse than one that failed loudly.
    """
    from_status = result.state.status
    to_status = _next_status(node, result, from_status)

    deps.events.append(
        result.state.run_id,
        node=node.name,
        from_status=from_status,
        to_status=to_status,
        node_status=result.status.value,
        payload={
            "candidate_id": result.state.candidate_id,
            "latency_ms": latency_ms,
            "events": [event.name for event in result.events],
            **({"error_code": result.error.error_code} if result.error else {}),
        },
    )

    if result.error is not None:
        deps.errors.record(result.error)

    state = result.state

    # A node that changed the integrity tier has decided something the queue and
    # the operations page both display. Persisting it here rather than in the
    # node keeps every node's contract the same: return a state, and the runner
    # writes it down.
    if state.integrity_tier is not tier_before:
        deps.runs.set_integrity_tier(state.run_id, state.integrity_tier)

    if result.failed:
        # A failed node is not a completed node. Recording it as one would make
        # a resume skip the very step that needs retrying.
        state = state.model_copy(update={"status": to_status})
        deps.runs.set_status(state.run_id, to_status)
    else:
        state = state.with_node_complete(node.name, to_status)
        deps.runs.mark_node_complete(state.run_id, node.name, to_status)

    if result.status is NodeStatus.DEGRADED:
        state = state.degraded_by(node.name)

    return state


def _next_status(node: Node, result: NodeResult, current: RunStatus) -> RunStatus:
    """Where the run goes after this node.

    A node may name its own next state, which is how SANITIZE reaches
    QUARANTINED. Anything it names that the state machine forbids is ignored in
    favour of the declared status, so a node cannot invent a transition.
    """
    if result.failed:
        return node.failure_status

    proposed = result.next_status or node.success_status
    if proposed is current or can_transition(current, proposed):
        return proposed
    return node.success_status


def _record_budget_stop(state: RunState, node: Node, deps: Deps) -> RunState:
    """Stop before spending, and say so in the run's own record."""
    deps.events.append(
        state.run_id,
        node=node.name,
        from_status=state.status,
        to_status=RunStatus.MANUAL_REVIEW_REQUIRED,
        node_status=NodeStatus.FAILED.value,
        payload={"reason": "budget_ceiling_reached"},
    )
    deps.errors.record(
        ErrorRecord(
            error_id=uuid4(),
            run_id=state.run_id,
            node=node.name,
            error_code="BUDGET_EXCEEDED",
            error_class="BudgetExceeded",
            message_redacted=(
                "This run reached its cost ceiling before finishing. The work done so far is saved."
            ),
            retryable=False,
            attempt=1,
            resulting_state=RunStatus.MANUAL_REVIEW_REQUIRED,
            occurred_at=datetime.now(UTC),
        )
    )
    stopped = state.model_copy(update={"status": RunStatus.MANUAL_REVIEW_REQUIRED})
    deps.runs.set_status(state.run_id, RunStatus.MANUAL_REVIEW_REQUIRED)
    return stopped.degraded_by("budget_ceiling_reached")


def resume(run_id: UUID, state: RunState, deps: Deps) -> RunOutcome:
    """Continue a run from where it stopped.

    Resumption is ordinary execution: the runner already skips completed nodes,
    so there is no separate resume path that could drift from the normal one.
    """
    return run(state, deps)
