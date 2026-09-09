"""Every way a provider misbehaves, and what happens when it does.

A provider that always works is not the one anyone runs against. These are the
behaviours a real one has — timeouts, rate limits, truncated JSON, a response
with no usage metadata — and each has a defined outcome rather than an
undefined one.

"Defined" is the point. A recruiter should never see a traceback, and a cost
figure should never be invented to paper over a missing measurement.
"""

from __future__ import annotations

import pytest

from domain.contracts.enums import ModelTier
from domain.contracts.responses import AssessmentResponse
from domain.ports.models import (
    BlockKind,
    GenerationRequest,
    ModelUnavailable,
    PromptBlock,
    Usage,
)
from infrastructure.models.fake import FailingModelClient, ScriptedModelClient
from infrastructure.models.repairing import RepairingClient, validate_response

VALID = '{"criterion_id": "evaluation-practice", "evidence": []}'
INVALID_SCHEMA = '{"criterion_id": "evaluation-practice", "overall_score": 8}'
MALFORMED = '{"criterion_id": "evaluation-practice", "evidence": [   '


def request(**overrides: object) -> GenerationRequest:
    fields: dict[str, object] = {
        "call_site": "assess.criterion",
        "tier": ModelTier.CHEAP,
        "system_prompt": "Find and quote text supporting this criterion.",
        "user_blocks": (PromptBlock(kind=BlockKind.DOCUMENT, content="a CV"),),
        "response_schema": AssessmentResponse,
        "nonce": "a3f9",
    }
    fields.update(overrides)
    return GenerationRequest(**fields)  # type: ignore[arg-type]


# --- transport failures ------------------------------------------------------------


@pytest.mark.parametrize("mode", ["timeout", "rate_limited", "server_error"])
def test_a_transport_failure_is_a_typed_error(mode: str) -> None:
    """Not a bare exception. The node above converts this into a state with a
    sentence, and it needs to know the kind of failure to decide which."""
    client = FailingModelClient(mode=mode)

    with pytest.raises(ModelUnavailable) as caught:
        client.structured_generate(request())

    assert caught.value.error_code == "MODEL_UNAVAILABLE"
    assert caught.value.retryable is True


def test_a_rate_limit_carries_its_retry_delay() -> None:
    """Retrying immediately after a 429 earns another 429. The provider said
    when to come back, and that is worth keeping."""
    client = FailingModelClient(mode="rate_limited")

    with pytest.raises(ModelUnavailable) as caught:
        client.structured_generate(request())

    assert getattr(caught.value, "retry_after_s", None) == 30


def test_a_transport_failure_is_not_repaired() -> None:
    """Repair fixes malformed content. It cannot fix a network, and asking a
    second time would double the wait before the caller learns anything."""
    inner = FailingModelClient(mode="timeout")
    client = RepairingClient(inner=inner)

    with pytest.raises(ModelUnavailable):
        client.structured_generate(request())

    assert inner.calls == 1


# --- content failures ----------------------------------------------------------------


@pytest.mark.parametrize("mode", ["empty", "malformed_json", "schema_invalid"])
def test_a_bad_response_is_a_result_not_an_exception(mode: str) -> None:
    """The caller gets something to record. An exception here would arrive at a
    recruiter as a traceback."""
    result = FailingModelClient(mode=mode).structured_generate(request())

    assert result.parsed is None
    assert result.validation_error
    assert result.ok is False


def test_an_invented_field_is_rejected_rather_than_ignored() -> None:
    """A model returning "overall_score": 8 must fail validation, not have the
    field silently dropped. Dropping it would hide that the model tried."""
    parsed, error = validate_response(INVALID_SCHEMA, AssessmentResponse)

    assert parsed is None
    assert error is not None and "overall_score" in error


def test_the_error_names_the_field_so_repair_can_act_on_it() -> None:
    _, error = validate_response('{"evidence": []}', AssessmentResponse)

    assert error is not None
    assert "criterion_id" in error


# --- usage metadata --------------------------------------------------------------------


def test_a_response_without_usage_is_flagged_not_filled_in() -> None:
    """A guessed token count that looks measured is worse than a missing one.
    Nothing downstream may present this as a real figure."""
    result = FailingModelClient(mode="missing_usage").structured_generate(request())

    assert result.usage.input_tokens == 0
    assert result.metadata.get("usage_is_measured") is False


def test_usage_is_part_of_every_result() -> None:
    """A call cannot happen without accounting, because the accounting is part
    of what a call returns."""
    client = ScriptedModelClient(responses=[VALID], usages=[Usage(120, 34)])

    result = client.structured_generate(request())

    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 34
    assert result.usage.total_tokens == 154


# --- the repair path ---------------------------------------------------------------------


def test_an_invalid_response_is_repaired_once() -> None:
    """Exactly two provider calls: the original and one correction."""
    inner = ScriptedModelClient(responses=[MALFORMED, VALID])
    client = RepairingClient(inner=inner)

    result = client.structured_generate(request())

    assert inner.call_count == 2
    assert result.ok
    assert result.repaired is True


def test_a_valid_response_is_not_repaired() -> None:
    inner = ScriptedModelClient(responses=[VALID])
    client = RepairingClient(inner=inner)

    result = client.structured_generate(request())

    assert inner.call_count == 1
    assert result.repaired is False


def test_a_second_failure_is_not_repaired_again() -> None:
    """A model that returns malformed JSON twice will not be fixed by a third
    request. That is where a loop would start."""
    inner = ScriptedModelClient(responses=[MALFORMED, MALFORMED])
    client = RepairingClient(inner=inner)

    result = client.structured_generate(request())

    assert inner.call_count == 2
    assert result.ok is False
    assert result.validation_error


def test_the_repair_does_not_resend_the_document() -> None:
    """A sixty-page CV resent for a missing comma would cost more than the
    original call. The model has already read it; what it needs is the error."""
    inner = ScriptedModelClient(responses=[MALFORMED, VALID])
    client = RepairingClient(inner=inner)

    client.structured_generate(request())

    second = inner.calls[1]
    kinds = [block.kind for block in second.user_blocks]
    assert kinds == [BlockKind.REPAIR]
    assert not any(block.kind is BlockKind.DOCUMENT for block in second.user_blocks)


def test_the_repair_quotes_the_actual_error() -> None:
    """Paraphrasing the error is how the second attempt fails the same way."""
    inner = ScriptedModelClient(responses=[INVALID_SCHEMA, VALID])
    client = RepairingClient(inner=inner)

    client.structured_generate(request())

    repair_body = inner.calls[1].user_blocks[0].content
    assert "overall_score" in repair_body


def test_repair_stays_at_the_same_tier() -> None:
    """Escalation is a separate decision made by the routing layer. This one
    does not know other tiers exist."""
    inner = ScriptedModelClient(responses=[MALFORMED, VALID])
    client = RepairingClient(inner=inner)

    client.structured_generate(request(tier=ModelTier.CHEAP))

    assert all(call.tier is ModelTier.CHEAP for call in inner.calls)


def test_the_repaired_result_sums_the_usage_of_both_calls() -> None:
    """Charging only for the second call would understate what the run cost."""
    inner = ScriptedModelClient(
        responses=[MALFORMED, VALID], usages=[Usage(100, 10), Usage(40, 20)]
    )
    client = RepairingClient(inner=inner)

    result = client.structured_generate(request())

    assert result.usage.input_tokens == 140
    assert result.usage.output_tokens == 30
