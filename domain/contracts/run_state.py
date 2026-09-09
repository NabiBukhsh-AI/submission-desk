"""What travels between nodes.

``RunState`` is the whole of a run in flight. It is frozen, so a node cannot
mutate what it was given: it returns a new state, the runner commits it, and a
crash therefore leaves the database at a node boundary rather than halfway
through one.

That immutability is not a style preference. It is what makes resumption
correct: a node that had edited the state in place and then failed would leave
behind a partially updated run that the next attempt would build on.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import ConfigDict, Field

from domain.contracts.base import Contract
from domain.contracts.enums import IntegrityTier, RunStatus


class NodeStatus(str, Enum):
    """How a node finished.

    ``DEGRADED`` is the one that earns its place. A node that produced a usable
    but incomplete result is neither a success to be ignored nor a failure to be
    routed around: the run continues and the reviewer is told.
    """

    OK = "ok"
    SKIPPED = "skipped"
    DEGRADED = "degraded"
    FAILED = "failed"


class DomainEvent(Contract):
    """Something worth recording that happened inside a node.

    Nodes emit these; the runner writes them. A node cannot forget to be
    observed, because the observation is not the node's job.
    """

    name: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class RunState(Contract):
    """A run in flight.

    Carries only what a later node needs. Documents, extracted text, evidence
    and the rest are held by their repositories and fetched through ``Deps``,
    so this stays small enough to copy on every node boundary.
    """

    # The pipeline holds interim objects of many shapes here, so this one
    # contract permits extra keys while every other contract forbids them.
    model_config = ConfigDict(frozen=True, extra="allow", str_strip_whitespace=False)

    run_id: UUID
    candidate_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    status: RunStatus
    completed_nodes: tuple[str, ...] = ()
    started_at: datetime

    rubric_hash: str = ""
    content_key: str = ""
    blind_mode: bool = True
    calibration_enabled: bool = False
    integrity_tier: IntegrityTier = IntegrityTier.CLEAN

    #: Regenerated per run. Wraps candidate text in a region the document cannot
    #: close, because closing it needs a value chosen after the document was
    #: written.
    nonce: str = ""

    #: Set when a node produced something usable but incomplete, so the reviewer
    #: is told rather than left to notice.
    degraded_reasons: tuple[str, ...] = ()

    def with_node_complete(self, node: str, status: RunStatus) -> RunState:
        nodes = (
            self.completed_nodes if node in self.completed_nodes else (*self.completed_nodes, node)
        )
        return self.model_copy(update={"completed_nodes": nodes, "status": status})

    def degraded_by(self, reason: str) -> RunState:
        if reason in self.degraded_reasons:
            return self
        return self.model_copy(update={"degraded_reasons": (*self.degraded_reasons, reason)})


class NodeResult(Contract):
    """What a node hands back.

    A node never raises to the runner and never commits. It returns this, with
    ``FAILED`` and an error record if something went wrong, which is what keeps
    the runner's loop short enough to read in one sitting.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    state: RunState
    status: NodeStatus
    events: tuple[DomainEvent, ...] = ()
    error: Any = None
    #: Where the run should go next. None means the node did not change status.
    next_status: RunStatus | None = None

    @property
    def failed(self) -> bool:
        return self.status is NodeStatus.FAILED
