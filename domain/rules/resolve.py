"""Evidence to criterion state.

Stage one of the rule engine. A pure function over span-validated evidence and
one criterion's configuration, producing a state and the id of the rule that
produced it.

Two rows in the table below carry most of the argument for this whole system:

``R-CONFLICT`` never resolves itself. A CV claiming five years and a cover
letter claiming two is exactly where a human is cheap and a model is dangerous,
so a contradiction scores nothing *and* routes to a person, rather than being
quietly averaged into a slightly lower band.

``R-INSUFFICIENT`` is not ``NOT_MET``. Absence of evidence is not evidence of
absence. Collapsing the two is the single most common failure of a naive
screener, and keeping them apart is what the insufficient-evidence recall metric
measures.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from domain.contracts.enums import CriterionState, EvidenceState
from domain.contracts.evidence import EvidenceItem
from domain.contracts.rubric import Criterion


@dataclass(frozen=True)
class CriterionResolution:
    """One criterion's outcome, with the rule that decided it.

    Not a stored contract: the fields that persist live on
    ``CriterionAssessment``. This is the value the rule engine passes to
    aggregation, and keeping it out of the contract set means the rule engine
    can gain a diagnostic field without a schema migration.
    """

    criterion_id: str
    state: CriterionState
    rule_id: str
    supported_count: int
    contradicted_count: int
    requires_human: bool = False
    unassessed_reason: str | None = None
    #: Weight and points are attached by aggregation, not here, so that this
    #: function stays a statement about evidence rather than about scoring.
    notes: tuple[str, ...] = field(default_factory=tuple)


def resolve_criterion(
    evidence: Sequence[EvidenceItem],
    criterion: Criterion,
    *,
    unassessed_reason: str | None = None,
) -> CriterionResolution:
    """Decide one criterion's state from its evidence.

    ``evidence`` must already be span-validated: items whose quotation could not
    be located in the source are filtered out upstream and never reach here.
    Passing an unvalidated list would let a fabricated quotation carry a
    criterion, which is the failure this system exists to prevent.

    ``unassessed_reason`` is set when the criterion was never put to a model at
    all, because a budget ceiling was reached or a repair failed. That is a
    different fact from "the document is silent", and it is recorded as such.
    """
    if unassessed_reason is not None:
        return CriterionResolution(
            criterion_id=criterion.id,
            state=CriterionState.INSUFFICIENT_EVIDENCE,
            rule_id="R-UNASSESSED",
            supported_count=0,
            contradicted_count=0,
            requires_human=True,
            unassessed_reason=unassessed_reason,
        )

    usable = [item for item in evidence if item.is_usable]
    supported = sum(1 for item in usable if item.state is EvidenceState.SUPPORTED)
    contradicted = sum(1 for item in usable if item.state is EvidenceState.CONTRADICTED)

    def resolution(
        state: CriterionState, rule_id: str, *, human: bool = False
    ) -> CriterionResolution:
        return CriterionResolution(
            criterion_id=criterion.id,
            state=state,
            rule_id=rule_id,
            supported_count=supported,
            contradicted_count=contradicted,
            requires_human=human,
        )

    # A criterion configured with min_supported = 0 would otherwise satisfy the
    # R-MET condition on an empty evidence list, making silence a positive
    # finding. Non-negotiable rule 3 says absence yields insufficient_evidence,
    # never a guess, so no-evidence short-circuits ahead of the table.
    if supported == 0 and contradicted == 0:
        return resolution(CriterionState.INSUFFICIENT_EVIDENCE, "R-INSUFFICIENT")

    # The table, top to bottom, first match wins. Past the guard above there is
    # at least one item, so the remaining rows are exhaustive and the trailing
    # branch is R-PARTIAL rather than a fallthrough nobody reaches.
    if supported >= 1 and contradicted >= 1:
        return resolution(CriterionState.CONTRADICTED, "R-CONFLICT", human=True)

    if supported == 0:
        return resolution(CriterionState.NOT_MET, "R-NOT-MET")

    if supported >= criterion.min_supported:
        return resolution(CriterionState.MET, "R-MET")

    return resolution(CriterionState.PARTIAL, "R-PARTIAL")
