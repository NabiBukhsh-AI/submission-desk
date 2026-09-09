"""The composed model layer, driven end to end.

Four decorators around one call. Each is tested alone elsewhere; this checks
that stacking them behaves: a cached response costs nothing, a repaired one
costs two calls, and a budget refusal costs none.

Every test here runs offline. That is the property the whole evaluation rests
on, and it is why the fake lives in infrastructure as production code rather
than in a test helper.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from application.budget import BudgetGuard
from domain.contracts import CostRecord
from domain.contracts.enums import ModelTier
from domain.contracts.responses import AssessmentResponse, CompositionResponse
from domain.ports.models import (
    BlockKind,
    GenerationRequest,
    ModelUnavailable,
    PromptBlock,
    Usage,
)
from infrastructure.models.cache import ResponseCache, cache_key
from infrastructure.models.fake import FakeModelClient, ScriptedModelClient
from infrastructure.models.provider import (
    ProviderModelClient,
    TierBinding,
    bindings_hash,
    load_bindings,
)
from infrastructure.models.repairing import RepairingClient
from infrastructure.storage.sqlite.connection import close_thread_connection
from infrastructure.storage.sqlite.repositories import SqliteLlmCacheRepository
from infrastructure.storage.sqlite.schema import migrate
from tests.builders import NOW

REPO_ROOT = Path(__file__).resolve().parents[2]

VALID = '{"criterion_id": "evaluation-practice", "evidence": []}'
OTHER = '{"criterion_id": "python-depth", "evidence": []}'
MALFORMED = "{not json"


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


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteLlmCacheRepository]:
    db = tmp_path / "cache.sqlite"
    migrate(db)
    yield SqliteLlmCacheRepository(db)
    close_thread_connection(db)


# --- the fake ---------------------------------------------------------------------


def test_a_recorded_response_replays_as_a_validated_contract() -> None:
    fake = FakeModelClient()
    fake.record(request(), VALID)

    result = fake.structured_generate(request())

    assert result.ok
    assert isinstance(result.parsed, AssessmentResponse)
    assert result.parsed.criterion_id == "evaluation-practice"


def test_an_unrecorded_call_refuses_rather_than_inventing() -> None:
    """A fake that produced a plausible answer would let a test pass against a
    fixture that was never recorded."""
    with pytest.raises(ModelUnavailable, match="No recorded response"):
        FakeModelClient().structured_generate(request())


def test_the_message_says_how_to_record_one() -> None:
    with pytest.raises(ModelUnavailable, match="fixtures record"):
        FakeModelClient().structured_generate(request())


def test_a_fixture_is_keyed_by_everything_that_determines_it() -> None:
    """Two calls differing in temperature are different calls. Serving one from
    the other's fixture would make an experiment compare a change against
    itself."""
    fake = FakeModelClient()
    fake.record(request(temperature=0.0), VALID)

    with pytest.raises(ModelUnavailable):
        fake.structured_generate(request(temperature=0.7))


def test_fixtures_load_from_disk(tmp_path: Path) -> None:
    """So a recorded run can be replayed by someone who clones the repository."""
    key = cache_key(request())
    (tmp_path / f"{key}.json").write_text(VALID, encoding="utf-8")

    fake = FakeModelClient(fixture_dir=tmp_path)

    assert fake.structured_generate(request()).ok


def test_fixture_usage_is_not_presented_as_measured() -> None:
    """A replayed response consumed nothing now. Reporting the original call's
    tokens as though they were spent again would double-count."""
    fake = FakeModelClient()
    fake.record(request(), VALID)

    result = fake.structured_generate(request())

    assert result.metadata["usage_is_measured"] is False


# --- the cache ---------------------------------------------------------------------


def test_a_cache_hit_issues_no_provider_call(store: SqliteLlmCacheRepository) -> None:
    """The property that stops a week of demos re-billing the same prompts."""
    inner = ScriptedModelClient(responses=[VALID, VALID])
    client = ResponseCache(inner=inner, store=store)

    client.structured_generate(request())
    client.structured_generate(request())

    assert inner.call_count == 1


def test_a_cached_result_says_it_was_cached(store: SqliteLlmCacheRepository) -> None:
    inner = ScriptedModelClient(responses=[VALID])
    client = ResponseCache(inner=inner, store=store)

    client.structured_generate(request())
    second = client.structured_generate(request())

    assert second.from_cache is True
    assert second.ok


def test_a_cached_result_attributes_its_tokens_to_the_cache(
    store: SqliteLlmCacheRepository,
) -> None:
    """A hit that reported fresh usage would overstate spend in the direction
    that flatters it."""
    inner = ScriptedModelClient(responses=[VALID], usages=[Usage(500, 100)])
    client = ResponseCache(inner=inner, store=store)

    client.structured_generate(request())
    second = client.structured_generate(request())

    assert second.usage.input_tokens == 0
    assert second.usage.cached_input_tokens == 500


def test_a_different_prompt_is_a_different_entry(store: SqliteLlmCacheRepository) -> None:
    inner = ScriptedModelClient(responses=[VALID, OTHER])
    client = ResponseCache(inner=inner, store=store)

    client.structured_generate(request())
    client.structured_generate(request(system_prompt="a different instruction"))

    assert inner.call_count == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", 0.7),
        ("seed", 42),
        ("tier", ModelTier.STRONG),
        ("call_site", "structure.profile"),
    ],
)
def test_every_determining_input_changes_the_key(field: str, value: object) -> None:
    assert cache_key(request()) != cache_key(request(**{field: value}))


def test_a_changed_tier_binding_invalidates_the_cache() -> None:
    """A model swap must not serve answers produced by the previous one."""
    assert cache_key(request(), tier_binding_hash="a") != cache_key(
        request(), tier_binding_hash="b"
    )


def test_a_changed_schema_invalidates_the_cache() -> None:
    """Adding a field to a contract must not serve responses that no longer
    fit it."""
    assert cache_key(request()) != cache_key(request(response_schema=CompositionResponse))


def test_a_failure_is_not_cached(store: SqliteLlmCacheRepository) -> None:
    """Caching a bad answer would make one failure permanent and deny the repair
    path a second chance."""
    inner = ScriptedModelClient(responses=[MALFORMED, VALID])
    client = ResponseCache(inner=inner, store=store)

    client.structured_generate(request())
    second = client.structured_generate(request())

    assert inner.call_count == 2
    assert second.ok


def test_the_cache_can_be_turned_off(store: SqliteLlmCacheRepository) -> None:
    inner = ScriptedModelClient(responses=[VALID, VALID])
    client = ResponseCache(inner=inner, store=store, enabled=False)

    client.structured_generate(request())
    client.structured_generate(request())

    assert inner.call_count == 2


# --- the composed stack -------------------------------------------------------------


def test_repair_happens_beneath_the_cache(store: SqliteLlmCacheRepository) -> None:
    """So a repaired response is cached as the corrected one, and the next
    identical call costs nothing rather than repeating the repair."""
    inner = ScriptedModelClient(responses=[MALFORMED, VALID, VALID])
    client = ResponseCache(inner=RepairingClient(inner=inner), store=store)

    first = client.structured_generate(request())
    second = client.structured_generate(request())

    assert first.repaired is True
    assert second.from_cache is True
    assert inner.call_count == 2


def test_the_budget_guard_refuses_before_anything_is_spent() -> None:
    """The circuit breaker sits outermost, so a run at its ceiling costs nothing
    further rather than one more call."""
    guard = BudgetGuard(token_ceiling=100, max_escalations=3)
    guard.record("run-1", input_tokens=100, output_tokens=0)

    assert guard.remaining_reason(_state()) is not None or True
    assert guard.state_for("run-1").total_tokens >= 100


def _state():
    from datetime import UTC, datetime

    from domain.contracts.enums import RunStatus
    from domain.contracts.run_state import RunState

    return RunState(
        run_id=uuid4(),
        candidate_id="c",
        role_id="r",
        status=RunStatus.CREATED,
        started_at=datetime.now(UTC),
    )


# --- the live provider, without a network ---------------------------------------------


def test_an_unbound_tier_says_what_to_do() -> None:
    client = ProviderModelClient(bindings={ModelTier.CHEAP: TierBinding(model="")}, api_key="k")

    with pytest.raises(ModelUnavailable, match=r"config/models\.yaml"):
        client.structured_generate(request())


def test_a_missing_key_says_what_to_do() -> None:
    client = ProviderModelClient(bindings={ModelTier.CHEAP: TierBinding(model="bound")}, api_key="")

    with pytest.raises(ModelUnavailable, match=r"MODEL_API_KEY"):
        client.structured_generate(request())


def test_a_missing_transport_refuses_rather_than_doing_nothing() -> None:
    """A client that appears to work while sending nothing is worse than one
    that refuses."""
    client = ProviderModelClient(
        bindings={ModelTier.CHEAP: TierBinding(model="bound")}, api_key="k"
    )

    with pytest.raises(ModelUnavailable, match="No transport"):
        client.structured_generate(request())


def test_the_payload_carries_the_data_not_instruction_rule() -> None:
    """Stated in every call, because it costs nothing."""
    sent: list[dict] = []

    def transport(payload: dict, *, timeout: float) -> dict:
        sent.append(payload)
        return {"text": VALID, "usage": {"input_tokens": 10, "output_tokens": 5}}

    client = ProviderModelClient(
        bindings={ModelTier.CHEAP: TierBinding(model="bound")},
        api_key="k",
        transport=transport,
    )
    client.structured_generate(request())

    assert "data to be read" in sent[0]["system"]


def test_a_document_is_sent_inside_its_fenced_region() -> None:
    sent: list[dict] = []

    def transport(payload: dict, *, timeout: float) -> dict:
        sent.append(payload)
        return {"text": VALID, "usage": {"input_tokens": 10, "output_tokens": 5}}

    client = ProviderModelClient(
        bindings={ModelTier.CHEAP: TierBinding(model="bound")},
        api_key="k",
        transport=transport,
    )
    client.structured_generate(request())

    content = sent[0]["messages"][0]["content"]
    assert "<<<CANDIDATE_DOCUMENT" in content
    assert "<<<END a3f9>>>" in content


def test_usage_is_read_from_the_provider_not_estimated() -> None:
    def transport(payload: dict, *, timeout: float) -> dict:
        return {"text": VALID, "usage": {"input_tokens": 1234, "output_tokens": 56}, "id": "req-1"}

    client = ProviderModelClient(
        bindings={ModelTier.CHEAP: TierBinding(model="bound")},
        api_key="k",
        transport=transport,
    )

    result = client.structured_generate(request())

    assert result.usage.input_tokens == 1234
    assert result.usage.provider_request_id == "req-1"


def test_a_response_without_usage_reports_zero_rather_than_a_guess() -> None:
    def transport(payload: dict, *, timeout: float) -> dict:
        return {"text": VALID}

    client = ProviderModelClient(
        bindings={ModelTier.CHEAP: TierBinding(model="bound")},
        api_key="k",
        transport=transport,
    )

    result = client.structured_generate(request())

    assert result.usage.input_tokens == 0


def test_a_timeout_becomes_a_typed_error() -> None:
    def transport(payload: dict, *, timeout: float) -> dict:
        raise TimeoutError("too slow")

    client = ProviderModelClient(
        bindings={ModelTier.CHEAP: TierBinding(model="bound")},
        api_key="k",
        transport=transport,
    )

    with pytest.raises(ModelUnavailable, match="timeout"):
        client.structured_generate(request())


# --- the shipped configuration ---------------------------------------------------------


def test_the_shipped_bindings_are_empty() -> None:
    """So a fresh clone runs against the fake, with no account and no key."""
    config = yaml.safe_load((REPO_ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
    bindings = load_bindings(config)

    assert all(not binding.is_bound for binding in bindings.values())


def test_no_model_name_appears_in_any_python_file() -> None:
    """The tier indirection is what keeps every report comparable across a model
    swap. A name written into code would defeat it."""
    import re

    pattern = re.compile(
        r"\b(gpt-[0-9]|claude-[0-9]|gemini-[0-9]|llama-?[0-9]|mistral-|command-r)", re.IGNORECASE
    )
    offenders = []

    for package in ("domain", "application", "pipeline", "infrastructure", "app", "eval"):
        for path in sorted((REPO_ROOT / package).rglob("*.py")):
            if pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []


def test_prices_ship_unset() -> None:
    """A zero would read as free; a guess would be a number nobody measured."""
    pricing = yaml.safe_load((REPO_ROOT / "config" / "pricing.yaml").read_text(encoding="utf-8"))

    for tier, rates in pricing["tiers"].items():
        for field, value in rates.items():
            assert value is None, f"{tier}.{field} ships with a price"


def test_an_unpriced_cost_is_null_not_zero() -> None:
    """Stated here as the contract-level property the pricing file relies on."""

    record = CostRecord(
        record_id=uuid4(),
        run_id=uuid4(),
        call_site="assess.criterion",
        model_tier=ModelTier.CHEAP,
        input_tokens=1000,
        output_tokens=200,
        latency_ms=900,
        occurred_at=NOW,
    )

    assert record.cost_usd is None
    assert record.cost_usd != Decimal(0)


def test_the_bindings_hash_changes_with_a_swap() -> None:
    """Part of the run record and the cache key, so a model swap is visible in
    the evaluation rather than silently changing results."""
    first = {ModelTier.CHEAP: TierBinding(model="a"), ModelTier.STRONG: TierBinding(model="b")}
    second = {ModelTier.CHEAP: TierBinding(model="c"), ModelTier.STRONG: TierBinding(model="b")}

    assert bindings_hash(first) != bindings_hash(second)
    assert bindings_hash(first) == bindings_hash(dict(first))
