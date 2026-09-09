"""The configuration every contract shares.

Three properties are non-negotiable and are set here once rather than repeated
on twenty-five models, where one omission would be invisible:

``extra="forbid"``
    A model that invents a field fails validation instead of having the field
    silently dropped. This is what makes a schema a contract.

``frozen=True``
    Node functions cannot mutate state in place, which is what makes the
    runner's per-node commit meaningful.

``str_strip_whitespace=False``
    Whitespace is significant. Stripping it would move character offsets and
    break span validation against the source text.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Contract(BaseModel):
    """Base for every contract in the system."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=False,
        use_enum_values=False,
        validate_default=True,
    )


def require_utc(value: datetime) -> datetime:
    """Reject a naive timestamp.

    Everything persisted is compared across runs and machines, so a timestamp
    without a zone is not a time. Offsets other than UTC are accepted and
    normalised, because the caller's clock is not this model's business.
    """
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value
