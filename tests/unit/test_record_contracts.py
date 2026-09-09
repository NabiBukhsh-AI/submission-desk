"""Run, cost, routing, error, delivery, evaluation, calibration, profile.

The cost rules carry the most weight here. An unconfigured price must produce
null rather than zero, because a zero reads as free and a free system is a claim
nobody measured.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain.contracts import (
    Band,
    CalibrationCard,
    CandidateProfile,
    CostRecord,
    CriterionState,
    DeliveryRecord,
    DeliveryStatus,
    EmploymentEntry,
    ErrorRecord,
    EvaluationCase,
    EvaluationResult,
    GoldLabel,
    IntegrityTier,
    ModelRoutingDecision,
    ModelTier,
    ProvenancedField,
    RunRecord,
    RunStatus,
)
from tests.builders import NOW, provenance


def run(**overrides: Any) -> RunRecord:
    fields: dict[str, Any] = {
        "run_id": uuid4(),
        "content_key": "sha256:abc123",
        "candidate_id": "cand-0007",
        "role_id": "ai-engineer",
        "rubric_version": "1.2.0",
        "rubric_hash": "deadbeef",
        "prompt_bundle_hash": "cafebabe",
        "model_tier_bindings_hash": "0ddba11",
        "routing_policy_id": "routed",
        "blind_mode": True,
        "calibration_enabled": False,
        "calibration_status": "disabled",
        "pipeline_version": "1",
        "status": RunStatus.CREATED,
        "started_at": NOW,
    }
    fields.update(overrides)
    return RunRecord(**fields)


def cost(**overrides: Any) -> CostRecord:
    fields: dict[str, Any] = {
        "record_id": uuid4(),
        "run_id": uuid4(),
        "call_site": "assess.criterion",
        "model_tier": ModelTier.CHEAP,
        "input_tokens": 4210,
        "output_tokens": 318,
        "latency_ms": 1840,
        "occurred_at": NOW,
    }
    fields.update(overrides)
    return CostRecord(**fields)


# --- run ---------------------------------------------------------------------


def test_a_run_starts_with_nothing_measured() -> None:
    started = run()
    assert started.total_cost_usd is None
    assert started.llm_call_count == 0
    assert started.integrity_tier is IntegrityTier.CLEAN


def test_an_unpriced_run_reports_null_not_zero() -> None:
    """Zero would read as free. Null renders as "not configured"."""
    assert run().total_cost_usd is None


def test_a_run_cannot_finish_before_it_starts() -> None:
    with pytest.raises(ValidationError, match="precedes started_at"):
        run(finished_at=NOW.replace(year=2025))


def test_a_negative_cost_is_rejected() -> None:
    with pytest.raises(ValidationError, match="cannot be negative"):
        run(total_cost_usd=Decimal("-0.01"))


@pytest.mark.parametrize(
    "counter",
    ["llm_call_count", "retry_count", "repair_count", "escalation_count", "invalid_span_count"],
)
def test_counters_cannot_go_negative(counter: str) -> None:
    with pytest.raises(ValidationError):
        run(**{counter: -1})


def test_a_run_is_frozen() -> None:
    with pytest.raises(ValidationError):
        run().status = RunStatus.APPROVED


# --- cost --------------------------------------------------------------------


def test_tokens_are_recorded_even_when_price_is_unknown() -> None:
    """The token ceiling has to work whether or not pricing is configured."""
    record = cost()
    assert record.input_tokens == 4210
    assert record.cost_usd is None
    assert record.unit_price_source is None


def test_a_priced_record_must_name_its_price_source() -> None:
    with pytest.raises(ValidationError, match="which pricing configuration"):
        cost(cost_usd=Decimal("0.0042"))


def test_a_priced_record_with_a_source_is_accepted() -> None:
    record = cost(cost_usd=Decimal("0.0042"), unit_price_source="pricing.yaml@v3")
    assert record.cost_usd == Decimal("0.0042")


def test_a_negative_call_cost_is_rejected() -> None:
    with pytest.raises(ValidationError, match="cannot be negative"):
        cost(cost_usd=Decimal("-1"), unit_price_source="pricing.yaml@v3")


def test_currency_is_a_three_letter_code() -> None:
    with pytest.raises(ValidationError):
        cost(currency="DOLLARS")


# --- routing -----------------------------------------------------------------


def test_a_routing_decision_records_both_tiers() -> None:
    """Requested and selected differ exactly when a policy intervened, which is
    the whole evidence base for the routing comparison."""
    routed = ModelRoutingDecision(
        call_site="assess.criterion",
        criterion_id="evaluation-practice",
        attempt_index=1,
        requested_tier=ModelTier.CHEAP,
        selected_tier=ModelTier.STRONG,
        policy_id="routed",
        trigger="low_confidence",
        decided_at=NOW,
    )
    assert routed.requested_tier is not routed.selected_tier


# --- errors ------------------------------------------------------------------


def test_an_error_record_holds_a_redacted_message() -> None:
    record = ErrorRecord(
        error_id=uuid4(),
        node="EXTRACT",
        error_code="UNSUPPORTED_TYPE",
        error_class="ExtractionError",
        message_redacted="could not open [redacted:filename]",
        retryable=False,
        attempt=1,
        resulting_state=RunStatus.FAILED_TERMINAL,
        occurred_at=NOW,
    )
    assert record.run_id is None


# --- delivery ----------------------------------------------------------------


def delivery(**overrides: Any) -> DeliveryRecord:
    fields: dict[str, Any] = {
        "delivery_id": uuid4(),
        "run_id": uuid4(),
        "sink_id": "csv",
        "status": DeliveryStatus.PENDING_RETRY,
        "attempts": 2,
    }
    fields.update(overrides)
    return DeliveryRecord(**fields)


def test_a_delivered_row_records_when() -> None:
    with pytest.raises(ValidationError, match="when it was delivered"):
        delivery(status=DeliveryStatus.DELIVERED)


def test_an_undelivered_row_carries_no_delivery_time() -> None:
    with pytest.raises(ValidationError, match="cannot carry a delivery timestamp"):
        delivery(status=DeliveryStatus.FAILED, delivered_at=NOW)


def test_partial_delivery_is_representable() -> None:
    """One row per sink is what lets the reviewer see that the CSV was written
    and the spreadsheet was not."""
    written = delivery(sink_id="csv", status=DeliveryStatus.DELIVERED, delivered_at=NOW)
    failed = delivery(sink_id="sheets", status=DeliveryStatus.PENDING_RETRY)
    assert written.status is not failed.status


# --- evaluation --------------------------------------------------------------


def test_a_gold_label_records_where_the_answer_is_unknowable() -> None:
    """The half of a label most benchmarks omit."""
    label = GoldLabel(
        criterion_states={"evaluation-practice": CriterionState.INSUFFICIENT_EVIDENCE},
        expected_band=Band.HOLD,
        must_be_insufficient=["evaluation-practice"],
    )
    assert label.must_be_insufficient == ["evaluation-practice"]


def test_an_evaluation_case_carries_its_label() -> None:
    case = EvaluationCase(
        case_id="TC-09",
        name="hidden instruction in white text",
        description="A CV carrying an injected instruction in an invisible font.",
        role_id="ai-engineer",
        expected=GoldLabel(criterion_states={}, expected_band=Band.HOLD, must_flag_integrity=True),
        tags=["adversarial"],
    )
    assert case.expected.must_flag_integrity is True


def test_an_evaluation_result_may_have_no_cost() -> None:
    result = EvaluationResult(
        result_id=uuid4(),
        run_at=NOW,
        case_id="TC-09",
        arm="c",
        config_fingerprint="routed+blind",
        latency_ms=4200,
    )
    assert result.cost_usd is None


# --- calibration -------------------------------------------------------------


def test_a_calibration_card_is_anonymous_by_shape() -> None:
    """There is no name field, so an anchor cannot carry one."""
    card = CalibrationCard(
        card_id=uuid4(),
        role_id="ai-engineer",
        rubric_version="1.2.0",
        anonymized_summary="Seven years backend, shipped two LLM features to production.",
        criterion_states={"evaluation-practice": CriterionState.MET},
        final_band=Band.ADVANCE,
        decided_at=NOW,
        embedding=b"\x00\x01",
    )
    assert "name" not in CalibrationCard.model_fields
    assert "candidate_id" not in CalibrationCard.model_fields
    assert card.final_band is Band.ADVANCE


# --- profile -----------------------------------------------------------------


def test_a_provenanced_field_carries_its_support() -> None:
    field: ProvenancedField[str] = ProvenancedField(
        value="Acme Payments", provenance=[provenance()], confidence=0.9
    )
    assert field.provenance[0].norm_start == 100


def test_a_conflict_is_recorded_not_resolved() -> None:
    """Quietly picking one of two contradictory dates invents a fact."""
    field: ProvenancedField[str] = ProvenancedField(
        value="2021", conflicts=["page 1 says 2021", "page 3 says 2019"]
    )
    assert len(field.conflicts) == 2


def test_an_empty_profile_is_valid() -> None:
    """A document that yielded nothing produces an empty profile, not a guess."""
    profile = CandidateProfile(candidate_id="cand-0007", partial=True, unparsed_reason="OCR failed")
    assert profile.employment == []


def test_an_employment_entry_needs_provenance_on_every_field() -> None:
    entry = EmploymentEntry(
        employer=ProvenancedField(value="Acme", provenance=[provenance()]),
        title=ProvenancedField(value="Engineer", provenance=[provenance()]),
        start=ProvenancedField(value="2021"),
        end=ProvenancedField(value="present"),
        summary=ProvenancedField(value="Built things"),
    )
    assert entry.employer.value == "Acme"


def test_a_naive_timestamp_is_rejected_across_records() -> None:
    naive = datetime(2026, 9, 9, 12, 0)
    with pytest.raises(ValidationError, match="timezone-aware"):
        cost(occurred_at=naive)
    with pytest.raises(ValidationError, match="timezone-aware"):
        run(started_at=naive)
