"""The ASSESS node: evidence in, evidence out, never a judgment.

Three properties carry this phase.

Criteria are independent. Assessing them in a different order produces
byte-identical results, which is what makes the parallelism safe and the
comparisons meaningful.

A fabricated quotation is rejected, not accepted. It is kept and shown to the
reviewer, and it never reaches the rule engine.

A budget ceiling produces insufficient evidence with a reason, not a low score.
"We did not check" and "the document says no" are different facts.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from application.budget import BudgetGuard
from application.deps import Deps
from domain.contracts import (
    CandidateDocument,
    CriterionKind,
    CriterionState,
    DocumentRole,
    ExtractionMethod,
    ModelTier,
    RunStatus,
    SpanValidation,
)
from domain.contracts.run_state import NodeStatus, RunState
from domain.contracts.source_text import OffsetRun, PageSpan, SourceText
from domain.fairness import mentions_protected_attribute
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.models.fake import ScriptedModelClient
from infrastructure.models.routing.policies import AllCheapPolicy, AllStrongPolicy, RoutedPolicy
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline.assess import node
from tests.builders import criterion, rubric
from tests.workflow.conftest import make_run_record

CV_TEXT = (
    "Ana Ferreira, Senior Backend Engineer. "
    "Owned a multi-service payments backend and its on-call rotation for two years. "
    "Introduced a nightly evaluation suite; releases were blocked when agreement fell below 0.8. "
    "Reduced p95 latency from 900ms to 210ms on the routing service."
)

QUOTATION = "Introduced a nightly evaluation suite"
FABRICATION = "Led a team of forty engineers across three continents"


def evidence_json(document_id, span: str, *, state: str = "supported", confidence: float = 0.85):
    start = CV_TEXT.find(span)
    return json.dumps(
        {
            "criterion_id": "evaluation-practice",
            "evidence": [
                {
                    "state": state,
                    "claim": "The document describes an evaluation suite gating releases.",
                    "verbatim_span": span,
                    "document_id": str(document_id),
                    "page_start": 1,
                    "page_end": 1,
                    "norm_start": max(start, 0),
                    "norm_end": max(start, 0) + len(span),
                    "confidence": confidence,
                }
            ],
        }
    )


def absent_json():
    return json.dumps(
        {
            "criterion_id": "evaluation-practice",
            "evidence": [
                {
                    "state": "insufficient_evidence",
                    "claim": "The document does not address this point anywhere.",
                    "confidence": 0.9,
                }
            ],
        }
    )


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "assess.sqlite"), blob_dir=str(tmp_path / "blobs")
    )
    yield build_deps(settings)
    close_thread_connection(settings.db_path)


def store_document(deps: Deps, run_id) -> SourceText:
    digest = uuid4().hex + uuid4().hex[:32]
    document = CandidateDocument(
        document_id=uuid4(),
        candidate_id="cand-0007",
        original_filename="cv.pdf",
        document_sha256=digest,
        mime_type="application/pdf",
        size_bytes=len(CV_TEXT),
        blob_path=f"data/blobs/{digest[:2]}/{digest}",
        doc_role=DocumentRole.CV,
        received_at=datetime.now(UTC),
    )
    deps.candidates.add_document(document, run_id=run_id)

    source = SourceText(
        document_id=document.document_id,
        raw_text=CV_TEXT,
        normalized_text=CV_TEXT,
        offset_runs=[OffsetRun(norm_start=0, raw_start=0, length=len(CV_TEXT))],
        pages=[
            PageSpan(
                page_number=1,
                norm_start=0,
                norm_end=len(CV_TEXT),
                extraction_method=ExtractionMethod.DIGITAL_PDF,
            )
        ],
        normalization_profile_id=deps.source_profile_id,
        extraction_confidence=0.95,
    )
    deps.candidates.put_source_text(source, document_sha256=digest)
    return source


def run_assess(
    deps: Deps,
    responses,
    *,
    role=None,
    policy=None,
    budget=None,
    blind: bool = False,
):
    """Assess one candidate.

    ``responses`` may be a callable taking the stored document's id, because a
    response citing a span has to name the document it came from, and that id is
    only known once the document exists.
    """
    record = make_run_record()
    deps.runs.create(record)
    source = store_document(deps, record.run_id)

    prepared = responses(source.document_id) if callable(responses) else responses
    client = ScriptedModelClient(responses=prepared)
    wired = Deps(
        **{
            **deps.__dict__,
            "models": client,
            "router": policy or RoutedPolicy(),
            "budget": budget
            if budget is not None
            else BudgetGuard(token_ceiling=120_000, max_escalations=3),
        }
    )
    state = RunState(
        run_id=record.run_id,
        candidate_id=record.candidate_id,
        role_id=record.role_id,
        status=RunStatus.CALIBRATED,
        started_at=record.started_at,
        nonce="a3f9",
        blind_mode=blind,
    )
    state = state.model_copy(update={"rubric": role or _one_criterion()})
    return node(state, wired), client, source


def _one_criterion(kind: CriterionKind = CriterionKind.STANDARD):
    return rubric(criteria=[criterion(id="evaluation-practice", kind=kind)], min_coverage=0.0)


def _three_criteria():
    return rubric(
        criteria=[
            criterion(id="evaluation-practice"),
            criterion(id="python-depth"),
            criterion(id="cost-awareness"),
        ],
        min_coverage=0.0,
    )


# --- evidence in, evidence out --------------------------------------------------


def test_a_located_quotation_becomes_accepted_evidence(deps: Deps) -> None:
    result, _, _ = run_assess(deps, lambda doc: [evidence_json(doc, QUOTATION)])

    assert result.status is NodeStatus.OK
    assessment = result.state.assessments[0]
    assert len(assessment.evidence) == 1
    assert assessment.evidence[0].span_validation in (
        SpanValidation.VALID_EXACT,
        SpanValidation.VALID_NORMALIZED,
    )


def test_a_fabricated_quotation_is_rejected_not_accepted(deps: Deps) -> None:
    """It is kept and shown to the reviewer, and it never reaches the rule
    engine. That is the whole hallucination check."""
    result, _, _ = run_assess(deps, lambda doc: [evidence_json(doc, FABRICATION)])

    assessment = result.state.assessments[0]
    assert assessment.evidence == []
    assert len(assessment.rejected_evidence) == 1
    assert assessment.rejected_evidence[0].span_validation is SpanValidation.INVALID_NOT_FOUND


def test_a_rejected_item_is_persisted_for_the_reviewer(deps: Deps) -> None:
    result, _, _ = run_assess(deps, lambda doc: [evidence_json(doc, FABRICATION)])

    stored = deps.evidence.evidence_for_run(result.state.run_id, rejected=True)
    assert len(stored) == 1


def test_absence_of_evidence_is_recorded_as_absence(deps: Deps) -> None:
    """Not a low score, and not not_met."""
    result, _, _ = run_assess(deps, [absent_json()])

    assessment = result.state.assessments[0]
    assert assessment.resolved_state is CriterionState.INSUFFICIENT_EVIDENCE
    assert assessment.resolution_rule_id == "R-INSUFFICIENT"


def test_a_response_with_a_score_field_fails_validation(deps: Deps) -> None:
    """The schema forbids extra fields, so a model trying to grade the candidate
    fails rather than having the field silently dropped.

    Two responses are supplied because a repair failure is itself an escalation
    trigger: the cheap tier being out of its depth is exactly when the stronger
    one is worth trying.
    """
    with_score = json.dumps(
        {"criterion_id": "evaluation-practice", "evidence": [], "overall_score": 8}
    )

    result, client, _ = run_assess(deps, [with_score, with_score])

    assert client.call_count == 2
    assert result.state.assessments[0].unassessed_reason == "repair_failed"


def test_a_repair_failure_escalates_once(deps: Deps) -> None:
    """Trigger 5. Schema trouble twice usually means the cheap tier is out of
    its depth on this document rather than that the prompt is unclear."""
    with_score = json.dumps(
        {"criterion_id": "evaluation-practice", "evidence": [], "overall_score": 8}
    )

    _, client, _ = run_assess(deps, [with_score, absent_json()])

    assert client.call_count == 2
    assert client.calls[1].tier is ModelTier.STRONG


def test_the_model_cannot_set_its_own_span_verdict(deps: Deps) -> None:
    """span_validation is absent from the response schema and assigned by the
    validator. This asserts the node does the assigning."""
    result, _, _ = run_assess(deps, lambda doc: [evidence_json(doc, QUOTATION)])

    item = result.state.assessments[0].evidence[0]
    assert item.span_validation is not SpanValidation.NOT_APPLICABLE


# --- independence -----------------------------------------------------------------


def test_criteria_are_assessed_independently(deps: Deps) -> None:
    """No criterion's prompt contains another criterion's result."""
    role = _three_criteria()
    responses = [absent_json(), absent_json(), absent_json()]

    _, client, _ = run_assess(deps, responses, role=role)

    for call in client.calls:
        sent = call.system_prompt
        others = {"python-depth", "cost-awareness", "evaluation-practice"}
        mentioned = {name for name in others if name in sent}
        assert len(mentioned) <= 1, f"a prompt mentioned several criteria: {mentioned}"


def test_every_criterion_is_assessed(deps: Deps) -> None:
    role = _three_criteria()

    result, _, _ = run_assess(deps, [absent_json()] * 3, role=role)

    assert {a.criterion_id for a in result.state.assessments} == {
        "evaluation-practice",
        "python-depth",
        "cost-awareness",
    }


def test_results_come_back_in_rubric_order(deps: Deps) -> None:
    """Criteria run in parallel, so the output must not depend on which thread
    finished first: a run whose evidence ordering varied would produce a
    different derivation trace each time."""
    role = _three_criteria()

    result, _, _ = run_assess(deps, [absent_json()] * 3, role=role)

    assert [a.criterion_id for a in result.state.assessments] == [
        "evaluation-practice",
        "python-depth",
        "cost-awareness",
    ]


# --- routing ------------------------------------------------------------------------


def test_all_cheap_never_reaches_for_the_strong_tier(deps: Deps) -> None:
    _, client, _ = run_assess(deps, [absent_json()], policy=AllCheapPolicy())

    assert all(call.tier is ModelTier.CHEAP for call in client.calls)


def test_all_strong_uses_the_strong_tier(deps: Deps) -> None:
    _, client, _ = run_assess(deps, [absent_json()], policy=AllStrongPolicy())

    assert all(call.tier is ModelTier.STRONG for call in client.calls)


def test_a_high_stakes_criterion_starts_strong_under_routing(deps: Deps) -> None:
    role = _one_criterion(CriterionKind.HIGH_STAKES)

    _, client, _ = run_assess(deps, [absent_json()], role=role, policy=RoutedPolicy())

    assert client.calls[0].tier is ModelTier.STRONG


def test_a_fabricated_span_escalates_and_both_results_are_kept(deps: Deps) -> None:
    """The strong result replaces the cheap one for scoring, and both persist.
    That asymmetry is what makes escalation value measurable afterwards."""
    result, client, _ = run_assess(
        deps, lambda doc: [evidence_json(doc, FABRICATION), evidence_json(doc, QUOTATION)]
    )

    assessment = result.state.assessments[0]
    assert client.call_count == 2
    assert assessment.escalated is True
    assert assessment.escalation_trigger == "invalid_span"
    assert assessment.tier_used is ModelTier.STRONG


def test_an_escalation_records_what_it_changed(deps: Deps) -> None:
    """Per trigger, the evaluation reports how often escalating changed an
    answer. That needs the before state recorded at the time."""
    result, _, _ = run_assess(
        deps, lambda doc: [evidence_json(doc, FABRICATION), evidence_json(doc, QUOTATION)]
    )

    assessment = result.state.assessments[0]
    assert assessment.pre_escalation_state is not None
    assert assessment.escalation_changed_state is not None


def test_a_criterion_escalates_at_most_once(deps: Deps) -> None:
    """Two failures do not buy a third attempt."""
    _, client, _ = run_assess(
        deps, lambda doc: [evidence_json(doc, FABRICATION), evidence_json(doc, FABRICATION)]
    )

    assert client.call_count == 2


# --- the budget ----------------------------------------------------------------------


def test_a_run_at_its_ceiling_assesses_nothing_further(deps: Deps) -> None:
    """The criteria become insufficient evidence with a reason, not a low
    score. A budget ceiling is a fact about the run, not about the candidate."""
    exhausted = BudgetGuard(token_ceiling=100, max_escalations=3)
    role = _three_criteria()
    record = make_run_record()
    deps.runs.create(record)
    store_document(deps, record.run_id)
    exhausted.record(record.run_id, input_tokens=100, output_tokens=0)

    client = ScriptedModelClient(responses=[])
    wired = Deps(
        **{**deps.__dict__, "models": client, "router": RoutedPolicy(), "budget": exhausted}
    )
    state = RunState(
        run_id=record.run_id,
        candidate_id=record.candidate_id,
        role_id=record.role_id,
        status=RunStatus.CALIBRATED,
        started_at=record.started_at,
    ).model_copy(update={"rubric": role})

    result = node(state, wired)

    assert client.call_count == 0
    assert result.status is NodeStatus.DEGRADED
    assert result.next_status is RunStatus.MANUAL_REVIEW_REQUIRED
    assert all(a.unassessed_reason == "budget_ceiling" for a in result.state.assessments)


def test_a_capped_run_marks_itself_so_aggregation_can_say_why(deps: Deps) -> None:
    exhausted = BudgetGuard(token_ceiling=10, max_escalations=3)
    record = make_run_record()
    deps.runs.create(record)
    store_document(deps, record.run_id)
    exhausted.record(record.run_id, input_tokens=10, output_tokens=0)

    wired = Deps(
        **{
            **deps.__dict__,
            "models": ScriptedModelClient(responses=[]),
            "router": RoutedPolicy(),
            "budget": exhausted,
        }
    )
    state = RunState(
        run_id=record.run_id,
        candidate_id=record.candidate_id,
        role_id=record.role_id,
        status=RunStatus.CALIBRATED,
        started_at=record.started_at,
    ).model_copy(update={"rubric": _one_criterion()})

    result = node(state, wired)

    assert result.state.budget_capped is True


# --- forbidden content -----------------------------------------------------------------


def test_a_claim_mentioning_a_protected_attribute_is_dropped(deps: Deps) -> None:
    """The schema stops a model recording this as a field. This stops it
    arriving as prose in a claim, which is where an instruction-following model
    would put it."""
    result, _, _ = run_assess(
        deps, lambda doc: [_claiming(doc, "The candidate is 34 years old and married.")]
    )

    assessment = result.state.assessments[0]
    assert assessment.evidence == []
    assert assessment.rejected_evidence == []


def test_the_drop_is_counted_rather_than_silent(deps: Deps) -> None:
    result, _, _ = run_assess(
        deps, lambda doc: [_claiming(doc, "Their nationality suggests strong English.")]
    )

    payload = next(
        event.payload for event in result.events if event.name == "assess.criterion_assessed"
    )
    assert payload["dropped_forbidden"] == 1


@pytest.mark.parametrize(
    "claim",
    [
        "They are 42 years old.",
        "Their gender is not stated.",
        "Their nationality suggests strong English.",
        "She is married with two children at home.",
        "The candidate mentions a disability.",
        "A good culture fit for this team.",
    ],
)
def test_the_filter_covers_the_protected_grounds(claim: str) -> None:
    assert mentions_protected_attribute(claim)


@pytest.mark.parametrize(
    "claim",
    [
        "Shipped a language-model feature used by the support team.",
        "Replaced a manual triage process with an automated one.",
        "Fixed a race condition in the scheduler.",
        "Rolled out single sign-on across three services.",
        "Managed the storage of images and their metadata.",
        "Disabled the legacy endpoint after migration.",
    ],
)
def test_ordinary_engineering_prose_survives(claim: str) -> None:
    """The failure this filter is most likely to cause.

    A substring rule reads "age" inside "language" and "triage", which on an AI
    rubric deletes the evidence for the criteria that matter most. It is worth a
    test of its own because a dropped claim is indistinguishable from a model
    that found nothing.
    """
    assert not mentions_protected_attribute(claim)


# --- what reaches the model --------------------------------------------------------------


def test_the_document_is_sent_as_an_untrusted_block(deps: Deps) -> None:
    from domain.ports.models import BlockKind

    _, client, _ = run_assess(deps, [absent_json()])

    kinds = [block.kind for block in client.calls[0].user_blocks]
    assert BlockKind.DOCUMENT in kinds
    assert CV_TEXT not in client.calls[0].system_prompt


def test_blind_mode_uses_the_redaction_aware_prompt(deps: Deps) -> None:
    _, client, _ = run_assess(deps, [absent_json()], blind=True)

    assert "redacted" in client.calls[0].system_prompt.lower()


def test_the_ordinary_prompt_is_used_when_not_blind(deps: Deps) -> None:
    _, client, _ = run_assess(deps, [absent_json()], blind=False)

    assert "redacted" not in client.calls[0].system_prompt.lower()


def test_the_call_is_made_at_the_declared_site(deps: Deps) -> None:
    _, client, _ = run_assess(deps, [absent_json()])

    assert client.calls[0].call_site == "assess.criterion"


def test_the_criterion_question_reaches_the_prompt(deps: Deps) -> None:
    _, client, _ = run_assess(deps, [absent_json()])

    assert "measurement of system quality" in client.calls[0].system_prompt


# --- observability -------------------------------------------------------------------------


def test_each_criterion_records_what_happened(deps: Deps) -> None:
    result, _, _ = run_assess(deps, [absent_json()])

    payload = next(
        event.payload for event in result.events if event.name == "assess.criterion_assessed"
    )
    for field in ("criterion_id", "tier", "escalated", "state", "accepted", "rejected"):
        assert field in payload


def _claiming(document_id: UUID, claim: str) -> str:
    """A response whose quotation is real but whose claim strays."""
    start = CV_TEXT.find(QUOTATION)
    return json.dumps(
        {
            "criterion_id": "evaluation-practice",
            "evidence": [
                {
                    "state": "supported",
                    "claim": claim,
                    "verbatim_span": QUOTATION,
                    "document_id": str(document_id),
                    "norm_start": start,
                    "norm_end": start + len(QUOTATION),
                    "confidence": 0.9,
                }
            ],
        }
    )
