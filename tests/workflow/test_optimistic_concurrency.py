"""Two people with the same candidate open.

Not a rare case. A recruiter and a hiring manager both opening the queue on a
Monday morning is the ordinary case, and the failure it produces is the quiet
kind: the second decision overwrites the first, and nobody learns that two
different judgements were made.

So a write carries the version its page was rendered from, and a write whose
version has moved is refused with a sentence telling the reviewer to reload.
Refusing is the whole feature: arriving last is not a reason to win.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from application.deps import Deps
from application.use_cases.submit_review import STALE_MESSAGE, ReviewRejected, submit_review
from domain.contracts.enums import CriterionState, OverrideReason, ReviewAction, RunStatus
from domain.contracts.review import Override
from domain.ports.repositories import StaleRunVersion
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from tests.workflow.conftest_review import seed_run, wire


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "concurrency.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        reviewer_id="rec-014",
    )
    yield wire(build_deps(settings))
    close_thread_connection(settings.db_path)


# --- the race ---------------------------------------------------------------------


def test_a_stale_write_is_refused(deps: Deps) -> None:
    """Two reviewers rendered the same page. The first one decides; the second
    one's write carries a version that has moved on."""
    run = seed_run(deps)
    stale_version = run.version

    submit_review(run.run_id, ReviewAction.APPROVE, deps)

    with pytest.raises(ReviewRejected) as raised:
        submit_review(
            run.run_id,
            ReviewAction.REJECT,
            deps,
            expected_version=stale_version,
        )

    assert raised.value.error_code in ("STALE_WRITE", "ILLEGAL_TRANSITION")


def test_the_first_decision_survives(deps: Deps) -> None:
    """The point of refusing. Losing a race must not mean losing a decision."""
    run = seed_run(deps)
    stale_version = run.version

    submit_review(run.run_id, ReviewAction.APPROVE, deps)

    with pytest.raises(ReviewRejected):
        submit_review(run.run_id, ReviewAction.REJECT, deps, expected_version=stale_version)

    assert deps.runs.get(run.run_id).status is RunStatus.APPROVED
    assert deps.reviews.get_for_run(run.run_id).action is ReviewAction.APPROVE


def test_a_stale_write_records_no_decision(deps: Deps) -> None:
    """The run is updated before the decision is written, so a refused
    transition never leaves an authorisation to deliver behind it."""
    run = seed_run(deps)

    with pytest.raises(ReviewRejected):
        submit_review(run.run_id, ReviewAction.APPROVE, deps, expected_version=99)

    assert deps.reviews.get_for_run(run.run_id) is None
    assert deps.runs.get(run.run_id).status is RunStatus.READY_FOR_REVIEW


def test_the_message_tells_the_reviewer_what_to_do(deps: Deps) -> None:
    """Not "conflict", not "version mismatch". A sentence with an action."""
    run = seed_run(deps)

    with pytest.raises(ReviewRejected) as raised:
        submit_review(run.run_id, ReviewAction.APPROVE, deps, expected_version=99)

    assert raised.value.message == STALE_MESSAGE
    assert "Reload" in raised.value.message


def test_a_current_write_succeeds(deps: Deps) -> None:
    """The control. A test suite that only proved writes get refused would pass
    with the feature broken shut."""
    run = seed_run(deps)

    result = submit_review(run.run_id, ReviewAction.APPROVE, deps, expected_version=run.version)

    assert result.status is RunStatus.APPROVED


def test_omitting_the_version_uses_the_one_just_read(deps: Deps) -> None:
    """A caller with no page behind it — a script, a test — still gets a
    consistent write rather than an unconditional one."""
    run = seed_run(deps)

    result = submit_review(run.run_id, ReviewAction.APPROVE, deps)

    assert result.status is RunStatus.APPROVED


# --- the repository underneath ------------------------------------------------------


def test_the_version_advances_on_every_write(deps: Deps) -> None:
    run = seed_run(deps)

    stored = deps.runs.update(
        run.model_copy(update={"status": RunStatus.NEEDS_REVIEW}),
        expected_version=run.version,
    )

    assert stored.version == run.version + 1


def test_the_repository_refuses_a_stale_version_directly(deps: Deps) -> None:
    """Tested at the repository as well as through the use case, because this is
    where the guarantee actually lives."""
    run = seed_run(deps)
    deps.runs.update(
        run.model_copy(update={"status": RunStatus.NEEDS_REVIEW}),
        expected_version=run.version,
    )

    with pytest.raises(StaleRunVersion):
        deps.runs.update(
            run.model_copy(update={"status": RunStatus.REJECTED}),
            expected_version=run.version,
        )


def test_two_sequential_writes_both_land(deps: Deps) -> None:
    """Reading between writes is what makes a second write legitimate."""
    run = seed_run(deps)

    first = deps.runs.update(
        run.model_copy(update={"status": RunStatus.NEEDS_REVIEW}),
        expected_version=run.version,
    )
    second = deps.runs.update(
        first.model_copy(update={"status": RunStatus.REJECTED}),
        expected_version=first.version,
    )

    assert second.status is RunStatus.REJECTED
    assert second.version == run.version + 2


# --- overrides in a race ---------------------------------------------------------------


def test_a_stale_override_does_not_change_the_recommendation(deps: Deps) -> None:
    """The worst version of this bug: a rejected write that still moved the
    band, so the run shows a score nobody approved."""
    run = seed_run(deps)
    before = deps.evidence.recommendation_for_run(run.run_id)

    with pytest.raises(ReviewRejected):
        submit_review(
            run.run_id,
            ReviewAction.APPROVE,
            deps,
            expected_version=99,
            overrides=[
                Override(
                    criterion_id="python-depth",
                    previous_state=CriterionState.MET,
                    new_state=CriterionState.NOT_MET,
                    reason_code=OverrideReason.EVIDENCE_MISREAD,
                    reason_text="The assessment misread the Python experience.",
                )
            ],
        )

    after = deps.evidence.recommendation_for_run(run.run_id)
    assert after.band is before.band
    assert after.criterion_states == before.criterion_states


def test_the_decision_records_the_version_it_saw(deps: Deps) -> None:
    """So a later reader can tell which rendering of the run was decided on."""
    run = seed_run(deps)

    submit_review(run.run_id, ReviewAction.APPROVE, deps)

    assert deps.reviews.get_for_run(run.run_id).run_version == run.version
