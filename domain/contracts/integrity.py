"""The security record for one document.

Never suppressed, always shown when it is not clean. Sanitisation flags and
wraps; it does not delete, because deleting would hide content from the
reviewer and move the offsets that every span depends on.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, model_validator

from domain.contracts.base import Contract
from domain.contracts.enums import IntegrityFindingKind, IntegrityTier, Severity
from domain.contracts.source_text import Provenance

EXCERPT_MAX_CHARS = 240


class IntegrityFinding(Contract):
    kind: IntegrityFindingKind
    severity: Severity
    detector: str = Field(min_length=1)
    detector_confidence: float = Field(ge=0.0, le=1.0)
    excerpt: str = Field(max_length=EXCERPT_MAX_CHARS)
    provenance: Provenance | None = None


class IntegrityReport(Contract):
    document_id: UUID
    tier: IntegrityTier
    findings: list[IntegrityFinding] = Field(default_factory=list)
    classifier_used: bool = False
    classifier_unavailable: bool = False
    sanitized_char_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _tier_matches_the_findings(self) -> IntegrityReport:
        """The tier is the maximum severity present, not an independent opinion.

        Stated as an invariant rather than computed on the way in, so that a
        report assembled from a database row is checked as strictly as one built
        by the detectors.
        """
        severities = {finding.severity for finding in self.findings}

        if Severity.HIGH in severities:
            expected = IntegrityTier.QUARANTINE
        elif Severity.MEDIUM in severities:
            expected = IntegrityTier.SUSPECT
        else:
            expected = IntegrityTier.CLEAN

        if self.tier is not expected:
            raise ValueError(
                f"tier {self.tier.value} does not match the findings; "
                f"the highest severity present implies {expected.value}"
            )
        return self
