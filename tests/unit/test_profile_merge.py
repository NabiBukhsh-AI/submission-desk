"""Putting a chunked profile back together without deciding anything.

The rule under test throughout: a conflict is recorded, never resolved. If one
page group says a role ended in 2021 and another says 2019, both survive and the
reviewer is told. Quietly picking one would invent a fact, and nobody would know
a choice had been made on their behalf.
"""

from __future__ import annotations

from typing import Any

import pytest

from domain.contracts.profile import CandidateProfile, EmploymentEntry, ProvenancedField
from domain.provenance.merge import merge_profiles


def field(value: Any, *, confidence: float | None = None) -> ProvenancedField[Any]:
    return ProvenancedField(value=value, confidence=confidence)


def entry(employer: str, title: str, start: str = "2021", end: str = "present") -> EmploymentEntry:
    return EmploymentEntry(
        employer=field(employer),
        title=field(title),
        start=field(start),
        end=field(end),
        summary=field("did the work"),
    )


def profile(**overrides: Any) -> CandidateProfile:
    fields: dict[str, Any] = {"candidate_id": "cand-0007"}
    fields.update(overrides)
    return CandidateProfile(**fields)


# --- the conflict rule -------------------------------------------------------------


def test_a_date_conflict_is_recorded_not_resolved() -> None:
    """The headline case, and the reason the merge is code rather than a model."""
    first = profile(employment=[entry("Acme Payments", "Lead Engineer", end="2021")])
    second = profile(employment=[entry("Acme Payments", "Lead Engineer", end="2019")])

    merged = merge_profiles([first, second])

    conflicts = merged.employment[0].end.conflicts
    assert len(conflicts) == 1
    assert "2021" in conflicts[0] and "2019" in conflicts[0]


def test_a_conflict_names_the_field_it_is_about() -> None:
    """A reviewer reads this. "one part says X, another says Y" without saying
    what X and Y are about is a note nobody can act on."""
    first = profile(employment=[entry("Acme", "Lead Engineer")])
    second = profile(employment=[entry("Acme", "Lead Engineer", start="2018")])

    merged = merge_profiles([first, second])

    assert "start date" in merged.employment[0].start.conflicts[0]


def test_a_conflicted_field_keeps_a_value() -> None:
    """A profile that empties a field on disagreement is less useful than one
    that says "this, and note the document also says that"."""
    first = profile(employment=[entry("Acme", "Lead", end="2021")])
    second = profile(employment=[entry("Acme", "Lead", end="2019")])

    merged = merge_profiles([first, second])

    assert merged.employment[0].end.value == "2021"


def test_a_disagreement_is_no_more_certain_than_its_less_certain_half() -> None:
    first = profile(
        employment=[
            EmploymentEntry(
                employer=field("Acme"),
                title=field("Lead"),
                start=field("2021", confidence=0.9),
                end=field("2021", confidence=0.9),
                summary=field("x"),
            )
        ]
    )
    second = profile(
        employment=[
            EmploymentEntry(
                employer=field("Acme"),
                title=field("Lead"),
                start=field("2018", confidence=0.4),
                end=field("2021", confidence=0.9),
                summary=field("x"),
            )
        ]
    )

    merged = merge_profiles([first, second])

    assert merged.employment[0].start.confidence == 0.4


def test_agreement_keeps_the_provenance_from_both_readings() -> None:
    """So a reviewer can see the claim was supported in two places."""
    from tests.builders import provenance

    first = profile(
        employment=[
            EmploymentEntry(
                employer=ProvenancedField(value="Acme", provenance=[provenance()]),
                title=field("Lead"),
                start=field("2021"),
                end=field("present"),
                summary=field("x"),
            )
        ]
    )
    second = profile(
        employment=[
            EmploymentEntry(
                employer=ProvenancedField(value="Acme", provenance=[provenance()]),
                title=field("Lead"),
                start=field("2021"),
                end=field("present"),
                summary=field("x"),
            )
        ]
    )

    merged = merge_profiles([first, second])

    assert len(merged.employment[0].employer.provenance) == 2
    assert merged.employment[0].employer.conflicts == []


# --- matching the same role across chunks -------------------------------------------


def test_the_same_role_read_twice_becomes_one_entry() -> None:
    """Page groups overlap in practice, and two entries for one job would look
    to a reviewer like a candidate who held the role twice."""
    first = profile(employment=[entry("Acme Payments", "Lead Engineer")])
    second = profile(employment=[entry("Acme Payments", "Lead Engineer")])

    merged = merge_profiles([first, second])

    assert len(merged.employment) == 1


def test_matching_ignores_case_and_punctuation() -> None:
    """ "Acme Payments," and "acme payments" are the same employer."""
    first = profile(employment=[entry("Acme Payments,", "Lead Engineer")])
    second = profile(employment=[entry("acme payments", "lead engineer")])

    merged = merge_profiles([first, second])

    assert len(merged.employment) == 1


def test_two_roles_at_one_employer_stay_apart() -> None:
    """Promotions are common, and merging them would erase a career step."""
    first = profile(employment=[entry("Acme", "Engineer", start="2018", end="2021")])
    second = profile(employment=[entry("Acme", "Lead Engineer", start="2021")])

    merged = merge_profiles([first, second])

    assert len(merged.employment) == 2


def test_different_employers_stay_apart() -> None:
    first = profile(employment=[entry("Acme", "Engineer")])
    second = profile(employment=[entry("Northwind", "Engineer")])

    merged = merge_profiles([first, second])

    assert len(merged.employment) == 2


# --- nulls and unions ----------------------------------------------------------------


def test_a_null_field_yields_to_a_stated_one() -> None:
    """One page group not mentioning a date is not disagreement about it."""
    first = profile(
        employment=[
            EmploymentEntry(
                employer=field("Acme"),
                title=field("Lead"),
                start=field(None),
                end=field("present"),
                summary=field("x"),
            )
        ]
    )
    second = profile(employment=[entry("Acme", "Lead", start="2021")])

    merged = merge_profiles([first, second])

    assert merged.employment[0].start.value == "2021"
    assert merged.employment[0].start.conflicts == []


def test_lists_are_unioned_without_duplicates() -> None:
    first = profile(technologies=[field("Python"), field("SQLite")])
    second = profile(technologies=[field("python"), field("Kubernetes")])

    merged = merge_profiles([first, second])

    assert [item.value for item in merged.technologies] == ["Python", "SQLite", "Kubernetes"]


def test_list_order_is_first_seen_not_sorted() -> None:
    """The order things appear in a CV carries meaning: the first technology
    listed is usually the one the candidate leads with."""
    first = profile(technologies=[field("Zig"), field("Ada")])

    merged = merge_profiles([first, profile()])

    assert [item.value for item in merged.technologies] == ["Zig", "Ada"]


def test_empty_values_are_dropped_from_lists() -> None:
    first = profile(education=[field(None), field("BSc Computer Science")])

    merged = merge_profiles([first, profile()])

    assert [item.value for item in merged.education] == ["BSc Computer Science"]


# --- partiality carries through -------------------------------------------------------


def test_a_partial_chunk_makes_the_whole_profile_partial() -> None:
    """A profile assembled from a good page group and a failed one is not a
    complete profile, and saying otherwise would hide which half is missing."""
    first = profile(employment=[entry("Acme", "Lead")])
    second = profile(partial=True, unparsed_reason="this page could not be read")

    merged = merge_profiles([first, second])

    assert merged.partial is True
    assert merged.unparsed_reason == "this page could not be read"


def test_a_clean_merge_is_not_partial() -> None:
    merged = merge_profiles([profile(employment=[entry("Acme", "Lead")]), profile()])

    assert merged.partial is False


# --- degenerate inputs -------------------------------------------------------------------


def test_one_profile_merges_to_itself() -> None:
    only = profile(employment=[entry("Acme", "Lead")])

    assert merge_profiles([only]) is only


def test_merging_nothing_is_an_error() -> None:
    """Rather than an empty profile, which would look like a candidate whose
    documents said nothing."""
    with pytest.raises(ValueError, match="nothing to merge"):
        merge_profiles([])


def test_the_merge_is_deterministic() -> None:
    """Asking a model to reconcile two partial profiles would be asking it to
    choose between two dates, which is a judgment nobody could check."""
    parts = [
        profile(employment=[entry("Acme", "Lead", end="2021")], technologies=[field("Python")]),
        profile(employment=[entry("Acme", "Lead", end="2019")], technologies=[field("Go")]),
    ]

    assert merge_profiles(parts).model_dump() == merge_profiles(parts).model_dump()
