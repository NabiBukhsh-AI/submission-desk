"""The pipeline, declared once.

A static ordered tuple. Not a graph builder, not a plugin system, not a list
assembled at runtime from configuration: eleven nodes in a fixed order, visible
in one screen, with the failure state of each written down beside it.

This is the shape the architecture argues for at length. The complete set of
steps is known before a document arrives, so nothing about a candidate can
change *which* steps run, only what they return. A component whose job is to
decide the step graph at runtime would have nothing to decide.

Adding a node, removing one, or reordering them requires a decision record. That
is deliberate friction: the order encodes the guarantees. Sanitisation runs
before structuring so an adversarial document costs nothing; aggregation runs
before composition so a reviewer never sees a band with nothing to act on.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from application.deps import Deps
from domain.contracts.enums import RunStatus
from domain.contracts.run_state import NodeResult, NodeStatus, RunState
from pipeline import extract, intake

NodeFn = Callable[[RunState, Deps], NodeResult]


@dataclass(frozen=True)
class Node:
    """One stage, with what happens when it fails.

    ``required`` distinguishes a node whose failure stops the run from one whose
    failure degrades it. Calibration failing is a worse assessment; extraction
    failing is no assessment at all.
    """

    name: str
    fn: NodeFn
    success_status: RunStatus
    failure_status: RunStatus
    required: bool = True
    #: What the reviewer is told when this node fails, in their language.
    failure_message: str = "This step could not be completed."


def _placeholder(name: str) -> NodeFn:
    """A node that does nothing but record that it ran.

    Replaced phase by phase as each real node arrives. Present so the runner,
    the event log, and resumption can be tested end to end before any of the
    document handling exists.
    """

    def run_node(state: RunState, deps: Deps) -> NodeResult:
        return NodeResult(state=state, status=NodeStatus.SKIPPED)

    run_node.__name__ = f"placeholder_{name.lower()}"
    return run_node


#: The eleven nodes, in order. Each entry names its success state, the state a
#: failure routes to, and whether the run can continue without it.
PIPELINE: tuple[Node, ...] = (
    Node(
        name="CONFIG",
        fn=_placeholder("CONFIG"),
        success_status=RunStatus.CREATED,
        failure_status=RunStatus.FAILED_TERMINAL,
        failure_message="The role configuration could not be loaded.",
    ),
    Node(
        name="INTAKE",
        fn=intake.node,
        success_status=RunStatus.INTAKE_OK,
        failure_status=RunStatus.FAILED_TERMINAL,
        failure_message="These files could not be accepted.",
    ),
    Node(
        name="EXTRACT",
        fn=extract.node,
        success_status=RunStatus.EXTRACTED,
        failure_status=RunStatus.FAILED_TERMINAL,
        failure_message="No readable text could be taken from these documents.",
    ),
    Node(
        name="SANITIZE",
        fn=_placeholder("SANITIZE"),
        success_status=RunStatus.SANITIZED,
        failure_status=RunStatus.QUARANTINED,
        failure_message="A document contained content a security scanner flagged.",
    ),
    Node(
        name="STRUCTURE",
        fn=_placeholder("STRUCTURE"),
        success_status=RunStatus.STRUCTURED,
        failure_status=RunStatus.MANUAL_REVIEW_REQUIRED,
        required=False,
        failure_message="Only part of the candidate's history could be read.",
    ),
    Node(
        name="CALIBRATE",
        fn=_placeholder("CALIBRATE"),
        success_status=RunStatus.CALIBRATED,
        failure_status=RunStatus.CALIBRATED,
        required=False,
        failure_message="Past decisions were not available as a reference.",
    ),
    Node(
        name="ASSESS",
        fn=_placeholder("ASSESS"),
        success_status=RunStatus.ASSESSED,
        failure_status=RunStatus.MANUAL_REVIEW_REQUIRED,
        failure_message="The assessment could not be completed.",
    ),
    Node(
        name="AGGREGATE",
        fn=_placeholder("AGGREGATE"),
        success_status=RunStatus.AGGREGATED,
        failure_status=RunStatus.MANUAL_REVIEW_REQUIRED,
        failure_message="A recommendation could not be computed.",
    ),
    Node(
        name="COMPOSE",
        fn=_placeholder("COMPOSE"),
        success_status=RunStatus.COMPOSED,
        failure_status=RunStatus.NEEDS_REVIEW,
        required=False,
        failure_message="The suggested questions could not be written.",
    ),
    Node(
        name="REVIEW",
        fn=_placeholder("REVIEW"),
        success_status=RunStatus.READY_FOR_REVIEW,
        failure_status=RunStatus.NEEDS_REVIEW,
        failure_message="This candidate needs a closer look.",
    ),
    Node(
        name="DELIVER",
        fn=_placeholder("DELIVER"),
        success_status=RunStatus.DELIVERED,
        failure_status=RunStatus.DELIVERY_PENDING_RETRY,
        required=False,
        failure_message="The package was prepared but could not be sent yet.",
    ),
)

#: Node names in order, for resumption and for the operations page.
NODE_NAMES: tuple[str, ...] = tuple(node.name for node in PIPELINE)

#: The nodes that run before a reviewer sees anything. Everything past REVIEW
#: requires a persisted decision, which is why the runner stops there.
NODES_BEFORE_REVIEW: tuple[str, ...] = NODE_NAMES[: NODE_NAMES.index("REVIEW")]


def node_by_name(name: str) -> Node:
    for node in PIPELINE:
        if node.name == name:
            return node
    raise KeyError(f"no node named {name!r}")
