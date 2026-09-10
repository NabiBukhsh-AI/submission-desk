"""Writing down what a call consumed, once, in the place everything reads.

Three things want this number and they must not disagree. The budget guard reads
it to decide whether the next node may run. The operations page reads it to
report what a hundred candidates cost. The evaluation reads it to compare three
routing policies. If a node recorded usage into the guard and forgot the ledger,
the guard would work, the page would show zero, and the routing comparison would
be a table of blanks with a confident heading over it.

That is exactly what happened before this module existed: usage reached the
budget guard and nothing else, so ``llm_calls`` was empty and every run record
carried zero tokens.

So there is one function, and it does all three: guard, ledger, and the run's
running totals.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from application.deps import Deps
from domain.contracts.cost import CostRecord
from domain.contracts.enums import ModelTier


def record_call(
    deps: Deps,
    run_id: UUID,
    *,
    call_site: str,
    tier: ModelTier,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    latency_ms: int = 0,
    escalated: bool = False,
) -> Decimal | None:
    """Account for one model call. Returns the cost, or ``None`` if unpriced.

    Tokens are always real, so the guard and the ledger are always right.
    Money is real or absent — never zero — because a zero reads as free and
    free is a claim nobody measured.
    """
    cost = _cost_of(
        deps,
        tier,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
    )

    if deps.budget is not None:
        deps.budget.record(
            run_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            escalated=escalated,
        )

    deps.costs.record(
        CostRecord(
            record_id=uuid4(),
            run_id=run_id,
            call_site=call_site,
            model_tier=tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            unit_price_source=_source(deps) if cost is not None else None,
            cost_usd=cost,
            latency_ms=max(latency_ms, 0),
            occurred_at=datetime.now(UTC),
        )
    )

    deps.runs.add_usage(
        run_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        cost_usd=cost,
        escalated=escalated,
    )

    return cost


def _cost_of(
    deps: Deps,
    tier: ModelTier,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
) -> Decimal | None:
    pricing = deps.pricing
    if pricing is None:
        return None
    result: Decimal | None = pricing.cost_of(
        tier,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
    )
    return result


def _source(deps: Deps) -> str:
    """Which price list produced the figure, so it can be audited later."""
    return str(getattr(deps.pricing, "source", "pricing:unknown"))
