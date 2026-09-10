"""Text a parser reads and a person does not.

Four mechanisms, four detectors, and the corpus proves each one against a real
PDF rather than against a mock. That matters here more than elsewhere: two of
these detectors were written correctly against a fixture that turned out not to
contain the attack at all, because the base PDF fonts silently replaced every
character the payload was made of. A test against a stub would still be passing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain.contracts.enums import IntegrityFindingKind, Severity
from infrastructure.extraction.dispatcher import Extractor
from infrastructure.security.detectors import (
    HIDDEN_RUN_MIN_CHARS,
    MIN_VISIBLE_PT,
    TextSpan,
    detect_hidden_colour,
    detect_hidden_size,
    detect_homoglyphs,
    detect_invisible_glyphs,
    detect_offpage,
)
from tests.adversarial.conftest import scan_of

LONG = "x" * (HIDDEN_RUN_MIN_CHARS + 10)


def fired(scan, detector: str) -> bool:
    return any(finding.detector == detector for finding in scan.findings)


# --- against the corpus ---------------------------------------------------------


def test_white_on_white_is_found(corpus: Path, extractor: Extractor) -> None:
    assert fired(scan_of(corpus / "hidden_white_text.pdf", extractor), "D-HIDDEN-COLOUR")


def test_two_point_text_is_found(corpus: Path, extractor: Extractor) -> None:
    assert fired(scan_of(corpus / "tiny_text.pdf", extractor), "D-HIDDEN-SIZE")


def test_cropped_out_text_is_found(corpus: Path, extractor: Extractor) -> None:
    """The library will not return text outside the crop box, so the scanner
    lifts the crop to read the page. Without that step this document scans
    clean, which is exactly what the attack is for."""
    assert fired(scan_of(corpus / "offpage_text.pdf", extractor), "D-OFFPAGE")


def test_zero_width_characters_are_found(corpus: Path, extractor: Extractor) -> None:
    """Read from the raw text. The normalization profile strips these, so a
    scanner looking at normalized text would report that no document has ever
    contained one."""
    assert fired(scan_of(corpus / "zero_width.txt", extractor), "D-ZERO-WIDTH")


def test_mixed_script_words_are_found(corpus: Path, extractor: Extractor) -> None:
    assert fired(scan_of(corpus / "homoglyph.txt", extractor), "D-HOMOGLYPH")


def test_the_demo_document_trips_three_independent_detectors(
    corpus: Path, extractor: Extractor
) -> None:
    """The point of the demo. One control failing does not clear the document."""
    scan = scan_of(corpus / "tc09_visible_and_hidden.pdf", extractor)
    detectors = {finding.detector for finding in scan.findings}

    assert {"D-INSTR-IMPERATIVE", "D-HIDDEN-COLOUR", "D-HIDDEN-SIZE"} <= detectors


def test_no_clean_document_reports_hidden_text(corpus: Path, extractor: Extractor) -> None:
    for name in ("clean_engineer.pdf", "clean_researcher.pdf", "clean_multilingual.pdf"):
        scan = scan_of(corpus / name, extractor)
        hidden = [
            finding for finding in scan.findings if finding.kind is IntegrityFindingKind.HIDDEN_TEXT
        ]
        assert hidden == [], f"{name} reported hidden text"


# --- the rules, against literals --------------------------------------------------


def test_colour_matching_the_background_is_hidden() -> None:
    span = TextSpan(text=LONG, colour=(1.0, 1.0, 1.0), background=(1.0, 1.0, 1.0))

    assert detect_hidden_colour([span])


def test_near_white_on_white_is_hidden() -> None:
    """Exact equality would be trivially defeated by one shade off."""
    span = TextSpan(text=LONG, colour=(0.98, 0.99, 0.99), background=(1.0, 1.0, 1.0))

    assert detect_hidden_colour([span])


def test_grey_on_white_is_not_hidden() -> None:
    """Light grey body text is a design choice a lot of CV templates make."""
    span = TextSpan(text=LONG, colour=(0.55, 0.55, 0.55), background=(1.0, 1.0, 1.0))

    assert detect_hidden_colour([span]) == []


def test_transparent_text_is_hidden() -> None:
    span = TextSpan(text=LONG, alpha=0.0)

    assert detect_hidden_colour([span])


def test_a_short_invisible_run_is_ignored() -> None:
    """A few white characters are a rendering artefact. Forty are a sentence."""
    span = TextSpan(text="ok", colour=(1.0, 1.0, 1.0), background=(1.0, 1.0, 1.0))

    assert detect_hidden_colour([span]) == []


@pytest.mark.parametrize("size", [0.5, 1.0, 2.0, 2.9])
def test_text_below_three_points_is_hidden(size: float) -> None:
    assert detect_hidden_size([TextSpan(text=LONG, font_size=size)])


@pytest.mark.parametrize("size", [3.0, 6.0, 8.0, 11.0])
def test_small_but_readable_text_is_not_hidden(size: float) -> None:
    """Six-point type is a footnote. Two-point type is not a design decision."""
    assert detect_hidden_size([TextSpan(text=LONG, font_size=size)]) == []


def test_the_visibility_floor_is_three_points() -> None:
    assert MIN_VISIBLE_PT == 3.0


def test_a_span_with_no_recorded_size_is_not_guessed_at() -> None:
    """Plain text has no font size. Treating "unknown" as "hidden" would flag
    every text document there is."""
    assert detect_hidden_size([TextSpan(text=LONG, font_size=None)]) == []


def test_text_outside_the_visible_box_is_hidden() -> None:
    span = TextSpan(
        text=LONG,
        bbox=(72.0, 700.0, 400.0, 715.0),
        media_box=(0.0, 0.0, 595.0, 600.0),
    )

    assert detect_offpage([span])


def test_text_inside_the_visible_box_is_not() -> None:
    span = TextSpan(
        text=LONG,
        bbox=(72.0, 100.0, 400.0, 115.0),
        media_box=(0.0, 0.0, 595.0, 600.0),
    )

    assert detect_offpage([span]) == []


def test_text_behind_an_opaque_image_is_hidden() -> None:
    assert detect_offpage([TextSpan(text=LONG, behind_image=True)])


@pytest.mark.parametrize(
    "char",
    ["\u200b", "‌", "‍", "⁠", "﻿", "‮", "­"],
)
def test_each_invisible_character_is_reported(char: str) -> None:
    findings = detect_invisible_glyphs(f"Pyt{char}hon")

    assert findings
    assert findings[0].severity is Severity.MEDIUM


def test_a_tag_character_is_reported() -> None:
    """A whole Unicode block that can encode hidden ASCII."""
    assert detect_invisible_glyphs("text\U000e0041\U000e0042")


def test_the_count_of_each_kind_is_reported() -> None:
    """A reviewer wants to know whether it was one stray character or two
    hundred."""
    findings = detect_invisible_glyphs("a\u200bb\u200bc\u200bd")

    assert "(3)" in findings[0].excerpt


def test_ordinary_text_reports_no_invisible_characters() -> None:
    assert detect_invisible_glyphs("Built the ingest pipeline in Python.") == []


def test_a_cyrillic_letter_inside_a_latin_word_is_reported() -> None:
    assert detect_homoglyphs("Engineering Мanager")


def test_a_greek_letter_inside_a_latin_word_is_reported() -> None:
    assert detect_homoglyphs("Αrchitect")


def test_a_whole_word_in_one_script_is_not_reported() -> None:
    """Multilingual CVs are ordinary. Mixing scripts inside one word is not."""
    assert detect_homoglyphs("Менеджер developer") == []


def test_accented_latin_is_not_reported() -> None:
    """The most likely false positive: half of Europe writes like this."""
    assert detect_homoglyphs("Ingénieur logiciel à Lyon, responsable facturation") == []
