"""Recompute a band from overridden criterion states.

The reviewer never types a band. They change what a criterion resolved to, and
the same ``aggregate()`` the pipeline used produces the band from that.

This is the whole reason the rule engine is a pure function over a rubric and a
list of resolutions. Two call sites, one implementation: if the interface had its
own scoring path, a reviewer's override could produce a band the pipeline could
never have produced, and the derivation shown beside it would be a fiction.

A test asserts the two paths agree on identical inputs. That test is the
contract this module exists to keep.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from application.deps import Deps
from domain.contracts.assessment import CriterionAssessment
from domain.contracts.enums import Band, CriterionState
from domain.contracts.recommendation import Recommendation
from domain.contracts.review import Override
from domain.contracts.rubric import RoleRubric
from domain.rules import AggregationFlags, aggregate
from domain.rules.resolve import CriterionResolution

#: The rule id recorded when a state came from a person rather than the table.
#: A derivation that showed "R-MET" for an overridden criterion would be
#: claiming the evidence supported something a reviewer decided by hand.
OVERRIDE_RULE = "R-REVIEWER-OVERRIDE"


@dataclass(frozen=True)
class RecomputeResult:
    """The band an override would produce, and what changed to produce it."""

    recommendation: Recommendation
    changed: tuple[str, ...] = ()

    @property
    def band(self) -> Band:
        return self.recommendation.band


def resolutions_from(
    assessments: list[CriterionAssessment],
    overrides: list[Override] | None = None,
) -> list[CriterionResolution]:
    """Rebuild the rule engine's view, with a reviewer's changes applied.

    The counts are carried from the assessment rather than recomputed, because
    the evidence list has already been filtered to what the span validator
    accepted. Recomputing from the unfiltered list would quietly re-admit the
    quotations that could not be found.
    """
    by_criterion = {override.criterion_id: override for override in (overrides or [])}

    resolved = []
    for assessment in assessments:
        override = by_criterion.get(assessment.criterion_id)
        state = override.new_state if override else assessment.resolved_state

        resolved.append(
            CriterionResolution(
                criterion_id=assessment.criterion_id,
                state=state,
                rule_id=OVERRIDE_RULE if override else assessment.resolution_rule_id,
                supported_count=sum(
                    1 for item in assessment.evidence if item.state.value == "supported"
                ),
                contradicted_count=sum(
                    1 for item in assessment.evidence if item.state.value == "contradicted"
                ),
                requires_human=state is CriterionState.CONTRADICTED,
                unassessed_reason=assessment.unassessed_reason,
            )
        )
    return resolved


def recompute(
    run_id: UUID,
    deps: Deps,
    overrides: list[Override] | None = None,
    *,
    rubric: RoleRubric | None = None,
) -> RecomputeResult:
    """What the band becomes if these overrides are applied.

    Used twice: to preview a change while the reviewer is still deciding, and to
    compute the band actually stored when they submit. Same function both times,
    so the number in the preview is the number that gets saved.
    """
    run = deps.runs.get(run_id)
    if run is None:
        raise LookupError(f"no run {run_id}")

    role = rubric if rubric is not None else _rubric_for(deps, run.role_id)
    assessments = deps.evidence.assessments_for_run(run_id)

    recommendation = aggregate(
        resolutions_from(assessments, overrides),
        role,
        run_id=run_id,
        integrity=run.integrity_tier,
        flags=AggregationFlags(
            invalid_span_present=run.invalid_span_count > 0,
            # A reviewer has read the run by the time they override, so the
            # flags that mean "somebody should look at this" have served their
            # purpose. The integrity tier is kept because a manipulated document
            # is a fact about the evidence, not about who has looked at it.
            partial_profile=False,
            budget_capped=False,
        ),
    )

    changed = tuple(
        override.criterion_id for override in (overrides or []) if _differs(assessments, override)
    )

    return RecomputeResult(recommendation=recommendation, changed=changed)


def _differs(assessments: list[CriterionAssessment], override: Override) -> bool:
    for assessment in assessments:
        if assessment.criterion_id == override.criterion_id:
            return assessment.resolved_state is not override.new_state
    return False


def _rubric_for(deps: Deps, role_id: str) -> RoleRubric:
    if deps.rubric_loader is None:
        raise LookupError("no role definitions are configured")
    loaded: RoleRubric = deps.rubric_loader(role_id)
    return loaded
