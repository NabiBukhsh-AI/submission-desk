"""The queue's vocabulary, in one place.

A status is an internal word. A chip is what a recruiter reads, and the two are
not the same: ``needs_review`` is a state name and "Needs a closer look" is a
sentence fragment somebody can act on.

Keeping the mapping here rather than in the page means the wording is reviewable
as a set, and that a status with no chip is a visible gap rather than a raw enum
value appearing in the interface.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.contracts.enums import Band, RunStatus


@dataclass(frozen=True)
class Chip:
    """How one status appears in the queue."""

    label: str
    icon: str
    #: Whether this run is waiting on a person. Drives the default filter, so a
    #: recruiter opening the queue sees their work rather than everything.
    needs_attention: bool = False
    help: str = ""


CHIPS: dict[RunStatus, Chip] = {
    RunStatus.CREATED: Chip("Starting", "⏳", help="The run has been created but not yet read."),
    RunStatus.INTAKE_OK: Chip("Reading files", "⏳"),
    RunStatus.EXTRACTED: Chip("Reading text", "⏳"),
    RunStatus.SANITIZED: Chip("Checking documents", "⏳"),
    RunStatus.STRUCTURED: Chip("Reading history", "⏳"),
    RunStatus.CALIBRATED: Chip("Preparing", "⏳"),
    RunStatus.ASSESSED: Chip("Assessing", "⏳"),
    RunStatus.AGGREGATED: Chip("Scoring", "⏳"),
    RunStatus.COMPOSED: Chip("Writing questions", "⏳"),
    RunStatus.READY_FOR_REVIEW: Chip(
        "Ready to review",
        "✅",
        needs_attention=True,
        help="Everything checked out. Open it when you have a moment.",
    ),
    RunStatus.NEEDS_REVIEW: Chip(
        "Needs a closer look",
        "⚠️",
        needs_attention=True,
        help="Something about this one wants a person's judgement. The reason is on the page.",
    ),
    RunStatus.QUARANTINED: Chip(
        "Documents flagged",
        "🛑",
        needs_attention=True,
        help="A document contained content aimed at an automated reader. Nothing was assessed.",
    ),
    RunStatus.MANUAL_REVIEW_REQUIRED: Chip(
        "Could not finish",
        "⚠️",
        needs_attention=True,
        help="A step failed. The candidate's documents are still here to read.",
    ),
    RunStatus.NEEDS_INFO: Chip(
        "Waiting on the candidate",
        "✉️",
        help="You asked for more information. Nothing to do until it arrives.",
    ),
    RunStatus.APPROVED: Chip("Approved", "👍"),
    RunStatus.REJECTED: Chip("Not proceeding", "👎"),
    RunStatus.DELIVERED: Chip("Sent", "📤"),
    RunStatus.DELIVERY_PENDING_RETRY: Chip(
        "Sending",
        "📤",
        help="Approved and prepared. The system is still trying to send it.",
    ),
    RunStatus.INTERRUPTED: Chip(
        "Interrupted",
        "⏸️",
        needs_attention=True,
        help="This run stopped part-way. It can be started again from where it left off.",
    ),
    RunStatus.FAILED_TERMINAL: Chip(
        "Could not be processed",
        "🛑",
        help="These files could not be read at all. The reason is on the run.",
    ),
}

#: What a band is called on a card. The enum values are readable but not
#: sentences, and a recruiter reads these dozens of times a day.
BAND_LABELS: dict[Band, str] = {
    Band.ADVANCE: "Advance",
    Band.ADVANCE_WITH_RESERVATIONS: "Advance, with reservations",
    Band.HOLD: "Hold",
    Band.DECLINE: "Decline",
    Band.INSUFFICIENT_INFORMATION: "Not enough information",
}


def chip_for(status: RunStatus) -> Chip:
    """How this status appears. Never a raw enum value."""
    return CHIPS.get(status, Chip(status.value.replace("_", " ").capitalize(), "•"))


def render(status: RunStatus) -> str:
    """One line for a table cell."""
    chip = chip_for(status)
    return f"{chip.icon} {chip.label}"


def band_label(band: Band | None) -> str:
    if band is None:
        return "—"
    return BAND_LABELS.get(band, band.value.replace("_", " ").capitalize())


def needs_attention(status: RunStatus) -> bool:
    return chip_for(status).needs_attention
