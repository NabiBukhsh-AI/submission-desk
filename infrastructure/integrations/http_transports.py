"""The HTTP calls behind the Drive source, the Sheets sink and the Slack notifier.

Each adapter above this module speaks to a two- or one-method transport
protocol and is tested against a fake. These are the real ones: standard
library HTTP, the documented REST shapes, and every non-2xx answer raised as
the adapter's own error type carrying the status, so the shared classifier
decides what happens next (retry, wait, disable, give up).

Nothing here retries or sleeps; that is the adapter's job. Nothing here logs
a token.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from infrastructure.integrations.drive import DriveTransportError
from infrastructure.integrations.google_auth import GoogleAuthError, ServiceAccount
from infrastructure.integrations.sheets import SheetsTransportError
from infrastructure.integrations.slack import SlackTransportError

DRIVE_API = "https://www.googleapis.com/drive/v3"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
SLACK_API = "https://slack.com/api/chat.postMessage"
TIMEOUT_SECONDS = 60

#: Slack answers HTTP 200 with ``ok: false`` and a reason. The reasons that
#: mean "fix the configuration" map to the statuses the classifier already
#: understands; anything else is permanent.
SLACK_ERROR_STATUS = {
    "invalid_auth": 401,
    "not_authed": 401,
    "token_revoked": 401,
    "account_inactive": 401,
    "missing_scope": 403,
    "not_in_channel": 403,
    "channel_not_found": 404,
    "ratelimited": 429,
}


def _read(request: urllib.request.Request, urlopen: Any) -> tuple[bytes, dict[str, str]]:
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read(), {k.lower(): v for k, v in response.headers.items()}


def _json(data: bytes) -> dict[str, Any]:
    parsed = json.loads(data.decode("utf-8")) if data else {}
    return parsed if isinstance(parsed, dict) else {}


class DriveHttpTransport:
    """``files.list`` on one folder and ``files.get?alt=media`` for the bytes."""

    def __init__(self, account: ServiceAccount, *, urlopen: Any = None) -> None:
        self.account = account
        self._urlopen = urlopen or urllib.request.urlopen

    def list_files(self, folder_id: str) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        page_token = ""
        while True:
            query = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "nextPageToken, files(id, name, size, mimeType)",
                "pageSize": "1000",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                query["pageToken"] = page_token
            body = _json(self._get(f"{DRIVE_API}/files?{urllib.parse.urlencode(query)}")[0])
            files.extend(item for item in body.get("files", []) if isinstance(item, dict))
            page_token = str(body.get("nextPageToken") or "")
            if not page_token:
                return files

    def download(self, file_id: str) -> tuple[bytes, int | None]:
        data, headers = self._get(
            f"{DRIVE_API}/files/{urllib.parse.quote(file_id)}?alt=media&supportsAllDrives=true"
        )
        length = headers.get("content-length")
        return data, int(length) if length and length.isdigit() else None

    def _get(self, url: str) -> tuple[bytes, dict[str, str]]:
        try:
            token = self.account.token()
        except GoogleAuthError as refused:
            raise DriveTransportError(refused.status, str(refused)) from refused
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            return _read(request, self._urlopen)
        except urllib.error.HTTPError as error:
            raise DriveTransportError(error.code, _google_detail(error)) from error
        except urllib.error.URLError as error:
            raise DriveTransportError(503, "Google Drive could not be reached.") from error


class SheetsHttpTransport:
    """``values.get`` on one column, ``values.append`` for the rows, and one
    ``batchUpdate`` to format the header when the sheet was empty."""

    def __init__(self, account: ServiceAccount, *, urlopen: Any = None) -> None:
        self.account = account
        self._urlopen = urlopen or urllib.request.urlopen

    def read_column(self, spreadsheet_id: str, column: str) -> list[str]:
        span = urllib.parse.quote(f"{column}:{column}")
        body = _json(self._call(f"{SHEETS_API}/{spreadsheet_id}/values/{span}"))
        return [str(row[0]) for row in body.get("values", []) if row]

    def append_rows(self, spreadsheet_id: str, rows: list[list[str]]) -> str:
        query = urllib.parse.urlencode(
            {"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"}
        )
        body = _json(
            self._call(
                f"{SHEETS_API}/{spreadsheet_id}/values/A1:append?{query}",
                payload={"values": rows},
            )
        )
        updates = body.get("updates") or {}
        return str(updates.get("updatedRange") or spreadsheet_id)

    def format_sheet(self, spreadsheet_id: str, widths: tuple[int, ...]) -> None:
        """Bold frozen header, wrapped text everywhere, a width per column."""
        meta = _json(self._call(f"{SHEETS_API}/{spreadsheet_id}?fields=sheets.properties.sheetId"))
        sheets = meta.get("sheets") or [{}]
        sheet_id = int((sheets[0].get("properties") or {}).get("sheetId", 0))
        requests: list[dict[str, Any]] = [
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                    "fields": "gridProperties.frozenRowCount",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id},
                    "cell": {
                        "userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}
                    },
                    "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True},
                            "backgroundColor": {"red": 0.93, "green": 0.94, "blue": 0.96},
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,backgroundColor)",
                }
            },
            *(
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": index,
                            "endIndex": index + 1,
                        },
                        "properties": {"pixelSize": width},
                        "fields": "pixelSize",
                    }
                }
                for index, width in enumerate(widths)
            ),
        ]
        self._call(f"{SHEETS_API}/{spreadsheet_id}:batchUpdate", payload={"requests": requests})

    def _call(self, url: str, payload: dict[str, Any] | None = None) -> bytes:
        try:
            token = self.account.token()
        except GoogleAuthError as refused:
            raise SheetsTransportError(refused.status, str(refused)) from refused
        headers = {"Authorization": f"Bearer {token}"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, headers=headers, method="POST" if data else "GET"
        )
        try:
            return _read(request, self._urlopen)[0]
        except urllib.error.HTTPError as error:
            raise SheetsTransportError(error.code, _google_detail(error)) from error
        except urllib.error.URLError as error:
            raise SheetsTransportError(503, "Google Sheets could not be reached.") from error


class SlackHttpTransport:
    """``chat.postMessage`` with a bot token. One call is the whole integration."""

    def __init__(self, token: str, *, urlopen: Any = None) -> None:
        self._token = token
        self._urlopen = urlopen or urllib.request.urlopen

    def post(self, channel: str, text: str, payload: dict[str, Any]) -> str:
        body = json.dumps({"channel": channel, "text": text, "unfurl_links": False}).encode()
        request = urllib.request.Request(
            SLACK_API,
            data=body,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            answer = _json(_read(request, self._urlopen)[0])
        except urllib.error.HTTPError as error:
            raise SlackTransportError(error.code, f"Slack answered HTTP {error.code}.") from error
        except urllib.error.URLError as error:
            raise SlackTransportError(503, "Slack could not be reached.") from error
        if not answer.get("ok"):
            reason = str(answer.get("error") or "unknown_error")
            raise SlackTransportError(SLACK_ERROR_STATUS.get(reason, 400), f"Slack said: {reason}.")
        return str(answer.get("ts") or "")


def _google_detail(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read().decode("utf-8"))
        return str((body.get("error") or {}).get("message") or "")[:200] or f"HTTP {error.code}"
    except Exception:
        return f"HTTP {error.code}"
