"""Appending to a spreadsheet, once.

The behaviour worth testing hardest is duplicate suppression. A retry after a
lost response is the ordinary case — the append worked and the reply never
arrived — and without the pre-read the recruiter gets two rows for one decision
and has to work out which is real.

The other thing tested here is what a row does not contain. A spreadsheet full
of quotations from candidates' CVs is a copy of their documents under different
access controls, and the sheet is the artefact most likely to be shared.
"""

from __future__ import annotations

import pytest

from domain.ports.sinks import AdapterError, DeliveryPayload
from infrastructure.integrations.sheets import (
    HEADER,
    SCOPE,
    SheetsSink,
    SheetsTransportError,
    row_for,
)
from tests.fakes.fake_transports import FakeSheets, Script, no_sleep

PAYLOAD = DeliveryPayload(
    run_id="01a08a99",
    candidate_id="cand-0007",
    role_id="ai-engineer",
    band="advance",
    score=0.82,
    coverage=0.91,
    derivation=("Seven of nine points were assessable.", "The score is 0.82."),
    reviewer_id="rec-014",
    reviewer_action="approve",
    decided_at="2026-03-04T10:00:00+00:00",
    criterion_states={"python-depth": "met"},
    override_count=1,
    integrity_tier="clean",
    information_requests=("Could you tell us about your evaluation work?",),
)


def sink(sheets: FakeSheets) -> SheetsSink:
    return SheetsSink(sheets, spreadsheet_id="sheet-1", qps=0, sleep=no_sleep)


# --- appending -------------------------------------------------------------------


def test_a_package_is_appended() -> None:
    sheets = FakeSheets()

    result = sink(sheets).deliver(PAYLOAD)

    assert result.ok
    assert sheets.rows[0] == list(HEADER)
    assert len(sheets.data_rows) == 1
    assert sheets.data_rows[0][0] == "01a08a99"


def test_a_batch_is_one_call() -> None:
    """Twenty candidates means one request. The difference between a batch that
    finishes and one that spends the afternoon being rate-limited."""
    sheets = FakeSheets()
    payloads = [
        DeliveryPayload(**{**PAYLOAD.__dict__, "run_id": f"run-{index}"}) for index in range(20)
    ]

    result = sink(sheets).deliver_batch(payloads)

    assert result.ok
    assert sheets.append_count == 1
    assert len(sheets.data_rows) == 20


def test_the_written_range_is_reported() -> None:
    """So a reviewer can be told where the result went."""
    result = sink(FakeSheets()).deliver(PAYLOAD)

    assert result.external_ref.startswith("Sheet1!A")


def test_an_empty_batch_is_not_a_call() -> None:
    sheets = FakeSheets()

    assert sink(sheets).deliver_batch([]).ok
    assert sheets.append_count == 0


# --- the header -----------------------------------------------------------------------


def test_an_empty_sheet_gets_a_header_and_is_formatted_once() -> None:
    """What a recruiter opens is a table with labelled columns, frozen and
    wrapped. The second delivery finds the header and adds nothing but rows."""
    sheets = FakeSheets()
    sink(sheets).deliver(PAYLOAD)
    second = DeliveryPayload(**{**PAYLOAD.__dict__, "run_id": "run-b"})

    sink(sheets).deliver(second)

    assert sheets.rows[0] == list(HEADER)
    assert [row[0] for row in sheets.data_rows] == ["01a08a99", "run-b"]
    assert sheets.calls.count("format") == 1


def test_a_sheet_that_already_has_rows_gets_no_second_header() -> None:
    sheets = FakeSheets(rows=[list(HEADER), ["run-old", "x", "ai-engineer"]])

    sink(sheets).deliver(PAYLOAD)

    assert sum(1 for row in sheets.rows if row == list(HEADER)) == 1
    assert "format" not in sheets.calls


def test_a_formatting_failure_is_not_a_delivery_failure() -> None:
    """The rows are there. Bold headers are not what a delivery is."""

    class Unformattable(FakeSheets):
        def format_sheet(self, spreadsheet_id: str, widths: tuple[int, ...]) -> None:
            raise SheetsTransportError(500, "formatting is down")

    sheets = Unformattable()

    result = sink(sheets).deliver(PAYLOAD)

    assert result.ok
    assert result.detail["formatted"] is False
    assert len(sheets.data_rows) == 1


# --- duplicate suppression ----------------------------------------------------------


def test_the_same_run_is_not_appended_twice() -> None:
    """The headline. A retry after a lost response must not double the row."""
    sheets = FakeSheets()
    sink(sheets).deliver(PAYLOAD)

    sink(sheets).deliver(PAYLOAD)

    assert len(sheets.data_rows) == 1


def test_a_repeat_reports_success() -> None:
    """The rows the caller wanted are in the sheet. That is what success means,
    and failing here would make a retry loop forever."""
    sheets = FakeSheets()
    sink(sheets).deliver(PAYLOAD)

    result = sink(sheets).deliver(PAYLOAD)

    assert result.ok
    assert result.detail["appended"] == 0
    assert result.detail["skipped"] == 1


def test_a_partial_repeat_appends_only_what_is_missing() -> None:
    """A batch where half landed before the connection dropped."""
    sheets = FakeSheets()
    first = DeliveryPayload(**{**PAYLOAD.__dict__, "run_id": "run-a"})
    second = DeliveryPayload(**{**PAYLOAD.__dict__, "run_id": "run-b"})
    sink(sheets).deliver(first)

    result = sink(sheets).deliver_batch([first, second])

    assert len(sheets.data_rows) == 2
    assert result.detail["appended"] == 1
    assert result.detail["skipped"] == 1


def test_the_key_column_is_read_before_appending() -> None:
    sheets = FakeSheets()

    sink(sheets).deliver(PAYLOAD)

    assert sheets.calls[0] == "read"


# --- failures -------------------------------------------------------------------------


def test_a_rate_limit_is_retried() -> None:
    sheets = FakeSheets(append_script=Script([429, 429]))

    assert sink(sheets).deliver(PAYLOAD).ok
    assert sheets.append_count == 3


@pytest.mark.parametrize("status", [401, 403])
def test_an_auth_failure_is_not_retried(status: int) -> None:
    sheets = FakeSheets(append_script=Script([status] * 5))

    result = sink(sheets).deliver(PAYLOAD)

    assert result.error_code is AdapterError.AUTH
    assert sheets.append_count == 1


def test_an_auth_failure_disables_the_sink() -> None:
    sheets = FakeSheets(append_script=Script([403]))
    writer = sink(sheets)

    writer.deliver(PAYLOAD)

    assert writer.healthy() is False


def test_a_permanent_failure_is_reported_not_raised() -> None:
    """A sink that raised would take down a delivery other sinks completed."""
    sheets = FakeSheets(append_script=Script([400] * 5))

    result = sink(sheets).deliver(PAYLOAD)

    assert result.ok is False
    assert result.error_code is AdapterError.PERMANENT


def test_an_unconfigured_spreadsheet_says_what_to_do() -> None:
    writer = SheetsSink(FakeSheets(), spreadsheet_id="", sleep=no_sleep)

    result = writer.deliver(PAYLOAD)

    assert result.error_code is AdapterError.CONFIG
    assert "SHEETS_SPREADSHEET_ID" in result.message
    assert writer.healthy() is False


def test_a_failure_reads_as_a_sentence() -> None:
    sheets = FakeSheets(append_script=Script([403] * 5))

    message = sink(sheets).deliver(PAYLOAD).message

    assert "credentials" in message
    assert "403" not in message


# --- what a row contains ------------------------------------------------------------------


def test_a_row_matches_the_header() -> None:
    """A field added upstream must not shift the columns of a sheet somebody has
    already built filters on."""
    assert len(row_for(PAYLOAD)) == len(HEADER)


def test_a_row_carries_the_reasoning() -> None:
    """The auditable part. A band with no derivation beside it is an opinion."""
    assert "Seven of nine points" in " ".join(row_for(PAYLOAD))


def test_a_row_carries_no_quotation() -> None:
    """The evidence stays in the system. A spreadsheet of candidates' own words
    is a copy of their CVs under different access controls."""
    row = " ".join(row_for(PAYLOAD))

    assert "Could you tell us" not in row
    assert "verbatim" not in row


def test_an_absent_score_is_blank_rather_than_zero() -> None:
    """A blank cell reads as "no score". A zero reads as "scored nothing", and
    those are different candidates."""
    unscored = DeliveryPayload(**{**PAYLOAD.__dict__, "score": None})

    assert row_for(unscored)[4] == ""


def test_the_run_id_is_first() -> None:
    """Column A is what duplicate suppression reads."""
    assert HEADER[0] == "Run id"
    assert row_for(PAYLOAD)[0] == PAYLOAD.run_id


def test_the_scope_is_declared() -> None:
    assert "spreadsheets" in SCOPE
