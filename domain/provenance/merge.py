"""Putting a chunked profile back together, without deciding anything.

A long CV is read in page groups, and the pieces have to become one profile.
The merge is deterministic Python and never a model call, because reconciling
two partial profiles means choosing between two dates, and a model choosing
would be a judgment nobody could check.

The rule that matters: a conflict is recorded, never resolved. If one chunk says
a role ended in 2021 and another says 2019, the profile keeps both and says so.
Quietly picking one would invent a fact, and the reviewer would have no way to
know a choice had been made on their behalf.

Pure. No I/O, no clock, no randomness.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from domain.contracts.profile import CandidateProfile, EmploymentEntry, ProvenancedField


def merge_profiles(profiles: Sequence[CandidateProfile]) -> CandidateProfile:
    """Combine profiles extracted from separate chunks of one document.

    Employment is keyed on the normalised employer and title, so the same role
    read from two overlapping page groups becomes one entry rather than two.
    Everything else is unioned, keeping first-seen order.
    """
    if not profiles:
        raise ValueError("nothing to merge")
    if len(profiles) == 1:
        return profiles[0]

    candidate_id = profiles[0].candidate_id
    employment = _merge_employment([entry for profile in profiles for entry in profile.employment])

    return CandidateProfile(
        candidate_id=candidate_id,
        employment=employment,
        education=_union([field for profile in profiles for field in profile.education]),
        technologies=_union([field for profile in profiles for field in profile.technologies]),
        languages=_union([field for profile in profiles for field in profile.languages]),
        artefact_links=_union([field for profile in profiles for field in profile.artefact_links]),
        total_years_claimed=_first_stated([profile.total_years_claimed for profile in profiles]),
        partial=any(profile.partial for profile in profiles),
        unparsed_reason=next(
            (profile.unparsed_reason for profile in profiles if profile.unparsed_reason), None
        ),
    )


def _merge_employment(entries: Sequence[EmploymentEntry]) -> list[EmploymentEntry]:
    """One entry per role, with disagreements attached rather than settled."""
    merged: dict[tuple[str, str], EmploymentEntry] = {}

    for entry in entries:
        key = (_normalise(entry.employer.value), _normalise(entry.title.value))
        existing = merged.get(key)

        if existing is None:
            merged[key] = entry
            continue

        merged[key] = EmploymentEntry(
            employer=_merge_field(existing.employer, entry.employer, "employer"),
            title=_merge_field(existing.title, entry.title, "title"),
            start=_merge_field(existing.start, entry.start, "start date"),
            end=_merge_field(existing.end, entry.end, "end date"),
            summary=_merge_field(existing.summary, entry.summary, "summary"),
        )

    return list(merged.values())


def _merge_field(
    first: ProvenancedField[Any], second: ProvenancedField[Any], label: str
) -> ProvenancedField[Any]:
    """Combine two readings of one field.

    Where they agree, the provenance from both is kept, so a reviewer can see
    the claim was supported in two places. Where they disagree, the first
    reading stands as the value and the disagreement is recorded, because a
    profile with no value at all is less useful to a reviewer than one that says
    "this, and note that the document also says that".
    """
    if first.value is None:
        return second
    if second.value is None:
        return first

    provenance = [*first.provenance, *second.provenance]

    if _normalise(first.value) == _normalise(second.value):
        return ProvenancedField(
            value=first.value,
            provenance=provenance,
            confidence=_lower_of(first.confidence, second.confidence),
            conflicts=[*first.conflicts, *second.conflicts],
        )

    note = f"{label}: one part of the document says {first.value!r}, another says {second.value!r}"
    return ProvenancedField(
        value=first.value,
        provenance=provenance,
        # A disagreement is not more certain than either reading of it, and it
        # is less certain than the more doubtful of the two.
        confidence=_lower_of(first.confidence, second.confidence),
        conflicts=[*first.conflicts, *second.conflicts, note],
    )


def _union(fields: Sequence[ProvenancedField[Any]]) -> list[ProvenancedField[Any]]:
    """Distinct values, in the order first seen.

    Order is first-seen rather than sorted because the order things appear in a
    CV carries meaning: the first technology listed is usually the one the
    candidate leads with.
    """
    seen: dict[str, ProvenancedField[Any]] = {}
    for field in fields:
        key = _normalise(field.value)
        if not key:
            continue
        if key not in seen:
            seen[key] = field
            continue
        existing = seen[key]
        seen[key] = ProvenancedField(
            value=existing.value,
            provenance=[*existing.provenance, *field.provenance],
            confidence=_lower_of(existing.confidence, field.confidence),
            conflicts=[*existing.conflicts, *field.conflicts],
        )
    return list(seen.values())


def _first_stated(fields: Sequence[ProvenancedField[Any] | None]) -> ProvenancedField[Any] | None:
    for field in fields:
        if field is not None and field.value is not None:
            return field
    return None


def _lower_of(first: float | None, second: float | None) -> float | None:
    present = [value for value in (first, second) if value is not None]
    return min(present) if present else None


def _normalise(value: Any) -> str:
    """A key for matching two readings of the same thing.

    Case and punctuation vary between page groups; "Acme Payments," and "acme
    payments" are the same employer. Anything more aggressive would merge two
    genuinely different roles at one company.
    """
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()
