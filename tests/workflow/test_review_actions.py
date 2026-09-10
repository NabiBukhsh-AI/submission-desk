"""The three decisions, and the door each one opens.

Approve is the only action that can lead to delivery, and it leads there through
a persisted row rather than through a status change. That distinction is the
whole approval gate: a delivery adapter checks for the decision independently, so
an APPROVED run with no decision behind it still cannot send.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from application.deps import Deps
from application.use_cases.submit_review import (
    NEXT_STATUS,
    STALE_MESSAGE,
    ReviewRejected,
    submit_review,
)
from domain.contracts.enums import ReviewAction, RunStatus
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from tests.workflow.conftest_review import seed_run, wire

REVIEWER = "rec-014"


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "review.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        reviewer_id=REVIEWER,
    )
    yield wire(build_deps(settings))
    close_thread_connection(settings.db_path)


# --- the three actions -----------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (ReviewAction.APPROVE, RunStatus.APPROVED),
        (ReviewAction.REJECT, RunStatus.REJECTED),
        (ReviewAction.REQUEST_INFO, RunStatus.NEEDS_INFO),
    ],
)
def test_each_action_moves_the_run(deps: Deps, action: ReviewAction, expected: RunStatus) -> None:
    run = seed_run(deps)

    result = submit_review(run.run_id, action, deps)

    assert result.status is expected
    assert deps.runs.get(run.run_id).status is expected


@pytest.mark.parametrize("action", list(ReviewAction))
def test_every_action_persists_a_decision(deps: Deps, action: ReviewAction) -> None:
    """The row is the authorisation. A status with no row behind it is not one."""
    run = seed_run(deps)

    submit_review(run.run_id, action, deps)

    decision = deps.reviews.get_for_run(run.run_id)
    assert decision is not None
    assert decision.action is action


def test_the_decision_is_attributed(deps: Deps) -> None:
    """Stamped from configuration rather than typed. An unattributable approval
    is not an approval."""
    run = seed_run(deps)

    submit_review(run.run_id, ReviewAction.APPROVE, deps)

    assert deps.reviews.get_for_run(run.run_id).reviewer_id == REVIEWER


def test_an_unconfigured_reviewer_is_refused(deps: Deps) -> None:
    unattributed = Deps(**{**deps.__dict__, "settings": deps.settings.__class__()})
    run = seed_run(unattributed)

    with pytest.raises(ReviewRejected) as raised:
        submit_review(run.run_id, ReviewAction.APPROVE, unattributed)

    assert raised.value.error_code == "NO_REVIEWER"


def test_an_explicit_reviewer_overrides_the_default(deps: Deps) -> None:
    run = seed_run(deps)

    submit_review(run.run_id, ReviewAction.APPROVE, deps, reviewer_id="rec-099")

    assert deps.reviews.get_for_run(run.run_id).reviewer_id == "rec-099"


# --- what is recorded -------------------------------------------------------------


def test_the_time_taken_is_recorded(deps: Deps) -> None:
    """One of the two numbers the whole evaluation rests on: what a review costs
    a person, measured rather than estimated."""
    run = seed_run(deps)

    submit_review(run.run_id, ReviewAction.APPROVE, deps, elapsed_seconds=94)

    assert deps.reviews.get_for_run(run.run_id).elapsed_seconds == 94


def test_a_negative_elapsed_time_is_clamped(deps: Deps) -> None:
    """A clock that went backwards is not a reason to refuse a decision."""
    run = seed_run(deps)

    submit_review(run.run_id, ReviewAction.APPROVE, deps, elapsed_seconds=-5)

    assert deps.reviews.get_for_run(run.run_id).elapsed_seconds == 0


def test_the_trust_rating_is_recorded(deps: Deps) -> None:
    run = seed_run(deps)

    submit_review(run.run_id, ReviewAction.APPROVE, deps, trust_rating=4)

    assert deps.reviews.get_for_run(run.run_id).trust_rating == 4


def test_comments_are_recorded(deps: Deps) -> None:
    run = seed_run(deps)

    submit_review(run.run_id, ReviewAction.REJECT, deps, comments="Not enough production depth.")

    assert deps.reviews.get_for_run(run.run_id).comments == "Not enough production depth."


def test_the_run_records_which_action_was_taken(deps: Deps) -> None:
    """So the queue can show it without joining to the decision table."""
    run = seed_run(deps)

    submit_review(run.run_id, ReviewAction.REJECT, deps)

    assert deps.runs.get(run.run_id).reviewer_action is ReviewAction.REJECT


def test_a_decided_run_is_finished(deps: Deps) -> None:
    run = seed_run(deps)

    submit_review(run.run_id, ReviewAction.APPROVE, deps)

    assert deps.runs.get(run.run_id).finished_at is not None


def test_a_run_needing_information_is_not_finished(deps: Deps) -> None:
    """It goes back to the candidate rather than closing, which is why
    request-info is not a rejection."""
    run = seed_run(deps)

    submit_review(run.run_id, ReviewAction.REQUEST_INFO, deps)

    assert deps.runs.get(run.run_id).finished_at is None


# --- illegal transitions -----------------------------------------------------------


def test_a_run_that_has_not_reached_review_cannot_be_approved(deps: Deps) -> None:
    """Unreachable from the interface for the same reason it is unreachable from
    the pipeline: both call ``can_transition``."""
    run = seed_run(deps, status=RunStatus.EXTRACTED)

    with pytest.raises(ReviewRejected) as raised:
        submit_review(run.run_id, ReviewAction.APPROVE, deps)

    assert raised.value.error_code == "ILLEGAL_TRANSITION"


def test_an_already_decided_run_cannot_be_decided_again(deps: Deps) -> None:
    run = seed_run(deps)
    submit_review(run.run_id, ReviewAction.APPROVE, deps)

    with pytest.raises(ReviewRejected) as raised:
        submit_review(run.run_id, ReviewAction.REJECT, deps)

    assert "already been decided" in raised.value.message


def test_a_quarantined_run_cannot_be_approved(deps: Deps) -> None:
    """Its only doors are review-anyway and reject."""
    run = seed_run(deps, status=RunStatus.QUARANTINED)

    with pytest.raises(ReviewRejected) as raised:
        submit_review(run.run_id, ReviewAction.APPROVE, deps)

    assert "quarantined" in raised.value.message


def test_a_quarantined_run_can_be_rejected(deps: Deps) -> None:
    run = seed_run(deps, status=RunStatus.QUARANTINED)

    result = submit_review(run.run_id, ReviewAction.REJECT, deps)

    assert result.status is RunStatus.REJECTED


def test_a_missing_run_is_refused_by_name(deps: Deps) -> None:
    from uuid import uuid4

    with pytest.raises(ReviewRejected) as raised:
        submit_review(uuid4(), ReviewAction.APPROVE, deps)

    assert raised.value.error_code == "NO_SUCH_RUN"


# --- the approval gate --------------------------------------------------------------


def test_no_action_reaches_delivered(deps: Deps) -> None:
    """Delivery is downstream of approval, not a thing a reviewer can do."""
    assert RunStatus.DELIVERED not in NEXT_STATUS.values()


def test_only_approve_leads_towards_delivery() -> None:
    from domain.state_machine import ALLOWED

    towards = {
        action
        for action, status in NEXT_STATUS.items()
        if RunStatus.DELIVERED in ALLOWED.get(status, frozenset())
    }

    assert towards == {ReviewAction.APPROVE}


def test_a_rejected_run_is_terminal() -> None:
    from domain.state_machine import ALLOWED

    assert ALLOWED[RunStatus.REJECTED] == frozenset()


# --- what the reviewer reads -----------------------------------------------------------


def test_every_message_is_written_for_a_person(deps: Deps) -> None:
    for action in ReviewAction:
        run = seed_run(deps)
        result = submit_review(run.run_id, action, deps)

        assert result.message[0].isupper()
        assert result.message.endswith(".")
        for jargon in ("none", "null", "traceback", "enum", "_"):
            assert jargon not in result.message.lower()


def test_the_stale_message_says_what_to_do() -> None:
    """Not "conflict" or "version mismatch": a sentence with an action in it."""
    assert "Reload the page" in STALE_MESSAGE
