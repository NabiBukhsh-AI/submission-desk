"""The benchmark, and the labels it is scored against.

Gold labels live in ``eval/gold/``, a directory the pipeline never reads. That
separation is enforced by a test rather than by discipline, because a benchmark
whose answers are reachable from the system under test measures nothing.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field, field_validator

from domain.contracts.base import Contract, require_utc
from domain.contracts.enums import Band, CriterionState


class GoldLabel(Contract):
    """What a human decided, recorded before the system was run.

    ``must_be_insufficient`` is the half of the label most systems omit: it
    records the criteria where the recruiter could not tell from the document,
    so that a system inventing an answer there is marked wrong rather than
    lucky.
    """

    criterion_states: dict[str, CriterionState] = Field(default_factory=dict)

    #: What a recruiter decided, when they decided anything.
    #:
    #: Nullable on purpose. A case testing whether the system abstains has no
    #: opinion about the band, and a case where two competent recruiters would
    #: reasonably disagree should not assert one. Metrics compute only over the
    #: fields a case actually asserts, so a null here removes the case from the
    #: band columns rather than scoring it as a miss — otherwise the benchmark
    #: punishes itself for being precise about what it tests.
    expected_band: Band | None = None
    must_be_insufficient: list[str] = Field(default_factory=list)
    must_flag_integrity: bool = False
    forbidden_claims: list[str] = Field(default_factory=list)
    notes: str | None = None


class EvaluationCase(Contract):
    case_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    documents: list[str] = Field(default_factory=list)
    role_id: str = Field(min_length=1)
    expected: GoldLabel
    tags: list[str] = Field(default_factory=list)
    arms: list[str] = Field(default_factory=list)


class EvaluationResult(Contract):
    result_id: UUID
    run_at: datetime
    case_id: str = Field(min_length=1)
    arm: str = Field(min_length=1)
    config_fingerprint: str = Field(min_length=1)
    criterion_states: dict[str, CriterionState] = Field(default_factory=dict)
    band: Band | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    cost_usd: Decimal | None = None
    latency_ms: int = Field(ge=0)
    escalations: int = Field(default=0, ge=0)
    invalid_spans: int = Field(default=0, ge=0)
    notes: str | None = None

    _utc = field_validator("run_at")(require_utc)
