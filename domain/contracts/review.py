"""The human decision, and the overrides that teach the system.

``post_override_band`` is recomputed by the rule engine from the overridden
states. The reviewer never types a band, which is what preserves the
deterministic-scoring contract even when a human disagrees with the machine.

An override is the most valuable record the system produces: it is a labelled
disagreement with a reason code, which is the raw material for the next rubric
change and the next prompt change.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from domain.contracts.base import Contract, require_utc
from domain.contracts.enums import Band, CriterionState, OverrideReason, ReviewAction


class Override(Contract):
    criterion_id: str = Field(min_length=1)
    previous_state: CriterionState
    new_state: CriterionState
    reason_code: OverrideReason
    reason_text: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def _states_differ(self) -> Override:
        if self.previous_state is self.new_state:
            raise ValueError("an override that changes nothing is not an override")
        return self


class ReviewDecision(Contract):
    decision_id: UUID
    run_id: UUID
    reviewer_id: str = Field(min_length=1, max_length=128)
    action: ReviewAction
    overrides: list[Override] = Field(default_factory=list)
    comments: str | None = None
    elapsed_seconds: int = Field(ge=0)
    trust_rating: int | None = Field(default=None, ge=1, le=5)
    post_override_band: Band | None = None
    decided_at: datetime
    run_version: int = Field(ge=0)

    _utc = field_validator("decided_at")(require_utc)

    @model_validator(mode="after")
    def _one_override_per_criterion(self) -> ReviewDecision:
        ids = [override.criterion_id for override in self.overrides]
        duplicates = sorted({name for name in ids if ids.count(name) > 1})
        if duplicates:
            raise ValueError(
                f"a criterion may be overridden once per decision; repeated: {duplicates}"
            )
        return self
