"""Every run accounted for.

The run record is what makes a result reproducible. It names the rubric that
scored it, the prompts that read it, the tier bindings that were in force, the
routing policy, and whether blind mode was on — so a band produced in March can
be re-derived in June, and a difference can be attributed to something specific
rather than to "the model changed".

A null in any of those turns a reproducible run into an anecdote. So they are
checked as a set, after a real run rather than on a hand-built object, because
the failure this catches is a field the pipeline forgot to fill in.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from application.deps import Deps
from application.use_cases.process_candidate import process_candidate
from domain.contracts.run import RunRecord
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from tests.workflow.test_end_to_end import (
    STRONG_ANSWERS,
    STRONG_CV,
    AnsweringModel,
    _FolderSource,
    _load_shipped_rubric,
    upload,
)

#: What must never be null on a completed run.
#:
#: Each one answers a question somebody will ask about a result later. The
#: hashes answer "scored by what"; the policy and the flags answer "under which
#: configuration"; the version answers "by which code".
REQUIRED = (
    "run_id",
    "content_key",
    "candidate_id",
    "role_id",
    "rubric_version",
    "rubric_hash",
    "prompt_bundle_hash",
    "model_tier_bindings_hash",
    "routing_policy_id",
    "calibration_status",
    "pipeline_version",
    "status",
    "started_at",
    "integrity_tier",
)

#: Counters that must be present and non-negative, though zero is a legitimate
#: value for every one of them.
COUNTERS = (
    "total_input_tokens",
    "total_output_tokens",
    "cached_input_tokens",
    "llm_call_count",
    "retry_count",
    "repair_count",
    "escalation_count",
    "validation_failure_count",
    "invalid_span_count",
    "override_count",
    "version",
)


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "completeness.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        blind_mode=False,
        sanitize_render_diff=False,
    )
    built = build_deps(settings)
    yield Deps(
        **{
            **built.__dict__,
            "rubric_loader": _load_shipped_rubric,
            "source": _FolderSource(tmp_path / "inbox"),
        }
    )
    close_thread_connection(settings.db_path)


@pytest.fixture
def completed(deps: Deps):
    """One candidate, all the way to a reviewer."""
    candidate = upload(deps, STRONG_CV)
    model = AnsweringModel(STRONG_CV, STRONG_ANSWERS)
    wired = Deps(**{**deps.__dict__, "models": model})

    result = process_candidate(candidate, "ai-engineer", wired)
    stored = wired.runs.get(result.run_id)
    assert stored is not None
    return stored


# --- the record ---------------------------------------------------------------------


@pytest.mark.parametrize("field", REQUIRED)
def test_no_required_field_is_null(completed: RunRecord, field: str) -> None:
    value = getattr(completed, field)

    assert value is not None, f"{field} is null on a completed run"
    assert value != "", f"{field} is empty on a completed run"


@pytest.mark.parametrize("field", COUNTERS)
def test_every_counter_is_present(completed: RunRecord, field: str) -> None:
    """Zero is a legitimate value. None is not: it means nobody counted."""
    value = getattr(completed, field)

    assert value is not None
    assert value >= 0


def test_the_content_key_is_real_after_intake(completed: RunRecord) -> None:
    """It starts as a placeholder because the documents have not been hashed
    yet. A run that reached a reviewer still carrying the placeholder could not
    be deduplicated against."""
    assert completed.content_key
    assert not completed.content_key.startswith("pending:")


def test_the_rubric_is_identified_by_hash(completed: RunRecord) -> None:
    """Two runs with the same hash were scored by the same rules. A run whose
    hash is not in the repository cannot be reproduced."""
    assert completed.rubric_hash
    assert completed.rubric_hash not in ("no-rubric", "unloadable")


def test_the_prompts_are_identified(completed: RunRecord) -> None:
    assert completed.prompt_bundle_hash
    assert completed.prompt_bundle_hash != "no-prompts"


def test_the_tier_bindings_are_identified(completed: RunRecord) -> None:
    """Binds the run to config/models.yaml without naming a model anywhere."""
    assert completed.model_tier_bindings_hash


def test_the_routing_policy_is_recorded(completed: RunRecord) -> None:
    """Without it the three-way cost comparison has no grouping key."""
    assert completed.routing_policy_id in ("routed", "all_cheap", "all_strong")


def test_the_completed_nodes_are_recorded(completed: RunRecord) -> None:
    """Resumption reads this, and so does anybody asking how far a run got."""
    assert completed.completed_nodes
    assert "ASSESS" in completed.completed_nodes


def test_the_run_is_finished_or_awaiting_a_person(completed: RunRecord) -> None:
    from domain.state_machine import is_reviewable

    assert completed.finished_at is not None or is_reviewable(completed.status)


# --- what a null would cost -----------------------------------------------------------


def test_the_record_survives_a_round_trip(deps: Deps, completed: RunRecord) -> None:
    """Read back from the database and validated again, because a field that
    only exists in memory is a field that is missing."""
    reloaded = deps.runs.get(completed.run_id)

    assert reloaded is not None
    for field in REQUIRED:
        assert getattr(reloaded, field) is not None


def test_a_cost_figure_is_absent_rather_than_zero(completed: RunRecord) -> None:
    """Unpriced by default. A zero here would propagate into every report as a
    measured figure."""
    assert completed.total_cost_usd is None


def test_the_token_counts_are_real(completed: RunRecord) -> None:
    """Tokens come from usage metadata whether or not prices are configured,
    which is what keeps the ceiling working unpriced."""
    assert completed.total_input_tokens > 0
    assert completed.total_output_tokens > 0


def test_every_required_field_is_declared_on_the_contract() -> None:
    """A field renamed on the contract without updating this list would leave
    the list checking nothing."""
    for field in (*REQUIRED, *COUNTERS):
        assert field in RunRecord.model_fields, f"{field} is not a field of RunRecord"
