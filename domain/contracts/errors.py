"""One typed failure.

``message_redacted`` has already passed through the log redactor by the time it
reaches this contract. Provider errors echo their input, so an unredacted
message is a route by which candidate text reaches the database and, from there,
a handover archive.

This is the error *record*. The exception taxonomy that produces it lives in
``domain/errors.py``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from domain.contracts.base import Contract, require_utc
from domain.contracts.enums import RunStatus


class ErrorRecord(Contract):
    error_id: UUID
    run_id: UUID | None = None
    node: str = Field(min_length=1)
    error_code: str = Field(min_length=1)
    error_class: str = Field(min_length=1)
    message_redacted: str = Field(max_length=4000)
    retryable: bool
    attempt: int = Field(ge=0)
    resulting_state: RunStatus | None = None
    occurred_at: datetime

    _utc = field_validator("occurred_at")(require_utc)
