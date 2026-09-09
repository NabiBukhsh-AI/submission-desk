"""The profile, step by step, and what it deliberately leaves alone.

Two halves again. The transformations must be aggressive enough that a correct
quotation written slightly differently still matches. They must be conservative
enough that they never make a wrong quotation match a right one, which is why
case, digits, and spelling are untouched.
"""

from __future__ import annotations

import pytest

from domain.provenance.normalization import (
    PROFILE_ID,
    collapse_whitespace,
    dehyphenate,
    expand_ligatures,
    normalize,
    normalize_with_map,
    remove_invisible,
    unify_dashes,
    unify_quotes,
)

# --- what is transformed ----------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after"),
    [("ﬁnal", "final"), ("ﬂow", "flow"), ("oﬃce", "office"), ("æon", "aeon")],
)
def test_ligatures_are_expanded(before: str, after: str) -> None:
    """PDFs from older typesetters carry these, and a model quoting the page
    writes the plain letters."""
    assert normalize(before) == after


@pytest.mark.parametrize("dash", ["–", "—", "−", "‑", "―"])
def test_every_dash_becomes_a_hyphen(dash: str) -> None:
    assert normalize(f"multi{dash}service") == "multi-service"


@pytest.mark.parametrize(("fancy", "plain"), [("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"')])
def test_curly_quotes_become_straight(fancy: str, plain: str) -> None:
    """The single most common reason a correct quotation fails to match."""
    assert normalize(f"the team{fancy}s work") == f"the team{plain}s work"


def test_invisible_characters_are_removed() -> None:
    """This doubles as an injection control: hidden text is frequently smuggled
    in exactly these code points."""
    assert normalize("pro\u200bduction­ready") == "productionready"


def test_a_word_broken_across_a_line_is_rejoined() -> None:
    assert normalize("evalu-\nation suite") == "evaluation suite"


def test_a_genuine_compound_at_a_line_end_is_left_alone() -> None:
    """ "multi-\\nService" is a hyphenated compound that happens to wrap, not a
    broken word, and joining it would produce a string in no document."""
    assert normalize("multi-\nService") == "multi- Service"


def test_a_list_bullet_is_not_treated_as_a_hyphenated_word() -> None:
    assert "-" in normalize("summary\n- shipped to production")


def test_wrapped_text_collapses_to_single_spaces() -> None:
    """This is what lets a model's single-line quotation match source text that
    was wrapped across three lines."""
    assert normalize("Introduced a nightly\n   evaluation\tsuite") == (
        "Introduced a nightly evaluation suite"
    )


def test_leading_and_trailing_whitespace_is_stripped() -> None:
    assert normalize("   padded   ") == "padded"


# --- what is deliberately left alone ------------------------------------------------


def test_case_is_preserved() -> None:
    """Lowercasing would make "Python" and "python" indistinguishable, and the
    reviewer reads the quotation as written."""
    assert normalize("Senior Backend Engineer") == "Senior Backend Engineer"


def test_digits_are_never_altered() -> None:
    """A CV's numbers are frequently the evidence itself."""
    assert normalize("Reduced p95 latency from 900ms to 210ms") == (
        "Reduced p95 latency from 900ms to 210ms"
    )


def test_spelling_is_not_corrected() -> None:
    """Fixing spelling would let a fabricated span match a real one, which is
    the failure this whole subsystem exists to catch."""
    assert normalize("recieved an award") == "recieved an award"


def test_ordinary_punctuation_survives() -> None:
    assert normalize("shipped; measured, and reviewed.") == "shipped; measured, and reviewed."


def test_the_table_delimiter_survives() -> None:
    """A real cross-cell quotation contains the pipe, which is what separates it
    from an invented smooth sentence."""
    assert "|" in normalize("Acme Payments | Lead Engineer")


# --- the offset map ------------------------------------------------------------------


def test_the_map_covers_the_output_completely() -> None:
    """A gap means some character has no route back to the original document,
    and a span citing it would be unverifiable."""
    result = normalize_with_map("The team’s multi–service backend")

    covered = sum(run.length for run in result.runs)
    assert covered == len(result.text)


def test_the_map_is_ordered_and_contiguous_in_normalised_space() -> None:
    result = normalize_with_map("Introduced a nightly\n   evaluation suite")

    expected_start = 0
    for run in result.runs:
        assert run.norm_start == expected_start
        expected_start += run.length


def test_the_map_points_back_at_the_right_characters() -> None:
    """The property that makes a highlight land on the right words."""
    original = "The team’s multi–service backend"
    result = normalize_with_map(original)

    for run in result.runs:
        for offset in range(run.length):
            normalised_char = result.text[run.norm_start + offset]
            original_char = original[run.raw_start + offset]
            # They differ only where the profile substituted one for another.
            assert normalized_matches(normalised_char, original_char)


def normalized_matches(normalised: str, original: str) -> bool:
    from domain.provenance.normalization import DASHES, LIGATURES, QUOTES

    if normalised == original:
        return True
    for table in (DASHES, QUOTES, LIGATURES):
        if table.get(original, "").startswith(normalised):
            return True
    # Whitespace of any kind collapses to a single space.
    return normalised == " " and original.isspace()


def test_empty_text_produces_an_empty_map() -> None:
    result = normalize_with_map("")

    assert result.text == ""
    assert result.runs == ()


def test_text_of_only_whitespace_normalises_away() -> None:
    result = normalize_with_map("   \n\t  ")

    assert result.text == ""


# --- the profile id ----------------------------------------------------------------


def test_the_profile_has_a_versioned_id() -> None:
    """It is stored on every provenance record, so a profile change invalidates
    the extraction cache rather than silently mismatching spans validated under
    the old rules."""
    assert PROFILE_ID == "np-v1-nfkc-ws-dash-hyphen"


def test_each_step_reports_where_every_character_came_from() -> None:
    """Composed, these are what make the final map exact rather than inferred."""
    for step in (
        expand_ligatures,
        unify_dashes,
        unify_quotes,
        remove_invisible,
        dehyphenate,
        collapse_whitespace,
    ):
        text, mapping = step("a–b ﬁ c\nd")
        assert len(mapping) == len(text), step.__name__
        assert all(0 <= index < len("a–b ﬁ c\nd") for index in mapping), step.__name__


def test_normalisation_is_idempotent() -> None:
    """Normalising twice must equal normalising once, or the validator's
    comparison depends on how many times each side has been through it."""
    messy = "The  team’s\n  multi–service  ﬁnal\u200b report"

    assert normalize(normalize(messy)) == normalize(messy)
