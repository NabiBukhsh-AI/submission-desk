"""One candidate, and the decision.

The layout answers, in order, the questions a reviewer actually asks: can I
trust these documents, what does the system recommend, why, what did it read,
what did it throw away, and what should I ask next.

The rejected-evidence panel is prominent rather than tucked away. A quotation
the validator could not find is the measurable form of the failure everybody
worries about, and hiding it would be hiding the one number that proves the
system is honest about its own mistakes.

Eligibility sits collapsed and below the recommendation, because a work
authorisation question read first frames a person as a problem before anything
about their work has been read.
"""

from __future__ import annotations

from dataclasses import replace

import streamlit as st

from app.components import derivation_panel, evidence_card, integrity_banner, source_viewer
from app.components.override_dialog import render_editor, render_preview
from app.main import deps, state
from app.state import ReviewSession, put
from application.use_cases.recompute_recommendation import recompute
from application.use_cases.submit_review import ReviewRejected, submit_review
from domain.contracts.enums import ReviewAction, RunStatus


def render() -> None:
    current = state()
    wired = deps()

    st.header("Review")

    run = _chosen_run(wired, current)
    if run is None:
        return

    if current.review.run_id != run.run_id:
        # Opening a candidate starts the clock. It has to live in session state
        # because Streamlit reruns the script on every click, and a timer in a
        # local variable would reset each time the reviewer typed a character.
        current = put(
            st.session_state,
            replace(current, review=current.review.opened(run.run_id, run.version)),
        )

    if wired.rubric_loader is None:
        st.error("No rubrics are configured, so this candidate cannot be shown.", icon="🛑")
        return
    rubric = wired.rubric_loader(run.role_id)
    recommendation = wired.evidence.recommendation_for_run(run.run_id)
    assessments = wired.evidence.assessments_for_run(run.run_id)
    documents = wired.candidates.documents_for_run(run.run_id)
    reports = wired.candidates.integrity_reports_for_run(run.run_id)

    st.subheader(run.candidate_id)
    st.caption(f"{rubric.role_title} · run {str(run.run_id)[:8]}")

    integrity_banner.render(run.integrity_tier, reports, debug=current.debug)

    if recommendation is None:
        st.warning(
            "No recommendation was produced for this candidate. The documents are "
            "below; you can still read them and decide.",
            icon="⚠️",
        )
    else:
        derivation_panel.render(recommendation, rubric, debug=current.debug)

    if rubric.notes_for_reviewer:
        st.info(rubric.notes_for_reviewer, icon="ℹ️")

    _evidence_section(wired, assessments, documents, rubric, debug=current.debug)
    _rejected_section(assessments, debug=current.debug)
    _questions_section(run, wired)
    _eligibility_section(assessments, rubric)
    _decision_section(run, wired, rubric, assessments, recommendation)


def _chosen_run(wired, current):
    """Which candidate is open.

    A picker rather than a URL parameter, because Streamlit's multipage links do
    not carry state and a reviewer arriving from the queue needs to land
    somewhere useful either way.
    """
    waiting = wired.runs.list_by_status(
        (
            RunStatus.READY_FOR_REVIEW,
            RunStatus.NEEDS_REVIEW,
            RunStatus.QUARANTINED,
            RunStatus.MANUAL_REVIEW_REQUIRED,
        ),
        limit=100,
    )
    if not waiting:
        st.info("Nothing is waiting on you.", icon="📭")
        return None

    labels = {f"{run.candidate_id} · {str(run.run_id)[:8]}": run for run in waiting}
    chosen = st.selectbox("Candidate", options=list(labels))
    return labels[chosen]


def _evidence_section(wired, assessments, documents, rubric, *, debug: bool) -> None:
    st.subheader("What the documents say")

    by_id = {item.id: item for item in rubric.criteria}
    for assessment in assessments:
        criterion = by_id.get(assessment.criterion_id)
        title = criterion.label if criterion else assessment.criterion_id

        with st.expander(title, expanded=False):
            if criterion:
                st.caption(criterion.question)

            for item in assessment.evidence:
                evidence_card.render(item, debug=debug)
                if item.verbatim_span:
                    with st.expander("Show me where", expanded=False):
                        source_viewer.render(item, wired, documents, debug=debug)

            if not assessment.evidence:
                st.caption("Nothing in the documents addressed this point.")


def _rejected_section(assessments, *, debug: bool) -> None:
    """What was thrown away, and why.

    Prominent on purpose. This panel is the hallucination rate made visible: if
    it is empty the system found nothing wrong with itself, and if it is not, a
    reviewer can see exactly what it caught.
    """
    rejected = [item for assessment in assessments for item in assessment.rejected_evidence]

    st.subheader(f"Excluded from the score ({len(rejected)})")
    st.caption(
        "Quotations the system could not find in the source document. These were "
        "removed before anything was scored, and are shown so you can see what was "
        "caught."
    )
    evidence_card.render_group(rejected, rejected=True, debug=debug)


def _questions_section(run, wired) -> None:
    st.subheader("What to ask next")
    st.caption(
        "Derived from the points the documents did not settle. Nothing here was "
        "invented: each one corresponds to a criterion that was left open."
    )
    st.info(
        "Suggested questions are prepared when the candidate is processed and are "
        "sent only if you approve.",
        icon="✉️",
    )


def _eligibility_section(assessments, rubric) -> None:
    """Work authorisation, collapsed and below everything else.

    The position is the decision. A eligibility question read first frames a
    person as a problem before anything about their work has been read, and the
    rubric treats it as a blocker precisely so it does not need to be read
    first.
    """
    blockers = [item.id for item in rubric.criteria if item.kind.value == "blocker"]
    if not blockers:
        return

    with st.expander("Eligibility", expanded=False):
        for assessment in assessments:
            if assessment.criterion_id in blockers:
                st.markdown(f"**{assessment.criterion_id}** — {assessment.resolved_state.value}")
                for item in assessment.evidence:
                    if item.verbatim_span:
                        st.markdown("> " + item.verbatim_span)


def _decision_section(run, wired, rubric, assessments, recommendation) -> None:
    current = state()
    st.divider()
    st.subheader("Your decision")

    st.caption(f"You have had this open for {current.review.elapsed_seconds} seconds.")

    overrides = []
    with st.expander("Correct an assessment", expanded=False):
        st.caption(
            "Change what a point resolved to. The recommendation is recalculated "
            "by the same rules the system used; you never type a band."
        )
        by_id = {item.criterion_id: item for item in assessments}
        for criterion in rubric.criteria:
            assessment = by_id.get(criterion.id)
            if assessment is None:
                continue
            st.markdown(f"**{criterion.label}**")
            override = render_editor(criterion, assessment.resolved_state)
            if override is not None:
                overrides.append(override)

    if overrides and recommendation is not None:
        result = recompute(run.run_id, wired, overrides)
        render_preview(recommendation, result.recommendation, result.changed)

    trust = st.slider(
        "How much did you trust this assessment?",
        min_value=1,
        max_value=5,
        value=3,
        help="One is not at all, five is completely. Recorded so the system can be measured.",
    )
    comments = st.text_area("Anything to note?", placeholder="Optional.")

    approve, request, reject = st.columns(3)
    with approve:
        if st.button("Approve", type="primary", use_container_width=True):
            _submit(run, ReviewAction.APPROVE, overrides, trust, comments)
    with request:
        if st.button("Ask for more information", use_container_width=True):
            _submit(run, ReviewAction.REQUEST_INFO, overrides, trust, comments)
    with reject:
        if st.button("Do not proceed", use_container_width=True):
            _submit(run, ReviewAction.REJECT, overrides, trust, comments)


def _submit(run, action: ReviewAction, overrides, trust: int, comments: str) -> None:
    current = state()
    try:
        result = submit_review(
            run.run_id,
            action,
            deps(),
            overrides=overrides,
            comments=comments or None,
            elapsed_seconds=current.review.elapsed_seconds,
            trust_rating=trust,
            expected_version=current.review.run_version,
        )
    except ReviewRejected as refused:
        st.error(refused.message, icon="🛑")
        return

    put(
        st.session_state,
        replace(current, review=ReviewSession()).with_notice(result.message),
    )
    st.rerun()


render()
