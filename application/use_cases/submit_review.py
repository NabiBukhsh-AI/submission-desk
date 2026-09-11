"""The human decision, and the only door to delivery.

Everything before this runs automatically. Nothing after it happens without a
row in this table, and delivery adapters check for that row independently of the
run's status: an APPROVED status with no decision behind it still cannot send.

Three things are enforced here rather than in the interface.

The reviewer never types a band. They change criterion states, and the same
``aggregate()`` the pipeline used recomputes the band from those. A band a person
typed would be a number with no derivation behind it.

The transition goes through the state machine, so an illegal one is unreachable
from the interface for the same reason it is unreachable from the pipeline: they
call the same function.

A stale write is refused. Two reviewers with the page open is not a rare case,
and the second one arriving last should be told to reload rather than quietly
overwriting the first one's decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from application.deps import Deps
from application.use_cases.recompute_recommendation import recompute
from domain.contracts.enums import Band, ReviewAction, RunStatus
from domain.contracts.review import Override, ReviewDecision
from domain.ports.repositories import StaleRunVersion
from domain.state_machine import can_transition

#: Where each action leaves the run. REQUEST_INFO returns it to the candidate
#: rather than closing it, which is why it is not a rejection.
NEXT_STATUS: dict[ReviewAction, RunStatus] = {
    ReviewAction.APPROVE: RunStatus.APPROVED,
    ReviewAction.REJECT: RunStatus.REJECTED,
    ReviewAction.REQUEST_INFO: RunStatus.NEEDS_INFO,
}

#: What the reviewer is told when their write lost a race.
STALE_MESSAGE = (
    "Somebody else changed this candidate while you had it open. "
    "Reload the page to see their decision before making yours."
)


class ReviewRejected(Exception):
    """The decision could not be recorded, with a sentence saying why."""

    def __init__(self, message: str, *, error_code: str = "REVIEW_REJECTED") -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code


@dataclass(frozen=True)
class ReviewResult:
    """What was recorded."""

    decision: ReviewDecision
    status: RunStatus
    band: Band | None
    message: str


def submit_review(
    run_id: UUID,
    action: ReviewAction,
    deps: Deps,
    *,
    reviewer_id: str | None = None,
    overrides: list[Override] | None = None,
    comments: str | None = None,
    elapsed_seconds: int = 0,
    trust_rating: int | None = None,
    expected_version: int | None = None,
) -> ReviewResult:
    """Record one decision and move the run.

    ``expected_version`` is the version the reviewer's page was rendered from.
    Passing it is what makes a lost update visible instead of silent.
    """
    run = deps.runs.get(run_id)
    if run is None:
        raise ReviewRejected(f"There is no run {run_id}.", error_code="NO_SUCH_RUN")

    reviewer = reviewer_id or deps.settings.reviewer_id
    if not reviewer:
        # Stamped from configuration rather than typed, so a decision can always
        # be attributed. An unattributable approval is not an approval.
        raise ReviewRejected(
            "No reviewer is configured, so this decision could not be attributed "
            "to anybody. Set REVIEWER_ID and try again.",
            error_code="NO_REVIEWER",
        )

    target = NEXT_STATUS[action]
    if not can_transition(run.status, target):
        raise ReviewRejected(
            _cannot_message(run.status, action),
            error_code="ILLEGAL_TRANSITION",
        )

    overrides = list(overrides or [])
    band = _band_after(run_id, deps, overrides) if overrides else run.final_band

    decision = ReviewDecision(
        decision_id=uuid4(),
        run_id=run_id,
        reviewer_id=reviewer,
        action=action,
        overrides=overrides,
        comments=comments,
        elapsed_seconds=max(elapsed_seconds, 0),
        trust_rating=trust_rating,
        post_override_band=band,
        decided_at=datetime.now(UTC),
        run_version=run.version,
    )

    updated = run.model_copy(
        update={
            "status": target,
            "reviewer_action": action,
            "override_count": len(overrides),
            "final_band": band,
            "finished_at": datetime.now(UTC) if target is not RunStatus.NEEDS_INFO else None,
        }
    )

    try:
        stored = deps.runs.update(
            updated,
            expected_version=expected_version if expected_version is not None else run.version,
        )
    except StaleRunVersion as stale:
        raise ReviewRejected(STALE_MESSAGE, error_code="STALE_WRITE") from stale

    # The decision is written after the run, so a decision row never exists for
    # a run that refused the transition. The other order would leave an
    # authorisation to deliver attached to a run nobody moved.
    deps.reviews.save(decision)

    if overrides:
        result = recompute(run_id, deps, overrides)
        deps.evidence.save_recommendation(run_id, result.recommendation)

    return ReviewResult(
        decision=decision,
        status=stored.status,
        band=band,
        message=_message_for(action, len(overrides)),
    )


def _band_after(run_id: UUID, deps: Deps, overrides: list[Override]) -> Band | None:
    """The band the overrides produce, from the same rules the pipeline used."""
    return recompute(run_id, deps, overrides).recommendation.band


def _cannot_message(status: RunStatus, action: ReviewAction) -> str:
    """Why this decision is not available, in a sentence a recruiter can act on."""
    if status in (RunStatus.APPROVED, RunStatus.REJECTED, RunStatus.DELIVERED):
        return "This candidate has already been decided. Reload the page to see what was recorded."
    if status is RunStatus.QUARANTINED:
        return (
            "This candidate's documents were quarantined. Choose Review anyway "
            "or Reject; the other actions are not available until then."
        )
    return (
        f"This candidate is not ready to be {action.value.replace('_', ' ')}d yet. "
        "The run has not reached a reviewer."
    )


def _message_for(action: ReviewAction, override_count: int) -> str:
    tail = (
        f" {override_count} assessment(s) were corrected and the recommendation was recalculated."
        if override_count
        else ""
    )
    return {
        ReviewAction.APPROVE: "Approved. The package is ready to send." + tail,
        ReviewAction.REJECT: "Rejected. Nothing will be sent." + tail,
        ReviewAction.REQUEST_INFO: (
            "Marked as needing more information. The requests are ready to send." + tail
        ),
    }[action]
