"""The interface's words, in one place, for every interface.

The queue chips live in ``status_chip``; these are the rest. Kept out of the
Streamlit components so a second interface — the HTTP API and whatever renders
it — reads the same sentences instead of inventing gentler ones. The wording is
reviewable as a set here, and a state with no label is a visible gap rather
than a raw enum value on somebody's screen.
"""

from __future__ import annotations

from app.components.status_chip import needs_attention
from domain.contracts.enums import CriterionState, OverrideReason, RunStatus, Severity

#: What each criterion state is called.
STATE_LABELS: dict[CriterionState, str] = {
    CriterionState.MET: "Met",
    CriterionState.PARTIAL: "Partly met",
    CriterionState.NOT_MET: "Not met",
    CriterionState.CONTRADICTED: "The documents disagree",
    CriterionState.INSUFFICIENT_EVIDENCE: "Not addressed",
}

#: What each override reason means, in the reviewer's terms. The code routes
#: the improvement work; the sentence is what makes the code get chosen right.
REASON_LABELS: dict[OverrideReason, str] = {
    OverrideReason.EVIDENCE_MISREAD: "The quotation is there, but it does not mean that",
    OverrideReason.EVIDENCE_MISSED: "The CV says this somewhere the system did not find",
    OverrideReason.SPAN_WRONG: "The quotation does not match what the document says",
    OverrideReason.RUBRIC_WRONG: "The rubric asks the wrong question for this role",
    OverrideReason.THRESHOLD_WRONG: "The bar for this point is set too high or too low",
    OverrideReason.CRITERION_AMBIGUOUS: "The criterion could be read more than one way",
    OverrideReason.CONTEXT_MODEL_LACKS: "This needs knowledge the system does not have",
    OverrideReason.OTHER: "Something else",
}

#: What each detector looks for, in a sentence.
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

#: The queue filters, named by what a recruiter is looking for rather than by
#: status, because "READY_FOR_REVIEW or NEEDS_REVIEW or QUARANTINED" is not a
#: thing anybody is looking for.
QUEUE_FILTERS: dict[str, tuple[RunStatus, ...]] = {
    "Needs attention": tuple(status for status in RunStatus if needs_attention(status)),
    "Waiting on a candidate": (RunStatus.NEEDS_INFO,),
    "Decided": (
        RunStatus.APPROVED,
        RunStatus.REJECTED,
        RunStatus.DELIVERED,
        RunStatus.DELIVERY_PENDING_RETRY,
    ),
    "In progress": (
        RunStatus.CREATED,
        RunStatus.INTAKE_OK,
        RunStatus.EXTRACTED,
        RunStatus.SANITIZED,
        RunStatus.STRUCTURED,
        RunStatus.CALIBRATED,
        RunStatus.ASSESSED,
        RunStatus.AGGREGATED,
        RunStatus.COMPOSED,
    ),
    "Everything": tuple(RunStatus),
}

#: An empty list means different things under different filters.
EMPTY_MESSAGES: dict[str, str] = {
    "Needs attention": "Nothing is waiting on you.",
    "Waiting on a candidate": "Nobody has been asked for more information.",
    "Decided": "No decisions have been made yet.",
    "In progress": "Nothing is being processed right now.",
    "Everything": "No candidates have been uploaded yet.",
}
