"""Appending results to a spreadsheet.

Append-only. This adapter never updates or deletes a row, because a recruiter's
spreadsheet is a document people edit by hand, and an integration that rewrites
rows will eventually rewrite one somebody was in the middle of.

One batch call per delivery batch, not one per candidate. Twenty candidates
means one request, which is the difference between a batch that finishes and a
batch that spends its afternoon being rate-limited.

Duplicate suppression is a pre-read of column A. A retry after a partial failure
is the ordinary case — the append succeeded and the response was lost — and
without the check the reviewer gets two rows for one decision and has to work out
which is real. The same pre-read says whether the sheet is empty: the first
delivery into an empty sheet writes the header row first and formats it once —
bold, frozen, wrapped text, readable column widths — so what a recruiter opens
is a table, not a dump.

What goes in a row is narrow on purpose: the outcome and the reasoning, never
the evidence. A spreadsheet full of quotations from candidates' CVs is a copy of
their documents under different access controls.
"""

from __future__ import annotations

import os
import time
from typing import Any, Protocol

from domain.ports.sinks import AdapterError, AdapterResult, DeliveryPayload
from infrastructure.integrations.retrying import TokenBucket, classify_status, with_retries

#: What this adapter asks for. Append and read, on one spreadsheet.
SCOPE = "https://www.googleapis.com/auth/spreadsheets"

DEFAULT_QPS = 2.0

#: The header, in order, as a recruiter reads it. Fixed rather than derived,
#: so a field added upstream does not shift the columns of a sheet somebody has
#: already built filters on.
HEADER: tuple[str, ...] = (
    "Run id",
    "Candidate",
    "Role",
    "Outcome",
    "Score",
    "Coverage",
    "Reviewer",
    "Decision",
    "Decided at",
    "Corrections",
    "Integrity",
    "Reasoning",
)

#: Column widths in pixels, in header order. The reasoning column is wide and
#: wrapped, because it is the one somebody actually reads.
COLUMN_WIDTHS: tuple[int, ...] = (140, 140, 160, 170, 70, 90, 110, 110, 170, 100, 90, 520)

#: Column A holds the run id, and is what a retry reads back to avoid a
#: duplicate row.
KEY_COLUMN = "A"


class SheetsTransportError(Exception):
    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or f"HTTP {status}")
        self.status = status


class SheetsTransport(Protocol):
    """The two calls this adapter makes."""

    def read_column(self, spreadsheet_id: str, column: str) -> list[str]:
        """Everything in one column, for duplicate suppression."""
        ...

    def append_rows(self, spreadsheet_id: str, rows: list[list[str]]) -> str:
        """Append, returning whatever the far side calls the range it wrote."""
        ...

    def format_sheet(self, spreadsheet_id: str, widths: tuple[int, ...]) -> None:
        """Make the first row a header and the table readable. Called once,
        after the header is written; cosmetic, so a failure is not a delivery
        failure."""
        ...


class SheetsSink:
    """Appends one row per delivered package."""

    sink_id = "sheets"

    def __init__(
        self,
        transport: SheetsTransport,
        *,
        spreadsheet_id: str = "",
        qps: float = DEFAULT_QPS,
        sleep: Any = time.sleep,
    ) -> None:
        self.transport = transport
        self.spreadsheet_id = spreadsheet_id or os.environ.get("SHEETS_SPREADSHEET_ID", "")
        self.bucket = TokenBucket(rate_per_second=qps)
        self._sleep = sleep
        self._disabled_reason: str | None = None

    def healthy(self) -> bool:
        return self._disabled_reason is None and bool(self.spreadsheet_id)

    def deliver(self, payload: DeliveryPayload) -> AdapterResult:
        """One package. Uses the batch path with a single row."""
        return self.deliver_batch([payload])

    def deliver_batch(self, payloads: list[DeliveryPayload]) -> AdapterResult:
        """Every package in one call, skipping any already present."""
        if not self.spreadsheet_id:
            return AdapterResult.failed(
                AdapterError.CONFIG,
                "No spreadsheet is configured. Set SHEETS_SPREADSHEET_ID, or "
                "remove sheets from SINK_ADAPTERS.",
            )
        if not payloads:
            return AdapterResult.succeeded("", detail={"appended": 0, "skipped": 0})

        started = time.monotonic()
        existing = with_retries(self._read_keys, sleep=self._sleep)

        if not existing.ok:
            self._maybe_disable(existing)
            return existing

        keys: list[str] = existing.detail.get("keys", [])
        already = set(keys)
        fresh = [payload for payload in payloads if payload.run_id not in already]
        skipped = len(payloads) - len(fresh)

        if not fresh:
            # Everything was already there. A retry after a lost response looks
            # exactly like this, and reporting success is correct: the rows the
            # caller wanted are in the sheet.
            return AdapterResult.succeeded(
                self.spreadsheet_id,
                latency_ms=_elapsed(started),
                detail={"appended": 0, "skipped": skipped},
            )

        rows = [row_for(payload) for payload in fresh]
        # An empty sheet gets its header first, in the same append, so a
        # recruiter never opens a sheet of unlabelled columns.
        empty = not keys
        if empty:
            rows.insert(0, list(HEADER))
        result = with_retries(lambda: self._append(rows), sleep=self._sleep)

        if not result.ok:
            self._maybe_disable(result)
            return result

        formatted = self._format() if empty else None

        return AdapterResult(
            ok=True,
            external_ref=result.external_ref,
            attempts=result.attempts,
            latency_ms=_elapsed(started),
            detail={"appended": len(fresh), "skipped": skipped, "formatted": formatted},
        )

    # --- the two calls -------------------------------------------------------

    def _read_keys(self) -> AdapterResult:
        self.bucket.take(sleep=self._sleep)
        try:
            keys = self.transport.read_column(self.spreadsheet_id, KEY_COLUMN)
        except Exception as error:
            return _failure(error, "the spreadsheet could not be read")
        return AdapterResult.succeeded(self.spreadsheet_id, detail={"keys": keys})

    def _append(self, rows: list[list[str]]) -> AdapterResult:
        self.bucket.take(sleep=self._sleep)
        try:
            written = self.transport.append_rows(self.spreadsheet_id, rows)
        except Exception as error:
            return _failure(error, "the results could not be added to the spreadsheet")
        return AdapterResult.succeeded(written)

    def _format(self) -> bool:
        """Once, when the header is written. The rows are already there; a
        formatting call that fails leaves a plain table, which is still the
        result, so it neither retries nor fails the delivery."""
        self.bucket.take(sleep=self._sleep)
        try:
            self.transport.format_sheet(self.spreadsheet_id, COLUMN_WIDTHS)
        except Exception:
            return False
        return True

    def _maybe_disable(self, result: AdapterResult) -> None:
        if result.error_code and result.error_code.disables_adapter:
            self._disabled_reason = result.message


def row_for(payload: DeliveryPayload) -> list[str]:
    """One package as a row of strings.

    Only fields that survive redaction. No quotations, because a quotation is
    the candidate's own words and a spreadsheet is not where their CV should be
    duplicated.
    """
    return [
        payload.run_id,
        payload.candidate_id,
        payload.role_id,
        payload.band,
        # Empty rather than zero. Blank reads as "no score"; zero reads as
        # "scored nothing", and those are different candidates.
        "" if payload.score is None else f"{payload.score:.2f}",
        f"{payload.coverage:.2f}",
        payload.reviewer_id,
        payload.reviewer_action,
        payload.decided_at,
        str(payload.override_count),
        payload.integrity_tier,
        " | ".join(payload.derivation),
    ]


def _failure(error: Exception, what: str) -> AdapterResult:
    status = getattr(error, "status", None)
    if status is None:
        return AdapterResult.failed(
            AdapterError.TRANSIENT,
            f"Google Sheets could not be reached, so {what}. It may work shortly.",
        )

    code = classify_status(int(status))
    return AdapterResult.failed(code, _message_for(code, what))


def _message_for(code: AdapterError, what: str) -> str:
    return {
        AdapterError.AUTH: (
            f"Access to the spreadsheet was refused, so {what}. The credentials "
            "need renewing, or the sheet has not been shared with this system."
        ),
        AdapterError.CONFIG: (
            f"The configured spreadsheet was not found, so {what}. Check SHEETS_SPREADSHEET_ID."
        ),
        AdapterError.RATE_LIMITED: (
            f"Google Sheets is asking for fewer requests, so {what} yet. "
            "This will retry on its own."
        ),
        AdapterError.TRANSIENT: (
            f"Google Sheets had a problem, so {what}. This will retry on its own."
        ),
        AdapterError.PERMANENT: f"Google Sheets refused the request, so {what}.",
    }[code]


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
