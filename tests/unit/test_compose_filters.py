"""What composition refuses to pass on.

Composition is the one place a model writes text that goes to a candidate. Two
things follow from that. It is the highest-value target for a prompt injection,
and it is the only place where a model's phrasing could put a discriminatory
question in a recruiter's outbox.

So the model is asked for wording and nothing else, and everything it returns is
checked against facts derived in code: a question about a point that was not a
gap is dropped, and a question that mentions a protected attribute is dropped.
Failure of the whole call is not a failure of the run.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from application.deps import Deps
from domain.contracts.enums import CriterionState, RunStatus
from domain.contracts.responses import CompositionResponse
from domain.contracts.run_state import NodeStatus, RunState
from domain.ports.models import GenerationResult, ModelUnavailable, Usage
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline.compose import Gap, mentions_forbidden, node
from tests.builders import criterion, rubric


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    """Real repositories against a throwaway database.

    The node persists nothing itself, but it holds handles that must exist, and
    a fake Deps would only prove the fake was consistent with itself.
    """
    settings = settings_from_env(
        db_path=str(tmp_path / "compose.sqlite"), blob_dir=str(tmp_path / "blobs")
    )
    yield build_deps(settings)
    close_thread_connection(settings.db_path)


RUBRIC = rubric(
    criteria=[
        criterion(id="evaluation-practice", label="Measures quality before scaling", weight=5),
        criterion(id="production-engineering", label="Runs what they build", weight=4),
    ],
    min_coverage=0.5,
)


# --- the filter itself -------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "How old are you?",
        "Do you have children at home?",
        "What is your nationality?",
        "Are you married?",
        "Would you be a good culture fit here?",
        "Do you have a disability we should know about?",
        "Are you a native speaker?",
    ],
)
def test_a_question_about_a_protected_attribute_is_caught(question: str) -> None:
    """Each of these is unlawful to ask in most of the world and wrong to ask
    anywhere. The prompt forbids them; this is the check that they did not
    arrive anyway."""
    assert mentions_forbidden(question, list(RUBRIC.forbidden_attributes))


@pytest.mark.parametrize(
    "question",
    [
        "Tell me about a time you shipped a language-model feature.",
        "How did you replace the manual triage process?",
        "What did the on-call rotation look like?",
        "How did you decide which cases to route to a cheaper model?",
        "Tell me about a race condition you debugged.",
        "How did you manage the storage of scanned documents?",
        "How did you trace parent-child spans across services?",
    ],
)
def test_ordinary_interview_questions_are_not_caught(question: str) -> None:
    """The other half of the property, and the easier one to get wrong. A filter
    that eats "language" removes exactly the questions this role needs."""
    assert not mentions_forbidden(question, list(RUBRIC.forbidden_attributes))


def test_a_rubric_can_add_its_own_attribute() -> None:
    """A recruiter protecting against something new edits YAML, not code."""
    assert not mentions_forbidden("Which university did you attend?", [])
    assert mentions_forbidden("Which university did you attend?", ["university"])


def test_an_attribute_written_with_underscores_still_matches() -> None:
    """Rubrics write ``marital_status``; sentences write "marital status"."""
    assert mentions_forbidden("What is your marital status?", ["marital_status"])


# --- the node ----------------------------------------------------------------------


@dataclass
class _Recommendation:
    criterion_states: dict[str, CriterionState]
    requires_human: bool = False


class _Prompt:
    versioned_id = "composition/questions@1"

    def render(self, **_: object) -> str:
        return "write some questions"


class _Prompts:
    bundle_hash = "test"

    def get(self, _name: str) -> _Prompt:
        return _Prompt()


class _Model:
    """Returns whatever the test told it to, without a network."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls = 0

    def structured_generate(self, request: Any) -> GenerationResult:
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return GenerationResult(
            parsed=self.response,
            raw_text="{}",
            usage=Usage(10, 5),
            tier=request.tier,
            latency_ms=1,
        )


def state_with_gaps() -> RunState:
    recommendation = _Recommendation(
        criterion_states={
            "evaluation-practice": CriterionState.INSUFFICIENT_EVIDENCE,
            "production-engineering": CriterionState.MET,
        }
    )
    from datetime import UTC, datetime
    from uuid import uuid4

    return RunState(
        run_id=uuid4(),
        candidate_id="cand-0007",
        role_id="ai-engineer",
        status=RunStatus.AGGREGATED,
        started_at=datetime.now(UTC),
    ).model_copy(update={"rubric": RUBRIC, "recommendation": recommendation})


def compose_with(response: Any, base: Deps) -> tuple[Any, _Model]:
    model = _Model(response)
    deps = Deps(**{**base.__dict__, "models": model, "prompts": _Prompts()})
    return node(state_with_gaps(), deps), model


def response(*, requests: list[tuple[str, str]], questions: list[tuple[str, str]]):
    return CompositionResponse.model_validate(
        {
            "information_requests": [
                {"criterion_id": cid, "request": text} for cid, text in requests
            ],
            "interview_questions": [
                {"criterion_id": cid, "question": text} for cid, text in questions
            ],
        }
    )


def test_a_forbidden_question_is_dropped(deps: Deps) -> None:
    """The headline case. The model wrote it; the recruiter never sees it."""
    result, _ = compose_with(
        response(
            requests=[("evaluation-practice", "Could you describe how you measured quality?")],
            questions=[("evaluation-practice", "How old were you when you started?")],
        ),
        deps,
    )

    package = result.state.package
    assert package.interview_questions == ()
    assert package.dropped_questions == 1


def test_a_clean_question_survives(deps: Deps) -> None:
    result, _ = compose_with(
        response(
            requests=[("evaluation-practice", "Could you describe how you measured quality?")],
            questions=[("evaluation-practice", "Tell me about an evaluation suite you built.")],
        ),
        deps,
    )

    assert len(result.state.package.interview_questions) == 1


def test_a_question_about_something_that_was_not_a_gap_is_dropped(deps: Deps) -> None:
    """The model does not get to decide what was missing. A question about a
    criterion the documents answered would read as if nobody had looked."""
    result, _ = compose_with(
        response(
            requests=[],
            questions=[("production-engineering", "Tell me about running a service.")],
        ),
        deps,
    )

    assert result.state.package.interview_questions == ()
    assert result.state.package.dropped_questions == 1


def test_a_question_about_an_invented_criterion_is_dropped(deps: Deps) -> None:
    result, _ = compose_with(
        response(requests=[], questions=[("leadership", "Tell me about leading a team.")]),
        deps,
    )

    assert result.state.package.interview_questions == ()


def test_the_drop_is_counted_rather_than_silent(deps: Deps) -> None:
    """A question that vanishes with no record is a question nobody can audit."""
    result, _ = compose_with(
        response(requests=[], questions=[("evaluation-practice", "Are you married?")]),
        deps,
    )

    dropped = next(event for event in result.events if event.name == "compose.questions_dropped")
    assert dropped.payload["count"] == 1


def test_a_gap_the_model_skipped_still_gets_a_request(deps: Deps) -> None:
    """Otherwise a dropped question becomes a gap the recruiter never hears
    about, which is worse than a dull one."""
    result, _ = compose_with(response(requests=[], questions=[]), deps)

    package = result.state.package
    assert {cid for cid, _ in package.information_requests} == {"evaluation-practice"}


# --- when composition fails ---------------------------------------------------------


def test_an_unavailable_model_produces_templates_not_an_empty_package(deps: Deps) -> None:
    """A reviewer with plain questions is better served than one with none."""
    result, _ = compose_with(ModelUnavailable("no provider configured"), deps)

    package = result.state.package
    assert package.used_templates is True
    assert len(package.information_requests) == 1
    assert len(package.interview_questions) == 1


def test_an_unusable_response_produces_templates(deps: Deps) -> None:
    result, _ = compose_with(None, deps)

    assert result.state.package.used_templates is True


def test_a_failed_composition_degrades_the_run_rather_than_failing_it(deps: Deps) -> None:
    """Composition is the last node that can still be wrong without costing
    anything: everything a reviewer needs to decide already exists."""
    result, _ = compose_with(ModelUnavailable("no provider configured"), deps)

    assert result.status is NodeStatus.DEGRADED
    assert result.error is None


def test_the_fallback_says_why_it_fell_back(deps: Deps) -> None:
    result, _ = compose_with(ModelUnavailable("no provider configured"), deps)

    event = next(item for item in result.events if item.name == "compose.fallback")
    assert event.payload["reason"] == "model_unavailable"


def test_no_gaps_means_no_model_call(deps: Deps) -> None:
    """A candidate whose documents answered everything costs nothing here."""
    from datetime import UTC, datetime
    from uuid import uuid4

    state = RunState(
        run_id=uuid4(),
        candidate_id="cand-0007",
        role_id="ai-engineer",
        status=RunStatus.AGGREGATED,
        started_at=datetime.now(UTC),
    ).model_copy(
        update={
            "rubric": RUBRIC,
            "recommendation": _Recommendation(
                criterion_states={
                    "evaluation-practice": CriterionState.MET,
                    "production-engineering": CriterionState.MET,
                }
            ),
        }
    )
    model = _Model(None)
    result = node(state, Deps(**{**deps.__dict__, "models": model, "prompts": _Prompts()}))

    assert model.calls == 0
    assert result.state.package.gaps == ()


# --- what the node hands on ----------------------------------------------------------


def test_composition_lands_on_composed(deps: Deps) -> None:
    """Whether a person sees this flagged is decided at REVIEW, which can see
    every reason. Composition only writes sentences."""
    result, _ = compose_with(response(requests=[], questions=[]), deps)

    assert result.next_status is RunStatus.COMPOSED


def test_a_missing_recommendation_fails_the_node(deps: Deps) -> None:
    """There is nothing to compose questions about, and inventing some would be
    the system talking to a candidate with no basis at all."""
    from datetime import UTC, datetime
    from uuid import uuid4

    state = RunState(
        run_id=uuid4(),
        candidate_id="cand-0007",
        role_id="ai-engineer",
        status=RunStatus.AGGREGATED,
        started_at=datetime.now(UTC),
    ).model_copy(update={"rubric": RUBRIC})

    result = node(state, Deps(**{**deps.__dict__, "models": _Model(None), "prompts": _Prompts()}))

    assert result.status is NodeStatus.FAILED
    assert result.error.error_code == "NO_RECOMMENDATION"


def test_the_failure_message_is_written_for_a_person(deps: Deps) -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    state = RunState(
        run_id=uuid4(),
        candidate_id="cand-0007",
        role_id="ai-engineer",
        status=RunStatus.AGGREGATED,
        started_at=datetime.now(UTC),
    ).model_copy(update={"rubric": RUBRIC})

    result = node(state, Deps(**{**deps.__dict__, "models": _Model(None), "prompts": _Prompts()}))

    assert "None" not in result.error.message_redacted
    assert result.error.message_redacted.endswith(".")


# --- what goes into the prompt --------------------------------------------------------


def test_the_prompt_never_carries_the_candidates_name(deps: Deps) -> None:
    """This call writes text that is sent onward. Nothing identifying belongs in
    it, whatever blind mode is set to."""
    captured: list[Any] = []

    class Capturing(_Model):
        def structured_generate(self, request: Any) -> GenerationResult:
            captured.append(request)
            return super().structured_generate(request)

    model = Capturing(response(requests=[], questions=[]))
    node(state_with_gaps(), Deps(**{**deps.__dict__, "models": model, "prompts": _Prompts()}))

    for request in captured:
        text = request.system_prompt + " ".join(block.content for block in request.user_blocks)
        assert "cand-0007" not in text


def test_the_gap_list_sent_to_the_model_names_only_gaps(deps: Deps) -> None:
    """The model is told what is missing, never asked."""
    gap = Gap(
        criterion_id="evaluation-practice",
        label="Measures quality before scaling",
        question="?",
        state=CriterionState.INSUFFICIENT_EVIDENCE,
    )

    assert gap.is_absent is True
    assert Gap("a", "b", "?", CriterionState.PARTIAL).is_absent is False
