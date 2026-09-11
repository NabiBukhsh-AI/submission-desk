"""The three-command promise, checked.

README says: clone, `make setup`, `make seed`, `make demo`, and there is a
candidate to review. This is the test that makes that sentence true rather than
hoped. It does what `make seed` does — generate the synthetic corpus, assess it
in demo mode with the offline model — and asserts that a candidate reached a
reviewer, that the injected one was quarantined without a model call, and that
nothing was sent anywhere.

Two ways to run it. On its own, it builds everything in a temporary directory,
so it is part of the ordinary suite and needs no state. Under `make smoke`, it
runs after `make seed` against the real data directory, which is what CI does
on a clean clone and is the only run that proves the Makefile targets, rather
than the functions behind them, work.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from application.deps import Deps
from application.use_cases.process_batch import process_batch
from domain.contracts.enums import RunStatus
from domain.state_machine import is_reviewable
from infrastructure.factory import DEMO_CORPUS, build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from scripts.make_synthetic_corpus import CANDIDATES, build

ROLE = "ai-engineer"


@pytest.fixture
def demo(tmp_path: Path) -> Iterator[Deps]:
    """Demo mode against a fresh database, reading the real synthetic corpus.

    The corpus is (re)generated first, exactly as `make seed` does, so the test
    does not depend on a previous run having left files behind.
    """
    build(DEMO_CORPUS)
    settings = settings_from_env(
        db_path=str(tmp_path / "smoke.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        demo_mode=True,
        model_provider="fake",
    )
    yield build_deps(settings)
    close_thread_connection(settings.db_path)


def seed(deps: Deps):
    candidates = deps.source.list_candidates()
    assert len(candidates) == len(CANDIDATES), "every synthetic candidate was found"
    return process_batch(candidates, ROLE, deps)


# --- the promise --------------------------------------------------------------------


def test_a_clean_clone_reaches_a_reviewable_candidate(demo: Deps) -> None:
    """The headline. No key, no network, no fixture recorded by hand."""
    summary = seed(demo)

    assert summary.reviewable >= 1, summary.sentence()
    assert summary.failures == [], summary.failures


def test_the_strong_candidate_is_ready_for_review(demo: Deps) -> None:
    seed(demo)

    runs = {run.candidate_id: run for run in _all_runs(demo)}

    assert is_reviewable(runs["rin-takahashi"].status)


def test_the_injected_candidate_is_quarantined_at_zero_cost(demo: Deps) -> None:
    """The security demonstration: the sentence is caught before any model
    call, so the run costs nothing and the reviewer sees why."""
    seed(demo)

    runs = {run.candidate_id: run for run in _all_runs(demo)}
    quarantined = runs["sam-okafor"]

    assert quarantined.status is RunStatus.QUARANTINED
    assert quarantined.llm_call_count == 0
    assert quarantined.total_input_tokens == 0


def test_the_sparse_candidate_is_not_scored(demo: Deps) -> None:
    """A one-page CV against a nine-point rubric produces a refusal to score,
    not a low score."""
    seed(demo)

    runs = {run.candidate_id: run for run in _all_runs(demo)}
    recommendation = demo.evidence.recommendation_for_run(runs["priya-raman"].run_id)

    assert recommendation is not None
    assert recommendation.band.value == "insufficient_information"
    assert recommendation.score is None


def test_nothing_was_sent_anywhere(demo: Deps) -> None:
    seed(demo)

    for run in _all_runs(demo):
        assert demo.deliveries.for_run(run.run_id) == []
        assert run.status is not RunStatus.DELIVERED


def test_seeding_twice_reuses_rather_than_reruns(demo: Deps) -> None:
    """`make seed` is idempotent. The second run finds every candidate already
    done and does nothing, which is what lets a person re-run the setup
    without doubling the queue."""
    seed(demo)
    second = seed(demo)

    assert second.reused == len(CANDIDATES)
    assert len(_all_runs(demo)) == len(CANDIDATES)


def test_every_result_says_it_came_from_the_stand_in(demo: Deps) -> None:
    """No model was involved, and the record must not suggest one was. Every
    usage row is marked unmeasured, so cost and token panels cannot present a
    size estimate as a provider's count."""
    seed(demo)

    for run in _all_runs(demo):
        for cost in demo.costs.for_run(run.run_id):
            assert cost.cost_usd is None


# --- the corpus itself ---------------------------------------------------------------


def test_the_corpus_is_where_demo_mode_reads(tmp_path: Path) -> None:
    written = build(tmp_path / "synthetic")

    assert written
    assert all(path.suffix == ".txt" for path in written)


def test_the_corpus_passes_the_pii_scanner(tmp_path: Path) -> None:
    """Every address and number is from a reserved range, so the generated
    files pass the scanner on their merits, not on the samples exemption."""
    from scripts import check_pii

    out = tmp_path / "corpus"
    build(out)

    for path in out.rglob("*.txt"):
        # Scanned as if it were anywhere: the point is that the content is
        # clean, not that its directory is exempt.
        assert check_pii.check_content(path, f"docs/{path.name}") == []


# --- what CI runs against the real data directory -----------------------------------------


@pytest.mark.skipif(
    os.environ.get("DEMO_MODE", "").lower() not in ("1", "true", "yes", "on"),
    reason="runs under `make smoke`, after `make seed`, against the real data directory",
)
def test_make_seed_left_a_reviewable_candidate() -> None:
    """The Makefile targets, not the functions. This is the only test that
    proves `make setup && make seed` did what the README says."""
    deps = build_deps(settings_from_env())
    try:
        runs = _all_runs(deps)
        assert any(is_reviewable(run.status) for run in runs), (
            "`make seed` did not leave a candidate ready to review"
        )
        assert deps.sinks == ()
    finally:
        close_thread_connection(deps.settings.db_path)


def _all_runs(deps: Deps) -> list:
    return deps.runs.list_by_status(tuple(RunStatus), limit=100)
