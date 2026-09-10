"""Building an anchor from a decided run.

Pure functions over contracts, so they live in the domain rather than beside the
index that uses them. Nothing here reads a file, calls a model, or touches a
database: a card is a deterministic rearrangement of a profile and a decision,
and that is exactly what makes the index reproducible.

The summary is built deterministically from the structured profile, not written
by a model. Two reasons. A generated summary would vary between runs, which
would make the index non-reproducible and every comparison against it
meaningless. And a generated summary is a place a name could reappear after
being removed.

So the fields are named explicitly: titles, years, technologies, education level.
Everything else is absent by construction rather than by filtering, which is the
same argument the notification allowlist makes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from domain.contracts.calibration import CalibrationCard
from domain.contracts.enums import Band, ReviewAction

#: The longest a summary may be. Long enough for a career, short enough that an
#: embedding is about the shape of it rather than about one paragraph.
MAX_SUMMARY_CHARS = 2000

#: How many roles from a history are described. Recent work is what a reviewer
#: is anchoring against; a job from 2009 is biography.
MAX_ROLES = 4

#: How many technologies. A list of thirty is keyword stuffing whoever wrote it.
MAX_TECHNOLOGIES = 12


def summary_of(profile: Any) -> str:
    """A career, in the fields that are allowed to be in one.

    Deterministic: the same profile produces the same string on every machine,
    forever. That is what makes the index reproducible and a comparison against
    it worth reading.
    """
    if profile is None:
        return "No structured history was available for this candidate."

    lines: list[str] = []

    employment = list(getattr(profile, "employment", []) or [])[:MAX_ROLES]
    for entry in employment:
        title = _value(getattr(entry, "title", None)) or "a role"
        years = _years_of(entry)
        lines.append(f"{title}{years}")

    technologies = [_value(item) for item in (getattr(profile, "technologies", []) or [])]
    technologies = [item for item in technologies if item][:MAX_TECHNOLOGIES]
    if technologies:
        lines.append("Technologies: " + ", ".join(technologies))

    education = _highest_education(profile)
    if education:
        lines.append(f"Education: {education}")

    if not lines:
        return "No structured history was available for this candidate."

    return "\n".join(lines)[:MAX_SUMMARY_CHARS]


def card_from(
    *,
    run_id: Any,
    role_id: str,
    rubric_version: str,
    profile: Any,
    recommendation: Any,
    decision: Any,
    embedding: bytes,
    redact: Any = None,
) -> CalibrationCard:
    """One anchor from one approved decision.

    Reviewer free text passes through the log redactor before it is stored. It
    is a person writing about another person, and the card outlives the run.
    """
    reason_text = _reviewer_text(decision)
    if reason_text and redact is not None:
        reason_text = redact(reason_text)

    return CalibrationCard(
        card_id=uuid4(),
        role_id=role_id,
        rubric_version=rubric_version,
        anonymized_summary=summary_of(profile),
        criterion_states=dict(getattr(recommendation, "criterion_states", {}) or {}),
        final_band=_band_of(recommendation, decision),
        reviewer_reason_codes=[
            override.reason_code for override in (getattr(decision, "overrides", []) or [])
        ],
        reviewer_reason_text_redacted=reason_text,
        decided_at=getattr(decision, "decided_at", None) or datetime.now(UTC),
        embedding=embedding,
        source_run_id=run_id,
    )


def should_create(decision: Any) -> bool:
    """Whether this decision earns a card.

    Only an approval. A rejection is a decision too, but a card built from one
    would teach the system what a recruiter declines, and the anchors exist to
    show what a good candidate for this role looked like.

    More importantly: nothing unreviewed ever becomes a card. A system learning
    from its own unchecked output is a system whose errors compound.
    """
    action = getattr(decision, "action", None)
    return action is ReviewAction.APPROVE


def _band_of(recommendation: Any, decision: Any) -> Band:
    """The band that was actually decided.

    A reviewer's corrected band wins. Anchoring on the band the model produced,
    when a person had changed it, would teach the index the error.
    """
    reviewed = getattr(decision, "post_override_band", None)
    if isinstance(reviewed, Band):
        return reviewed

    band = getattr(recommendation, "band", None)
    return band if isinstance(band, Band) else Band.HOLD


def _reviewer_text(decision: Any) -> str | None:
    parts = [
        override.reason_text
        for override in (getattr(decision, "overrides", []) or [])
        if getattr(override, "reason_text", None)
    ]
    comments = getattr(decision, "comments", None)
    if comments:
        parts.append(comments)
    return " ".join(parts)[:MAX_SUMMARY_CHARS] or None


def _value(field: Any) -> str:
    """A profile field's value, whatever shape it is in.

    Structured fields carry their own provenance, so the value is one level in.
    """
    if field is None:
        return ""
    inner = getattr(field, "value", field)
    return str(inner).strip() if inner else ""


def _years_of(entry: Any) -> str:
    """The dates, as the profile contract actually names them.

    ``start`` and ``end``, not ``start_date`` and ``end_date``. Reading the wrong
    names produced no error and no years, on every card, which is the kind of
    silence a summary builder is especially good at hiding.
    """
    start = _value(getattr(entry, "start", None))
    end = _value(getattr(entry, "end", None))
    if start and end:
        return f", {start} to {end}"
    if start:
        return f", from {start}"
    return ""


def _highest_education(profile: Any) -> str:
    """The qualification, not the institution.

    A university name is a proxy for a great many things that have nothing to do
    with whether somebody can do the job, and an anchor carrying one would teach
    the index to weight it.

    Education entries are provenanced strings on this contract, so the value is
    one level in rather than an attribute of an object.
    """
    entries = list(getattr(profile, "education", []) or [])
    levels = [_value(entry) for entry in entries]
    levels = [level for level in levels if level]
    return levels[0] if levels else ""
