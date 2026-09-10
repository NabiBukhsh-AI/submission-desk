"""The REVIEW node.

Not the review itself. This node stages a run for a person: it decides whether
they see it clean or flagged, records why, and stops.

Everything before this point is automatic. Nothing after it happens without a
persisted decision, which is why the runner is asked to stop here. That the gate
is a missing call rather than an ``if`` is deliberate: there is no flag to set
wrong, because the code that would deliver is simply never reached.

The flag never changes the band. A run flagged for contradictory evidence still
carries the recommendation the evidence produced; the flag says look harder, not
score lower.
"""

from __future__ import annotations

from application.deps import Deps
from domain.contracts.enums import CriterionState, IntegrityTier, RunStatus
from domain.contracts.run_state import DomainEvent, NodeResult, NodeStatus, RunState

#: Why a run reaches a reviewer flagged. Each is a sentence the interface shows
#: verbatim, so the reviewer knows what to look at before opening anything.
FLAG_REASONS: dict[str, str] = {
    "requires_human": "The rules require a person to decide this one.",
    "integrity": "A document contained content that was neutralised before it was read.",
    "partial_profile": "Only part of the candidate's history could be read.",
    "invalid_span": "A quotation could not be found in the source document and was removed.",
    "budget_capped": "This run reached its cost ceiling before every point was assessed.",
    "contradicted": "The documents contradict themselves on at least one point.",
    "degraded": "A step produced a usable but incomplete result.",
}


def reasons_to_flag(state: RunState, recommendation: object) -> list[str]:
    """Every reason this run wants a closer look, in the order shown.

    Returned as a list rather than a boolean because a reviewer opening a
    flagged run needs to know which of six things happened, and a single flag
    would send them looking.
    """
    found: list[str] = []

    if getattr(recommendation, "requires_human", False):
        found.append("requires_human")
    if state.integrity_tier is not IntegrityTier.CLEAN:
        found.append("integrity")
    if state.profile_partial:
        found.append("partial_profile")
    if state.invalid_span_present:
        found.append("invalid_span")
    if state.budget_capped:
        found.append("budget_capped")

    states = getattr(recommendation, "criterion_states", {}) or {}
    if any(value is CriterionState.CONTRADICTED for value in states.values()):
        found.append("contradicted")

    if state.degraded_reasons:
        found.append("degraded")

    return found


def node(state: RunState, deps: Deps) -> NodeResult:
    """Put this run in front of a person, clean or flagged."""
    recommendation = state.recommendation
    flags = reasons_to_flag(state, recommendation)

    # A run with no recommendation cannot be reviewed against anything, and is
    # flagged rather than failed: the reviewer can still read the documents and
    # decide, which is more use than a dead run.
    if recommendation is None and "requires_human" not in flags:
        flags.insert(0, "requires_human")

    return NodeResult(
        state=state,
        status=NodeStatus.OK,
        events=(
            DomainEvent(
                name="review.staged",
                payload={
                    "flagged": bool(flags),
                    "reasons": flags,
                    "band": getattr(getattr(recommendation, "band", None), "value", None),
                },
            ),
        ),
        next_status=RunStatus.NEEDS_REVIEW if flags else RunStatus.READY_FOR_REVIEW,
    )
