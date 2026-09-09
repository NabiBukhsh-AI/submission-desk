"""What the system recommends, and how it got there.

The derivation is the product. A band without the list of rules that produced it
is an opinion; a band with one is an argument a recruiter can check, disagree
with, and change by editing a rubric.

Rule descriptions are rendered verbatim in the reviewer interface, so they are
written in recruiter English rather than code English.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from domain.contracts.base import Contract
from domain.contracts.enums import Band, CriterionState

#: Bands that record why no score could be produced, rather than a score.
SCORELESS_BANDS = frozenset({Band.INSUFFICIENT_INFORMATION, Band.MANUAL_REVIEW_REQUIRED})


class DerivationStep(Contract):
    rule_id: str = Field(min_length=1)
    description: str = Field(min_length=1, max_length=1000)
    inputs: dict[str, Any] = Field(default_factory=dict)
    output: str = Field(min_length=1)


class Recommendation(Contract):
    run_id: UUID
    band: Band
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    coverage: float = Field(ge=0.0, le=1.0)
    criterion_states: dict[str, CriterionState]
    blockers_fired: list[str] = Field(default_factory=list)
    requires_human: bool = False
    requires_human_reasons: list[str] = Field(default_factory=list)
    derivation: list[DerivationStep] = Field(min_length=1)
    rubric_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _absent_score_is_explained(self) -> Recommendation:
        """A missing score is a stated outcome, not a gap.

        The reverse does not hold: a blocker can produce a decline alongside a
        perfectly good score, and hiding that score would hide the fact that the
        candidate was strong everywhere else.
        """
        if self.score is None and self.band not in SCORELESS_BANDS:
            raise ValueError(
                f"band {self.band.value} requires a score; only "
                f"{', '.join(sorted(b.value for b in SCORELESS_BANDS))} may omit one"
            )
        return self

    @model_validator(mode="after")
    def _human_review_states_its_reason(self) -> Recommendation:
        if self.requires_human and not self.requires_human_reasons:
            raise ValueError(
                "requires_human must name its reasons; the reviewer is told why, never merely that"
            )
        return self

    @model_validator(mode="after")
    def _blockers_are_known_criteria(self) -> Recommendation:
        unknown = sorted(set(self.blockers_fired) - set(self.criterion_states))
        if unknown:
            raise ValueError(f"blockers_fired names criteria absent from the rubric: {unknown}")
        return self
