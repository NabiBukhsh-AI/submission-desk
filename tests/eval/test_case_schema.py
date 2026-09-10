"""Every case validates, and the split is what it says it is.

A benchmark that silently drops a malformed case gets smaller without its n
changing, which is the one failure mode that makes every number in the report
wrong in the flattering direction.

The other thing checked here is the shape of the benchmark itself: that the
split was committed before any tuning, that the cases test different things, and
that the ones asserting abstention outnumber the ones asserting a band — because
a benchmark made mostly of easy cases measures mostly nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from domain.contracts.evaluation import EvaluationCase
from eval.cases import documents as case_documents
from eval.runner import CASES_DIR, SPLITS, load_cases

ALL_CASES = load_cases()


def case_files() -> list[Path]:
    return sorted(path for split in SPLITS for path in (CASES_DIR / split).glob("*.yaml"))


# --- every file is a case ------------------------------------------------------------


def test_there_are_cases_to_check() -> None:
    """A loader that silently found nothing would pass every test below."""
    assert case_files()


@pytest.mark.parametrize("path", case_files(), ids=lambda path: path.stem)
def test_every_file_validates(path: Path) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    EvaluationCase.model_validate(data)


def test_ids_are_unique() -> None:
    """A duplicate would overwrite a case in every dictionary keyed by it, and
    the benchmark would get smaller without the n changing."""
    ids = [case.case_id for case in ALL_CASES]

    assert len(ids) == len(set(ids))


def test_the_loader_refuses_a_duplicate(tmp_path: Path) -> None:
    """Checked directly, because the guarantee lives in the loader rather than
    in the files happening to be right today."""
    for split in SPLITS:
        (tmp_path / split).mkdir()

    body = {
        "case_id": "same",
        "name": "One",
        "description": "A case.",
        "role_id": "ai-engineer",
        "expected": {"criterion_states": {}},
    }
    (tmp_path / "dev" / "a.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    (tmp_path / "dev" / "b.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate case ids"):
        load_cases(root=tmp_path)


# --- every case has its document --------------------------------------------------------


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case.case_id)
def test_every_case_names_a_document_that_exists(case) -> None:
    """A case pointing at a missing file fails at intake and scores as a miss,
    which would look like a system problem rather than a benchmark problem."""
    for name in case.documents:
        assert name in case_documents.DOCUMENTS, f"{case.case_id} names {name}"


def test_every_document_belongs_to_a_case() -> None:
    """An orphaned document is either a case somebody forgot to write or dead
    weight, and both are worth knowing about."""
    referenced = {name for case in ALL_CASES for name in case.documents}

    assert set(case_documents.DOCUMENTS) == referenced


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case.case_id)
def test_every_case_names_exactly_one_document(case) -> None:
    """One document per case, so a failure points at one input."""
    assert len(case.documents) == 1


# --- every case explains itself ------------------------------------------------------------


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case.case_id)
def test_every_case_says_what_it_is_for(case) -> None:
    """A case with no description is a case nobody can tell is redundant."""
    assert len(case.description.strip()) > 60


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case.case_id)
def test_every_case_asserts_something(case) -> None:
    """A case that asserts nothing costs a run and measures nothing."""
    label = case.expected

    assert (
        label.expected_band is not None
        or label.criterion_states
        or label.must_be_insufficient
        or label.must_flag_integrity
        or label.forbidden_claims
    ), f"{case.case_id} asserts nothing"


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case.case_id)
def test_every_case_names_its_arms(case) -> None:
    """Which arms a case is fair to. The injected documents exclude arm A,
    because a recruiter cannot see hidden text and their decision is not a fair
    comparison."""
    assert case.arms
    assert set(case.arms) <= {"a", "b", "c"}


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case.case_id)
def test_every_case_is_tagged(case) -> None:
    assert case.tags


# --- the shape of the benchmark ---------------------------------------------------------------


def test_the_split_is_committed() -> None:
    """Decided before any tuning. A split chosen after looking at results is not
    a split."""
    assert (CASES_DIR / "dev").is_dir()
    assert (CASES_DIR / "holdout").is_dir()


def test_the_holdout_is_a_meaningful_fraction() -> None:
    """Too small and it measures nothing; too large and there is not enough to
    develop against."""
    dev = len(load_cases("dev"))
    holdout = len(load_cases("holdout"))

    assert holdout >= 3
    assert 0.2 <= holdout / (dev + holdout) <= 0.5


def test_the_splits_do_not_overlap() -> None:
    dev = {case.case_id for case in load_cases("dev")}
    holdout = {case.case_id for case in load_cases("holdout")}

    assert dev.isdisjoint(holdout)


def test_abstention_is_tested_more_than_agreement() -> None:
    """The point of the benchmark. Any system can be graded on whether it agreed
    about a strong candidate; the interesting question is whether it invents an
    answer where a person could not read one."""
    abstaining = sum(1 for case in ALL_CASES if case.expected.must_be_insufficient)
    banded = sum(1 for case in ALL_CASES if case.expected.expected_band is not None)

    assert abstaining >= banded


def test_the_benchmark_covers_the_named_risks() -> None:
    """Each of these is a way the system could be wrong in a manner a band
    accuracy figure would hide."""
    tags = {tag for case in ALL_CASES for tag in case.tags}

    for required in ("abstention", "security", "adversarial", "extraction"):
        assert required in tags, f"nothing tests {required}"


def test_at_least_one_case_expects_an_integrity_flag() -> None:
    assert any(case.expected.must_flag_integrity for case in ALL_CASES)


def test_at_least_one_case_forbids_a_specific_claim() -> None:
    """The specific fabrications a document invites, named per case."""
    assert any(case.expected.forbidden_claims for case in ALL_CASES)


def test_the_benchmark_is_small_enough_to_have_been_read() -> None:
    """Twelve cases, every one chosen deliberately. A benchmark nobody has read
    end to end is a benchmark nobody can vouch for, and its n belongs beside
    every number computed from it."""
    assert len(ALL_CASES) == 12


def test_the_cases_directory_explains_the_split() -> None:
    readme = (CASES_DIR / "README.md").read_text(encoding="utf-8")

    assert "holdout" in readme
    assert "before any tuning" in readme
