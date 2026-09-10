"""Every way an outside system can fail, and what the recruiter is told.

The suite that decides whether this is demoware. A system that works when
everything is available is not the same as one that works, and the difference
shows up on the afternoon a credential expires.

Two properties throughout.

Nothing raises past its adapter. A sink that threw would take down a delivery
other sinks had already completed; a notifier that threw would fail a decision
that was already recorded. Failures are values here, not exceptions.

Every message is written for a recruiter. Not a status code, not a traceback, not
"an error occurred" — a sentence naming what happened and what to do about it.
Somebody reading "HTTP 403" at five in the evening learns nothing they can act
on.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from application.deps import Deps
from domain.ports.notifiers import Notification, NotificationKind
from domain.ports.sinks import AdapterError
from domain.ports.sources import SourceUnavailable
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.integrations.csv_sink import CsvSink
from infrastructure.integrations.drive import DriveSource
from infrastructure.integrations.sheets import SheetsSink
from infrastructure.integrations.slack import SlackNotifier
from infrastructure.storage.sqlite.connection import close_thread_connection
from tests.fakes.fake_transports import FakeDrive, FakeSheets, FakeSlack, Script, no_sleep
from tests.integration.test_sheets_adapter import PAYLOAD

#: Every status an adapter classifies, and what it means for what happens next.
STATUSES = [401, 403, 404, 429, 500, 502, 503, 400, 418]


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "failures.sqlite"), blob_dir=str(tmp_path / "blobs")
    )
    yield build_deps(settings)
    close_thread_connection(settings.db_path)


# --- nothing escapes its adapter -------------------------------------------------


@pytest.mark.parametrize("status", STATUSES)
def test_a_sheets_failure_is_a_value_not_an_exception(status: int) -> None:
    """A sink that raised would take down a delivery other sinks completed."""
    sink = SheetsSink(
        FakeSheets(append_script=Script([status] * 6)),
        spreadsheet_id="sheet-1",
        qps=0,
        sleep=no_sleep,
    )

    result = sink.deliver(PAYLOAD)

    assert result.ok is False
    assert result.error_code is not None


@pytest.mark.parametrize("status", STATUSES)
def test_a_slack_failure_is_a_value_not_an_exception(status: int) -> None:
    """A notifier that raised would fail a decision already recorded, making the
    least important integration the one that decides whether a result survives."""
    notifier = SlackNotifier(
        FakeSlack(script=Script([status] * 6)), channel="#hiring", sleep=no_sleep
    )

    result = notifier.notify(Notification(kind=NotificationKind.BATCH_READY, ready_count=1))

    assert result.ok is False


@pytest.mark.parametrize("status", STATUSES)
def test_a_drive_failure_is_a_source_problem_not_a_crash(status: int) -> None:
    """Drive raises rather than returning a value, because a source that
    returned an empty list on an outage would look like a quiet morning."""
    source = DriveSource(
        FakeDrive(list_script=Script([status] * 6)),
        folder_id="folder-1",
        qps=0,
        sleep=no_sleep,
    )

    with pytest.raises(SourceUnavailable):
        source.list_candidates()


def test_a_transport_raising_something_unexpected_is_still_classified() -> None:
    """A library that raises its own exception type, not the one we defined."""

    class Exploding:
        def read_column(self, *_args, **_kwargs):
            raise RuntimeError("something nobody anticipated")

        def append_rows(self, *_args, **_kwargs):
            raise RuntimeError("something nobody anticipated")

    sink = SheetsSink(Exploding(), spreadsheet_id="sheet-1", qps=0, sleep=no_sleep)

    result = sink.deliver(PAYLOAD)

    assert result.ok is False
    assert result.error_code is AdapterError.TRANSIENT


def test_a_disk_failure_is_a_configuration_problem(tmp_path: Path) -> None:
    """The path is wrong or the volume is full. Neither is fixed by trying
    again in thirty seconds."""
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory", encoding="utf-8")

    sink = CsvSink(blocked / "results.csv")
    result = sink.deliver(PAYLOAD)

    assert result.ok is False
    assert result.error_code is AdapterError.CONFIG


# --- what is retried, and what is not ----------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_is_never_retried(status: int) -> None:
    """Asking again does not fix a credential, and repeating the attempt is how
    an account gets locked out during a demo."""
    sheets = FakeSheets(append_script=Script([status] * 6))
    SheetsSink(sheets, spreadsheet_id="s", qps=0, sleep=no_sleep).deliver(PAYLOAD)

    assert sheets.append_count == 1


def test_a_missing_resource_is_never_retried() -> None:
    sheets = FakeSheets(append_script=Script([404] * 6))
    SheetsSink(sheets, spreadsheet_id="s", qps=0, sleep=no_sleep).deliver(PAYLOAD)

    assert sheets.append_count == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_transient_failure_is_retried(status: int) -> None:
    sheets = FakeSheets(append_script=Script([status, status]))

    result = SheetsSink(sheets, spreadsheet_id="s", qps=0, sleep=no_sleep).deliver(PAYLOAD)

    assert result.ok
    assert sheets.append_count == 3


def test_retrying_is_bounded() -> None:
    """Three attempts, then a sentence. Not an infinite loop with a spinner."""
    sheets = FakeSheets(append_script=Script([429] * 10))
    SheetsSink(sheets, spreadsheet_id="s", qps=0, sleep=no_sleep).deliver(PAYLOAD)

    assert sheets.append_count == 3


def test_backoff_grows() -> None:
    """Without growth a retry storm arrives together, and arrives again."""
    import random

    from infrastructure.integrations.retrying import backoff_delay

    # A fixed generator per call, so the growth assertion is about the schedule
    # rather than about which way the jitter happened to fall.
    delays = [backoff_delay(attempt, rng=random.Random(0)) for attempt in range(1, 5)]

    assert delays[1] > delays[0]
    assert delays[2] > delays[1]


def test_backoff_is_capped_after_jitter() -> None:
    """Capping before applying jitter let a delay exceed the ceiling by thirty
    per cent, which is a cap that does not cap."""
    from infrastructure.integrations.retrying import MAX_DELAY_S, backoff_delay

    delays = [backoff_delay(attempt) for _ in range(50) for attempt in range(1, 12)]

    assert all(delay <= MAX_DELAY_S for delay in delays)


def test_backoff_is_jittered() -> None:
    """Twenty candidates hitting a rate limit must not retry in lockstep,
    arrive together, and be rate-limited again."""
    from infrastructure.integrations.retrying import backoff_delay

    delays = {round(backoff_delay(2), 6) for _ in range(20)}

    assert len(delays) > 1


# --- an adapter that has given up --------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403, 404])
def test_an_unfixable_failure_disables_the_adapter(status: int) -> None:
    """One message about a broken credential, not one per candidate."""
    sink = SheetsSink(
        FakeSheets(append_script=Script([status])), spreadsheet_id="s", qps=0, sleep=no_sleep
    )

    sink.deliver(PAYLOAD)

    assert sink.healthy() is False


@pytest.mark.parametrize("status", [429, 500])
def test_a_transient_failure_does_not_disable_the_adapter(status: int) -> None:
    """It will work again in a minute, and disabling it would need a restart."""
    sink = SheetsSink(
        FakeSheets(append_script=Script([status] * 6)),
        spreadsheet_id="s",
        qps=0,
        sleep=no_sleep,
    )

    sink.deliver(PAYLOAD)

    assert sink.healthy() is True


def test_the_error_taxonomy_says_what_happens_next() -> None:
    """Five values because five different things happen. A single "failed"
    would make the retry policy a guess."""
    assert AdapterError.TRANSIENT.retryable
    assert AdapterError.RATE_LIMITED.retryable
    assert not AdapterError.AUTH.retryable
    assert not AdapterError.CONFIG.retryable
    assert not AdapterError.PERMANENT.retryable

    assert AdapterError.AUTH.disables_adapter
    assert AdapterError.CONFIG.disables_adapter
    assert not AdapterError.TRANSIENT.disables_adapter


# --- what a recruiter reads ------------------------------------------------------------------


@pytest.mark.parametrize("status", STATUSES)
def test_no_message_is_a_status_code(status: int) -> None:
    """ "HTTP 403" at five in the evening tells somebody nothing they can act
    on."""
    sink = SheetsSink(
        FakeSheets(append_script=Script([status] * 6)),
        spreadsheet_id="s",
        qps=0,
        sleep=no_sleep,
    )

    message = sink.deliver(PAYLOAD).message

    assert message
    assert str(status) not in message
    assert "HTTP" not in message


@pytest.mark.parametrize("status", STATUSES)
def test_every_message_is_a_sentence(status: int) -> None:
    sink = SheetsSink(
        FakeSheets(append_script=Script([status] * 6)),
        spreadsheet_id="s",
        qps=0,
        sleep=no_sleep,
    )

    message = sink.deliver(PAYLOAD).message

    assert message[0].isupper()
    assert message.rstrip().endswith(".")


def test_a_configuration_failure_names_the_setting() -> None:
    """Somebody has to fix it, and naming the variable is the difference between
    a five-minute fix and an afternoon."""
    sink = SheetsSink(FakeSheets(), spreadsheet_id="", sleep=no_sleep)

    assert "SHEETS_SPREADSHEET_ID" in sink.deliver(PAYLOAD).message


def test_a_drive_configuration_failure_names_the_setting() -> None:
    source = DriveSource(FakeDrive(), folder_id="", sleep=no_sleep)

    with pytest.raises(SourceUnavailable, match="DRIVE_FOLDER_ID"):
        source.list_candidates()


def test_a_slack_configuration_failure_names_the_setting() -> None:
    notifier = SlackNotifier(FakeSlack(), channel="", sleep=no_sleep)

    assert (
        "SLACK_CHANNEL" in notifier.notify(Notification(kind=NotificationKind.BATCH_READY)).message
    )


def test_a_transient_message_says_it_will_retry() -> None:
    """Somebody watching a queue needs to know whether to do something."""
    sink = SheetsSink(
        FakeSheets(append_script=Script([503] * 6)),
        spreadsheet_id="s",
        qps=0,
        sleep=no_sleep,
    )

    assert "retry" in sink.deliver(PAYLOAD).message


def test_an_auth_message_says_what_is_wrong() -> None:
    sink = SheetsSink(
        FakeSheets(append_script=Script([403] * 6)),
        spreadsheet_id="s",
        qps=0,
        sleep=no_sleep,
    )

    message = sink.deliver(PAYLOAD).message

    assert "credentials" in message or "shared" in message


# --- the guarantee ----------------------------------------------------------------------------


def test_the_csv_sink_cannot_report_itself_unhealthy() -> None:
    """A sink that could be skipped is not a guarantee. This one has no
    credentials, no network and no rate limit, which is why it goes first."""
    assert CsvSink(Path("data/deliveries/results.csv")).healthy() is True


def test_the_csv_sink_needs_no_configuration(tmp_path: Path) -> None:
    result = CsvSink(tmp_path / "results.csv").deliver(PAYLOAD)

    assert result.ok


def test_a_rate_limiter_does_not_block_forever() -> None:
    """A token bucket with a zero rate would wait indefinitely, which is worse
    than being a bad client."""
    from infrastructure.integrations.retrying import TokenBucket

    slept: list[float] = []
    TokenBucket(rate_per_second=0).take(sleep=slept.append)

    assert slept == []
