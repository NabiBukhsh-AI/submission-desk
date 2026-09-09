"""The limits, the grouping rule, and the duplicate path.

Type sniffing and the caps are pure functions, so they are tested directly
rather than through the node. The folder source is tested against real files,
because its whole job is to be right about a filesystem.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from domain.identity import content_key
from domain.ports.sources import SourceUnavailable
from infrastructure.integrations.local import LocalFolderSource
from pipeline.intake import (
    IntakeLimits,
    RejectionReason,
    classify,
    estimate_pages,
    is_encrypted_pdf,
    looks_corrupt,
    mime_for,
    role_for,
    sniff_type,
)
from tests.fixtures import documents

REPO_ROOT = Path(__file__).resolve().parents[2]
LIMITS = IntakeLimits()


# --- type sniffing ------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (documents.pdf(), "pdf"),
        (documents.docx(), "docx"),
        (documents.plain_text(), "txt"),
    ],
)
def test_a_real_file_is_recognised(data: bytes, expected: str) -> None:
    assert sniff_type(data) == expected


@pytest.mark.parametrize(
    "data",
    [
        documents.windows_executable(),
        documents.shell_script(),
        documents.binary_noise(),
        documents.zip_bomb(3, 1000),
        b"",
    ],
)
def test_anything_else_is_unrecognised(data: bytes) -> None:
    """Returns None rather than guessing. The caller decides what to do."""
    assert sniff_type(data) is None


def test_a_zip_without_the_word_marker_is_not_a_docx() -> None:
    """Checked by looking for the marker in the bytes, not by opening the
    archive, so a bomb is refused before anything decompresses it."""
    assert sniff_type(documents.zip_bomb(2, 100)) is None


def test_a_text_file_of_control_characters_is_not_text() -> None:
    """It might decode, but nobody wrote it, and sending it to a model is
    sending noise."""
    assert sniff_type(bytes(range(1, 32)) * 200) is None


# --- the caps ------------------------------------------------------------------


def test_a_file_at_the_size_limit_is_accepted() -> None:
    """Boundaries are inclusive on the accepting side, so a document exactly at
    the limit is not refused by a rounding decision nobody documented."""
    padding = LIMITS.max_document_bytes - len(documents.pdf())
    at_limit = documents.pdf()[:-6] + b"\0" * padding + b"%%EOF\n"

    assert len(at_limit) == LIMITS.max_document_bytes
    assert classify(at_limit, LIMITS) is None


def test_one_byte_over_the_limit_is_refused() -> None:
    over = documents.pdf() + b"\0" * LIMITS.max_document_bytes

    assert classify(over, LIMITS) is RejectionReason.OVERSIZED


def test_the_page_cap_is_enforced() -> None:
    assert classify(documents.pdf(pages=LIMITS.max_pages + 1), LIMITS) is RejectionReason.OVERSIZED
    assert classify(documents.pdf(pages=LIMITS.max_pages), LIMITS) is None


def test_page_counting_does_not_mistake_the_pages_object_for_a_page() -> None:
    """A PDF's /Type /Pages node is the container, not a page. Counting it would
    overstate every document by one and refuse a sixty-page CV at the cap."""
    assert estimate_pages(documents.pdf(pages=3), "pdf") == 3


def test_page_counting_is_not_attempted_for_other_types() -> None:
    assert estimate_pages(documents.docx(), "docx") is None
    assert estimate_pages(documents.plain_text(), "txt") is None


def test_encryption_is_detected_from_the_dictionary() -> None:
    assert is_encrypted_pdf(documents.pdf(encrypted=True))
    assert not is_encrypted_pdf(documents.pdf())


def test_corruption_is_detected_from_a_missing_end_marker() -> None:
    assert looks_corrupt(documents.pdf(truncated=True), "pdf")
    assert not looks_corrupt(documents.pdf(), "pdf")


def test_a_truncated_archive_is_corrupt() -> None:
    intact = documents.docx()
    assert not looks_corrupt(intact, "docx")
    assert looks_corrupt(intact[: len(intact) // 2], "docx")


def test_the_empty_check_applies_to_text_not_to_binaries() -> None:
    """A short text file is empty; a short PDF is not.

    A PDF's text has not been extracted at this point, so judging its length
    here would refuse a valid one-page CV. Short text, by contrast, is all there
    is, and "almost no readable text" tells the recruiter more than a claim that
    the type is unsupported.
    """
    assert classify(b"Ana\n", LIMITS) is RejectionReason.EMPTY_DOCUMENT
    assert classify(b"x" * 100 + b"\n", LIMITS) is RejectionReason.EMPTY_DOCUMENT
    assert classify(documents.pdf(), LIMITS) is None
    assert classify(documents.plain_text(400), LIMITS) is None


# --- presentation --------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("ana_cv.pdf", "cv"),
        ("Resume-2026.docx", "cv"),
        ("cover_letter.pdf", "cover_letter"),
        ("portfolio.pdf", "portfolio"),
        ("notes.txt", "other"),
    ],
)
def test_the_document_role_is_guessed_from_the_name(filename: str, expected: str) -> None:
    """A guess, and it affects presentation only. Nothing in the assessment
    depends on it, so being wrong costs a label rather than a result."""
    assert role_for(filename).value == expected


def test_every_accepted_type_has_a_mime_type() -> None:
    for kind in LIMITS.accepted_types:
        assert "/" in mime_for(kind)


# --- the shipped limits file ----------------------------------------------------


def test_the_defaults_match_the_configuration_file() -> None:
    """The dataclass defaults and config/limits.yaml must agree, or the file a
    recruiter edits is not the file the system reads."""
    config = yaml.safe_load((REPO_ROOT / "config" / "limits.yaml").read_text(encoding="utf-8"))
    intake = config["intake"]

    assert intake["max_document_bytes"] == LIMITS.max_document_bytes
    assert intake["max_pages"] == LIMITS.max_pages
    assert intake["max_documents_per_candidate"] == LIMITS.max_documents_per_candidate
    assert intake["min_document_chars"] == LIMITS.min_document_chars
    assert tuple(intake["accepted_types"]) == LIMITS.accepted_types


# --- the folder source -----------------------------------------------------------


def test_one_subfolder_is_one_candidate(tmp_path: Path) -> None:
    (tmp_path / "ana").mkdir()
    (tmp_path / "ana" / "cv.pdf").write_bytes(documents.pdf())
    (tmp_path / "ana" / "cover.pdf").write_bytes(documents.pdf())
    (tmp_path / "ben").mkdir()
    (tmp_path / "ben" / "cv.pdf").write_bytes(documents.pdf())

    candidates = LocalFolderSource(tmp_path).list_candidates()

    assert [candidate.candidate_id for candidate in candidates] == ["ana", "ben"]
    assert len(candidates[0].documents) == 2


def test_loose_files_group_by_their_prefix(tmp_path: Path) -> None:
    """A rule a person can predict beats one that is usually right."""
    for name in ("ana-cv.pdf", "ana-cover.pdf", "ben_cv.pdf"):
        (tmp_path / name).write_bytes(documents.pdf())

    candidates = LocalFolderSource(tmp_path).list_candidates()

    assert [candidate.candidate_id for candidate in candidates] == ["ana", "ben"]
    assert len(candidates[0].documents) == 2


def test_files_of_other_types_are_not_offered(tmp_path: Path) -> None:
    (tmp_path / "cv.pdf").write_bytes(documents.pdf())
    (tmp_path / "notes.xlsx").write_bytes(b"whatever")
    (tmp_path / "photo.png").write_bytes(b"\x89PNG")

    candidates = LocalFolderSource(tmp_path).list_candidates()

    assert [document.filename for document in candidates[0].documents] == ["cv.pdf"]


def test_an_empty_folder_is_an_empty_list_not_an_error(tmp_path: Path) -> None:
    """A quiet morning is not an outage."""
    assert LocalFolderSource(tmp_path).list_candidates() == []


def test_a_missing_folder_is_an_outage(tmp_path: Path) -> None:
    """A recruiter whose path is wrong should be told, rather than shown an
    empty queue and left to conclude the system is broken."""
    with pytest.raises(SourceUnavailable, match="does not exist"):
        LocalFolderSource(tmp_path / "nope").list_candidates()


def test_a_file_where_a_folder_was_expected_is_an_outage(tmp_path: Path) -> None:
    target = tmp_path / "a-file.txt"
    target.write_text("not a folder", encoding="utf-8")

    with pytest.raises(SourceUnavailable, match="not a folder"):
        LocalFolderSource(target).list_candidates()


def test_the_limit_caps_the_candidate_list(tmp_path: Path) -> None:
    for name in ("a", "b", "c"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "cv.pdf").write_bytes(documents.pdf())

    assert len(LocalFolderSource(tmp_path).list_candidates(limit=2)) == 2


def test_fetching_outside_the_root_is_refused(tmp_path: Path) -> None:
    """A reference carrying a traversal cannot read outside the folder the
    recruiter pointed at."""
    from domain.ports.sources import DocumentRef

    root = tmp_path / "inbox"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("not for the pipeline", encoding="utf-8")

    source = LocalFolderSource(root)
    ref = DocumentRef(candidate_id="x", filename="secret.txt", external_ref=str(secret))

    with pytest.raises(SourceUnavailable, match="outside the folder"):
        source.fetch(ref)


def test_fetching_a_deleted_file_says_so(tmp_path: Path) -> None:
    from domain.ports.sources import DocumentRef

    source = LocalFolderSource(tmp_path)
    ref = DocumentRef(
        candidate_id="x", filename="gone.pdf", external_ref=str(tmp_path / "gone.pdf")
    )

    with pytest.raises(SourceUnavailable, match="no longer in the folder"):
        source.fetch(ref)


# --- the duplicate path ----------------------------------------------------------


def test_the_same_documents_and_configuration_give_the_same_key() -> None:
    """This is what makes a second identical run a lookup rather than a bill."""
    hashes = ["a" * 64, "b" * 64]

    def key(documents: list[str]) -> str:
        return content_key(
            documents,
            rubric_hash="r" * 64,
            prompt_bundle_hash="p" * 64,
            model_tier_bindings_hash="m" * 64,
            routing_policy_id="routed",
            blind_mode=True,
            calibration_enabled=False,
            pipeline_version="1",
        )

    assert key(hashes) == key(list(reversed(hashes)))
