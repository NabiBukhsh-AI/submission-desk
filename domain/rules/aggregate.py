"""Criterion states to a band, with the reasoning attached.

Stage two of the rule engine, and the half of the architectural claim that a
model never touches. Seven steps in a fixed order, each appending a derivation
step, so the answer to "why did this candidate get this band" is a finite list
rather than a transcript.

The two decisions that make this defensible rather than merely deterministic:

The score divides by *resolved* weight, never total weight. Dividing by the
total would punish a candidate for the system's own failure to find evidence,
which silently turns "we could not tell" into "they do not have it". The missing
information surfaces through coverage and through the gate instead.

The human-required overlay does not change the band. Downgrading a band because
a document was flagged, or because a span failed validation, would corrupt the
quality metrics with operational noise. The run routes to review and the reason
is displayed; the band still says what the evidence said.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from domain.contracts.enums import Band, CriterionKind, CriterionState, IntegrityTier
from domain.contracts.recommendation import DerivationStep, Recommendation
from domain.contracts.rubric import RoleRubric
from domain.rules.resolve import CriterionResolution


@dataclass(frozen=True)
class AggregationFlags:
    """Operational facts that route a run to a person without moving its band."""

    invalid_span_present: bool = False
    partial_profile: bool = False
    budget_capped: bool = False


#: Reason codes surfaced to the reviewer. Strings rather than an enum because
#: they are display keys resolved through explanations.yaml, and adding one
#: should not require a contract change.
REASON_CONTRADICTED = "contradicted_criterion"
REASON_INTEGRITY = "integrity_not_clean"
REASON_INVALID_SPAN = "invalid_span_present"
REASON_COVERAGE = "coverage_below_gate"
REASON_PARTIAL_PROFILE = "partial_profile"
REASON_BUDGET = "budget_capped"
REASON_UNASSESSED = "criterion_unassessed"
REASON_BLOCKER_UNKNOWN = "blocker_unknown"


def _step(rule_id: str, description: str, inputs: dict[str, object], output: str) -> DerivationStep:
    return DerivationStep(rule_id=rule_id, description=description, inputs=inputs, output=output)


def aggregate(
    resolutions: Sequence[CriterionResolution],
    rubric: RoleRubric,
    *,
    run_id: UUID,
    integrity: IntegrityTier = IntegrityTier.CLEAN,
    flags: AggregationFlags | None = None,
) -> Recommendation:
    """Turn resolved criteria into a recommendation.

    Pure: no clock, no randomness, no I/O. Given the same resolutions and the
    same rubric it returns the same band every time, which is the precondition
    for the routing, calibration, and fairness experiments meaning anything.
    """
    flags = flags or AggregationFlags()
    by_id = {resolution.criterion_id: resolution for resolution in resolutions}
    criterion_states = {
        criterion.id: by_id[criterion.id].state
        if criterion.id in by_id
        else CriterionState.INSUFFICIENT_EVIDENCE
        for criterion in rubric.criteria
    }
    derivation: list[DerivationStep] = []

    # --- Step 1: coverage --------------------------------------------------
    total_weight = sum(criterion.weight for criterion in rubric.criteria)
    resolved = [
        criterion
        for criterion in rubric.criteria
        if criterion_states[criterion.id] is not CriterionState.INSUFFICIENT_EVIDENCE
    ]
    resolved_weight = sum(criterion.weight for criterion in resolved)
    coverage = resolved_weight / total_weight if total_weight else 0.0

    derivation.append(
        _step(
            "R-COVERAGE",
            f"Evidence was found for {len(resolved)} of {len(rubric.criteria)} criteria, "
            f"covering {coverage:.0%} of the rubric by weight.",
            {"resolved_weight": resolved_weight, "total_weight": total_weight},
            f"coverage={coverage:.4f}",
        )
    )

    # --- Step 2: score over resolved criteria only -------------------------
    score: float | None = None
    if resolved_weight:
        earned = sum(
            criterion.weight * (criterion.state_points[criterion_states[criterion.id]] or 0.0)
            for criterion in resolved
        )
        score = earned / resolved_weight
        derivation.append(
            _step(
                "R-SCORE",
                f"Weighted score across the criteria that could be assessed is {score:.2f}. "
                f"Criteria with no evidence are excluded from this calculation rather "
                f"than counted against the candidate.",
                {"earned": round(earned, 6), "resolved_weight": resolved_weight},
                f"score={score:.4f}",
            )
        )

    # --- Step 3: coverage gate ---------------------------------------------
    # Nothing assessed is its own case. A rubric with the gate set to zero would
    # otherwise fall through to banding with no score at all, and scoring zero
    # criteria is not a low score, it is no answer.
    nothing_assessed = resolved_weight == 0
    if nothing_assessed or coverage < rubric.min_coverage:
        description = (
            "Nothing in the rubric could be assessed from these documents, so there is "
            "no basis for a recommendation. Ask for more material."
            if nothing_assessed
            else (
                f"Only {coverage:.0%} of the rubric could be assessed, below the "
                f"{rubric.min_coverage:.0%} minimum this role requires. There is not enough "
                f"information to place this candidate, so no score is reported."
            )
        )
        derivation.append(
            _step(
                "R-COVERAGE-GATE",
                description,
                {"coverage": round(coverage, 4), "min_coverage": rubric.min_coverage},
                Band.INSUFFICIENT_INFORMATION.value,
            )
        )
        return Recommendation(
            run_id=run_id,
            band=Band.INSUFFICIENT_INFORMATION,
            score=None,
            coverage=coverage,
            criterion_states=criterion_states,
            blockers_fired=[],
            requires_human=True,
            requires_human_reasons=[REASON_COVERAGE],
            derivation=derivation,
            rubric_version=rubric.version,
        )

    # --- Step 4: blockers ---------------------------------------------------
    blockers_fired, blocker_band, blocker_reasons = _apply_blockers(
        rubric, criterion_states, derivation
    )

    # --- Step 5: banding ----------------------------------------------------
    # Past the gate, resolved_weight is non-zero, so step 2 produced a score.
    assert score is not None, "a run past the coverage gate always carries a score"

    band = blocker_band
    if band is None:
        band = _band_for(score, rubric)
        derivation.append(
            _step(
                "R-BAND",
                f"A score of {score:.2f} falls in the {band.value.replace('_', ' ')} range "
                f"for this role.",
                {"score": round(score, 4)},
                band.value,
            )
        )

    # --- Step 6: human-required overlay -------------------------------------
    reasons = _human_reasons(criterion_states, resolutions, integrity, flags, blocker_reasons)

    if reasons:
        derivation.append(
            _step(
                "R-HUMAN-REQUIRED",
                "This candidate is routed for a closer look before anything is sent. "
                "The band above is unchanged: these are reasons to check the work, not "
                "reasons to think less of the candidate.",
                {"reasons": reasons},
                "requires_human=true",
            )
        )

    # Scoreless bands carry no score by contract, and a blocker decline keeps
    # the score it earned so the reviewer can see the candidate was strong
    # elsewhere.
    if band in (Band.INSUFFICIENT_INFORMATION, Band.MANUAL_REVIEW_REQUIRED):
        score = None

    return Recommendation(
        run_id=run_id,
        band=band,
        score=score,
        coverage=coverage,
        criterion_states=criterion_states,
        blockers_fired=blockers_fired,
        requires_human=bool(reasons),
        requires_human_reasons=reasons,
        derivation=derivation,
        rubric_version=rubric.version,
    )


def _apply_blockers(
    rubric: RoleRubric,
    criterion_states: dict[str, CriterionState],
    derivation: list[DerivationStep],
) -> tuple[list[str], Band | None, list[str]]:
    """Step 4. A blocker decides the outcome on its own, in two ways only.

    A failed requirement is a clean decline. An unchecked one is a question,
    because you may not decline someone for something you never looked at.
    """
    fired: list[str] = []
    band: Band | None = None
    reasons: list[str] = []

    for criterion in rubric.criteria:
        if criterion.kind is not CriterionKind.BLOCKER:
            continue
        state = criterion_states[criterion.id]

        if state in (CriterionState.NOT_MET, CriterionState.CONTRADICTED):
            fired.append(criterion.id)
            band = Band.DECLINE
            derivation.append(
                _step(
                    "R-BLOCKER-FAIL",
                    f"'{criterion.label}' is a requirement for this role and the documents "
                    f"do not meet it, so the recommendation is Decline regardless of the "
                    f"score elsewhere.",
                    {"criterion_id": criterion.id, "state": state.value},
                    Band.DECLINE.value,
                )
            )
        elif state is CriterionState.INSUFFICIENT_EVIDENCE:
            fired.append(criterion.id)
            # A known failure is a cleaner answer than an unknown, so a decline
            # already recorded is not downgraded to a question.
            if band is not Band.DECLINE:
                band = Band.MANUAL_REVIEW_REQUIRED
                reasons.append(REASON_BLOCKER_UNKNOWN)
            derivation.append(
                _step(
                    "R-BLOCKER-UNKNOWN",
                    f"'{criterion.label}' is a requirement for this role and the documents "
                    f"do not say either way. This needs checking with the candidate before "
                    f"a decision is made; it is not a reason to decline.",
                    {"criterion_id": criterion.id},
                    Band.MANUAL_REVIEW_REQUIRED.value,
                )
            )

    return fired, band, reasons


def _human_reasons(
    criterion_states: dict[str, CriterionState],
    resolutions: Sequence[CriterionResolution],
    integrity: IntegrityTier,
    flags: AggregationFlags,
    blocker_reasons: list[str],
) -> list[str]:
    """Step 6. Why this run wants a person, in the order the reasons were found.

    None of these move the band. They are reasons to check the work, not reasons
    to think less of the candidate, and conflating the two would put operational
    noise into the quality metrics.
    """
    reasons = list(blocker_reasons)

    if any(state is CriterionState.CONTRADICTED for state in criterion_states.values()):
        reasons.append(REASON_CONTRADICTED)
    if integrity is not IntegrityTier.CLEAN:
        reasons.append(REASON_INTEGRITY)
    if flags.invalid_span_present:
        reasons.append(REASON_INVALID_SPAN)
    if flags.partial_profile:
        reasons.append(REASON_PARTIAL_PROFILE)
    if flags.budget_capped:
        reasons.append(REASON_BUDGET)
    if any(resolution.unassessed_reason for resolution in resolutions):
        reasons.append(REASON_UNASSESSED)

    # Deduplicate while keeping the order the reasons were found in.
    return list(dict.fromkeys(reasons))


def _band_for(score: float | None, rubric: RoleRubric) -> Band:
    """First threshold whose cutoff the score reaches.

    The rubric validator guarantees the thresholds descend and reach zero, so a
    score in [0, 1] always lands somewhere and this cannot fall through.
    """
    if score is None:
        return Band.INSUFFICIENT_INFORMATION
    for threshold in rubric.bands:
        if score >= threshold.min_score:
            return threshold.band
    return rubric.bands[-1].band
