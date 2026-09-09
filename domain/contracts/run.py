"""Everything needed to explain, reproduce, or cost one run.

This is the contract that makes the evaluation possible. It records the hashes
of every input that could change an outcome, so "we changed the prompt" is
visible in a diff rather than remembered, and it records real provider usage, so
cost is measured rather than estimated.

``total_cost_usd`` is ``None`` when pricing is unconfigured. Never zero, never a
guess: a zero would read as free, and a guess would be a number nobody measured.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from domain.contracts.base import Contract, require_utc
from domain.contracts.enums import Band, IntegrityTier, ReviewAction, RunStatus


class RunRecord(Contract):
    run_id: UUID
    content_key: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1, max_length=128)
    role_id: str = Field(min_length=1)
    rubric_version: str = Field(min_length=1)
    rubric_hash: str = Field(min_length=1)

    prompt_bundle_hash: str = Field(min_length=1)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    # Binds the run to config/models.yaml without naming a model anywhere.
    model_tier_bindings_hash: str = Field(min_length=1)
    routing_policy_id: str = Field(min_length=1)

    blind_mode: bool
    calibration_enabled: bool
    calibration_status: str = Field(min_length=1)
    pipeline_version: str = Field(min_length=1)

    status: RunStatus
    completed_nodes: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime | None = None

    total_input_tokens: int = Field(default=0, ge=0)
    total_output_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    total_cost_usd: Decimal | None = None

    llm_call_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    repair_count: int = Field(default=0, ge=0)
    escalation_count: int = Field(default=0, ge=0)
    validation_failure_count: int = Field(default=0, ge=0)
    invalid_span_count: int = Field(default=0, ge=0)

    integrity_tier: IntegrityTier = IntegrityTier.CLEAN
    final_band: Band | None = None
    reviewer_action: ReviewAction | None = None
    override_count: int = Field(default=0, ge=0)
    error_codes: list[str] = Field(default_factory=list)
    version: int = Field(default=0, ge=0)

    _started_utc = field_validator("started_at")(require_utc)

    @field_validator("finished_at")
    @classmethod
    def _finished_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @field_validator("total_cost_usd")
    @classmethod
    def _cost_is_not_negative(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("cost cannot be negative")
        return value

    @model_validator(mode="after")
    def _finished_after_started(self) -> RunRecord:
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at precedes started_at")
        return self
