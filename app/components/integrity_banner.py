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

from domain.contracts.enums import IntegrityTier, Severity
from domain.security import banner_for

#: What each detector looks for, in a sentence. A reviewer sees the id in the
#: table and needs to know what it means without reading the source.
DETECTOR_LABELS: dict[str, str] = {
    "D-INSTR-IMPERATIVE": "An instruction addressed to whatever reads the file",
    "D-ROLE-TOKEN": "Chat formatting of the kind used to talk to a model",
    "D-HIDDEN-COLOUR": "Text the same colour as the page",
    "D-HIDDEN-SIZE": "Text too small for a person to read",
    "D-OFFPAGE": "Text outside the visible area of the page",
    "D-RENDER-DIFF": "Text in the file that does not appear when the page is printed",
    "D-ZERO-WIDTH": "Invisible characters inside words",
    "D-HOMOGLYPH": "Letters from another alphabet disguised as ordinary ones",
    "D-METADATA": "An instruction hidden in the document's properties",
    "D-REPETITION": "A phrase repeated far past the point of meaning it",
}

SEVERITY_LABELS: dict[Severity, str] = {
    Severity.HIGH: "Serious",
    Severity.MEDIUM: "Worth checking",
    Severity.LOW: "Minor",
}


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
