"""Why the system arrived at this band, in the reviewer's language.

The derivation is the product. A band with no derivation is an opinion, and a
derivation written in rule ids is an opinion with a serial number on it.

Nothing here computes anything. The rule engine already produced a list of steps
with a sentence attached to each; this arranges them on a page. If a number
needed working out at this layer, that would mean the rule engine had not
finished its job.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from app.components.status_chip import band_label
from app.vocabulary import STATE_LABELS
from domain.contracts.enums import Band, CriterionState

#: The icon beside each state. The label comes from the shared vocabulary.
STATE_ICONS: dict[CriterionState, str] = {
    CriterionState.MET: "✅",
    CriterionState.PARTIAL: "◐",
    CriterionState.NOT_MET: "✗",
    CriterionState.CONTRADICTED: "⚠️",
    CriterionState.INSUFFICIENT_EVIDENCE: "—",
}

#: How the headline reads. Deliberately not a colour scale: "Decline" in red and
#: "Advance" in green would make the band feel like a verdict rather than a
#: recommendation somebody is about to check.
BAND_TONE: dict[Band, str] = {
    Band.ADVANCE: "success",
    Band.ADVANCE_WITH_RESERVATIONS: "info",
    Band.HOLD: "info",
    Band.DECLINE: "info",
    Band.INSUFFICIENT_INFORMATION: "warning",
}


def render_headline(recommendation: Any) -> None:
    """The band, the score, and the one-line reason."""
    band = recommendation.band
    label = band_label(band)

    if band is Band.INSUFFICIENT_INFORMATION:
        st.warning(
            f"**{label}** — the documents did not cover enough of this role to score.",
            icon="⚠️",
        )
    else:
        st.markdown(f"### {label}")

    if recommendation.score is not None:
        st.progress(
            min(max(recommendation.score, 0.0), 1.0),
            text=f"Score {recommendation.score:.2f} of 1.00",
        )
    else:
        # Never a zero. A zero reads as "scored badly"; the honest statement is
        # that no score was produced.
        st.caption("No score was produced, because there was not enough to judge.")


def render_steps(recommendation: Any, *, debug: bool = False) -> None:
    """Every rule that fired, in the order it fired."""
    st.markdown("**How this was worked out**")

    for index, step in enumerate(recommendation.derivation, start=1):
        st.markdown(f"{index}. {step.description}")
        if debug:
            st.caption(f"`{step.rule_id}` inputs={step.inputs} output={step.output}")


def render_states(recommendation: Any, rubric: Any) -> None:
    """Each criterion, in rubric order, with what it resolved to."""
    st.markdown("**Point by point**")

    by_id = {item.id: item for item in rubric.criteria}
    for criterion_id, state in recommendation.criterion_states.items():
        criterion = by_id.get(criterion_id)
        label = STATE_LABELS.get(state, state.value)
        icon = STATE_ICONS.get(state, "•")
        name = criterion.label if criterion else criterion_id
        weight = f" · weight {criterion.weight}" if criterion else ""
        st.markdown(f"{icon} **{name}** — {label}{weight}")


def render_flags(recommendation: Any) -> None:
    """Why a person is being asked to look harder.

    Shown as sentences rather than codes, and separately from the band, because
    a flag never changes the band: it says look, not score lower.
    """
    if not recommendation.requires_human:
        return

    st.info("**This one wants a person's judgement.**", icon="⚠️")
    for reason in recommendation.requires_human_reasons:
        st.markdown(f"- {reason}")


def render(recommendation: Any, rubric: Any, *, debug: bool = False) -> None:
    render_headline(recommendation)
    render_flags(recommendation)
    render_steps(recommendation, debug=debug)
    with st.expander("Point by point", expanded=False):
        render_states(recommendation, rubric)
    if debug:
        st.json(recommendation.model_dump(mode="json"))
