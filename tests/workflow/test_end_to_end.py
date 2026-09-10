"""A candidate, start to reviewable, through every real node.

This is the test that would catch a system that works in pieces and not as a
whole. Real PDFs, real extraction, real span validation, the real rule engine,
and a model client replaying recorded answers. No network, no API key.

Three cases carry it: a strong candidate reaching review with a full
derivation, a sparse CV tripping the coverage gate, and a contradictory one
routing to a person.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from application.deps import Deps
from application.use_cases.process_candidate import process_candidate
from domain.contracts.enums import Band, CriterionState, RunStatus
from domain.ports.models import GenerationResult, Usage
from domain.ports.sources import CandidateRef, DocumentRef
from domain.rules import load_rubric
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.models.fake import FakeModelClient
from infrastructure.models.repairing import validate_response
from infrastructure.storage.sqlite.connection import close_thread_connection
from tests.fixtures import pdfs

REPO_ROOT = Path(__file__).resolve().parents[2]

STRONG_CV = """Ana Ferreira
Senior Backend Engineer

Acme Payments, Lead Engineer, 2021 to present.
Owned a multi-service payments backend and its on-call rotation for two years.
Shipped a language-model feature used by the support team every day.
Introduced a nightly evaluation suite; releases were blocked when agreement fell
below 0.8 against human labels.
Reduced cost per request by routing simple cases to a cheaper model.
Wrote the runbook and presented the results to stakeholders.
Replaced a manual triage process; handling time fell from 40 minutes to 6.
Built a document extraction pipeline handling scanned and multilingual inputs.
I am eligible to work in the United Kingdom without sponsorship.

Northwind Logistics, Backend Engineer, 2018 to 2021.
Built the routing service in Python. Reduced p95 latency from 900ms to 210ms.

BSc Computer Science, University of Porto, 2018.
"""

SPARSE_CV = """Ben Oyelaran
Engineer

Worked on some projects. Python.
"""

CONTRADICTORY_CV = """Chidi Okonkwo
Backend Engineer

Introduced a nightly evaluation suite that gated every release.
I have never worked on evaluation or measurement of model quality.
"""


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "e2e.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        blind_mode=False,
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


def _load_shipped_rubric(role_id: str):
    path = REPO_ROOT / "rubrics" / f"{role_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(role_id)
    return load_rubric(yaml.safe_load(path.read_text(encoding="utf-8")), source=path.name)


class _FolderSource:
    """Serves whatever was written into the inbox for this test."""

    source_id = "test"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def list_candidates(self, limit: int | None = None) -> list[CandidateRef]:
        return []

    def fetch(self, ref: DocumentRef) -> bytes:
        return Path(ref.external_ref).read_bytes()


class AnsweringModel(FakeModelClient):
    """Answers by call site and criterion, from text present in the document.

    Written as a lookup rather than a script because the criteria run in
    parallel: a script would bind answers to arrival order, and the test would
    then be asserting something about threads.
    """

    def __init__(self, cv_text: str, answers: dict[str, tuple[str, str]]) -> None:
        super().__init__()
        self.cv_text = cv_text
        self.answers = answers
        # The prompt names the criterion by its label, not its id, because that
        # is what a reader of the prompt needs. The test follows the prompt
        # rather than reaching past it.
        rubric = _load_shipped_rubric("ai-engineer")
        self.by_label = {item.label: item.id for item in rubric.criteria}
        self.document_id: UUID | None = None
        self.seen: list[str] = []

    def structured_generate(self, request):
        self.seen.append(request.call_site)

        if request.call_site == "structure.profile":
            raw = json.dumps({"candidate_id": "cand-0007", "partial": False})
        elif request.call_site == "compose.questions":
            raw = json.dumps({"interview_questions": [], "information_requests": []})
        else:
            raw = self._assessment(request)

        parsed, error = validate_response(raw, request.response_schema)
        return GenerationResult(
            parsed=parsed,
            raw_text=raw,
            usage=Usage(120, 30),
            tier=request.tier,
            latency_ms=5,
            validation_error=error,
        )

    def _assessment(self, request) -> str:
        criterion_id = next(
            (
                criterion_id
                for label, criterion_id in self.by_label.items()
                if label in request.system_prompt
            ),
            None,
        )
        document_id = self.document_id or next(
            (block.document_id for block in request.user_blocks if block.document_id is not None),
            None,
        )

        if criterion_id is None or document_id is None:
            return json.dumps(
                {
                    "criterion_id": criterion_id or "unknown",
                    "evidence": [
                        {
                            "state": "insufficient_evidence",
                            "claim": "The documents do not address this point.",
                            "confidence": 0.8,
                        }
                    ],
                }
            )

        state, span = self.answers[criterion_id]
        if state == "insufficient_evidence":
            return json.dumps(
                {
                    "criterion_id": criterion_id,
                    "evidence": [
                        {
                            "state": "insufficient_evidence",
                            "claim": "The documents do not address this point anywhere.",
                            "confidence": 0.85,
                        }
                    ],
                }
            )

        start = self.cv_text.find(span)
        return json.dumps(
            {
                "criterion_id": criterion_id,
                "evidence": [
                    {
                        "state": state,
                        "claim": f"The document shows this: {span[:60]}",
                        "verbatim_span": span,
                        "document_id": str(document_id),
                        "page_start": 1,
                        "page_end": 1,
                        "norm_start": max(start, 0),
                        "norm_end": max(start, 0) + len(span),
                        "confidence": 0.88,
                    }
                ],
            }
        )


def upload(deps: Deps, text: str) -> CandidateRef:
    """Write a CV into the inbox and describe it the way a source would."""
    inbox = Path(deps.source.root)
    path = inbox / "ana-cv.pdf"
    path.write_bytes(pdfs.text_pdf(text))
    return CandidateRef(
        candidate_id="cand-0007",
        documents=(
            DocumentRef(candidate_id="cand-0007", filename="ana-cv.pdf", external_ref=str(path)),
        ),
    )


#: What the model "finds" for each criterion: a state and a quotation that is
#: genuinely present in the CV above. Written this way so the span validator is
#: doing real work rather than being handed something it cannot fail.
STRONG_ANSWERS: dict[str, tuple[str, str]] = {
    "production-llm-delivery": (
        "supported",
        "Shipped a language-model feature used by the support team",
    ),
    "evaluation-practice": ("supported", "Introduced a nightly evaluation suite"),
    "workflow-automation": ("supported", "Replaced a manual triage process"),
    "production-engineering": (
        "supported",
        "Owned a multi-service payments backend and its on-call rotation",
    ),
    "python-depth": ("supported", "Built the routing service in Python"),
    "data-handling": (
        "supported",
        "Built a document extraction pipeline handling scanned",
    ),
    "cost-awareness": (
        "supported",
        "Reduced cost per request by routing simple cases to a cheaper model",
    ),
    "written-communication": (
        "supported",
        "Wrote the runbook and presented the results to stakeholders",
    ),
    "work-authorisation-stated": (
        "supported",
        "I am eligible to work in the United Kingdom without sponsorship",
    ),
}

SPARSE_ANSWERS = dict.fromkeys(STRONG_ANSWERS, ("insufficient_evidence", ""))

CONTRADICTORY_ANSWERS = {
    **SPARSE_ANSWERS,
    "evaluation-practice": (
        "supported",
        "Introduced a nightly evaluation suite that gated every release",
    ),
}


def run_pipeline(deps: Deps, cv_text: str, answers: dict) -> tuple:
    candidate = upload(deps, cv_text)
    model = AnsweringModel(cv_text, answers)
    wired = Deps(**{**deps.__dict__, "models": model})
    result = process_candidate(candidate, "ai-engineer", wired)
    return result, wired, model


# --- the strong candidate ------------------------------------------------------


def test_a_strong_candidate_reaches_review(deps: Deps) -> None:
    """The whole pipeline, on a real PDF, with no network."""
    result, _, _ = run_pipeline(deps, STRONG_CV, STRONG_ANSWERS)

    assert result.status in (RunStatus.READY_FOR_REVIEW, RunStatus.NEEDS_REVIEW)
    assert result.is_reviewable


def test_the_recommendation_is_persisted_with_its_derivation(deps: Deps) -> None:
    """The derivation is the product: a band without the rules that produced it
    is an opinion."""
    result, wired, _ = run_pipeline(deps, STRONG_CV, STRONG_ANSWERS)

    recommendation = wired.evidence.recommendation_for_run(result.run_id)
    assert recommendation is not None
    assert recommendation.derivation
    assert next(step.rule_id for step in recommendation.derivation) == "R-COVERAGE"


def test_a_strong_candidate_is_recommended_to_advance(deps: Deps) -> None:
    result, wired, _ = run_pipeline(deps, STRONG_CV, STRONG_ANSWERS)

    recommendation = wired.evidence.recommendation_for_run(result.run_id)
    assert recommendation.band in (Band.ADVANCE, Band.ADVANCE_WITH_RESERVATIONS)
    assert recommendation.score is not None


def test_every_claim_carries_a_located_quotation(deps: Deps) -> None:
    """The headline property, checked at the far end of the pipeline rather than
    at the unit that implements it."""
    result, wired, _ = run_pipeline(deps, STRONG_CV, STRONG_ANSWERS)

    accepted = wired.evidence.evidence_for_run(result.run_id, rejected=False)
    supported = [item for item in accepted if item.verbatim_span]

    assert supported
    for item in supported:
        assert item.span_validation.value.startswith("valid")
        assert item.provenance is not None


def test_the_derivation_reads_as_english(deps: Deps) -> None:
    """The reviewer sees these sentences verbatim."""
    result, wired, _ = run_pipeline(deps, STRONG_CV, STRONG_ANSWERS)

    recommendation = wired.evidence.recommendation_for_run(result.run_id)
    prose = " ".join(step.description for step in recommendation.derivation).lower()

    for jargon in ("null", "none", "traceback", "criterion_id", "enum"):
        assert jargon not in prose


def test_the_run_stops_at_the_reviewer(deps: Deps) -> None:
    """Nothing past composition happens without a persisted decision."""
    result, _, _ = run_pipeline(deps, STRONG_CV, STRONG_ANSWERS)

    assert result.outcome is not None
    assert "DELIVER" not in result.outcome.executed
    assert result.status is not RunStatus.DELIVERED


def test_every_node_is_recorded(deps: Deps) -> None:
    result, wired, _ = run_pipeline(deps, STRONG_CV, STRONG_ANSWERS)

    nodes = [row[1] for row in wired.events.for_run(result.run_id)]
    for expected in ("CONFIG", "INTAKE", "EXTRACT", "STRUCTURE", "ASSESS", "AGGREGATE", "COMPOSE"):
        assert expected in nodes


# --- the sparse candidate -------------------------------------------------------


def test_a_sparse_cv_declines_to_score(deps: Deps) -> None:
    """Upload a one-page CV against a nine-criterion rubric and the system says
    it does not know enough, rather than guessing low."""
    result, wired, _ = run_pipeline(deps, SPARSE_CV, SPARSE_ANSWERS)

    recommendation = wired.evidence.recommendation_for_run(result.run_id)
    assert recommendation.band is Band.INSUFFICIENT_INFORMATION
    assert recommendation.score is None


def test_the_gate_explains_itself_to_the_recruiter(deps: Deps) -> None:
    result, wired, _ = run_pipeline(deps, SPARSE_CV, SPARSE_ANSWERS)

    recommendation = wired.evidence.recommendation_for_run(result.run_id)
    gate = next(step for step in recommendation.derivation if step.rule_id == "R-COVERAGE-GATE")

    wording = gate.description.lower()
    assert "not enough information" in wording or "ask for more" in wording


def test_a_sparse_candidate_gets_specific_requests(deps: Deps) -> None:
    """One request per unresolved point, naming the point. A generic "please
    send more" would be useless to the recruiter and to the candidate."""
    result, _, _ = run_pipeline(deps, SPARSE_CV, SPARSE_ANSWERS)

    package = result.outcome.state.package
    assert package is not None
    assert len(package.information_requests) >= 5
    assert all(text.strip() for _, text in package.information_requests)


def test_absence_is_not_recorded_as_failure(deps: Deps) -> None:
    """The distinction the whole system rests on."""
    result, wired, _ = run_pipeline(deps, SPARSE_CV, SPARSE_ANSWERS)

    recommendation = wired.evidence.recommendation_for_run(result.run_id)
    assert all(
        state is not CriterionState.NOT_MET for state in recommendation.criterion_states.values()
    )


# --- the contradictory candidate --------------------------------------------------


def test_a_contradiction_routes_to_a_person(deps: Deps) -> None:
    """A document that disagrees with itself is where a human is cheap and a
    model is dangerous."""
    result, wired, _ = run_pipeline(deps, CONTRADICTORY_CV, CONTRADICTORY_ANSWERS)

    recommendation = wired.evidence.recommendation_for_run(result.run_id)
    assert recommendation.requires_human is True


def test_a_flagged_run_reaches_needs_review(deps: Deps) -> None:
    result, _, _ = run_pipeline(deps, SPARSE_CV, SPARSE_ANSWERS)

    assert result.status is RunStatus.NEEDS_REVIEW


# --- idempotency -------------------------------------------------------------------


def test_a_second_identical_run_is_a_new_run_not_a_crash(deps: Deps) -> None:
    """Two runs of the same candidate must both work. The content-key lookup
    short-circuits only once the documents have been hashed."""
    first, _, _ = run_pipeline(deps, STRONG_CV, STRONG_ANSWERS)
    second, _, _ = run_pipeline(deps, STRONG_CV, STRONG_ANSWERS)

    assert first.run_id != second.run_id
    assert second.status is not RunStatus.FAILED_TERMINAL


def test_a_missing_role_fails_before_anything_is_read(deps: Deps) -> None:
    """CONFIG runs first and does almost nothing, so a bad role costs nothing."""
    candidate = upload(deps, STRONG_CV)
    model = AnsweringModel(STRONG_CV, STRONG_ANSWERS)
    wired = Deps(**{**deps.__dict__, "models": model})

    result = process_candidate(candidate, "no-such-role", wired)

    assert result.status is RunStatus.FAILED_TERMINAL
    assert model.seen == [], "a model was called for a run that could not start"


def test_the_message_is_written_for_a_person(deps: Deps) -> None:
    result, _, _ = run_pipeline(deps, STRONG_CV, STRONG_ANSWERS)

    assert result.message
    for jargon in ("exception", "traceback", "none", "null"):
        assert jargon not in result.message.lower()
