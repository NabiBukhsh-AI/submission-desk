"""A reviewer disagrees, and the band is recomputed rather than typed.

The property this module exists for is in ``test_the_two_paths_agree``: the
interface and the pipeline call the same ``aggregate()``, so a reviewer's
override produces a band the pipeline could have produced from the same states.
If the interface had its own scoring path, the derivation shown beside an
overridden band would be a fiction.

Everything else here follows from that. The reviewer changes criterion states,
never a number. Each change carries a reason code, because an override with no
reason is a complaint and an override with one is the raw material for the next
rubric change.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from application.deps import Deps
from application.use_cases.recompute_recommendation import (
    OVERRIDE_RULE,
    recompute,
    resolutions_from,
)
from application.use_cases.submit_review import submit_review
from domain.contracts.enums import Band, CriterionState, OverrideReason, ReviewAction
from domain.contracts.review import Override
from domain.rules import AggregationFlags, aggregate
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from tests.workflow.conftest_review import RUBRIC, seed_run, wire


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "override.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        reviewer_id="rec-014",
    )
    yield wire(build_deps(settings))
    close_thread_connection(settings.db_path)


def override(
    criterion_id: str,
    previous: CriterionState,
    new: CriterionState,
    reason: OverrideReason = OverrideReason.EVIDENCE_MISSED,
) -> Override:
    return Override(
        criterion_id=criterion_id,
        previous_state=previous,
        new_state=new,
        reason_code=reason,
        reason_text="The CV describes this on page two and the assessment missed it.",
    )


# --- the property that matters --------------------------------------------------


def test_the_two_paths_agree(deps: Deps) -> None:
    """The interface and the pipeline produce identical output for identical
    input, because they are the same function.

    Asserted on the whole recommendation rather than on the band alone: two
    paths that agreed on the number and disagreed on the derivation would be
    worse than two that disagreed openly.
    """
    run = seed_run(deps)
    changes = [
        override("python-depth", CriterionState.MET, CriterionState.NOT_MET),
    ]

    through_the_interface = recompute(run.run_id, deps, changes).recommendation

    # The same states, handed straight to the rule engine the pipeline uses.
    assessments = deps.evidence.assessments_for_run(run.run_id)
    through_the_pipeline = aggregate(
        resolutions_from(assessments, changes),
        RUBRIC,
        run_id=run.run_id,
        flags=AggregationFlags(),
    )

    assert through_the_interface.band is through_the_pipeline.band
    assert through_the_interface.score == through_the_pipeline.score
    assert through_the_interface.criterion_states == through_the_pipeline.criterion_states
    assert [step.rule_id for step in through_the_interface.derivation] == [
        step.rule_id for step in through_the_pipeline.derivation
    ]


def test_an_override_is_recorded_in_the_derivation(deps: Deps) -> None:
    """A derivation showing R-MET for a criterion a person changed by hand would
    be claiming the evidence supported something it did not."""
    run = seed_run(deps)
    changes = [override("python-depth", CriterionState.MET, CriterionState.NOT_MET)]

    resolutions = resolutions_from(deps.evidence.assessments_for_run(run.run_id), changes)
    changed = next(item for item in resolutions if item.criterion_id == "python-depth")

    assert changed.rule_id == OVERRIDE_RULE


def test_an_unchanged_criterion_keeps_its_rule(deps: Deps) -> None:
    run = seed_run(deps)
    changes = [override("python-depth", CriterionState.MET, CriterionState.NOT_MET)]

    resolutions = resolutions_from(deps.evidence.assessments_for_run(run.run_id), changes)
    untouched = next(item for item in resolutions if item.criterion_id == "evaluation-practice")

    assert untouched.rule_id == "R-MET"


# --- what an override does to the band --------------------------------------------


def test_lowering_a_criterion_can_lower_the_band(deps: Deps) -> None:
    run = seed_run(deps)
    before = recompute(run.run_id, deps).recommendation

    after = recompute(
        run.run_id,
        deps,
        [
            override("evaluation-practice", CriterionState.MET, CriterionState.NOT_MET),
            override("production-engineering", CriterionState.MET, CriterionState.NOT_MET),
        ],
    ).recommendation

    assert after.score < before.score


def test_raising_a_criterion_can_raise_the_band(deps: Deps) -> None:
    run = seed_run(deps)
    before = recompute(run.run_id, deps).recommendation

    after = recompute(
        run.run_id,
        deps,
        [override("cost-awareness", CriterionState.PARTIAL, CriterionState.MET)],
    ).recommendation

    assert after.score > before.score


def test_an_override_to_contradicted_requires_a_person(deps: Deps) -> None:
    """A reviewer marking a contradiction is telling the system the documents
    disagree with themselves, and that routes the same way it would have from
    the pipeline."""
    run = seed_run(deps)

    result = recompute(
        run.run_id,
        deps,
        [
            override(
                "python-depth",
                CriterionState.MET,
                CriterionState.CONTRADICTED,
                OverrideReason.EVIDENCE_MISREAD,
            )
        ],
    )

    assert result.recommendation.requires_human is True


def test_no_override_leaves_the_band_alone(deps: Deps) -> None:
    """The preview with nothing changed is the band already on the run."""
    run = seed_run(deps)

    assert recompute(run.run_id, deps).recommendation.band is run.final_band


def test_the_changed_criteria_are_reported(deps: Deps) -> None:
    """So the dialog can say "this changes two assessments" before it says what
    it does to the band."""
    run = seed_run(deps)

    result = recompute(
        run.run_id,
        deps,
        [
            override("python-depth", CriterionState.MET, CriterionState.NOT_MET),
            override("cost-awareness", CriterionState.PARTIAL, CriterionState.MET),
        ],
    )

    assert set(result.changed) == {"python-depth", "cost-awareness"}


def test_an_override_that_changes_nothing_is_refused_by_the_contract() -> None:
    """An override recording the state it already had is not an override, and
    would pollute the improvement loop with entries nobody disagreed about."""
    with pytest.raises(ValueError, match="changes nothing"):
        Override(
            criterion_id="python-depth",
            previous_state=CriterionState.MET,
            new_state=CriterionState.MET,
            reason_code=OverrideReason.EVIDENCE_MISSED,
            reason_text="a reason long enough to pass",
        )


# --- submitting an override -----------------------------------------------------------


def test_submitting_persists_the_overrides(deps: Deps) -> None:
    run = seed_run(deps)
    changes = [override("python-depth", CriterionState.MET, CriterionState.NOT_MET)]

    submit_review(run.run_id, ReviewAction.APPROVE, deps, overrides=changes)

    decision = deps.reviews.get_for_run(run.run_id)
    assert len(decision.overrides) == 1
    assert decision.overrides[0].criterion_id == "python-depth"


def test_the_stored_band_is_the_recomputed_one(deps: Deps) -> None:
    """The number in the preview is the number that gets saved, because the
    preview and the save call the same function."""
    run = seed_run(deps)
    changes = [
        override("evaluation-practice", CriterionState.MET, CriterionState.NOT_MET),
        override("production-engineering", CriterionState.MET, CriterionState.NOT_MET),
    ]
    previewed = recompute(run.run_id, deps, changes).recommendation.band

    result = submit_review(run.run_id, ReviewAction.APPROVE, deps, overrides=changes)

    assert result.band is previewed
    assert deps.reviews.get_for_run(run.run_id).post_override_band is previewed


def test_the_recommendation_on_the_run_is_updated(deps: Deps) -> None:
    """A reviewer who overrides and comes back tomorrow sees what they decided,
    not what the model originally said."""
    run = seed_run(deps)
    changes = [
        override("evaluation-practice", CriterionState.MET, CriterionState.NOT_MET),
        override("production-engineering", CriterionState.MET, CriterionState.NOT_MET),
        override("python-depth", CriterionState.MET, CriterionState.NOT_MET),
    ]

    submit_review(run.run_id, ReviewAction.APPROVE, deps, overrides=changes)

    stored = deps.evidence.recommendation_for_run(run.run_id)
    assert stored.criterion_states["python-depth"] is CriterionState.NOT_MET


def test_the_override_count_is_on_the_run(deps: Deps) -> None:
    run = seed_run(deps)
    changes = [
        override("python-depth", CriterionState.MET, CriterionState.NOT_MET),
        override("cost-awareness", CriterionState.PARTIAL, CriterionState.MET),
    ]

    submit_review(run.run_id, ReviewAction.APPROVE, deps, overrides=changes)

    assert deps.runs.get(run.run_id).override_count == 2


def test_overrides_are_available_for_the_improvement_loop(deps: Deps) -> None:
    """The most valuable record the system produces: a labelled disagreement
    with a reason code."""
    run = seed_run(deps)
    submit_review(
        run.run_id,
        ReviewAction.APPROVE,
        deps,
        overrides=[
            override(
                "python-depth",
                CriterionState.MET,
                CriterionState.NOT_MET,
                OverrideReason.SPAN_WRONG,
            )
        ],
    )

    rows = deps.reviews.list_overrides()

    assert rows
    assert rows[0][1] == "python-depth"
    assert rows[0][4] == OverrideReason.SPAN_WRONG.value


def test_a_reviewer_cannot_set_a_band_directly() -> None:
    """There is no argument for one. The band is derived or it is nothing."""
    import inspect

    signature = inspect.signature(submit_review)

    assert "band" not in signature.parameters
    assert "score" not in signature.parameters


def test_one_override_per_criterion_is_enforced(deps: Deps) -> None:
    """Two contradictory changes to the same criterion in one decision would
    resolve by whichever the code happened to apply last."""
    from domain.contracts.review import ReviewDecision

    with pytest.raises(ValueError, match="once per decision"):
        ReviewDecision.model_validate(
            {
                "decision_id": uuid4(),
                "run_id": uuid4(),
                "reviewer_id": "rec-014",
                "action": ReviewAction.APPROVE,
                "overrides": [
                    override("python-depth", CriterionState.MET, CriterionState.NOT_MET),
                    override("python-depth", CriterionState.MET, CriterionState.PARTIAL),
                ],
                "elapsed_seconds": 10,
                "decided_at": __import__("datetime").datetime.now(__import__("datetime").UTC),
                "run_version": 0,
            }
        )


# --- the coverage gate still applies ----------------------------------------------------


def test_overriding_everything_to_insufficient_reaches_the_gate(deps: Deps) -> None:
    """A reviewer who says the system read nothing correctly gets the honest
    answer rather than a low score."""
    run = seed_run(deps)

    result = recompute(
        run.run_id,
        deps,
        [
            override(criterion.id, state, CriterionState.INSUFFICIENT_EVIDENCE)
            for criterion, state in zip(
                RUBRIC.criteria,
                [
                    CriterionState.MET,
                    CriterionState.MET,
                    CriterionState.MET,
                    CriterionState.PARTIAL,
                ],
                strict=True,
            )
        ],
    )

    assert result.recommendation.band is Band.INSUFFICIENT_INFORMATION
    assert result.recommendation.score is None
