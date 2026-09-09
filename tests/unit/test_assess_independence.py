"""Criteria do not see each other.

This is the property that makes three other things true. Parallelism is safe,
because nothing shared changes. Results are reproducible, because the order
threads finish in cannot affect the output. And the routing, calibration and
fairness comparisons mean something, because each holds one variable while
everything else stays fixed.

It would be easy to lose. Passing a summary of the run so far into each prompt
"for context" would improve nothing measurable and would silently make every
criterion depend on the ones before it.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from application.budget import BudgetGuard
from application.deps import Deps
from domain.contracts import CandidateDocument, DocumentRole, ExtractionMethod, RunStatus
from domain.contracts.run_state import RunState
from domain.contracts.source_text import OffsetRun, PageSpan, SourceText
from domain.ports.models import BlockKind
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.models.fake import FakeModelClient
from infrastructure.models.routing.policies import RoutedPolicy
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline.assess import node
from tests.builders import criterion, rubric
from tests.workflow.conftest import make_run_record

REPO_ROOT = Path(__file__).resolve().parents[2]

CV_TEXT = (
    "Introduced a nightly evaluation suite; releases were blocked below 0.8 agreement. "
    "Owned a multi-service payments backend and its on-call rotation. "
    "Reduced cost per request by routing simple cases to a cheaper model."
)

CRITERIA = ("evaluation-practice", "production-engineering", "cost-awareness")


def response_for(criterion_id: str) -> str:
    return json.dumps(
        {
            "criterion_id": criterion_id,
            "evidence": [
                {
                    "state": "insufficient_evidence",
                    "claim": f"The document does not address {criterion_id} directly.",
                    "confidence": 0.8,
                }
            ],
        }
    )


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "independence.sqlite"), blob_dir=str(tmp_path / "blobs")
    )
    yield build_deps(settings)
    close_thread_connection(settings.db_path)


def assess_in_order(deps: Deps, order: tuple[str, ...]):
    """Run the same assessment with the criteria in a given order."""
    record = make_run_record()
    deps.runs.create(record)

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
    deps.candidates.add_document(document, run_id=record.run_id)
    deps.candidates.put_source_text(
        SourceText(
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
            normalization_profile_id=deps.extractor.profile_id,
            extraction_confidence=0.95,
        ),
        document_sha256=digest,
    )

    role = rubric(
        criteria=[criterion(id=criterion_id, label=criterion_id) for criterion_id in order],
        min_coverage=0.0,
    )

    # A fake that answers by criterion rather than by call order, so the result
    # cannot depend on which thread got there first.
    class ByCriterion(FakeModelClient):
        def structured_generate(self, request):
            from domain.ports.models import GenerationResult, Usage
            from infrastructure.models.repairing import validate_response

            criterion_id = next(name for name in CRITERIA if name in request.system_prompt)
            raw = response_for(criterion_id)
            parsed, error = validate_response(raw, request.response_schema)
            return GenerationResult(
                parsed=parsed,
                raw_text=raw,
                usage=Usage(10, 5),
                tier=request.tier,
                latency_ms=0,
                validation_error=error,
            )

    wired = Deps(
        **{
            **deps.__dict__,
            "models": ByCriterion(),
            "router": RoutedPolicy(),
            "budget": BudgetGuard(token_ceiling=120_000, max_escalations=3),
        }
    )
    state = RunState(
        run_id=record.run_id,
        candidate_id=record.candidate_id,
        role_id=record.role_id,
        status=RunStatus.CALIBRATED,
        started_at=record.started_at,
        nonce="a3f9",
    ).model_copy(update={"rubric": role})

    return node(state, wired)


def comparable(result) -> list[dict]:
    """The parts of a result that should not depend on ordering.

    Evidence ids and timestamps are excluded because they are unique per run by
    design; everything else must match.
    """
    return sorted(
        (
            {
                "criterion_id": assessment.criterion_id,
                "state": assessment.resolved_state.value,
                "rule": assessment.resolution_rule_id,
                "tier": assessment.tier_used.value,
                "accepted": len(assessment.evidence),
                "rejected": len(assessment.rejected_evidence),
                "claims": sorted(item.claim for item in assessment.evidence),
            }
            for assessment in result.state.assessments
        ),
        key=lambda row: row["criterion_id"],
    )


def test_reversing_the_criteria_changes_nothing(deps: Deps) -> None:
    """The headline property. Same evidence, same states, same rules."""
    forward = assess_in_order(deps, CRITERIA)
    backward = assess_in_order(deps, tuple(reversed(CRITERIA)))

    assert comparable(forward) == comparable(backward)


def test_the_output_follows_rubric_order_not_completion_order(deps: Deps) -> None:
    """Criteria run in parallel. A run whose evidence ordering varied would
    produce a different derivation trace each time it ran."""
    forward = assess_in_order(deps, CRITERIA)
    backward = assess_in_order(deps, tuple(reversed(CRITERIA)))

    assert [a.criterion_id for a in forward.state.assessments] == list(CRITERIA)
    assert [a.criterion_id for a in backward.state.assessments] == list(reversed(CRITERIA))


def test_running_twice_gives_the_same_answer(deps: Deps) -> None:
    """Reproducibility, which every experiment downstream depends on."""
    first = assess_in_order(deps, CRITERIA)
    second = assess_in_order(deps, CRITERIA)

    assert comparable(first) == comparable(second)


def test_a_single_criterion_run_matches_the_full_run(deps: Deps) -> None:
    """If a criterion's answer changed when assessed alone, something was
    leaking between them."""
    full = assess_in_order(deps, CRITERIA)
    alone = assess_in_order(deps, ("evaluation-practice",))

    from_full = next(
        row for row in comparable(full) if row["criterion_id"] == "evaluation-practice"
    )
    from_alone = comparable(alone)[0]

    assert from_full == from_alone


# --- read from the source ---------------------------------------------------------


def test_no_prompt_carries_another_criterions_result(deps: Deps) -> None:
    """Checked by inspecting what was sent, rather than by trusting the design."""
    captured: list = []

    class Capturing(FakeModelClient):
        def structured_generate(self, request):
            captured.append(request)
            from domain.ports.models import GenerationResult, Usage
            from infrastructure.models.repairing import validate_response

            raw = response_for("evaluation-practice")
            parsed, error = validate_response(raw, request.response_schema)
            return GenerationResult(
                parsed=parsed,
                raw_text=raw,
                usage=Usage(1, 1),
                tier=request.tier,
                latency_ms=0,
                validation_error=error,
            )

    record = make_run_record()
    deps.runs.create(record)
    digest = uuid4().hex + uuid4().hex[:32]
    deps.candidates.add_document(
        CandidateDocument(
            document_id=uuid4(),
            candidate_id="cand-0007",
            original_filename="cv.pdf",
            document_sha256=digest,
            mime_type="application/pdf",
            size_bytes=len(CV_TEXT),
            blob_path=f"data/blobs/{digest[:2]}/{digest}",
            doc_role=DocumentRole.CV,
            received_at=datetime.now(UTC),
        ),
        run_id=record.run_id,
    )
    deps.candidates.put_source_text(
        SourceText(
            document_id=uuid4(),
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
            normalization_profile_id=deps.extractor.profile_id,
            extraction_confidence=0.9,
        ),
        document_sha256=digest,
    )

    role = rubric(criteria=[criterion(id=name, label=name) for name in CRITERIA], min_coverage=0.0)
    wired = Deps(
        **{
            **deps.__dict__,
            "models": Capturing(),
            "router": RoutedPolicy(),
            "budget": BudgetGuard(token_ceiling=120_000, max_escalations=3),
        }
    )
    node(
        RunState(
            run_id=record.run_id,
            candidate_id=record.candidate_id,
            role_id=record.role_id,
            status=RunStatus.CALIBRATED,
            started_at=record.started_at,
        ).model_copy(update={"rubric": role}),
        wired,
    )

    for request in captured:
        blocks = [block for block in request.user_blocks if block.kind is not BlockKind.DOCUMENT]
        for block in blocks:
            assert "insufficient_evidence" not in block.content
            assert "resolved_state" not in block.content


def test_the_node_never_reads_a_prior_assessment() -> None:
    """A structural check on the source.

    ``_attempt`` resolves the criterion it was given, which is its job. What it
    must not see is the list of other criteria's outcomes: if the prompt builder
    could reach that, a later edit could start passing "context" into each call
    and quietly make every criterion depend on the ones before it.
    """
    source = (REPO_ROOT / "pipeline" / "assess.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    for name in ("_attempt", "_document_blocks"):
        function = next(
            node_
            for node_ in ast.walk(tree)
            if isinstance(node_, ast.FunctionDef) and node_.name == name
        )
        referenced = {node_.id for node_ in ast.walk(function) if isinstance(node_, ast.Name)} | {
            node_.attr for node_ in ast.walk(function) if isinstance(node_, ast.Attribute)
        }

        assert "assessments" not in referenced, f"{name} can see other criteria's results"
        assert "outcomes" not in referenced, f"{name} can see other criteria's results"


def test_the_prompt_names_one_criterion_at_a_time() -> None:
    """The prompt itself says so, so a reader of the file learns the rule
    without reading the node."""
    prompt = (REPO_ROOT / "prompts" / "assessment" / "criterion.md").read_text(encoding="utf-8")

    assert "One criterion per call" in prompt
