"""What the system asks for when the documents did not say.

The brief's second demo case. A one-page CV against a nine-point rubric should
not produce a low score; it should produce a short list of specific questions
and an honest statement that there is not enough here to judge.

The distinction under test is the one the whole design rests on: absence is not
failure. A candidate whose CV never mentioned evaluation work has not failed the
evaluation criterion, and the difference is visible in the band, in the states,
and in what gets sent back to them.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from domain.contracts.enums import Band, CriterionState
from domain.contracts.recommendation import Recommendation
from domain.rules import AggregationFlags, aggregate
from domain.rules.resolve import CriterionResolution
from pipeline.compose import UNRESOLVED_STATES, Gap, find_gaps, template_question, template_request
from tests.builders import criterion, rubric

RUBRIC = rubric(
    criteria=[
        criterion(id="evaluation-practice", label="Measures quality before scaling", weight=5),
        criterion(id="production-engineering", label="Runs what they build", weight=4),
        criterion(id="python-depth", label="Substantial Python experience", weight=3),
        criterion(id="cost-awareness", label="Treats cost as a constraint", weight=2),
    ],
    min_coverage=0.70,
)


def resolution(criterion_id: str, state: CriterionState) -> CriterionResolution:
    rules = {
        CriterionState.MET: "R-MET",
        CriterionState.PARTIAL: "R-PARTIAL",
        CriterionState.NOT_MET: "R-NOT-MET",
        CriterionState.INSUFFICIENT_EVIDENCE: "R-INSUFFICIENT",
    }
    return CriterionResolution(
        criterion_id=criterion_id,
        state=state,
        rule_id=rules[state],
        supported_count=2 if state is CriterionState.MET else 0,
        contradicted_count=0,
    )


def recommend(states: dict[str, CriterionState]) -> Recommendation:
    return aggregate(
        [resolution(criterion_id, state) for criterion_id, state in states.items()],
        RUBRIC,
        run_id=uuid4(),
        flags=AggregationFlags(),
    )


SPARSE = dict.fromkeys([item.id for item in RUBRIC.criteria], CriterionState.INSUFFICIENT_EVIDENCE)


# --- the gate ---------------------------------------------------------------------


def test_a_sparse_cv_yields_insufficient_information() -> None:
    """Not a low band. A different kind of answer entirely."""
    assert recommend(SPARSE).band is Band.INSUFFICIENT_INFORMATION


def test_no_score_is_reported_below_the_gate() -> None:
    """A number here would be read as a judgement, and there is nothing to
    judge. ``None`` is the honest value and the contract permits it."""
    assert recommend(SPARSE).score is None


def test_silence_is_never_recorded_as_not_met() -> None:
    result = recommend(SPARSE)

    assert all(
        state is CriterionState.INSUFFICIENT_EVIDENCE for state in result.criterion_states.values()
    )
    assert CriterionState.NOT_MET not in result.criterion_states.values()


def test_the_gate_names_the_number_it_used() -> None:
    """A reviewer asking why it will not score this gets an answer with the
    threshold in it, not a policy statement."""
    result = recommend(SPARSE)
    gate = next(step for step in result.derivation if step.rule_id == "R-COVERAGE-GATE")

    assert gate.inputs["min_coverage"] == pytest.approx(0.70)
    assert "coverage" in gate.inputs


def test_partial_coverage_just_above_the_line_does_score() -> None:
    """The gate is a threshold, not a mood. Enough of the rubric assessed and a
    score appears."""
    states = {
        "evaluation-practice": CriterionState.MET,
        "production-engineering": CriterionState.MET,
        "python-depth": CriterionState.MET,
        "cost-awareness": CriterionState.INSUFFICIENT_EVIDENCE,
    }
    result = recommend(states)

    assert result.band is not Band.INSUFFICIENT_INFORMATION
    assert result.score is not None


# --- what gets asked --------------------------------------------------------------


def test_a_gap_is_derived_for_every_unresolved_point() -> None:
    """Derived from the states in code. A model is never asked which points are
    missing, because a model could invent one."""
    gaps = find_gaps(RUBRIC, recommend(SPARSE))

    assert {gap.criterion_id for gap in gaps} == set(SPARSE)


def test_a_met_criterion_produces_no_gap() -> None:
    """The failure a recruiter would notice: a candidate asked about something
    their CV answered on page two."""
    states = dict(SPARSE)
    states["python-depth"] = CriterionState.MET

    gaps = find_gaps(RUBRIC, recommend(states))

    assert "python-depth" not in {gap.criterion_id for gap in gaps}


def test_a_partly_answered_point_is_still_a_gap() -> None:
    states = dict(SPARSE)
    states["python-depth"] = CriterionState.PARTIAL

    gaps = find_gaps(RUBRIC, recommend(states))

    assert "python-depth" in {gap.criterion_id for gap in gaps}


def test_gaps_come_back_in_rubric_order() -> None:
    """So the recruiter's list reads the way their rubric does, rather than the
    order a dictionary happened to iterate."""
    gaps = find_gaps(RUBRIC, recommend(SPARSE))

    assert [gap.criterion_id for gap in gaps] == [item.id for item in RUBRIC.criteria]


def test_unresolved_states_are_exactly_the_two_open_ones() -> None:
    """Pinned deliberately. Adding NOT_MET here would start asking candidates to
    explain points the documents actually answered against them."""
    assert set(UNRESOLVED_STATES) == {
        CriterionState.INSUFFICIENT_EVIDENCE,
        CriterionState.PARTIAL,
    }


# --- the wording ------------------------------------------------------------------


def test_a_request_names_the_thing_that_was_missing() -> None:
    """A bare "please send more information" would be useless to both sides."""
    gap = Gap(
        criterion_id="evaluation-practice",
        label="Measures quality before scaling",
        question="Does the document show measurement of system quality?",
        state=CriterionState.INSUFFICIENT_EVIDENCE,
    )

    assert "measures quality before scaling" in template_request(gap).lower()


def test_an_absent_point_and_a_partial_one_are_asked_differently() -> None:
    """Telling somebody their CV does not mention what it does mention reads as
    carelessness, and they are right."""
    absent = Gap("a", "Measures quality", "?", CriterionState.INSUFFICIENT_EVIDENCE)
    partial = Gap("a", "Measures quality", "?", CriterionState.PARTIAL)

    assert "does not mention" in template_request(absent)
    assert "does not mention" not in template_request(partial)


def test_a_template_question_asks_for_an_account_not_an_adjective() -> None:
    """Asking whether somebody is good at something collects a yes. Asking for
    an occasion collects something a reviewer can weigh."""
    gap = Gap("a", "Runs what they build", "?", CriterionState.INSUFFICIENT_EVIDENCE)

    assert template_question(gap).lower().startswith("tell me about a time")


def test_templates_are_complete_sentences() -> None:
    """They are sent to a person as written."""
    for state in UNRESOLVED_STATES:
        gap = Gap("a", "Runs what they build", "?", state)
        for text in (template_request(gap), template_question(gap)):
            assert text[0].isupper()
            assert text.rstrip().endswith("?")


def test_no_template_leaks_an_identifier() -> None:
    """The label is written for a person; the id is not."""
    gap = Gap(
        criterion_id="production-engineering",
        label="Runs what they build",
        question="?",
        state=CriterionState.INSUFFICIENT_EVIDENCE,
    )

    assert "production-engineering" not in template_request(gap)
    assert "production-engineering" not in template_question(gap)
