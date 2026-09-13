"""Fakes for the three outside systems.

Each is scripted: a list of what happens on successive calls, so a test can say
"rate-limited twice, then succeed" in one line and read the retry behaviour off
the result. Recording every call is what lets a test assert that a 403 was not
retried, which is the assertion that matters most.

These are fakes rather than mocks. They behave like the far side — they store
rows, they return ids, they raise the statuses a real service raises — so a test
against one exercises the adapter's logic rather than confirming that a mock
returns what it was told to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from infrastructure.integrations.drive import DriveTransportError
from infrastructure.integrations.sheets import SheetsTransportError
from infrastructure.integrations.slack import SlackTransportError


@dataclass
class Script:
    """What happens on each successive call.

    ``None`` means succeed. An integer means raise that HTTP status. The list is
    consumed; once empty, every call succeeds, so "fail twice then work" is
    ``[429, 429]``.
    """

    statuses: list[int | None] = field(default_factory=list)

    def next(self) -> int | None:
        return self.statuses.pop(0) if self.statuses else None


@dataclass
class FakeDrive:
    """A folder of files, with a scripted failure pattern."""

    files: list[dict[str, Any]] = field(default_factory=list)
    contents: dict[str, bytes] = field(default_factory=dict)
    list_script: Script = field(default_factory=Script)
    download_script: Script = field(default_factory=Script)
    #: Set to return fewer bytes than promised, for the truncation case.
    truncate_to: int | None = None

    calls: list[tuple[str, str]] = field(default_factory=list)

    def list_files(self, folder_id: str) -> list[dict[str, Any]]:
        self.calls.append(("list", folder_id))
        status = self.list_script.next()
        if status is not None:
            raise DriveTransportError(status)
        return list(self.files)

    def download(self, file_id: str) -> tuple[bytes, int | None]:
        self.calls.append(("download", file_id))
        status = self.download_script.next()
        if status is not None:
            raise DriveTransportError(status)

        data = self.contents.get(file_id, b"")
        if self.truncate_to is not None:
            return data[: self.truncate_to], len(data)
        return data, len(data)

    @property
    def download_count(self) -> int:
        return sum(1 for kind, _ in self.calls if kind == "download")

    @property
    def list_count(self) -> int:
        return sum(1 for kind, _ in self.calls if kind == "list")


@dataclass
class FakeSheets:
    """A spreadsheet that remembers its rows."""

    rows: list[list[str]] = field(default_factory=list)
    read_script: Script = field(default_factory=Script)
    append_script: Script = field(default_factory=Script)

    calls: list[str] = field(default_factory=list)

    def read_column(self, spreadsheet_id: str, column: str) -> list[str]:
        self.calls.append("read")
        status = self.read_script.next()
        if status is not None:
            raise SheetsTransportError(status)
        index = ord(column.upper()) - ord("A")
        return [row[index] for row in self.rows if len(row) > index]

    def append_rows(self, spreadsheet_id: str, rows: list[list[str]]) -> str:
        self.calls.append("append")
        status = self.append_script.next()
        if status is not None:
            raise SheetsTransportError(status)

        start = len(self.rows) + 1
        self.rows.extend(rows)
        return f"Sheet1!A{start}:L{len(self.rows)}"

    def format_sheet(self, spreadsheet_id: str, widths: tuple[int, ...]) -> None:
        self.calls.append("format")

    @property
    def append_count(self) -> int:
        return self.calls.count("append")

    @property
    def data_rows(self) -> list[list[str]]:
        """Everything under the header row."""
        return self.rows[1:]


@dataclass
class FakeSlack:
    """A channel that remembers what was posted."""

    posted: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    script: Script = field(default_factory=Script)

    def post(self, channel: str, text: str, payload: dict[str, Any]) -> str:
        status = self.script.next()
        if status is not None:
            raise SlackTransportError(status)

        self.posted.append((channel, text, payload))
        return f"{channel}:{len(self.posted)}"

    @property
    def everything_sent(self) -> str:
        """Every character that reached the channel, for a leak assertion."""
        return " ".join(f"{text} {payload}" for _, text, payload in self.posted)


@dataclass
class RecordingSink:
    """A sink that succeeds, fails, or is unhealthy, on command.

    For the delivery tests, where what matters is what the node does with a
    failure rather than how any particular provider produces one.
    """

    sink_id: str = "recording"
    fail_with: Any = None
    is_healthy: bool = True
    delivered: list[Any] = field(default_factory=list)

    def healthy(self) -> bool:
        return self.is_healthy

    def deliver(self, payload: Any) -> Any:
        from domain.ports.sinks import AdapterResult

        if self.fail_with is not None:
            return AdapterResult.failed(self.fail_with, f"{self.sink_id} was not available.")

        self.delivered.append(payload)
        return AdapterResult.succeeded(f"{self.sink_id}:{len(self.delivered)}")


def no_sleep(_seconds: float) -> None:
    """Backoff, without the wait.

    A test that actually slept through three attempts would be slow enough that
    somebody would eventually delete it, and the retry behaviour would stop
    being tested.
    """
    return None
