"""Two variants of a CV differ in identity and in nothing else.

The precondition for the whole fairness experiment. Two documents that differ in
a name *and* in a comma are two experiments, and a flip between them cannot be
attributed to either — so a fairness result built on sloppy pairs is not a weak
result, it is a meaningless one.

The check is a multiset comparison after subtracting identity words. Multiset,
because a word appearing twice in one variant and once in the other is a real
difference that a set comparison reports as identical.
"""

from __future__ import annotations

import pytest

from eval.fairness.build_pairs import (
    BASE_CVS,
    build,
    differences,
    non_identity_tokens,
    substitute,
)
from eval.fairness.personas import Persona, load, reference

PERSONAS = load()
PAIRS = build(PERSONAS)
REFERENCE = reference(PERSONAS)


# --- the property -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("base", "variant"),
    PAIRS.pairs_against(REFERENCE),
    ids=lambda item: getattr(item, "variant_id", ""),
)
def test_variants_differ_only_in_identity(base, variant) -> None:
    """The headline. Strip the identity words and what remains is identical."""
    assert differences(base, variant) == {}, (
        f"{base.variant_id} and {variant.variant_id} differ beyond identity: "
        f"{dict(differences(base, variant))}"
    )


def test_the_check_catches_a_real_difference() -> None:
    """A test that could not fail would prove nothing. This edits one variant
    and confirms the check notices."""
    base, variant = PAIRS.pairs_against(REFERENCE)[0]
    tampered = type(variant)(
        base_id=variant.base_id,
        persona=variant.persona,
        text=variant.text.replace("Python", "Rust"),
    )

    assert differences(base, tampered) != {}


def test_the_check_is_a_multiset_not_a_set() -> None:
    """A word appearing twice in one variant and once in the other is a real
    difference, and a set comparison would call the documents identical."""
    base, variant = PAIRS.pairs_against(REFERENCE)[0]
    doubled = type(variant)(
        base_id=variant.base_id,
        persona=variant.persona,
        text=variant.text + "\nPython.",
    )

    assert differences(base, doubled) != {}


def test_diacritics_do_not_read_as_a_difference() -> None:
    """The bug this caught: an ASCII-only tokenizer split "Siobhán" into
    fragments that matched no identity word, so every pair involving that
    persona reported four spurious differences — and the persona chosen partly
    to exercise the normalisation profile was the one the check could not
    read."""
    accented = next(persona for persona in PERSONAS if "á" in persona.name.lower())
    pairs = [
        (base, variant)
        for base, variant in PAIRS.pairs_against(REFERENCE)
        if variant.persona.id == accented.id
    ]

    assert pairs
    for base, variant in pairs:
        assert differences(base, variant) == {}


# --- substitution --------------------------------------------------------------------


@pytest.mark.parametrize("persona", PERSONAS, ids=lambda p: p.id)
@pytest.mark.parametrize("base_id", sorted(BASE_CVS), ids=str)
def test_no_placeholder_survives(base_id: str, persona: Persona) -> None:
    """An unsubstituted placeholder would be a difference between variants and
    would also read as a corrupt CV to the extractor."""
    text = substitute(BASE_CVS[base_id], persona)

    assert "{{" not in text
    assert "}}" not in text


def test_an_unknown_placeholder_is_refused() -> None:
    with pytest.raises(ValueError, match="placeholder survived"):
        substitute("Hello {{UNKNOWN}}", PERSONAS[0])


@pytest.mark.parametrize("persona", PERSONAS, ids=lambda p: p.id)
def test_every_variant_carries_its_own_name(persona: Persona) -> None:
    text = substitute(BASE_CVS["strong"], persona)

    assert persona.name in text


@pytest.mark.parametrize("persona", PERSONAS, ids=lambda p: p.id)
def test_pronoun_forms_are_grammatical(persona: Persona) -> None:
    """Getting a pronoun case wrong would introduce a grammatical difference
    between variants, which is a difference."""
    text = substitute(BASE_CVS["borderline"], persona)

    assert "{{PRONOUN" not in text
    assert text.count(persona.name) >= 1


# --- the shape of the experiment ----------------------------------------------------------


def test_every_base_produces_a_variant_per_persona() -> None:
    assert PAIRS.total == len(BASE_CVS) * len(PERSONAS)


def test_pairs_are_against_one_reference() -> None:
    """Linear in the number of personas, and a flip means something specific:
    the answer changed relative to a name the file documents as unmarked."""
    expected = len(BASE_CVS) * (len(PERSONAS) - 1)

    assert len(PAIRS.pairs_against(REFERENCE)) == expected


def test_the_reference_persona_is_documented_as_unmarked() -> None:
    assert "unmarked" in REFERENCE.note.lower()


def test_base_cvs_vary_in_strength() -> None:
    """A fairness result measured only on strong candidates would miss a
    disparity that appears at the margin, which is where a screening system does
    the most damage."""
    assert {"strong", "borderline", "sparse"} <= set(BASE_CVS)


def test_every_base_cv_uses_pronouns() -> None:
    """A CV with no pronoun varies in name alone, which tests less than it
    appears to."""
    for base_id, template in BASE_CVS.items():
        assert "{{PRONOUN" in template, base_id


# --- the personas ----------------------------------------------------------------------------


def test_personas_are_configuration_not_code() -> None:
    """Adding one should be an edit and a rerun, not a code review of the
    measurement."""
    from pathlib import Path

    from eval.fairness.personas import DEFAULT_FILE

    assert DEFAULT_FILE.suffix == ".yaml"
    assert Path(DEFAULT_FILE).is_file()


def test_persona_ids_are_unique() -> None:
    ids = [persona.id for persona in PERSONAS]

    assert len(ids) == len(set(ids))


def test_there_are_enough_personas_to_measure_anything() -> None:
    assert len(PERSONAS) >= 4


def test_an_empty_persona_file_is_refused(tmp_path) -> None:
    """A fairness run with no personas would produce a rate over zero
    comparisons and read as a clean result."""
    from eval.fairness.personas import load as load_personas

    path = tmp_path / "empty.yaml"
    path.write_text("personas: []", encoding="utf-8")

    with pytest.raises(ValueError, match="declares no personas"):
        load_personas(path)


def test_identity_words_cover_every_substituted_value() -> None:
    """Anything a persona puts into a document must be subtractable from it, or
    the difference check would report it as a second variable."""
    for persona in PERSONAS:
        for value in persona.tokens.values():
            for word in value.lower().split():
                assert word.strip(".,") in persona.identity_words, (
                    f"{persona.id} substitutes {word!r} but does not recognise it as identity"
                )


def test_non_identity_tokens_exclude_the_name() -> None:
    persona = PERSONAS[1]
    text = substitute(BASE_CVS["strong"], persona)

    tokens = non_identity_tokens(text, persona)

    for word in persona.name.lower().split():
        assert word not in tokens
