"""Factual structure, never judgment.

Every field carries provenance, so a reviewer can verify any claim in two
clicks. What is absent matters as much as what is present: there is no field for
age, gender, nationality, marital status, personality, or culture fit, and a
model cannot report what the schema cannot hold.

Work authorisation is deliberately not here either. It is extracted separately
into a record the aggregation stage never reads and the interface reveals only
after the recommendation has been rendered, so it cannot colour the assessment.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import Field

from domain.contracts.base import Contract
from domain.contracts.source_text import Provenance

T = TypeVar("T")


class ProvenancedField(Contract, Generic[T]):
    """One extracted value, with the text that supports it.

    ``conflicts`` is populated by the chunk merge when two page groups disagree.
    Conflicts are surfaced, never resolved silently: a system that quietly picks
    one of two contradictory dates has invented a fact.
    """

    value: T | None = None
    provenance: list[Provenance] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    conflicts: list[str] = Field(default_factory=list)


class EmploymentEntry(Contract):
    employer: ProvenancedField[str]
    title: ProvenancedField[str]
    start: ProvenancedField[str]
    end: ProvenancedField[str]
    summary: ProvenancedField[str]


class CandidateProfile(Contract):
    candidate_id: str = Field(min_length=1, max_length=128)
    employment: list[EmploymentEntry] = Field(default_factory=list)
    education: list[ProvenancedField[str]] = Field(default_factory=list)
    technologies: list[ProvenancedField[str]] = Field(default_factory=list)
    languages: list[ProvenancedField[str]] = Field(default_factory=list)
    artefact_links: list[ProvenancedField[str]] = Field(default_factory=list)
    total_years_claimed: ProvenancedField[float] | None = None
    partial: bool = False
    unparsed_reason: str | None = None
