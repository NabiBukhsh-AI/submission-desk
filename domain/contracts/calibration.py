"""A past decision, kept as an anchor.

Retrieval exists here for exactly one purpose: showing the assessment step two
or three previously decided candidates for the same role, with the reasons the
recruiter recorded. It is not a general knowledge base and it is not decoration.

A card carries no name, no contact detail, and no nationality, and is created
only from runs a reviewer approved.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from domain.contracts.base import Contract, require_utc
from domain.contracts.enums import Band, CriterionState, OverrideReason


class CalibrationCard(Contract):
    card_id: UUID
    role_id: str = Field(min_length=1)
    rubric_version: str = Field(min_length=1)
    anonymized_summary: str = Field(min_length=1, max_length=2000)
    criterion_states: dict[str, CriterionState]
    final_band: Band
    reviewer_reason_codes: list[OverrideReason] = Field(default_factory=list)
    reviewer_reason_text_redacted: str | None = None
    decided_at: datetime
    embedding: bytes

    _utc = field_validator("decided_at")(require_utc)
