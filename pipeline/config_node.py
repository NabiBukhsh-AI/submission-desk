"""The CONFIG node.

Loads the role definition and the per-run identifiers everything downstream
depends on: the rubric, its hash, the prompt bundle hash, and the nonce that
makes a document's fenced region unforgeable.

It runs first and does almost nothing, which is the point. A run whose rubric
could not be loaded should fail here, before a document is read and before a
model is called, rather than three stages in with a partial result to explain.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from application.deps import Deps
from domain.contracts.enums import RunStatus
from domain.contracts.errors import ErrorRecord
from domain.contracts.run_state import DomainEvent, NodeResult, NodeStatus, RunState
from domain.errors import SubmissionDeskError
from domain.ports.models import make_nonce
from domain.rules import rubric_hash


def node(state: RunState, deps: Deps) -> NodeResult:
    """Load the role this candidate is being read against."""
    if deps.rubric_loader is None:
        return _failed(state, "No role definitions are configured.", "NO_RUBRIC_LOADER")

    try:
        rubric = deps.rubric_loader(state.role_id)
    except SubmissionDeskError as invalid:
        return _failed(state, str(invalid), "RUBRIC_INVALID")
    except FileNotFoundError:
        return _failed(
            state,
            f"There is no role definition called {state.role_id}. "
            "Check the name, or add the file to rubrics/.",
            "RUBRIC_NOT_FOUND",
        )

    carried = state.model_copy(
        update={
            "rubric": rubric,
            "rubric_hash": rubric_hash(rubric),
            # Regenerated per run. A document written months ago cannot contain
            # a value chosen at the moment it is read.
            "nonce": make_nonce(),
            # The rubric decides whether identity is redacted, unless the run
            # was started with an explicit choice.
            "blind_mode": state.blind_mode and rubric.blind_mode_default,
        }
    )

    return NodeResult(
        state=carried,
        status=NodeStatus.OK,
        events=(
            DomainEvent(
                name="config.rubric_loaded",
                payload={
                    "role_id": rubric.role_id,
                    "version": rubric.version,
                    "criteria": len(rubric.criteria),
                    "blind_mode": carried.blind_mode,
                    "min_coverage": rubric.min_coverage,
                },
            ),
        ),
        next_status=RunStatus.CREATED,
    )


def _failed(state: RunState, message: str, error_code: str) -> NodeResult:
    return NodeResult(
        state=state,
        status=NodeStatus.FAILED,
        events=(DomainEvent(name="config.failed", payload={"reason": error_code}),),
        error=ErrorRecord(
            error_id=uuid4(),
            run_id=state.run_id,
            node="CONFIG",
            error_code=error_code,
            error_class="ConfigFailed",
            message_redacted=message,
            retryable=False,
            attempt=1,
            resulting_state=RunStatus.FAILED_TERMINAL,
            occurred_at=datetime.now(UTC),
        ),
    )
