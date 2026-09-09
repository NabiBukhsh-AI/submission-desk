"""The one configuration object a recruiter edits.

Everything a recruiter might reasonably want to change lives here rather than in
Python: what is assessed, how much each criterion counts, how many pieces of
evidence a claim needs, where the band cutoffs sit, and what may never be
inferred. Changing "5 years" to "3 years" is a text edit.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field, field_validator, model_validator

from domain.contracts.base import Contract
from domain.contracts.enums import Band, CriterionKind, CriterionState

SLUG_PATTERN = re.compile(r"^[a-z0-9-]{3,64}$")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

#: The states that carry points. INSUFFICIENT_EVIDENCE is deliberately absent:
#: it is excluded from the denominator rather than scored as zero, because
#: "the document does not say" must not be punished like "the document says no".
SCORING_STATES = (
    CriterionState.MET,
    CriterionState.PARTIAL,
    CriterionState.NOT_MET,
    CriterionState.CONTRADICTED,
)

#: Bands produced by a gate rather than by a score cutoff, so they may not
#: appear in a rubric's threshold list.
GATE_BANDS = frozenset({Band.INSUFFICIENT_INFORMATION, Band.MANUAL_REVIEW_REQUIRED})

DEFAULT_FORBIDDEN_ATTRIBUTES = (
    "age",
    "gender",
    "nationality",
    "ethnicity",
    "religion",
    "marital_status",
    "family_status",
    "disability",
    "personality",
    "culture_fit",
    "appearance",
)

#: High-stakes criteria need corroboration, so they ask for more evidence.
HIGH_STAKES_MIN_SUPPORTED = 2


class Criterion(Contract):
    id: str
    label: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=10, max_length=1000)
    kind: CriterionKind
    weight: int = Field(ge=1, le=10)
    min_supported: int = Field(default=1, ge=0)
    state_points: dict[CriterionState, float | None]
    positive_examples: list[str] = Field(default_factory=list)
    negative_examples: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not SLUG_PATTERN.match(value):
            raise ValueError(
                "criterion id must be a slug of 3 to 64 lowercase letters, digits, or -"
            )
        return value

    @model_validator(mode="before")
    @classmethod
    def _high_stakes_default(cls, data: Any) -> Any:
        """High-stakes criteria default to requiring two supporting items.

        Applied before validation so it is a default the recruiter can override,
        not a constraint they cannot.
        """
        if (
            isinstance(data, dict)
            and "min_supported" not in data
            and data.get("kind") in (CriterionKind.HIGH_STAKES, CriterionKind.HIGH_STAKES.value)
        ):
            data = {**data, "min_supported": HIGH_STAKES_MIN_SUPPORTED}
        return data

    @model_validator(mode="after")
    def _points_are_complete_and_bounded(self) -> Criterion:
        missing = [state for state in SCORING_STATES if state not in self.state_points]
        if missing:
            raise ValueError(f"state_points is missing {', '.join(s.value for s in missing)}")

        for state in SCORING_STATES:
            points = self.state_points[state]
            if points is None:
                raise ValueError(f"state_points[{state.value}] must carry a number, not null")
            if not 0.0 <= points <= 1.0:
                raise ValueError(f"state_points[{state.value}] must be between 0 and 1")

        if self.state_points.get(CriterionState.INSUFFICIENT_EVIDENCE) is not None:
            raise ValueError(
                "insufficient_evidence must map to null; it is excluded from the "
                "denominator rather than scored as zero"
            )
        return self

    @model_validator(mode="after")
    def _blocker_needs_evidence(self) -> Criterion:
        """A blocker that requires no evidence could decline a candidate on silence."""
        if self.kind is CriterionKind.BLOCKER and self.min_supported == 0:
            raise ValueError("a blocker criterion must require at least one supporting item")
        return self


class BandThreshold(Contract):
    band: Band
    min_score: float = Field(ge=0.0, le=1.0)

    @field_validator("band")
    @classmethod
    def _not_a_gate_band(cls, value: Band) -> Band:
        if value in GATE_BANDS:
            raise ValueError(
                f"{value.value} is produced by a gate, not by a score cutoff, "
                "so it cannot appear in the band table"
            )
        return value


class RoleRubric(Contract):
    role_id: str
    role_title: str = Field(min_length=3, max_length=200)
    version: str
    criteria: list[Criterion] = Field(min_length=1, max_length=30)
    bands: list[BandThreshold] = Field(min_length=1)
    min_coverage: float = Field(default=0.70, ge=0.0, le=1.0)
    forbidden_attributes: list[str] = Field(
        default_factory=lambda: list(DEFAULT_FORBIDDEN_ATTRIBUTES),
        min_length=1,
    )
    blind_mode_default: bool = True
    notes_for_reviewer: str | None = None

    @field_validator("role_id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not SLUG_PATTERN.match(value):
            raise ValueError("role_id must be a slug of 3 to 64 lowercase letters, digits, or -")
        return value

    @field_validator("version")
    @classmethod
    def _semver(cls, value: str) -> str:
        if not SEMVER_PATTERN.match(value):
            raise ValueError("version must be semver, for example 1.2.0")
        return value

    @model_validator(mode="after")
    def _criteria_are_unique_and_scorable(self) -> RoleRubric:
        ids = [criterion.id for criterion in self.criteria]
        duplicates = sorted({name for name in ids if ids.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate criterion ids: {', '.join(duplicates)}")

        if all(criterion.kind is CriterionKind.BLOCKER for criterion in self.criteria):
            raise ValueError(
                "a rubric of blockers alone can only decline; at least one criterion "
                "must contribute to the score"
            )

        if sum(criterion.weight for criterion in self.criteria) <= 0:
            raise ValueError("total criterion weight must be greater than zero")
        return self

    @model_validator(mode="after")
    def _bands_are_monotonic_and_total(self) -> RoleRubric:
        """Cutoffs descend strictly and reach zero, so every score lands in a band."""
        scores = [threshold.min_score for threshold in self.bands]
        if scores != sorted(scores, reverse=True) or len(set(scores)) != len(scores):
            raise ValueError("band cutoffs must be listed in strictly decreasing order")

        named = [threshold.band for threshold in self.bands]
        if len(set(named)) != len(named):
            raise ValueError("each band may appear at most once in the band table")

        if scores[-1] != 0.0:
            raise ValueError(
                "the lowest band must start at 0.0 so that every score falls into a band"
            )
        return self
