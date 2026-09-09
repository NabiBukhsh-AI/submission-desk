"""What one LLM call actually consumed.

Tokens come from real provider usage metadata, never from an estimate. Cost is
tokens times a configured price, and when no price is configured for the tier
the cost is ``None``, which propagates to the interface as "not configured"
rather than as zero.

``unit_price_source`` records which pricing file produced the number, so a cost
figure in a report can be traced to the rates that were in force when it ran.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from domain.contracts.base import Contract, require_utc
from domain.contracts.enums import ModelTier


class CostRecord(Contract):
    record_id: UUID
    run_id: UUID
    call_site: str = Field(min_length=1)
    model_tier: ModelTier
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    unit_price_source: str | None = None
    cost_usd: Decimal | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    latency_ms: int = Field(ge=0)
    occurred_at: datetime

    _utc = field_validator("occurred_at")(require_utc)

    @model_validator(mode="after")
    def _priced_costs_name_their_source(self) -> CostRecord:
        """A cost figure without a price source cannot be audited later."""
        if self.cost_usd is not None:
            if self.cost_usd < 0:
                raise ValueError("cost cannot be negative")
            if not self.unit_price_source:
                raise ValueError(
                    "a cost figure must record which pricing configuration produced it"
                )
        return self
