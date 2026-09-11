"""Disagreeing with the system, in a form that teaches it something.

A reviewer changes what a criterion resolved to. They do not type a band: the
band is recomputed by the same rules the pipeline used, and the new one is shown
before they commit.

Both fields are required, and the reason code is the reason why. An override
with free text alone is a complaint; an override with a code is a routable
signal. ``EVIDENCE_MISSED`` points at a prompt, ``THRESHOLD_WRONG`` points at
the rubric, and ``CONTEXT_MODEL_LACKS`` points at a limitation worth writing
down rather than fixing.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from app.components.status_chip import band_label
from app.vocabulary import REASON_LABELS, STATE_LABELS
from domain.contracts.enums import CriterionState
from domain.contracts.review import Override

#: Short enough to type, long enough to be a reason. A single word is not one.
MIN_REASON_CHARS = 10


def render_editor(
    criterion: Any,
    current: CriterionState,
    *,
    key_prefix: str = "override",
) -> Override | None:
    """One criterion's override form, or None if nothing was changed.

    Returns a contract object rather than a dictionary, so an override that
    changes nothing or carries an empty reason is refused here rather than three
    layers down.
    """
    options = list(STATE_LABELS)

    new_state = st.selectbox(
        "What it should be",
        options=options,
        index=options.index(current) if current in options else 0,
        format_func=lambda state: STATE_LABELS[state],
        key=f"{key_prefix}-{criterion.id}-state",
    )

    if new_state is current:
        return None

    reason_code = st.selectbox(
        "Why",
        options=list(REASON_LABELS),
        format_func=lambda reason: REASON_LABELS[reason],
        key=f"{key_prefix}-{criterion.id}-reason",
    )
    reason_text = st.text_area(
        "What did the system get wrong?",
        key=f"{key_prefix}-{criterion.id}-text",
        placeholder="The CV describes this on page two, under Northwind Logistics.",
    )

    if len(reason_text.strip()) < MIN_REASON_CHARS:
        st.caption("A sentence is needed here before this change can be saved.")
        return None

    return Override(
        criterion_id=criterion.id,
        previous_state=current,
        new_state=new_state,
        reason_code=reason_code,
        reason_text=reason_text.strip(),
    )


def render_preview(before: Any, after: Any, changed: tuple[str, ...]) -> None:
    """What these changes do to the recommendation, before anything is saved.

    Computed by ``recompute_recommendation``, which calls the same
    ``aggregate()`` the pipeline used. The number previewed here is the number
    that gets stored, because it is produced by the same function.
    """
    if not changed:
        st.caption("Nothing has been changed yet.")
        return

    st.markdown(f"**{len(changed)} assessment(s) changed.**")

    left, right = st.columns(2)
    with left:
        st.caption("Now")
        st.markdown(f"**{band_label(before.band)}**")
        st.caption(_score_line(before))
    with right:
        st.caption("After your changes")
        st.markdown(f"**{band_label(after.band)}**")
        st.caption(_score_line(after))

    if after.band is before.band:
        st.info("The recommendation does not change.", icon="ℹ️")

    if after.requires_human and not before.requires_human:
        st.warning(
            "These changes mean this candidate now needs a person's judgement.",
            icon="⚠️",
        )


def _score_line(recommendation: Any) -> str:
    if recommendation.score is None:
        return "No score: not enough of the rubric was assessable."
    return f"Score {recommendation.score:.2f}"
