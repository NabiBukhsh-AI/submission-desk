"""The security record for one document.

Never suppressed, always shown when it is not clean. Sanitisation flags and
wraps; it does not delete, because deleting would hide content from the
reviewer and move the offsets that every span depends on.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from pydantic import Field, model_validator

from domain.contracts.base import Contract
from domain.contracts.enums import IntegrityFindingKind, IntegrityTier, Severity
from domain.contracts.source_text import Provenance

EXCERPT_MAX_CHARS = 240

#: A HIGH finding at or above this confidence halts the run; below it, the same
#: finding routes to a person instead. Low confidence never resolves to "assume
#: clean" — it resolves to "proceed and tell somebody", which keeps recall for
#: the recruiter without pretending the scanner was sure.
#:
#: It lives with the contract rather than with the detectors because the report
#: validates itself against it: a stored report whose tier does not follow from
#: its findings is rejected on the way back out of the database, not only on the
#: way in.
SUSPECT_CONFIDENCE = 0.75


class IntegrityFinding(Contract):
    kind: IntegrityFindingKind
    severity: Severity
    detector: str = Field(min_length=1)
    detector_confidence: float = Field(ge=0.0, le=1.0)
    excerpt: str = Field(max_length=EXCERPT_MAX_CHARS)
    provenance: Provenance | None = None


def tier_for(
    findings: Sequence[IntegrityFinding], *, threshold: float = SUSPECT_CONFIDENCE
) -> IntegrityTier:
    """Which tier a set of findings implies.

    QUARANTINE for a HIGH the detector was confident about. SUSPECT for a
    MEDIUM, or for a HIGH below the threshold. CLEAN when nothing rose above
    LOW: a LOW finding is reported and the run proceeds, which is the honest
    treatment of keyword stuffing.
    """
    if any(
        finding.severity is Severity.HIGH and finding.detector_confidence >= threshold
        for finding in findings
    ):
        return IntegrityTier.QUARANTINE

    if any(finding.severity in (Severity.HIGH, Severity.MEDIUM) for finding in findings):
        return IntegrityTier.SUSPECT

    return IntegrityTier.CLEAN


class IntegrityReport(Contract):
    document_id: UUID
    tier: IntegrityTier
    findings: list[IntegrityFinding] = Field(default_factory=list)
    classifier_used: bool = False
    classifier_unavailable: bool = False
    sanitized_char_count: int = Field(default=0, ge=0)

    #: The confidence threshold this tier was assigned under.
    #:
    #: Recorded rather than assumed, because the tier is a function of the
    #: findings *and* this number. A report that did not carry it could not be
    #: re-checked later: a deployment that tuned the threshold would have every
    #: stored report fail validation on the way back out, which is how a
    #: security record becomes a thing people stop reading.
    threshold: float = Field(default=SUSPECT_CONFIDENCE, ge=0.0, le=2.0)

    @model_validator(mode="after")
    def _tier_matches_the_findings(self) -> IntegrityReport:
        """The tier follows from the findings; it is not an independent opinion.

        Stated as an invariant rather than computed on the way in, so a report
        assembled from a database row is checked as strictly as one built by the
        detectors. A stored tier that has drifted from its findings — through a
        migration, a hand-edited row, or a detector change — is refused rather
        than shown to a reviewer as though somebody had decided it.

        Confidence is part of the rule. A HIGH finding the detector was unsure
        about implies SUSPECT, not QUARANTINE, which is what stops a shaky
        heuristic from halting a legitimate application.
        """
        expected = tier_for(self.findings, threshold=self.threshold)

        if self.tier is not expected:
            raise ValueError(
                f"tier {self.tier.value} does not match the findings; they imply {expected.value}"
            )
        return self
