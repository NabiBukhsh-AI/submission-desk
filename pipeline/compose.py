"""The COMPOSE node.

Produces what a recruiter actually sends: a request for the information the
documents did not contain, and questions to ask if they get an interview.

The division of labour is the point. *Which* gaps exist is worked out here, in
code, from the criterion states. Only the *wording* is asked of a model. A model
allowed to decide what was missing could invent a gap, and a recruiter would
send a candidate a question about something their CV answered on page two.

Composition failing does not fail the run. The fallback is a template built from
the criterion's own wording, which is duller than a written question and
perfectly usable. A reviewer with plain questions is better served than one with
none.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from application.deps import Deps
from domain.contracts.enums import CriterionState, RunStatus
from domain.contracts.errors import ErrorRecord
from domain.contracts.recommendation import Recommendation
from domain.contracts.responses import CompositionResponse
from domain.contracts.rubric import Criterion, RoleRubric
from domain.contracts.run_state import DomainEvent, NodeResult, NodeStatus, RunState
from domain.fairness import mentions_protected_attribute
from domain.ports.models import BlockKind, GenerationRequest, ModelUnavailable, PromptBlock

#: States that mean the documents left something open. Derived here rather than
#: asked for, so the list is a fact about the assessment rather than a model's
#: impression of one.
UNRESOLVED_STATES = (CriterionState.INSUFFICIENT_EVIDENCE, CriterionState.PARTIAL)


@dataclass(frozen=True)
class Gap:
    """One point the documents did not settle."""

    criterion_id: str
    label: str
    question: str
    state: CriterionState

    @property
    def is_absent(self) -> bool:
        return self.state is CriterionState.INSUFFICIENT_EVIDENCE


@dataclass(frozen=True)
class SubmissionPackage:
    """What the reviewer is handed."""

    gaps: tuple[Gap, ...]
    information_requests: tuple[tuple[str, str], ...]
    interview_questions: tuple[tuple[str, str], ...]
    used_templates: bool = False
    dropped_questions: int = 0


def find_gaps(rubric: RoleRubric, recommendation: Recommendation) -> list[Gap]:
    """The points the documents did not settle.

    Deterministic, and in rubric order so the recruiter's list reads the way
    their rubric does. A model is never asked which points these are.
    """
    by_id = {criterion.id: criterion for criterion in rubric.criteria}

    return [
        Gap(
            criterion_id=criterion_id,
            label=by_id[criterion_id].label,
            question=by_id[criterion_id].question,
            state=state,
        )
        for criterion_id, state in recommendation.criterion_states.items()
        if state in UNRESOLVED_STATES and criterion_id in by_id
    ]


def template_request(gap: Gap) -> str:
    """A sendable sentence, built from the criterion's own wording.

    The fallback when composition fails, and duller than a written one on
    purpose: it cannot be wrong about what is missing, because it says only
    what the rubric already said.
    """
    if gap.is_absent:
        return (
            f"Your application does not mention {gap.label.lower()}. "
            f"Could you tell us about your experience with this?"
        )
    return (
        f"Your application touches on {gap.label.lower()} but does not say much. "
        f"Could you give us more detail?"
    )


def template_question(gap: Gap) -> str:
    """An interview question built from the criterion.

    Asks for an account of something done rather than a self-assessment, which
    is the difference between a question that settles a point and one that
    collects an adjective.
    """
    return f"Tell me about a time you worked on {gap.label.lower()}. What did you do?"


def mentions_forbidden(text: str, forbidden_attributes: list[str]) -> bool:
    """Whether a generated question strays onto protected ground.

    A post-hoc filter over what came back, because the prompt asking for
    something is a request and this is a check. The rubric's own list is added
    to the shared one, so a recruiter adding an attribute protects against it
    without a code change.
    """
    return mentions_protected_attribute(text, forbidden_attributes)


def node(state: RunState, deps: Deps) -> NodeResult:
    """Write the package the reviewer receives."""
    rubric = state.rubric
    recommendation = state.recommendation

    if rubric is None or recommendation is None:
        return _failed(state, "No recommendation was available to package.", "NO_RECOMMENDATION")

    gaps = find_gaps(rubric, recommendation)
    package, events = _compose(state, deps, rubric, gaps)

    carried = state.model_copy(update={"package": package})

    events.append(
        DomainEvent(
            name="compose.package",
            payload={
                "gaps": len(package.gaps),
                "questions": len(package.interview_questions),
                "requests": len(package.information_requests),
                "dropped_questions": package.dropped_questions,
                "used_templates": package.used_templates,
            },
        )
    )

    return NodeResult(
        state=carried,
        status=NodeStatus.DEGRADED if package.used_templates else NodeStatus.OK,
        events=tuple(events),
        # Whether a person sees this flagged is decided at REVIEW, which is the
        # node that can see every reason. Composition only writes sentences.
        next_status=RunStatus.COMPOSED,
    )


def _compose(
    state: RunState, deps: Deps, rubric: RoleRubric, gaps: list[Gap]
) -> tuple[SubmissionPackage, list[DomainEvent]]:
    """Ask a model to phrase the gaps, falling back to templates."""
    events: list[DomainEvent] = []

    if not gaps:
        return SubmissionPackage(gaps=(), information_requests=(), interview_questions=()), events

    try:
        response = _ask(state, deps, rubric, gaps)
    except ModelUnavailable:
        events.append(DomainEvent(name="compose.fallback", payload={"reason": "model_unavailable"}))
        return _from_templates(gaps), events

    if response is None:
        events.append(DomainEvent(name="compose.fallback", payload={"reason": "invalid_response"}))
        return _from_templates(gaps), events

    known = {gap.criterion_id for gap in gaps}
    forbidden = list(rubric.forbidden_attributes)
    dropped = 0

    requests: list[tuple[str, str]] = []
    for asked in response.information_requests:
        if asked.criterion_id not in known or mentions_forbidden(asked.request, forbidden):
            dropped += 1
            continue
        requests.append((asked.criterion_id, asked.request))

    questions: list[tuple[str, str]] = []
    for question in response.interview_questions:
        if question.criterion_id not in known or mentions_forbidden(question.question, forbidden):
            dropped += 1
            continue
        questions.append((question.criterion_id, question.question))

    if dropped:
        events.append(DomainEvent(name="compose.questions_dropped", payload={"count": dropped}))

    # A gap the model did not phrase still gets a request, because a missing
    # question is a gap the recruiter never hears about.
    phrased = {criterion_id for criterion_id, _ in requests}
    for gap in gaps:
        if gap.criterion_id not in phrased:
            requests.append((gap.criterion_id, template_request(gap)))

    return (
        SubmissionPackage(
            gaps=tuple(gaps),
            information_requests=tuple(requests),
            interview_questions=tuple(questions),
            dropped_questions=dropped,
        ),
        events,
    )


def _ask(
    state: RunState, deps: Deps, rubric: RoleRubric, gaps: list[Gap]
) -> CompositionResponse | None:
    prompt = deps.prompts.get("composition/questions")

    request = GenerationRequest(
        call_site="compose.questions",
        tier=deps.settings.assess_tier,
        system_prompt=prompt.render(
            gaps="\n".join(_gap_line(gap) for gap in gaps),
            profile_summary=_summary_of(state),
        ),
        user_blocks=(
            PromptBlock(
                kind=BlockKind.INSTRUCTION,
                content="Write one information request and one interview question per point.",
            ),
        ),
        response_schema=CompositionResponse,
        # Some variety in phrasing is welcome here and nowhere else: this call
        # writes sentences, it does not decide anything.
        temperature=0.2,
        nonce=state.nonce or "",
    )

    result = deps.models.structured_generate(request)
    if result.ok and isinstance(result.parsed, CompositionResponse):
        return result.parsed
    return None


def _gap_line(gap: Gap) -> str:
    """One gap, as the prompt sees it. States what is open, never asks."""
    standing = "not addressed" if gap.is_absent else "partly addressed"
    return f"- {gap.criterion_id}: {gap.label} ({standing})"


def _summary_of(state: RunState) -> str:
    """What the documents did establish, in a line or two.

    Roles and employers only. Nothing about the person, because this text goes
    into a prompt and blind mode exists to keep it out.
    """
    profile = state.profile
    if profile is None or not profile.employment:
        return "The documents established little about this candidate's history."

    lines = []
    for entry in profile.employment[:4]:
        title = entry.title.value or "a role"
        employer = entry.employer.value or "an employer"
        lines.append(f"- {title} at {employer}")
    return "\n".join(lines)


def _from_templates(gaps: list[Gap]) -> SubmissionPackage:
    """The fallback. Duller than a written package, and complete."""
    return SubmissionPackage(
        gaps=tuple(gaps),
        information_requests=tuple((gap.criterion_id, template_request(gap)) for gap in gaps),
        interview_questions=tuple((gap.criterion_id, template_question(gap)) for gap in gaps),
        used_templates=True,
    )


def questions_for(rubric: RoleRubric, criterion_id: str) -> Criterion | None:
    return next((item for item in rubric.criteria if item.id == criterion_id), None)


def _failed(state: RunState, message: str, error_code: str) -> NodeResult:
    return NodeResult(
        state=state,
        status=NodeStatus.FAILED,
        events=(DomainEvent(name="compose.failed", payload={"reason": error_code}),),
        error=ErrorRecord(
            error_id=uuid4(),
            run_id=state.run_id,
            node="COMPOSE",
            error_code=error_code,
            error_class="ComposeFailed",
            message_redacted=message,
            retryable=False,
            attempt=1,
            resulting_state=RunStatus.NEEDS_REVIEW,
            occurred_at=datetime.now(UTC),
        ),
    )
