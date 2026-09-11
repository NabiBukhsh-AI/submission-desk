"""What has been processed, and what is waiting on a person.

Read from the database on every render rather than from session state, so a
refresh loses nothing and two people looking at the queue see the same thing.

The default filter is "needs attention" rather than "everything". A recruiter
opening this page wants their work, not an audit log; the audit log is one click
away and correctly not the first thing.
"""

from __future__ import annotations

from dataclasses import replace

import streamlit as st

from app.components.status_chip import band_label, chip_for, needs_attention
from app.main import deps, state
from app.state import put
from app.vocabulary import EMPTY_MESSAGES
from app.vocabulary import QUEUE_FILTERS as FILTERS


def render() -> None:
    current = state()
    st.header("Queue")

    chosen = st.radio(
        "Show",
        options=list(FILTERS),
        index=list(FILTERS).index(current.queue_filter) if current.queue_filter in FILTERS else 0,
        horizontal=True,
    )
    if chosen != current.queue_filter:
        current = put(st.session_state, replace(current, queue_filter=chosen))

    runs = deps().runs.list_by_status(FILTERS[chosen], limit=200)

    if not runs:
        st.info(_empty_message(chosen), icon="📭")
        return

    st.caption(f"{len(runs)} candidate(s).")

    for run in runs:
        _row(run, debug=current.debug)


def _row(run, *, debug: bool) -> None:
    chip = chip_for(run.status)

    with st.container(border=True):
        name, status, band, action = st.columns([3, 2, 2, 1])

        with name:
            st.markdown(f"**{run.candidate_id}**")
            st.caption(f"{run.role_id} · started {run.started_at:%d %b %H:%M}")

        with status:
            st.markdown(f"{chip.icon} {chip.label}")
            if chip.help:
                st.caption(chip.help)

        with band:
            st.markdown(band_label(run.final_band))
            if run.override_count:
                st.caption(f"{run.override_count} correction(s) by a reviewer")

        with action:
            if needs_attention(run.status):
                st.page_link(
                    "pages/3_Review.py",
                    label="Open",
                    icon="🔍",
                )
                st.caption(str(run.run_id)[:8])

        if debug:
            st.json(run.model_dump(mode="json"))


def _empty_message(filter_name: str) -> str:
    return EMPTY_MESSAGES.get(filter_name, "Nothing here.")


render()
