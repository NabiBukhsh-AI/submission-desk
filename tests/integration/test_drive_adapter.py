"""Reading from Drive, and knowing when to stop asking.

Three behaviours carry this adapter. It retries what is worth retrying. It never
retries an authentication failure, because a wrong credential does not become
right by being asked again and hammering an auth endpoint is how an account gets
locked during a demo. And it discards a partial download rather than returning
it, because a truncated CV would be hashed, stored and assessed as though it
were whole.
"""

from __future__ import annotations

import pytest

from domain.ports.sources import SourceUnavailable
from infrastructure.integrations.drive import SCOPE, DriveSource, candidate_id_for
from tests.fakes.fake_transports import FakeDrive, Script, no_sleep

FILES = [
    {"id": "f1", "name": "ana-cv.pdf", "size": "1200", "mimeType": "application/pdf"},
    {"id": "f2", "name": "ana-letter.pdf", "size": "800", "mimeType": "application/pdf"},
    {"id": "f3", "name": "ben-cv.pdf", "size": "900", "mimeType": "application/pdf"},
]

CONTENTS = {"f1": b"x" * 1200, "f2": b"y" * 800, "f3": b"z" * 900}


def source(drive: FakeDrive, **kwargs) -> DriveSource:
    return DriveSource(drive, folder_id="folder-1", qps=0, sleep=no_sleep, **kwargs)


def fake(**kwargs) -> FakeDrive:
    return FakeDrive(files=list(FILES), contents=dict(CONTENTS), **kwargs)


# --- the happy path ---------------------------------------------------------------


def test_files_are_grouped_into_candidates() -> None:
    result = source(fake()).list_candidates()

    assert [ref.candidate_id for ref in result] == ["ana", "ben"]
    assert len(result[0].documents) == 2


def test_a_document_is_fetched_whole() -> None:
    drive = fake()
    ref = source(drive).list_candidates()[0].documents[0]

    assert source(drive).fetch(ref) == CONTENTS["f1"]


def test_the_source_names_itself() -> None:
    """So a reviewer can be told where a document came from."""
    assert source(fake()).list_candidates()[0].source_id == "drive"


def test_an_empty_folder_is_a_fact_not_an_outage() -> None:
    """A quiet morning and a broken integration must not look the same."""
    assert source(FakeDrive()).list_candidates() == []


def test_a_limit_is_respected() -> None:
    assert len(source(fake()).list_candidates(limit=1)) == 1


# --- retrying ------------------------------------------------------------------------


def test_a_rate_limit_is_retried() -> None:
    drive = fake(list_script=Script([429, 429]))

    result = source(drive).list_candidates()

    assert result
    assert drive.list_count == 3


def test_a_server_error_is_retried() -> None:
    drive = fake(list_script=Script([503]))

    assert source(drive).list_candidates()
    assert drive.list_count == 2


def test_retrying_gives_up_eventually() -> None:
    """Three attempts, then a sentence. Not an infinite loop with a spinner."""
    drive = fake(list_script=Script([429, 429, 429, 429]))

    with pytest.raises(SourceUnavailable):
        source(drive).list_candidates()

    assert drive.list_count == 3


# --- what is never retried ------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_an_auth_failure_is_not_retried(status: int) -> None:
    """The assertion that matters most. Asking again does not fix a credential,
    and repeating the attempt is how an account gets locked out."""
    drive = fake(list_script=Script([status] * 5))

    with pytest.raises(SourceUnavailable):
        source(drive).list_candidates()

    assert drive.list_count == 1


def test_a_missing_folder_is_not_retried() -> None:
    drive = fake(list_script=Script([404] * 5))

    with pytest.raises(SourceUnavailable):
        source(drive).list_candidates()

    assert drive.list_count == 1


def test_an_auth_failure_disables_the_adapter() -> None:
    """One message about a broken credential, not one per candidate."""
    drive = fake(list_script=Script([403]))
    reader = source(drive)

    with pytest.raises(SourceUnavailable):
        reader.list_candidates()

    assert reader.healthy() is False


def test_a_rate_limit_does_not_disable_the_adapter() -> None:
    drive = fake(list_script=Script([429] * 5))
    reader = source(drive)

    with pytest.raises(SourceUnavailable):
        reader.list_candidates()

    assert reader.healthy() is True


# --- truncation -------------------------------------------------------------------------


def test_a_partial_download_is_refused() -> None:
    """A truncated CV that reached extraction would be assessed as though it
    were the whole document, and the candidate judged on the half that arrived."""
    drive = fake(truncate_to=100)
    ref = source(drive).list_candidates()[0].documents[0]

    with pytest.raises(SourceUnavailable) as raised:
        source(drive).fetch(ref)

    assert "incomplete" in str(raised.value)


def test_a_partial_download_is_retried_first() -> None:
    """Usually a dropped connection, and usually fine next time."""
    drive = fake(truncate_to=100)
    ref = source(drive).list_candidates()[0].documents[0]
    reader = source(drive)

    with pytest.raises(SourceUnavailable):
        reader.fetch(ref)

    assert drive.download_count == 3


# --- what a recruiter reads ---------------------------------------------------------------


def test_an_unconfigured_folder_says_what_to_do() -> None:
    reader = DriveSource(FakeDrive(), folder_id="", sleep=no_sleep)

    with pytest.raises(SourceUnavailable) as raised:
        reader.list_candidates()

    assert "DRIVE_FOLDER_ID" in str(raised.value)
    assert "upload files directly" in str(raised.value)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (403, "credentials"),
        (404, "DRIVE_FOLDER_ID"),
        (429, "fewer requests"),
        (500, "had a problem"),
    ],
)
def test_each_failure_reads_as_a_sentence(status: int, expected: str) -> None:
    """Never a status line. A recruiter reading "HTTP 403" learns nothing they
    can act on."""
    drive = fake(list_script=Script([status] * 5))

    with pytest.raises(SourceUnavailable) as raised:
        source(drive).list_candidates()

    message = str(raised.value)
    assert expected in message
    assert str(status) not in message


# --- the scope ------------------------------------------------------------------------------


def test_the_scope_is_read_only() -> None:
    """The security control. A credential that cannot write cannot delete a
    recruiter's drive however wrong the code is."""
    assert SCOPE.endswith("drive.readonly")


def test_the_transport_has_no_write_method() -> None:
    """Narrow by construction rather than by discipline."""
    from infrastructure.integrations.drive import DriveTransport

    methods = {name for name in dir(DriveTransport) if not name.startswith("_")}

    assert methods == {"list_files", "download"}


# --- grouping ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("ana-cv.pdf", "ana"),
        ("ana_letter.docx", "ana"),
        ("ben cv.pdf", "ben"),
        ("chidi.pdf", "chidi"),
        ("", "candidate"),
    ],
)
def test_the_candidate_is_guessed_from_the_filename(filename: str, expected: str) -> None:
    """A guess, and the interface shows it as one so a recruiter can correct it."""
    assert candidate_id_for(filename) == expected
