"""The CALIBRATE node. Off by default, and never load-bearing.

It offers the assessment step two or three past decisions for the same role, so
a borderline candidate is judged against what this recruiter actually did rather
than against a model's idea of a good CV.

Three refusals define it.

It is off by default. Calibration plausibly helps and has not been measured, and
the honest place for such a thing is behind a flag with an experiment attached.
CALIBRATION_ENABLED is false, and a run with it off records DISABLED rather than
silently doing nothing.

It never fails a run. Every path in this module produces a status and continues:
no corpus, no matches, no embedding, no index. An aid that could fail an
assessment would be worse than no aid.

Its output cannot be cited. The block is CALIBRATION-kind, the prompt says
reference-only, and the span validator resolves every quotation against the
candidate's own documents — so a sentence lifted from an anchor resolves nowhere
and is rejected. That last one is the control that holds when the other two are
ignored.
"""

from __future__ import annotations

from application.deps import Deps
from domain.calibration import summary_of
from domain.contracts.run_state import DomainEvent, NodeResult, NodeStatus, RunState
from domain.ports.calibration import (
    CalibrationQuery,
    CalibrationResult,
    CalibrationStatus,
)

#: What the block says about itself, in the model's own reading order. The
#: prompt repeats it; this is the belt to that pair of braces.
BLOCK_HEADER = (
    "REFERENCE ONLY — past decisions for this role, shown for calibration.\n"
    "These are not this candidate's documents. Nothing here may be quoted as "
    "evidence, and any quotation taken from this section will be rejected."
)

#: What a reviewer is told about each status, in the operations panel and in the
#: run's own record. A status code shown to a person is a defect.
STATUS_MESSAGES: dict[CalibrationStatus, str] = {
    CalibrationStatus.DISABLED: "Calibration is switched off for this deployment.",
    CalibrationStatus.APPLIED: "Past decisions for this role were used as a reference.",
    CalibrationStatus.EMPTY_CORPUS: (
        "No past decisions exist for this role yet, so there was nothing to compare against."
    ),
    CalibrationStatus.NO_MATCHES: (
        "No past decision for this role was close enough to be a useful reference."
    ),
    CalibrationStatus.EMBEDDING_FAILED: (
        "Past decisions could not be searched, so this candidate was assessed without a reference."
    ),
    CalibrationStatus.INDEX_UNAVAILABLE: (
        "The record of past decisions could not be read, so this candidate was "
        "assessed without a reference."
    ),
}


def node(state: RunState, deps: Deps) -> NodeResult:
    """Offer past decisions as anchors, or say why there are none."""
    if not _enabled(state, deps):
        return _proceed(state, CalibrationResult(status=CalibrationStatus.DISABLED))

    index = deps.calibration_index
    if index is None:
        return _proceed(state, CalibrationResult(status=CalibrationStatus.INDEX_UNAVAILABLE))

    result = index.search(_query_for(state, deps))
    return _proceed(state, result)


def _enabled(state: RunState, deps: Deps) -> bool:
    """Both the run and the deployment have to want it.

    The run's flag is recorded on the record, so a result from a calibrated run
    and one from an uncalibrated run can be told apart afterwards — which is the
    whole point of running the experiment.
    """
    return bool(state.calibration_enabled and deps.settings.calibration_enabled)


def _query_for(state: RunState, deps: Deps) -> CalibrationQuery:
    """What to search for.

    Built from the structured profile rather than from the document text, so the
    query carries the same redaction the cards do. A blind-mode run must not
    reach past its own redaction to search on a name.
    """
    rubric = state.rubric
    recommendation = state.recommendation

    return CalibrationQuery(
        role_id=state.role_id,
        rubric_version=getattr(rubric, "version", "1.0.0"),
        summary=summary_of(state.profile),
        criterion_states={
            criterion_id: getattr(value, "value", str(value))
            for criterion_id, value in (
                getattr(recommendation, "criterion_states", {}) or {}
            ).items()
        },
        exclude_run_id=state.run_id,
        top_k=deps.settings.calibration_top_k,
        staleness_days=deps.settings.calibration_staleness_days,
    )


def render_block(result: CalibrationResult) -> str | None:
    """The anchors as text, or None when there are none.

    Every card is rendered the same way and none of them carries a name, because
    a card cannot hold one: the contract has no field for it and the summary is
    built from named profile fields rather than written by anybody.
    """
    if not result.matches:
        return None

    lines = [BLOCK_HEADER, ""]
    for position, match in enumerate(result.matches, start=1):
        card = match.card
        lines.append(f"Past decision {position} — outcome: {card.final_band.value}")
        lines.append(card.anonymized_summary)

        if card.reviewer_reason_codes:
            reasons = ", ".join(code.value for code in card.reviewer_reason_codes)
            lines.append(f"The reviewer corrected this assessment for: {reasons}")

        lines.append("")

    return "\n".join(lines).strip()


def _proceed(state: RunState, result: CalibrationResult) -> NodeResult:
    """Carry on, whatever happened.

    Degraded rather than failed when calibration was wanted and could not be
    done, because the reviewer should be told that this candidate was assessed
    without the reference others got.
    """
    block = render_block(result)
    wanted_but_missing = result.status in (
        CalibrationStatus.EMBEDDING_FAILED,
        CalibrationStatus.INDEX_UNAVAILABLE,
    )

    return NodeResult(
        state=state.model_copy(
            update={
                "calibration_block": block,
                "calibration_status": result.status.value,
            }
        ),
        status=NodeStatus.DEGRADED if wanted_but_missing else NodeStatus.OK,
        events=(
            DomainEvent(
                name="calibrate.searched",
                payload={
                    "status": result.status.value,
                    "k": len(result.matches),
                    "card_ids": [str(match.card.card_id) for match in result.matches],
                    "bands": list(result.bands),
                    "band_diversity": len(set(result.bands)),
                    "similarities": [round(match.similarity, 4) for match in result.matches],
                    "considered": result.considered,
                    "excluded_stale": result.excluded_stale,
                    "diversity_applied": result.diversity_applied,
                    "message": STATUS_MESSAGES[result.status],
                },
            ),
        ),
    )
