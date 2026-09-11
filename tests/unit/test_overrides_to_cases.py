"""Overrides become draft cases, and drafts are visibly drafts.

The improvement loop's one mechanical step. What it must get right: the
reviewer's state wins, the band is not inferred, the document is a placeholder
that has to be replaced, and the file says all of that at the top.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from application.deps import Deps
from application.use_cases.submit_review import submit_review
from domain.contracts.enums import CriterionState, OverrideReason, ReviewAction
from domain.contracts.evaluation import EvaluationCase
from domain.contracts.review import Override
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from scripts.overrides_to_cases import HEADER, collect, write_drafts
from tests.workflow.conftest_review import seed_run, wire


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "o.sqlite"), blob_dir=str(tmp_path / "b"), reviewer_id="rec-1"
    )
    yield wire(build_deps(settings))
    close_thread_connection(settings.db_path)


def override(criterion: str, before: CriterionState, after: CriterionState) -> Override:
    return Override(
        criterion_id=criterion,
        previous_state=before,
        new_state=after,
        reason_code=OverrideReason.EVIDENCE_MISREAD,
        reason_text="The document says otherwise.",
    )


def test_no_overrides_means_no_drafts(deps: Deps, tmp_path: Path) -> None:
    assert write_drafts(deps, tmp_path / "drafts") == []


def test_the_reviewers_state_is_the_expected_one(deps: Deps, tmp_path: Path) -> None:
    run = seed_run(deps)
    submit_review(
        run.run_id,
        ReviewAction.APPROVE,
        deps,
        overrides=[override("python-depth", CriterionState.MET, CriterionState.PARTIAL)],
    )

    [path] = write_drafts(deps, tmp_path / "drafts")
    case = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert case["expected"]["criterion_states"] == {"python-depth": "partial"}


def test_the_band_is_not_inferred(deps: Deps, tmp_path: Path) -> None:
    """An override changes criteria. Deriving a band from the corrected states
    would be the system grading its own homework."""
    run = seed_run(deps)
    submit_review(
        run.run_id,
        ReviewAction.APPROVE,
        deps,
        overrides=[override("python-depth", CriterionState.MET, CriterionState.NOT_MET)],
    )

    [path] = write_drafts(deps, tmp_path / "drafts")
    case = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert case["expected"]["expected_band"] is None


def test_the_document_is_a_placeholder(deps: Deps, tmp_path: Path) -> None:
    """Real pilot documents never enter the repository, so the draft cannot
    point at one. It points at something that fails loudly instead."""
    run = seed_run(deps)
    submit_review(
        run.run_id,
        ReviewAction.APPROVE,
        deps,
        overrides=[override("python-depth", CriterionState.MET, CriterionState.NOT_MET)],
    )

    [path] = write_drafts(deps, tmp_path / "drafts")
    case = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert all("REPLACE-ME" in document for document in case["documents"])


def test_the_file_says_it_is_a_draft(deps: Deps, tmp_path: Path) -> None:
    run = seed_run(deps)
    submit_review(
        run.run_id,
        ReviewAction.APPROVE,
        deps,
        overrides=[override("python-depth", CriterionState.MET, CriterionState.NOT_MET)],
    )

    [path] = write_drafts(deps, tmp_path / "drafts")

    assert path.read_text(encoding="utf-8").startswith(HEADER)
    assert "draft" in path.name


def test_a_draft_is_a_valid_case_apart_from_its_document(deps: Deps, tmp_path: Path) -> None:
    """Once the document is replaced, the file loads through the same contract
    the runner uses. Anything else would be a draft nobody could promote."""
    run = seed_run(deps)
    submit_review(
        run.run_id,
        ReviewAction.APPROVE,
        deps,
        overrides=[
            override("python-depth", CriterionState.MET, CriterionState.NOT_MET),
            override(
                "cost-awareness", CriterionState.PARTIAL, CriterionState.INSUFFICIENT_EVIDENCE
            ),
        ],
    )

    [path] = write_drafts(deps, tmp_path / "drafts")
    case = EvaluationCase.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

    assert case.expected.must_be_insufficient == ["cost-awareness"]
    assert "draft" in case.tags


def test_overrides_are_grouped_by_run(deps: Deps, tmp_path: Path) -> None:
    first = seed_run(deps)
    second = seed_run(deps)
    for run in (first, second):
        submit_review(
            run.run_id,
            ReviewAction.APPROVE,
            deps,
            overrides=[override("python-depth", CriterionState.MET, CriterionState.NOT_MET)],
        )

    assert set(collect(deps)) == {first.run_id, second.run_id}
    assert len(write_drafts(deps, tmp_path / "drafts")) == 2


def test_nothing_from_the_document_is_written(deps: Deps, tmp_path: Path) -> None:
    """The override table holds ids and states. A draft built from it cannot
    leak a quotation, because it never had one."""
    run = seed_run(deps)
    submit_review(
        run.run_id,
        ReviewAction.APPROVE,
        deps,
        overrides=[override("python-depth", CriterionState.MET, CriterionState.NOT_MET)],
        comments="The candidate's email is r.hale@example.com",
    )

    [path] = write_drafts(deps, tmp_path / "drafts")

    assert "@" not in path.read_text(encoding="utf-8")
