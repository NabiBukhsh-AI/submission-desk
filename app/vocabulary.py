"""The interface's words, in one place, for every interface.

The queue chips live in ``status_chip``; these are the rest. Kept out of the
Streamlit components so a second interface — the HTTP API and whatever renders
it — reads the same sentences instead of inventing gentler ones. The wording is
reviewable as a set here, and a state with no label is a visible gap rather
than a raw enum value on somebody's screen.
"""

from __future__ import annotations

from app.components.status_chip import needs_attention
from domain.contracts.enums import (
    Band,
    CriterionKind,
    CriterionState,
    OverrideReason,
    RunStatus,
    Severity,
    SpanValidation,
)

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


# --- definitions ---------------------------------------------------------------------
#
# Everything the review page names, defined in a sentence a recruiter can read.
# A code with no entry here appears as itself on the page, which is the gap
# showing rather than hiding.

#: What each band means, and what it is not.
BAND_HELP: dict[Band, str] = {
    Band.ADVANCE: (
        "The documents show enough of the role, and the evidence found scores at or above "
        "the top cutoff for this role. Read the quotations before agreeing."
    ),
    Band.ADVANCE_WITH_RESERVATIONS: (
        "Enough of the role was shown and the score clears the second cutoff. Something "
        "is worth asking about: look at the points marked partly met or not met."
    ),
    Band.HOLD: (
        "Enough of the role was shown, and the score lands in the middle band. Neither a "
        "yes nor a no from the evidence alone; worth a second opinion or a conversation."
    ),
    Band.DECLINE: (
        "Enough of the role was shown, and the evidence scores below the lowest cutoff, "
        "or a requirement marked as a blocker was not met."
    ),
    Band.INSUFFICIENT_INFORMATION: (
        "No band. The documents address too little of the role - less than the coverage "
        "gate - for a score to mean anything. It is not a low score; ask for more material."
    ),
    Band.MANUAL_REVIEW_REQUIRED: (
        "No band yet. A requirement marked as a blocker is not answered by the documents "
        "either way, so a person has to check with the candidate before any decision. "
        "The evidence for everything else is scored below as if the blocker were met."
    ),
}

#: Why a run is routed to a person: the code the rules record, a title, what it
#: means, and what to do about it. None of these change the band.
HUMAN_REASONS: dict[str, dict[str, str]] = {
    "coverage_below_gate": {
        "title": "The documents address too little of the role to score",
        "meaning": (
            "Coverage is the share of the rubric, by weight, that the documents say "
            "anything about. Below this role's gate, no score is reported: a candidate "
            "is not marked down for what a one-page CV never mentioned."
        ),
        "action": (
            "Read the points marked 'not addressed'. If the person may have the "
            "experience, send the questions prepared below and process again when "
            "they answer. If the gate is wrong for this role, it is a setting on the "
            "Roles page."
        ),
    },
    "blocker_unknown": {
        "title": "A blocker requirement is not answered either way",
        "meaning": (
            "A blocker is a requirement that decides the outcome on its own - work "
            "authorisation, for example. The documents neither meet it nor fail it, so "
            "the system will not decline on silence and will not advance without an "
            "answer."
        ),
        "action": (
            "Ask the candidate the prepared question for that requirement. Once it is "
            "answered, correct the point below with the reason 'the CV says this "
            "somewhere the system did not find', or decline if the answer is no."
        ),
    },
    "contradicted_criterion": {
        "title": "The documents disagree with each other on a point",
        "meaning": (
            "Two quotations for the same requirement say opposite things - a CV and a "
            "cover letter, or two lines of one document. The system does not pick a side."
        ),
        "action": "Read both quotations and decide which stands; correct the point below.",
    },
    "integrity_not_clean": {
        "title": "Something in the documents was aimed at an automated reader",
        "meaning": (
            "The scanners found text that reads as an instruction to a screening "
            "system, or hidden text. It is quoted in the banner at the top. Nothing "
            "here changes the score; it changes how much the document can be trusted."
        ),
        "action": "Read the quoted passage and decide whether it was an accident or an attempt.",
    },
    "invalid_span_present": {
        "title": "A quotation the model offered was not in the document",
        "meaning": (
            "Every quotation is checked against the source text. At least one was not "
            "found and was removed before scoring. It is listed under 'Excluded from the "
            "score' so you can see what was caught."
        ),
        "action": (
            "Look at the excluded quotations. If the model missed something real, "
            "correct the point with the reason 'the CV says this somewhere the system "
            "did not find'."
        ),
    },
    "partial_profile": {
        "title": "The structured profile could not be fully read",
        "meaning": (
            "The employment and education summary was not completed from the "
            "documents. The quotations below are unaffected; only the summary is "
            "missing fields rather than guessing them."
        ),
        "action": "Read the documents directly for the summary; the assessment itself is complete.",
    },
    "budget_capped": {
        "title": "The run stopped at the token ceiling",
        "meaning": (
            "Each run has a spending ceiling. This one reached it before every point "
            "was assessed, so some points are marked 'not addressed' for that reason "
            "rather than because the documents are silent."
        ),
        "action": (
            "Check the settings for the ceiling, or process the candidate again with "
            "fewer documents."
        ),
    },
    "criterion_unassessed": {
        "title": "A point could not be assessed",
        "meaning": (
            "The model's answer for at least one requirement failed the contract twice "
            "(the first answer and one repair), or the provider was unavailable, so the "
            "point is recorded as not assessed rather than filled in."
        ),
        "action": "Read the document for that point yourself and correct it below if needed.",
    },
}

#: What each criterion state means for the score.
STATE_HELP: dict[CriterionState, str] = {
    CriterionState.MET: (
        "Enough verified quotations support it - at least the number the rubric asks "
        "for. Worth its full points."
    ),
    CriterionState.PARTIAL: (
        "Some verified support, but fewer quotations than the rubric asks for, or "
        "support alongside a doubt. Worth half points by default."
    ),
    CriterionState.NOT_MET: "The documents address it and the evidence says no. Worth no points.",
    CriterionState.CONTRADICTED: (
        "Verified quotations say opposite things. Worth no points, and routed to a person."
    ),
    CriterionState.INSUFFICIENT_EVIDENCE: (
        "The documents do not address it. Left out of the score entirely - neither "
        "credit nor penalty - and left out of coverage."
    ),
}

#: What each kind of requirement does to the outcome.
KIND_HELP: dict[CriterionKind, str] = {
    CriterionKind.STANDARD: "Counts toward the score by its weight.",
    CriterionKind.HIGH_STAKES: (
        "Counts by its weight, needs two verified quotations to be met, and is checked "
        "by the stronger model tier when the first answer is uncertain."
    ),
    CriterionKind.BLOCKER: (
        "Decides the outcome on its own. Not met means Decline whatever the score; not "
        "addressed means a person checks before any decision. Never decides on silence."
    ),
}

#: What the mechanical check of a quotation concluded.
SPAN_VALIDATION_HELP: dict[SpanValidation, str] = {
    SpanValidation.VALID_EXACT: "Found in the document at the position the model gave.",
    SpanValidation.VALID_NORMALIZED: (
        "Found in the document once spacing, dashes and quotes were normalised; the "
        "position the model gave was corrected."
    ),
    SpanValidation.VALID_FUZZY_OCR: (
        "Found on a scanned page within the tolerance for reading errors (the page was "
        "read by OCR)."
    ),
    SpanValidation.INVALID_NOT_FOUND: (
        "Not in the document. Removed from the score and shown in the excluded panel."
    ),
    SpanValidation.INVALID_OFFSET_MISMATCH: (
        "The text appears more than once and none of the occurrences is near where the "
        "model said; refused rather than guessed."
    ),
    SpanValidation.INVALID_WRONG_DOCUMENT: (
        "Quoted from a document that is not this candidate's. Removed."
    ),
    SpanValidation.NOT_APPLICABLE: "No quotation to check: the point was marked not addressed.",
}

#: The rest of the words on the page.
GLOSSARY: list[dict[str, str]] = [
    {
        "term": "Coverage",
        "definition": (
            "The share of the rubric, by weight, that the documents address - every "
            "requirement that resolved to anything other than 'not addressed'. A role "
            "sets a coverage gate; below it no score is reported."
        ),
    },
    {
        "term": "Score",
        "definition": (
            "A weighted average over the requirements that could be assessed: each "
            "requirement's points (1 for met, 0.5 for partly met, 0 for not met or "
            "contradicted) times its weight, divided by the weight of the assessed "
            "requirements. Requirements the documents never mention are excluded, not "
            "counted as zero. The bands are cutoffs on this score."
        ),
    },
    {
        "term": "Weight",
        "definition": (
            "How much a requirement counts in the score and in coverage, 1 to 10, set "
            "in the rubric."
        ),
    },
    {
        "term": "Quotations needed",
        "definition": (
            "How many separate verified quotations a requirement needs before it counts "
            "as met; fewer than that is partly met. Two for high-stakes requirements by "
            "default."
        ),
    },
    {
        "term": "Verified quotation",
        "definition": (
            "Text the model copied from the document that the system then found in the "
            "document. Only verified quotations count. Unverified ones are shown "
            "separately and never scored."
        ),
    },
    {
        "term": "Derivation",
        "definition": (
            "The rules that fired, in order, with their inputs and outputs. Every band "
            "can be traced through it by hand; nothing in it came from a model."
        ),
    },
    {
        "term": "Escalation",
        "definition": (
            "A second look at one requirement by the stronger model tier, taken when "
            "the first answer was uncertain or a quotation failed. At most a few per "
            "candidate."
        ),
    },
    {
        "term": "Repair",
        "definition": (
            "One retry when the model's answer did not fit the contract, showing it the "
            "error and the document again. A second failure marks the point not assessed."
        ),
    },
    {
        "term": "Blind mode",
        "definition": (
            "Names, pronouns and contact details are removed before the model reads the "
            "documents. The quotations you see are from the original."
        ),
    },
    {
        "term": "Integrity",
        "definition": (
            "What the scanners found before any model call. Clean: nothing. Suspect: "
            "something worth a look, assessed anyway. Quarantined: an instruction aimed "
            "at the screener; nothing was assessed and no tokens were spent."
        ),
    },
    {
        "term": "Cost and tokens",
        "definition": (
            "What the provider reported for every call on this run, priced at the rates "
            "on the Settings page. Never estimated; 'not configured' means no rates were "
            "entered."
        ),
    },
]
