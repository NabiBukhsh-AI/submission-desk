"""One sink, one attempt history.

A row per sink rather than a single status per run, which is what makes partial
delivery representable: the CSV was written, the spreadsheet append failed, and
the reviewer needs to see exactly that rather than a single word.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from domain.contracts.base import Contract, require_utc
from domain.contracts.enums import DeliveryStatus


class DeliveryRecord(Contract):
    delivery_id: UUID
    run_id: UUID
    sink_id: str = Field(min_length=1)
    status: DeliveryStatus
    attempts: int = Field(ge=0)
    external_ref: str | None = None
    last_error: str | None = None
    delivered_at: datetime | None = None

    @field_validator("delivered_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def _delivered_means_delivered(self) -> DeliveryRecord:
        """A delivered row carries the time it happened, so retries are bounded
        by evidence rather than by optimism."""
        if self.status is DeliveryStatus.DELIVERED and self.delivered_at is None:
            raise ValueError("a delivered record must record when it was delivered")
        if self.status is not DeliveryStatus.DELIVERED and self.delivered_at is not None:
            raise ValueError(f"status {self.status.value} cannot carry a delivery timestamp")
        return self
