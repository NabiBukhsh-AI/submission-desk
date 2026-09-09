"""Why each call went to the tier it went to.

Persisted per LLM call. This is the evidence for the routing claim: a comparison
table across policies is only credible if every individual decision was recorded
at the time it was made.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field, field_validator

from domain.contracts.base import Contract, require_utc
from domain.contracts.enums import ModelTier


class ModelRoutingDecision(Contract):
    call_site: str = Field(min_length=1)
    criterion_id: str | None = None
    attempt_index: int = Field(ge=0)
    requested_tier: ModelTier
    selected_tier: ModelTier
    policy_id: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    budget_remaining_usd: Decimal | None = None
    decided_at: datetime

    _utc = field_validator("decided_at")(require_utc)
