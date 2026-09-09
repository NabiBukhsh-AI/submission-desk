"""One criterion, assessed.

``resolved_state`` is set by the rule engine, never by a model. The invariant
that it equals ``resolve_criterion(evidence, criterion).state`` is checked
against the evaluation corpus rather than here, because recomputing it would
require the rubric this contract deliberately does not carry.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from domain.contracts.base import Contract
from domain.contracts.enums import CriterionState, ModelTier
from domain.contracts.evidence import EvidenceItem


class CriterionAssessment(Contract):
    criterion_id: str = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    rejected_evidence: list[EvidenceItem] = Field(default_factory=list)
    resolved_state: CriterionState
    resolution_rule_id: str = Field(min_length=1)
    tier_used: ModelTier
    escalated: bool = False
    escalation_trigger: str | None = None
    escalation_changed_state: bool | None = None
    pre_escalation_state: CriterionState | None = None
    chunks_shown: list[int] | None = None
    unassessed_reason: str | None = None

    @model_validator(mode="after")
    def _rejected_items_are_actually_rejected(self) -> CriterionAssessment:
        """The two lists are disjoint, and each holds what its name says.

        Kept separate rather than filtered at read time so that the reviewer can
        be shown what was discarded and why, which is the difference between a
        hallucination check and a hallucination check nobody can see.
        """
        scoring_ids = {item.evidence_id for item in self.evidence}
        rejected_ids = {item.evidence_id for item in self.rejected_evidence}
        overlap = scoring_ids & rejected_ids
        if overlap:
            raise ValueError(
                f"evidence cannot be both used and rejected: {sorted(map(str, overlap))}"
            )

        unusable = [item.evidence_id for item in self.evidence if not item.is_usable]
        if unusable:
            raise ValueError(
                f"evidence with an invalid span reached the scoring list: "
                f"{sorted(map(str, unusable))}"
            )
        return self

    @model_validator(mode="after")
    def _escalation_is_recorded_coherently(self) -> CriterionAssessment:
        """Escalation value is measurable only if the before state was kept."""
        if self.escalated and self.pre_escalation_state is None:
            raise ValueError(
                "an escalated criterion must record pre_escalation_state, "
                "otherwise the value of escalating cannot be measured afterwards"
            )
        if not self.escalated and self.escalation_changed_state is not None:
            raise ValueError("escalation_changed_state is meaningless without an escalation")
        return self
