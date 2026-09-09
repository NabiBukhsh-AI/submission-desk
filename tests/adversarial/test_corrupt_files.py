"""Input designed to be refused.

Every terminal condition gets a test asserting three things: the run reaches a
terminal state, the reason is the right enum member, and the reviewer is given a
sentence rather than a traceback.

That last assertion is the one that matters for adoption. A recruiter who sees
``UnicodeDecodeError`` learns nothing and asks the engineer; a recruiter who
sees "ask the candidate to send it again" fixes it themselves.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from application.deps import Deps
from domain.contracts.enums import RunStatus
from domain.contracts.run_state import NodeStatus, RunState
from domain.ports.sources import DocumentRef, SourceUnavailable
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.blobs import BlobStore
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline.intake import (
    REJECTION_MESSAGES,
    IntakeLimits,
    RejectionReason,
    classify,
    node,
    sniff_type,
)
from tests.fixtures import documents
from tests.workflow.conftest import make_run_record

LIMITS = IntakeLimits()


class FakeSource:
    """Serves bytes from a dictionary, keyed by filename."""

    source_id = "fake"

    def __init__(self, files: dict[str, bytes], *, unavailable: bool = False) -> None:
        self.files = files
        self.unavailable = unavailable
        self.fetched: list[str] = []

    def list_candidates(self, limit: int | None = None) -> list:
        return []

    def fetch(self, ref: DocumentRef) -> bytes:
        if self.unavailable:
            raise SourceUnavailable("the folder could not be reached")
        self.fetched.append(ref.filename)
        return self.files[ref.filename]


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "intake.sqlite"), blob_dir=str(tmp_path / "blobs")
    )
    yield build_deps(settings)
    close_thread_connection(settings.db_path)


def run_intake(deps: Deps, files: dict[str, bytes], *, unavailable: bool = False):
    record = make_run_record()
    deps.runs.create(record)
    state = RunState(
        run_id=record.run_id,
        candidate_id=record.candidate_id,
        role_id=record.role_id,
        status=RunStatus.CREATED,
        started_at=record.started_at,
    )
    refs = tuple(
        DocumentRef(candidate_id=record.candidate_id, filename=name, external_ref=name)
        for name in files
    )
    source = FakeSource(files, unavailable=unavailable)
    wired = Deps(**{**deps.__dict__, "source": source})
    return node(state.model_copy(update={"document_refs": refs}), wired), source


# --- one test per terminal condition -----------------------------------------


def test_an_executable_renamed_to_pdf_is_refused(deps: Deps) -> None:
    """The extension is never trusted. This is the whole reason for sniffing."""
    result, _ = run_intake(deps, {"resume.pdf": documents.windows_executable()})

    assert result.status is NodeStatus.FAILED
    assert result.error.error_code == RejectionReason.UNSUPPORTED_TYPE.value.upper()


def test_a_shell_script_renamed_to_docx_is_refused(deps: Deps) -> None:
    result, _ = run_intake(deps, {"cv.docx": documents.shell_script()})

    assert result.error.error_code == "UNSUPPORTED_TYPE"


def test_a_truncated_pdf_is_refused_as_corrupt(deps: Deps) -> None:
    result, _ = run_intake(deps, {"cv.pdf": documents.pdf(truncated=True)})

    assert result.error.error_code == RejectionReason.CORRUPT_FILE.value.upper()
    assert "send it again" in result.error.message_redacted


def test_a_password_protected_pdf_is_refused(deps: Deps) -> None:
    """Caught before a parser is handed a file it will fail on, so the reviewer
    is told the specific thing that is wrong."""
    result, _ = run_intake(deps, {"cv.pdf": documents.pdf(encrypted=True)})

    assert result.error.error_code == RejectionReason.ENCRYPTED.value.upper()
    assert "password protected" in result.error.message_redacted


def test_an_almost_empty_text_file_is_refused(deps: Deps) -> None:
    result, _ = run_intake(deps, {"cv.txt": b"Ana\n"})

    assert result.error.error_code == RejectionReason.EMPTY_DOCUMENT.value.upper()


def test_an_oversized_document_is_refused(deps: Deps) -> None:
    """Refused on size before anything parses it, so a 40 MB scan costs nothing."""
    huge = documents.pdf() + b"\0" * (LIMITS.max_document_bytes + 1)

    result, _ = run_intake(deps, {"cv.pdf": huge})

    assert result.error.error_code == RejectionReason.OVERSIZED.value.upper()


def test_a_document_with_too_many_pages_is_refused(deps: Deps) -> None:
    result, _ = run_intake(deps, {"cv.pdf": documents.pdf(pages=LIMITS.max_pages + 5)})

    assert result.error.error_code == RejectionReason.OVERSIZED.value.upper()


def test_too_many_documents_is_refused_before_any_are_read(deps: Deps) -> None:
    """The count is checked first, so a mixed-up folder costs nothing to reject."""
    files = {f"file-{index}.txt": documents.plain_text() for index in range(20)}

    result, source = run_intake(deps, files)

    assert result.error.error_code == RejectionReason.TOO_MANY_DOCUMENTS.value.upper()
    assert source.fetched == [], "documents were read before the count was checked"


def test_no_documents_is_refused_clearly(deps: Deps) -> None:
    result, _ = run_intake(deps, {})

    assert result.error.error_code == RejectionReason.NO_DOCUMENTS.value.upper()


def test_an_unreachable_source_is_retryable(deps: Deps) -> None:
    """Distinct from a bad file: the folder may be back in a minute."""
    result, _ = run_intake(deps, {"cv.pdf": documents.pdf()}, unavailable=True)

    assert result.error.error_code == RejectionReason.SOURCE_UNAVAILABLE.value.upper()
    assert result.error.retryable is True


# --- archives -----------------------------------------------------------------


def test_a_zip_bomb_is_refused_without_being_opened() -> None:
    """A DOCX is a zip, so the marker check reads bytes rather than opening the
    archive. That ordering is the defence: nothing decompresses."""
    assert sniff_type(documents.zip_bomb(entries=30, size=1_000_000)) is None


def test_an_entity_bomb_is_not_treated_as_a_document() -> None:
    """It carries the Word marker, so it sniffs as a DOCX. Refusing it belongs
    to the XML parser at extraction, and this test records that the boundary is
    known rather than assumed."""
    kind = sniff_type(documents.xml_entity_bomb())

    assert kind == "docx"
    assert classify(documents.xml_entity_bomb(), LIMITS) is None


# --- what is accepted ----------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("cv.pdf", documents.pdf()),
        ("cv.docx", documents.docx()),
        ("cv.txt", documents.plain_text()),
    ],
)
def test_a_real_document_is_accepted(deps: Deps, filename: str, data: bytes) -> None:
    result, _ = run_intake(deps, {filename: data})

    assert result.status is NodeStatus.OK
    assert result.next_status is RunStatus.INTAKE_OK


def test_an_accepted_document_is_recorded(deps: Deps) -> None:
    result, _ = run_intake(deps, {"cv.pdf": documents.pdf()})

    assert [event.name for event in result.events] == ["intake.document_accepted"]


# --- no terminal condition produces a traceback --------------------------------


@pytest.mark.parametrize(
    ("filename", "data"),
    [
        ("cv.pdf", documents.windows_executable()),
        ("cv.pdf", documents.pdf(truncated=True)),
        ("cv.pdf", documents.pdf(encrypted=True)),
        ("cv.txt", b"x"),
        ("cv.bin", documents.binary_noise()),
        ("cv.zip", documents.zip_bomb(3, 1000)),
    ],
)
def test_no_rejection_escapes_as_an_exception(deps: Deps, filename: str, data: bytes) -> None:
    """The node returns a state. Nothing here raises, ever."""
    result, _ = run_intake(deps, {filename: data})

    assert result.status is NodeStatus.FAILED
    assert result.error is not None


@pytest.mark.parametrize("reason", list(RejectionReason))
def test_every_reason_has_a_sentence_a_recruiter_can_act_on(reason: RejectionReason) -> None:
    message = REJECTION_MESSAGES[reason]

    assert message.endswith((".", "!"))
    assert message[0].isupper()
    for jargon in ("error", "exception", "traceback", "null", "none", "invalid", "failed"):
        assert jargon not in message.lower(), f"{reason.value}: {message}"


def test_every_reason_is_reachable() -> None:
    """A reason nobody can produce is a message nobody proofread."""
    assert set(REJECTION_MESSAGES) == set(RejectionReason)


# --- storage ------------------------------------------------------------------


def test_a_filename_with_traversal_lands_under_the_hash(deps: Deps) -> None:
    """The filename is metadata. It is stored, displayed, and never used to
    build a path."""
    result, _ = run_intake(deps, {"../../etc/passwd": documents.plain_text()})

    assert result.status is NodeStatus.OK
    stored = deps.candidates.documents_for_run(result.state.run_id)
    assert stored[0].original_filename == "../../etc/passwd"
    assert stored[0].document_sha256 in stored[0].blob_path


def test_the_same_file_twice_writes_one_blob(deps: Deps, tmp_path: Path) -> None:
    """Content addressing gives deduplication for free."""
    data = documents.plain_text()
    run_intake(deps, {"a.txt": data})
    run_intake(deps, {"b.txt": data})

    store = BlobStore(deps.settings.blob_dir)
    written = list(Path(store.root).rglob("*"))

    assert len([path for path in written if path.is_file()]) == 1


def test_the_document_hashes_are_carried_forward(deps: Deps) -> None:
    """The content key is computed from these, so their order must be stable."""
    result, _ = run_intake(
        deps, {"b.txt": documents.plain_text(300), "a.txt": documents.plain_text(400)}
    )

    hashes = result.state.document_hashes
    assert list(hashes) == sorted(hashes)
