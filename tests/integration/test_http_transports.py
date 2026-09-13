"""The real transports behind Drive, Sheets and Slack, driven with no network.

What is asserted: the service-account JWT is what Google's token endpoint
accepts (signed with the key, verifiable with its public half, the documented
claims); each transport sends the documented request and reads the documented
answer; every refusal becomes the adapter's own error carrying a status the
shared classifier understands; and the factory wires each integration only
when its settings are present.

check_secrets: planted-shapes. The key below is generated in the test and
signs nothing real.
check_pii: invented-identifiers. The service-account address has to look like
one for the claims to be the documented shape; the project does not exist.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from infrastructure.factory import build_deps, settings_from_env
from infrastructure.integrations import google_auth
from infrastructure.integrations.drive import DriveSource, DriveTransportError
from infrastructure.integrations.http_transports import (
    DriveHttpTransport,
    SheetsHttpTransport,
    SlackHttpTransport,
)
from infrastructure.integrations.sheets import SheetsSink, SheetsTransportError
from infrastructure.integrations.slack import SlackNotifier, SlackTransportError
from infrastructure.storage.sqlite.connection import close_thread_connection


@pytest.fixture(scope="module")
def key_pair() -> tuple[rsa.RSAPrivateKey, str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return private, pem


@pytest.fixture
def key_file(tmp_path: Path, key_pair: tuple[rsa.RSAPrivateKey, str]) -> Path:
    path = tmp_path / "service-account.json"
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "client_email": "desk@example-project.iam.gserviceaccount.com",
                "private_key": key_pair[1],
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
        encoding="utf-8",
    )
    return path


class _Http:
    """A scripted urlopen: each call pops the next (status, body, headers)."""

    def __init__(self, *responses: tuple[int, Any]) -> None:
        self.responses = list(responses)
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float) -> Any:
        self.requests.append(request)
        status, payload = self.responses.pop(0)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "err", {}, io.BytesIO(raw))
        return _Response(raw)


class _Response:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.headers = {"Content-Length": str(len(raw))}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.raw


def _decode(segment: str) -> dict[str, Any]:
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


# --- the credential --------------------------------------------------------------------------


def test_the_assertion_is_signed_with_the_key_and_carries_the_documented_claims(
    key_file: Path, key_pair: tuple[rsa.RSAPrivateKey, str]
) -> None:
    key = json.loads(key_file.read_text(encoding="utf-8"))

    token = google_auth.signed_assertion(
        key, "https://www.googleapis.com/auth/drive.readonly", now=1_000
    )

    header, claims, signature = token.split(".")
    assert _decode(header) == {"alg": "RS256", "typ": "JWT"}
    assert _decode(claims) == {
        "iss": "desk@example-project.iam.gserviceaccount.com",
        "scope": "https://www.googleapis.com/auth/drive.readonly",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": 1000,
        "exp": 4600,
    }
    key_pair[0].public_key().verify(
        base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)),
        f"{header}.{claims}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_the_token_is_exchanged_once_and_reused_until_it_nears_expiry(key_file: Path) -> None:
    http = _Http((200, {"access_token": "ya29.first", "expires_in": 3600}))
    account = google_auth.ServiceAccount(key_file, "scope", urlopen=http)

    assert account.token(now=0) == "ya29.first"
    assert account.token(now=3000) == "ya29.first"
    assert len(http.requests) == 1
    body = http.requests[0].data.decode()
    assert "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer" in body
    assert "assertion=" in body


def test_a_refused_credential_is_a_sentence_with_a_status(key_file: Path) -> None:
    http = _Http((400, {"error": "invalid_grant", "error_description": "Invalid JWT Signature."}))
    account = google_auth.ServiceAccount(key_file, "scope", urlopen=http)

    with pytest.raises(google_auth.GoogleAuthError, match="Invalid JWT Signature") as refused:
        account.token()
    assert refused.value.status == 400


def test_a_missing_key_file_says_which_variable(tmp_path: Path) -> None:
    account = google_auth.ServiceAccount(tmp_path / "nope.json", "scope")

    with pytest.raises(google_auth.GoogleAuthError, match="GOOGLE_APPLICATION_CREDENTIALS"):
        account.token()


# --- Drive -------------------------------------------------------------------------------------


def _account(key_file: Path, http: _Http) -> google_auth.ServiceAccount:
    account = google_auth.ServiceAccount(key_file, "scope", urlopen=http)
    account._token, account._expires_at = "ya29.test", 10**12
    return account


def test_drive_lists_a_folder_page_by_page_and_downloads_by_id(key_file: Path) -> None:
    http = _Http(
        (
            200,
            {
                "files": [
                    {
                        "id": "f1",
                        "name": "ana-cv.pdf",
                        "size": "1200",
                        "mimeType": "application/pdf",
                    }
                ],
                "nextPageToken": "p2",
            },
        ),
        (
            200,
            {
                "files": [
                    {
                        "id": "f2",
                        "name": "ana-letter.pdf",
                        "size": "300",
                        "mimeType": "application/pdf",
                    }
                ]
            },
        ),
        (200, b"%PDF-1.4 bytes"),
    )
    transport = DriveHttpTransport(_account(key_file, http), urlopen=http)

    files = transport.list_files("folder-1")
    data, expected = transport.download("f1")

    assert [item["name"] for item in files] == ["ana-cv.pdf", "ana-letter.pdf"]
    first, second, third = http.requests
    assert "%27folder-1%27+in+parents" in first.full_url and "trashed" in first.full_url
    assert "pageToken=p2" in second.full_url
    assert third.full_url.endswith("/files/f1?alt=media&supportsAllDrives=true")
    assert third.get_header("Authorization") == "Bearer ya29.test"
    assert data == b"%PDF-1.4 bytes" and expected == len(data)


@pytest.mark.parametrize(
    ("status", "fragment"), [(403, "shared"), (404, "folder"), (429, "fewer"), (503, "retry")]
)
def test_drive_refusals_reach_the_adapter_as_sentences(
    key_file: Path, status: int, fragment: str
) -> None:
    # Three of each: retryable refusals are asked again before they are reported.
    http = _Http(*[(status, {"error": {"message": "nope"}})] * 3)
    source = DriveSource(
        DriveHttpTransport(_account(key_file, http), urlopen=http),
        folder_id="folder-1",
        sleep=lambda _s: None,
    )

    with pytest.raises(Exception, match=fragment):
        source.list_candidates()


def test_a_bad_credential_reaches_the_adapter_as_an_auth_failure(key_file: Path) -> None:
    http = _Http((400, {"error": "invalid_grant"}))
    account = google_auth.ServiceAccount(key_file, "scope", urlopen=http)
    transport = DriveHttpTransport(account, urlopen=http)

    with pytest.raises(DriveTransportError) as refused:
        transport.list_files("folder-1")
    assert refused.value.status == 400


# --- Sheets ------------------------------------------------------------------------------------


def test_sheets_reads_the_key_column_and_appends_rows(key_file: Path) -> None:
    http = _Http(
        (200, {"values": [["run_id"], ["01a0-existing"]]}),
        (200, {"updates": {"updatedRange": "Sheet1!A3:L3"}}),
    )
    transport = SheetsHttpTransport(_account(key_file, http), urlopen=http)

    keys = transport.read_column("sheet-1", "A")
    written = transport.append_rows("sheet-1", [["01a0-new", "ana", "ai-engineer"]])

    assert keys == ["run_id", "01a0-existing"]
    assert written == "Sheet1!A3:L3"
    read, append = http.requests
    assert read.full_url.endswith("/spreadsheets/sheet-1/values/A%3AA")
    assert append.full_url.endswith(
        "/values/A1:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS"
    )
    assert json.loads(append.data) == {"values": [["01a0-new", "ana", "ai-engineer"]]}


def test_a_sheets_refusal_carries_its_status(key_file: Path) -> None:
    http = _Http((403, {"error": {"message": "The caller does not have permission"}}))
    transport = SheetsHttpTransport(_account(key_file, http), urlopen=http)

    with pytest.raises(SheetsTransportError, match="permission") as refused:
        transport.read_column("sheet-1", "A")
    assert refused.value.status == 403
    assert SheetsSink(transport, spreadsheet_id="sheet-1").healthy()


# --- Slack -------------------------------------------------------------------------------------


def test_slack_posts_with_the_bot_token_and_returns_the_message_stamp() -> None:
    http = _Http((200, {"ok": True, "ts": "1726234567.000100"}))
    transport = SlackHttpTransport("xoxb-test-token", urlopen=http)

    stamp = transport.post("C0123", "3 candidates are ready to review", {"batch": "b1"})

    assert stamp == "1726234567.000100"
    request = http.requests[0]
    assert request.full_url == "https://slack.com/api/chat.postMessage"
    assert request.get_header("Authorization") == "Bearer xoxb-test-token"
    assert json.loads(request.data)["channel"] == "C0123"


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        ("invalid_auth", 401),
        ("missing_scope", 403),
        ("channel_not_found", 404),
        ("ratelimited", 429),
    ],
)
def test_slack_says_ok_false_and_the_reason_becomes_a_status(reason: str, status: int) -> None:
    http = _Http((200, {"ok": False, "error": reason}))
    transport = SlackHttpTransport("xoxb-test-token", urlopen=http)

    with pytest.raises(SlackTransportError, match=reason) as refused:
        transport.post("C0123", "hello", {})
    assert refused.value.status == status
    assert SlackNotifier(transport, channel="C0123").healthy()


# --- the factory ---------------------------------------------------------------------------------


def _deps(tmp_path: Path, **overrides: object) -> Any:
    settings = settings_from_env(
        db_path=str(tmp_path / "w.sqlite"), blob_dir=str(tmp_path / "blobs"), **overrides
    )
    return build_deps(settings)


def test_nothing_is_wired_without_its_settings(tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    try:
        assert deps.source.source_id == "local"
        assert [sink.sink_id for sink in deps.sinks] == ["csv"]
        assert deps.notifier.notifier_id == "console"
    finally:
        close_thread_connection(deps.settings.db_path)


def test_each_integration_is_wired_when_its_settings_are_present(
    tmp_path: Path, key_file: Path
) -> None:
    deps = _deps(
        tmp_path,
        source_adapter="drive",
        google_credentials_path=str(key_file),
        drive_folder_id="folder-1",
        sheets_spreadsheet_id="sheet-1",
        slack_bot_token="xoxb-test-token",
        slack_channel="C0123",
    )
    try:
        assert deps.source.source_id == "drive" and deps.source.folder_id == "folder-1"
        assert [sink.sink_id for sink in deps.sinks] == ["csv", "sheets"]
        assert deps.notifier.notifier_id == "slack" and deps.notifier.channel == "C0123"
        # Construction touched no network: the account has no token yet.
        assert deps.source.transport.account._token == ""
    finally:
        close_thread_connection(deps.settings.db_path)


def test_demo_mode_wires_none_of_them(tmp_path: Path, key_file: Path) -> None:
    deps = _deps(
        tmp_path,
        demo_mode=True,
        google_credentials_path=str(key_file),
        sheets_spreadsheet_id="sheet-1",
        slack_bot_token="xoxb-test-token",
        slack_channel="C0123",
    )
    try:
        assert deps.sinks == () and deps.notifier is None
    finally:
        close_thread_connection(deps.settings.db_path)
