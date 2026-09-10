"""A past decision cannot become evidence about the present one.

This is the risk calibration introduces, and it is a real one. Text from another
candidate's card is in the prompt; a model that quoted it would produce a claim
about this candidate supported by somebody else's history, and the claim would
read exactly like a real one.

Three controls, and only the third is a control rather than a request. The
block's kind is CALIBRATION rather than DOCUMENT. The prompt says the section is
reference-only and non-citable. And the span validator resolves every quotation
against the candidate's own documents, so a sentence lifted from a card resolves
nowhere and is rejected — which holds whether or not the model read either of
the first two.

So the test plants a distinctive sentence in a card, has the model quote it, and
asserts it never reaches the score.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from application.deps import Deps
from application.use_cases.process_candidate import process_candidate
from domain.contracts.calibration import CalibrationCard
from domain.contracts.enums import Band, CriterionState, SpanValidation
from domain.ports.calibration import CalibrationQuery, embedding_text
from domain.ports.models import BlockKind
from infrastructure.calibration.embedder import LocalEmbedder
from infrastructure.calibration.index import NumpyCalibrationIndex
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.models.repairing import validate_response
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline.calibrate import BLOCK_HEADER, render_block
from tests.workflow.test_end_to_end import (
    STRONG_ANSWERS,
    STRONG_CV,
    AnsweringModel,
    _FolderSource,
    _load_shipped_rubric,
    upload,
)

#: A sentence that appears in no candidate's CV and could only have come from an
#: anchor. If it ever reaches the evidence, it came from a card.
PLANTED = "Directed the orbital telemetry consolidation programme at Kestrel Dynamics"

#: The structure response every model in this module returns.
#:
#: A real profile rather than an empty one, because an empty profile produces a
#: fallback summary with no words in common with any card, every similarity is
#: zero, and the whole module would pass by testing nothing. The first version of
#: this file did exactly that.
PROFILE_RESPONSE = {
    "candidate_id": "cand-0007",
    "partial": False,
    "employment": [
        {
            "employer": {"value": "Acme Payments"},
            "title": {"value": "Lead Engineer"},
            "start": {"value": "2021"},
            "end": {"value": "present"},
            "summary": {"value": "Owned a multi-service payments backend"},
        }
    ],
    "education": [{"value": "BSc Computer Science"}],
    "technologies": [{"value": "python"}, {"value": "kubernetes"}],
}


class ProfilingModel(AnsweringModel):
    """Answers structure with a real profile, so calibration has something to
    search with."""

    def structured_generate(self, request):
        if request.call_site == "structure.profile":
            raw = json.dumps(PROFILE_RESPONSE)
            parsed, error = validate_response(raw, request.response_schema)
            from domain.ports.models import GenerationResult, Usage

            return GenerationResult(
                parsed=parsed,
                raw_text=raw,
                usage=Usage(120, 30),
                tier=request.tier,
                latency_ms=5,
                validation_error=error,
            )
        return super().structured_generate(request)


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "leak.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        blind_mode=False,
        calibration_enabled=True,
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


def plant_card(deps: Deps, *, role_id: str = "ai-engineer") -> CalibrationCard:
    """A card carrying the distinctive sentence."""
    embedder = LocalEmbedder()
    summary = f"{PLANTED}\nTechnologies: python, kubernetes"
    states = {"python-depth": CriterionState.MET}

    card = CalibrationCard(
        card_id=uuid4(),
        role_id=role_id,
        rubric_version="1.0.0",
        anonymized_summary=summary,
        criterion_states=states,
        final_band=Band.ADVANCE,
        reviewer_reason_codes=[],
        reviewer_reason_text_redacted=None,
        decided_at=datetime.now(UTC) - timedelta(days=3),
        embedding=embedder.embed(embedding_text(summary, states)) or b"",
    )
    deps.calibration.add(card)
    return card


class QuotingModel(ProfilingModel):
    """A model that does the worst possible thing: quotes an anchor.

    Not a straw man. A model shown a block of similar-looking career text and
    asked for a quotation has every reason to take one from the nearest passage,
    and the fact that the passage belongs to a different person is not visible in
    the text.
    """

    def _assessment(self, request) -> str:
        criterion_id = next(
            (
                criterion_id
                for label, criterion_id in self.by_label.items()
                if label in request.system_prompt
            ),
            None,
        )
        document_id = next(
            (block.document_id for block in request.user_blocks if block.document_id is not None),
            None,
        )

        return json.dumps(
            {
                "criterion_id": criterion_id or "python-depth",
                "evidence": [
                    {
                        "state": "supported",
                        "claim": f"The candidate {PLANTED.lower()}.",
                        "verbatim_span": PLANTED,
                        "document_id": str(document_id) if document_id else None,
                        "page_start": 1,
                        "page_end": 1,
                        "norm_start": 0,
                        "norm_end": len(PLANTED),
                        "confidence": 0.95,
                    }
                ],
            }
        )


def run_with(deps: Deps, model_class=AnsweringModel):
    candidate = upload(deps, STRONG_CV)
    model = model_class(STRONG_CV, STRONG_ANSWERS)
    wired = Deps(**{**deps.__dict__, "models": model})
    return process_candidate(candidate, "ai-engineer", wired), wired, model


# --- the leak -----------------------------------------------------------------------


def test_a_planted_sentence_never_becomes_evidence(deps: Deps) -> None:
    """The headline. The model quoted the anchor; the score never saw it."""
    plant_card(deps)

    result, wired, _ = run_with(deps, QuotingModel)

    accepted = wired.evidence.evidence_for_run(result.run_id, rejected=False)
    assert all(PLANTED not in (item.verbatim_span or "") for item in accepted)


def test_the_attempt_is_rejected_and_kept(deps: Deps) -> None:
    """Rejected rather than dropped. A quotation the validator could not find is
    exactly what a reviewer needs to see."""
    plant_card(deps)

    result, wired, _ = run_with(deps, QuotingModel)

    rejected = wired.evidence.evidence_for_run(result.run_id, rejected=True)
    assert any(PLANTED in (item.verbatim_span or "") for item in rejected)


def test_the_rejection_says_it_was_not_in_the_document(deps: Deps) -> None:
    plant_card(deps)

    result, wired, _ = run_with(deps, QuotingModel)

    rejected = wired.evidence.evidence_for_run(result.run_id, rejected=True)
    assert all(
        item.span_validation
        in (SpanValidation.INVALID_NOT_FOUND, SpanValidation.INVALID_WRONG_DOCUMENT)
        for item in rejected
        if PLANTED in (item.verbatim_span or "")
    )


def test_a_quoted_anchor_cannot_raise_the_band(deps: Deps) -> None:
    """Every claim was fabricated from an anchor, so every claim was rejected,
    so the recommendation rests on nothing."""
    plant_card(deps)

    result, wired, _ = run_with(deps, QuotingModel)

    recommendation = wired.evidence.recommendation_for_run(result.run_id)
    assert recommendation.band is Band.INSUFFICIENT_INFORMATION


# --- how the block reaches the model ---------------------------------------------------


def test_the_anchor_actually_reaches_the_model(deps: Deps) -> None:
    """The premise of every assertion below.

    Written first and separately because the earlier version of this module was
    passing while the anchor never left the index: an empty profile produced a
    fallback summary, every similarity was zero, and "no calibration block was a
    document block" was true because there was no calibration block. A vacuous
    security test is worse than none, because it reports a control as working.
    """
    plant_card(deps)
    captured: list = []

    class Capturing(ProfilingModel):
        def structured_generate(self, request):
            captured.append(request)
            return super().structured_generate(request)

    candidate = upload(deps, STRONG_CV)
    result = process_candidate(
        candidate,
        "ai-engineer",
        Deps(**{**deps.__dict__, "models": Capturing(STRONG_CV, STRONG_ANSWERS)}),
    )

    assert result.outcome.state.calibration_status == "applied"
    assert any(PLANTED in block.content for request in captured for block in request.user_blocks), (
        "the planted anchor never reached the model, so nothing below is being tested"
    )


def test_the_block_is_never_a_document_block(deps: Deps) -> None:
    """A CALIBRATION block and a DOCUMENT block are different kinds, and only
    one of them is what a span is validated against."""
    plant_card(deps)
    captured: list = []

    class Capturing(ProfilingModel):
        def structured_generate(self, request):
            captured.append(request)
            return super().structured_generate(request)

    candidate = upload(deps, STRONG_CV)
    process_candidate(
        candidate,
        "ai-engineer",
        Deps(**{**deps.__dict__, "models": Capturing(STRONG_CV, STRONG_ANSWERS)}),
    )

    for request in captured:
        for block in request.user_blocks:
            if PLANTED in block.content:
                assert block.kind is BlockKind.CALIBRATION


def test_the_block_never_reaches_the_system_prompt(deps: Deps) -> None:
    """Structural, enforced by the client rather than by wording."""
    plant_card(deps)
    captured: list = []

    class Capturing(ProfilingModel):
        def structured_generate(self, request):
            captured.append(request)
            return super().structured_generate(request)

    candidate = upload(deps, STRONG_CV)
    process_candidate(
        candidate,
        "ai-engineer",
        Deps(**{**deps.__dict__, "models": Capturing(STRONG_CV, STRONG_ANSWERS)}),
    )

    for request in captured:
        assert PLANTED not in request.system_prompt


def test_the_block_says_it_cannot_be_quoted() -> None:
    """The request, alongside the control. A model that reads it has been told;
    a model that ignores it is caught anyway."""
    assert "REFERENCE ONLY" in BLOCK_HEADER
    assert "not this candidate" in BLOCK_HEADER
    assert "rejected" in BLOCK_HEADER


def test_a_rendered_block_carries_no_name() -> None:
    """A card cannot hold one: the contract has no field for it, and the summary
    is built from named profile fields rather than written by anybody."""
    from domain.ports.calibration import CalibrationMatch, CalibrationResult, CalibrationStatus

    embedder = LocalEmbedder()
    summary = "Senior Backend Engineer, 2021 to present\nTechnologies: python"
    card = CalibrationCard(
        card_id=uuid4(),
        role_id="ai-engineer",
        rubric_version="1.0.0",
        anonymized_summary=summary,
        criterion_states={},
        final_band=Band.ADVANCE,
        decided_at=datetime.now(UTC),
        embedding=embedder.embed(summary) or b"",
    )

    block = render_block(
        CalibrationResult(
            status=CalibrationStatus.APPLIED,
            matches=(CalibrationMatch(card=card, similarity=0.9),),
        )
    )

    assert block is not None
    assert "Ana" not in block
    assert "@" not in block


# --- what cannot become a card -------------------------------------------------------------


def test_an_unapproved_run_never_becomes_a_card(deps: Deps) -> None:
    """A system that learns from its own unchecked output compounds its errors
    instead of correcting them."""
    from domain.calibration import should_create

    class _Decision:
        action = None

    assert should_create(_Decision()) is False
    assert should_create(None) is False


def test_a_rejection_never_becomes_a_card() -> None:
    """A rejection is a decision too, but the anchors exist to show what a good
    candidate for this role looked like."""
    from domain.calibration import should_create
    from domain.contracts.enums import ReviewAction

    class _Decision:
        action = ReviewAction.REJECT

    assert should_create(_Decision()) is False


def test_an_approval_does_become_a_card() -> None:
    from domain.calibration import should_create
    from domain.contracts.enums import ReviewAction

    class _Decision:
        action = ReviewAction.APPROVE

    assert should_create(_Decision()) is True


# --- the index cannot reach across roles ------------------------------------------------------


def test_a_card_from_another_role_is_never_returned(deps: Deps) -> None:
    """An anchor from a different role is a decision made against different
    criteria, which is worse than no anchor."""
    plant_card(deps, role_id="data-scientist")
    index = NumpyCalibrationIndex(deps.calibration, LocalEmbedder())

    result = index.search(
        CalibrationQuery(
            role_id="ai-engineer",
            rubric_version="1.0.0",
            summary=PLANTED,
            criterion_states={},
        )
    )

    assert all(match.card.role_id == "ai-engineer" for match in result.matches)


def test_the_planted_sentence_is_not_in_any_candidate_document() -> None:
    """The premise of the whole module. If this sentence were in the CV, the
    leak test would be asserting nothing."""
    assert PLANTED not in STRONG_CV
