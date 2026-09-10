"""Both sinks, and the redactor in front of them.

The claim is an ordering claim: nothing reaches a file or a table without
passing through the redactor first. An ordering claim cannot be checked by
reading the code, because the chain is assembled at runtime — so this writes a
record carrying a synthetic email and reads both destinations back.

Two sinks rather than one, because they answer different questions. The file is
what an engineer greps; the table is what the operations page queries. Deriving
one from the other would let the number in a report and the line in a file
disagree.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from infrastructure.observability.logging import SECURITY_LOG, configure, get_logger
from infrastructure.storage.sqlite.schema import migrate

EMAIL = "ana.ferreira@example.com"
PHONE = "+44 20 7946 0958"
NAME = "Ana Ferreira"


@pytest.fixture
def sinks(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """A database and a log directory, with the chain pointed at both."""
    db = tmp_path / "logs.sqlite"
    migrate(db)
    directory = tmp_path / "logs"

    configure(db_path=db, log_dir=directory, names=[NAME], log_spans=False, console=False)
    yield db, directory


def files_in(directory: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in directory.glob("*.jsonl"))


def rows_in(db: Path) -> list[sqlite3.Row]:
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute("SELECT * FROM log_events ORDER BY log_id").fetchall()
    finally:
        connection.close()


# --- the ordering claim -----------------------------------------------------------


def test_an_email_reaches_neither_sink(sinks: tuple[Path, Path]) -> None:
    """The headline. Written to both destinations and read back from both."""
    db, directory = sinks

    get_logger("test").info("intake.document_accepted", run_id="r1", contact=EMAIL)

    assert EMAIL not in files_in(directory)
    assert EMAIL not in "".join(row["payload"] for row in rows_in(db))


def test_a_phone_number_reaches_neither_sink(sinks: tuple[Path, Path]) -> None:
    db, directory = sinks

    get_logger("test").info("intake.document_accepted", run_id="r1", contact=PHONE)

    assert "7946" not in files_in(directory)
    assert "7946" not in "".join(row["payload"] for row in rows_in(db))


def test_a_configured_name_reaches_neither_sink(sinks: tuple[Path, Path]) -> None:
    db, directory = sinks

    get_logger("test").info("structure.profile", run_id="r1", candidate=NAME)

    assert NAME not in files_in(directory)
    assert NAME not in "".join(row["payload"] for row in rows_in(db))


def test_a_span_reaches_neither_sink_by_default(sinks: tuple[Path, Path]) -> None:
    """A span is candidate-authored text, so logging one logs part of a CV."""
    db, directory = sinks

    get_logger("test").info(
        "assess.criterion", run_id="r1", verbatim_span="Owned the payments backend"
    )

    assert "payments backend" not in files_in(directory)
    assert "payments backend" not in "".join(row["payload"] for row in rows_in(db))


def test_a_nested_identifier_is_caught(sinks: tuple[Path, Path]) -> None:
    """A log payload is a nested dictionary. A top-level-only rule would miss
    every interesting field."""
    db, directory = sinks

    get_logger("test").info(
        "intake.document_accepted",
        run_id="r1",
        document={"metadata": {"author": EMAIL}},
    )

    assert EMAIL not in files_in(directory)
    assert EMAIL not in "".join(row["payload"] for row in rows_in(db))


# --- what must survive ----------------------------------------------------------------


def test_the_event_name_survives(sinks: tuple[Path, Path]) -> None:
    db, _ = sinks

    get_logger("test").info("assess.criterion", run_id="r1", contact=EMAIL)

    assert rows_in(db)[0]["event"] == "assess.criterion"


def test_the_run_id_survives(sinks: tuple[Path, Path]) -> None:
    """A log line nobody can attribute to a run is a log line nobody can use."""
    db, _ = sinks

    get_logger("test").info("assess.criterion", run_id="01a08a99")

    assert rows_in(db)[0]["run_id"] == "01a08a99"


def test_the_numbers_survive(sinks: tuple[Path, Path]) -> None:
    """Token counts and latencies are what a log line is for."""
    db, _ = sinks

    get_logger("test").info("assess.criterion", run_id="r1", input_tokens=1200, latency_ms=430)

    payload = json.loads(rows_in(db)[0]["payload"])
    assert payload["input_tokens"] == 1200
    assert payload["latency_ms"] == 430


def test_a_timestamp_is_added(sinks: tuple[Path, Path]) -> None:
    db, _ = sinks

    get_logger("test").info("assess.criterion", run_id="r1")

    assert rows_in(db)[0]["occurred_at"]


def test_the_level_is_recorded(sinks: tuple[Path, Path]) -> None:
    db, _ = sinks

    get_logger("test").warning("assess.degraded", run_id="r1")

    assert rows_in(db)[0]["level"] == "warning"


# --- both sinks ---------------------------------------------------------------------


def test_the_same_event_lands_in_both(sinks: tuple[Path, Path]) -> None:
    """Deriving one from the other would let a report and a file disagree."""
    db, directory = sinks

    get_logger("test").info("aggregate.recommendation", run_id="r1", band="advance")

    assert "aggregate.recommendation" in files_in(directory)
    assert len(rows_in(db)) == 1


def test_the_daily_file_is_named_by_date(sinks: tuple[Path, Path]) -> None:
    """The question people ask is "what happened on Tuesday"."""
    _, directory = sinks

    get_logger("test").info("assess.criterion", run_id="r1")

    names = [path.name for path in directory.glob("events-*.jsonl")]
    assert names
    assert names[0].startswith("events-")


def test_each_event_is_one_line(sinks: tuple[Path, Path]) -> None:
    """So the file can be grepped and streamed."""
    _, directory = sinks
    log = get_logger("test")

    log.info("a.one", run_id="r1")
    log.info("a.two", run_id="r1")

    path = next(directory.glob("events-*.jsonl"))
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 2
    for line in lines:
        json.loads(line)


# --- security events -------------------------------------------------------------------


def test_a_security_event_also_goes_to_its_own_file(sinks: tuple[Path, Path]) -> None:
    """So an incident can be read without knowing which day it happened."""
    _, directory = sinks

    get_logger("test").warning("sanitize.document_scanned", run_id="r1", tier="quarantine")

    security = directory / SECURITY_LOG.name
    assert security.exists()
    assert "sanitize.document_scanned" in security.read_text(encoding="utf-8")


def test_an_ordinary_event_does_not(sinks: tuple[Path, Path]) -> None:
    _, directory = sinks

    get_logger("test").info("assess.criterion", run_id="r1")

    security = directory / SECURITY_LOG.name
    assert not security.exists() or "assess.criterion" not in security.read_text(encoding="utf-8")


def test_a_security_event_is_redacted_too(sinks: tuple[Path, Path]) -> None:
    """The file most likely to be shared outside the team."""
    _, directory = sinks

    get_logger("test").warning("sanitize.document_scanned", run_id="r1", contact=EMAIL)

    assert EMAIL not in (directory / SECURITY_LOG.name).read_text(encoding="utf-8")


# --- failure -----------------------------------------------------------------------------


def test_a_logging_failure_does_not_raise(tmp_path: Path) -> None:
    """A full disk must not fail a candidate's assessment. Losing a log line is
    not losing an assessment."""
    configure(
        db_path=tmp_path / "does" / "not" / "exist.sqlite",
        log_dir=tmp_path / "also" / "missing",
        console=False,
    )

    get_logger("test").info("assess.criterion", run_id="r1")
