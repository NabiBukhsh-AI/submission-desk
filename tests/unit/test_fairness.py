"""The protected-attribute filter, and the false positives it must not have.

This module exists because the filter was wrong for two phases and nothing
caught it. ``"age" in claim`` reads true for "language" and "triage", so every
evidence claim about a language-model system was silently dropped, on an AI
engineering rubric, where those are the claims that matter most. The failure was
invisible: a dropped claim and a model that found nothing look identical from
outside.

So both directions are tested, and the second direction is the one with the
teeth. Catching "what is your nationality" is easy. Not catching "shipped a
language-model feature" is what a substring rule cannot do.
"""

from __future__ import annotations

import pytest

from domain.fairness import (
    PROTECTED_PHRASES,
    PROTECTED_WORDS,
    TECHNICAL_SENSES,
    mentions_protected_attribute,
    offending_terms,
)

# --- what must be caught -------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "How old are you?",
        "The candidate is 42 years old.",
        "What is your date of birth?",
        "Their gender is not stated on the CV.",
        "Is the candidate male or female?",
        "What is your nationality?",
        "Their ethnic background is not clear.",
        "This looks like a racial background question.",
        "Does the candidate practise a religion?",
        "She is married with two children.",
        "What is your marital status?",
        "Is she pregnant?",
        "Do you have a disability?",
        "A strong personality, good culture fit.",
        "Judging by their appearance in the photograph.",
        "Are you a native speaker of English?",
        "What is your sexual orientation?",
    ],
)
def test_a_protected_attribute_is_caught(text: str) -> None:
    assert mentions_protected_attribute(text)


# --- what must not be caught ----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # The two that were actually broken.
        "Shipped a language-model feature used by the support team.",
        "Replaced a manual triage process with an automated one.",
        # Words that contain a protected term as a substring.
        "Managed the storage of scanned documents.",
        "Wrote the package manager integration.",
        "Reduced average latency across the fleet.",
        "Built a message broker for internal services.",
        "Ran a salvage operation on a corrupted index.",
        "Worked on international payments.",
        "Traced the request through the middleware.",
        # Words whose engineering sense is ordinary.
        "Fixed a race condition in the scheduler.",
        "Debugged a data race under load.",
        "Rolled out single sign-on across three services.",
        "Kept a single source of truth for configuration.",
        "The service is single-threaded by design.",
        "Walked the children of the root node.",
        "Spawned a child process per worker.",
        "Traced parent-child spans across services.",
        "Disabled the legacy endpoint after migration.",
    ],
)
def test_ordinary_engineering_prose_survives(text: str) -> None:
    """The half that a substring filter gets wrong, silently."""
    assert not mentions_protected_attribute(text), offending_terms(text)


def test_the_word_that_broke_it_is_pinned() -> None:
    """Named explicitly so a future edit to the lexicon cannot quietly
    reintroduce the bug this module was written for."""
    assert not mentions_protected_attribute("language")
    assert not mentions_protected_attribute("triage")
    assert mentions_protected_attribute("age")


# --- the rubric's own additions --------------------------------------------------


def test_a_rubric_attribute_is_added_to_the_filter() -> None:
    assert not mentions_protected_attribute("Which school did you attend?")
    assert mentions_protected_attribute("Which school did you attend?", ["school"])


def test_an_underscored_attribute_matches_the_written_phrase() -> None:
    """Rubrics write identifiers; sentences write words."""
    assert mentions_protected_attribute("What is your family status?", ["family_status"])


def test_an_added_attribute_also_matches_on_word_boundaries() -> None:
    """A rubric addition gets the same treatment as a built-in one, rather than
    reintroducing substring matching through the back door."""
    assert not mentions_protected_attribute("Reschooled the model on new data.", ["school"])


def test_an_empty_addition_list_changes_nothing() -> None:
    assert not mentions_protected_attribute("Shipped a language-model feature.", [])


def test_a_blank_attribute_does_not_match_everything() -> None:
    """An empty string in a rubric would otherwise match every text there is."""
    assert not mentions_protected_attribute("Shipped a feature.", ["", "   "])


# --- what the log says -----------------------------------------------------------


def test_the_matched_term_is_reported() -> None:
    """ "Dropped: mentions 'gender'" tells a reviewer why a question vanished.
    A bare count does not."""
    assert offending_terms("Their gender is not stated.") == ["gender"]


def test_terms_are_reported_once_and_sorted() -> None:
    found = offending_terms("Her gender, her age, and her gender again.")

    assert found == ["age", "gender"]


def test_nothing_is_reported_for_clean_text() -> None:
    assert offending_terms("Shipped a language-model feature.") == []


# --- the shape of the lexicon -----------------------------------------------------


def test_every_term_is_lowercase_and_stripped() -> None:
    """Matching is case-insensitive, so a capital in the table would be a
    reader's confusion rather than a behaviour change. Still worth pinning."""
    for term in (*PROTECTED_WORDS, *PROTECTED_PHRASES, *TECHNICAL_SENSES):
        assert term == term.lower().strip()
        assert term


def test_no_single_word_appears_in_both_tables() -> None:
    """A term that is both protected and technical would resolve by whichever
    pass ran first, which is not a rule anybody could read."""
    assert not set(PROTECTED_WORDS) & set(TECHNICAL_SENSES)


def test_matching_is_case_insensitive() -> None:
    assert mentions_protected_attribute("WHAT IS YOUR NATIONALITY?")
    assert mentions_protected_attribute("Marital Status")


def test_a_phrase_split_across_a_line_still_matches() -> None:
    """Text extracted from a PDF is full of these."""
    assert mentions_protected_attribute("the candidate is 42 years\nold")
