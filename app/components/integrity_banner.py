"""What a reviewer is told about the documents before anything else.

Never suppressed, and never softened. A quarantined document gets a red banner
with the flagged text quoted in full, because a reviewer deciding whether to
override a halt needs to read exactly what was in the file — including the part
that was aimed at the machine.

The wording comes from ``domain.security`` rather than from here, so a second
interface cannot invent a gentler version of the red one.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from app.vocabulary import DETECTOR_LABELS, SEVERITY_LABELS
from domain.contracts.enums import IntegrityTier
from domain.security import banner_for


def render(tier: IntegrityTier, reports: list[Any], *, debug: bool = False) -> None:
    """The banner, and the findings behind it."""
    message = banner_for(tier)

    if tier is IntegrityTier.QUARANTINE:
        st.error(message, icon="🛑")
    elif tier is IntegrityTier.SUSPECT:
        st.warning(message, icon="⚠️")
    else:
        st.success(message, icon="✅")
        return

    findings = [finding for report in reports for finding in report.findings]
    if not findings:
        return

    st.markdown("**What was found**")
    for finding in findings:
        with st.container(border=True):
            st.markdown(
                f"**{SEVERITY_LABELS.get(finding.severity, finding.severity.value)}** — "
                f"{DETECTOR_LABELS.get(finding.detector, finding.detector)}"
            )
            # Quoted in full rather than summarised. Deciding whether this was
            # an attack or an accident means reading the words.
            st.code(finding.excerpt, language=None)
            st.caption(f"Detector {finding.detector}")

    if debug:
        st.json([report.model_dump(mode="json") for report in reports])


def coverage_note(reports: list[Any]) -> str:
    """Whether every check actually ran.

    "We found nothing" and "we could not look" are different statements, and a
    reviewer reading a clean result is entitled to know which one it is.
    """
    if not reports:
        return "These documents have not been checked."
    return "Every document was checked."
