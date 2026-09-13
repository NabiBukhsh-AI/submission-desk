"""Structured logs, to two places, through one redactor.

Two sinks because they answer different questions. The JSONL file is what an
engineer greps at three in the afternoon when a run went wrong; the SQLite table
is what the operations page queries and what the evaluation aggregates. Writing
to one and deriving the other would mean the number in a report and the line in
a file could disagree.

The redactor is the first processor in the chain. That is the whole security
design: no sink is reachable without passing through it, so a future log call
cannot leak an address by forgetting a convention. A test writes a record
containing a synthetic email and reads both files back to prove the ordering.

No vendor, no collector, no tracing service. The system runs on a laptop with no
account, and an observability platform would make the offline claim false.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from infrastructure.observability.redactor import Redactor

#: Where the day's events go. Dated rather than rotated by size, because the
#: question people ask is "what happened on Tuesday".
LOG_DIR = Path("data/logs")

#: Security findings are appended here as well as to the day's file, so an
#: incident can be read without knowing which day it happened.
SECURITY_LOG = LOG_DIR / "security.jsonl"

#: Events whose name starts with one of these are security events.
SECURITY_PREFIXES = ("sanitize.", "security.", "injection.")


def _log_path(when: datetime | None = None) -> Path:
    stamp = (when or datetime.now(UTC)).strftime("%Y%m%d")
    return LOG_DIR / f"events-{stamp}.jsonl"


class JsonlSink:
    """Append one line per event.

    Opened per write rather than held open. A long-lived handle would be faster
    and would lose the tail of the log whenever a process was killed, which is
    exactly when somebody wants to read it.
    """

    def __init__(self, directory: Path | str = LOG_DIR) -> None:
        self.directory = Path(directory)

    def __call__(self, _logger: Any, _method: str, event: dict[str, Any]) -> dict[str, Any]:
        line = json.dumps(event, default=str, ensure_ascii=False)

        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / _log_path().name).open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

            if str(event.get("event", "")).startswith(SECURITY_PREFIXES):
                with (self.directory / SECURITY_LOG.name).open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError:
            # A full disk must not fail a run. The SQLite sink still has the
            # record, and losing a log line is not losing an assessment.
            pass

        return event


class SqliteSink:
    """Write the same event to the database the operations page reads.

    Best effort for the same reason as the file sink: a logging failure is not a
    reason to fail a candidate's assessment.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def __call__(self, _logger: Any, _method: str, event: dict[str, Any]) -> dict[str, Any]:
        try:
            connection = sqlite3.connect(self.db_path, timeout=5.0)
            with connection:
                connection.execute(
                    """
                    INSERT INTO log_events (occurred_at, level, event, run_id, payload)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(event.get("timestamp") or datetime.now(UTC).isoformat()),
                        str(event.get("level", "info")),
                        str(event.get("event", "")),
                        str(event["run_id"]) if event.get("run_id") else None,
                        json.dumps(event, default=str, ensure_ascii=False),
                    ),
                )
            connection.close()
        except sqlite3.Error:
            pass

        return event


def console_line(_logger: Any, _method: str, event: dict[str, Any]) -> str:
    """One line a person can follow while the system works.

    ``12:26:05  model.call  run=01a099a7 candidate=x tier=tier_cheap ...`` —
    the time, the event, then the fields in the order they were logged. The
    JSONL file has the full record; this is the glance.
    """
    stamp = str(event.pop("timestamp", ""))[11:19]
    name = str(event.pop("event", ""))
    event.pop("level", None)
    fields = " ".join(f"{key}={_short(value)}" for key, value in event.items())
    return f"{stamp}  {name:<16} {fields}"


#: A uuid's first block is enough to tell runs apart on one screen. Anything
#: longer than a line's worth is cut.
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
LINE_CHARS = 80


def _short(value: Any) -> str:
    text = str(value)
    if UUID.fullmatch(text):
        return text[:8]
    return text if len(text) <= LINE_CHARS else text[: LINE_CHARS - 3] + "..."


def drop_output(_logger: Any, _method: str, event: dict[str, Any]) -> str:
    """The last processor. The sinks have already written; nothing goes to stdout.

    Returning an empty string rather than the rendered event keeps the console
    clean during a demo, where a wall of JSON is worse than nothing.
    """
    return ""


def configure(
    *,
    db_path: Path | str | None = None,
    log_dir: Path | str | None = None,
    names: list[str] | None = None,
    log_spans: bool | None = None,
    console: bool | None = None,
) -> None:
    """Build the processor chain, redactor first.

    Called once at startup. Calling it again replaces the chain, which is what
    a test needs and what nothing else should do.
    """
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        # First among the things that touch the payload. Everything below this
        # line sees redacted values and nothing above it writes anywhere.
        Redactor(names=names, log_spans=log_spans),
        # An explicit directory wins; otherwise LOG_DIR from the environment,
        # otherwise the day's file under data/logs.
        JsonlSink(log_dir or os.environ.get("LOG_DIR", "").strip() or LOG_DIR),
    ]

    if db_path is not None:
        processors.append(SqliteSink(db_path))

    show_console = (
        console
        if console is not None
        else os.environ.get("LOG_CONSOLE", "").strip().lower() in ("1", "true", "yes", "on")
    )
    processors.append(console_line if show_console else drop_output)

    structlog.configure(
        processors=processors,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str = "submission_desk") -> Any:
    """A logger. Configures a default chain if nobody has configured one.

    The default has no database sink, because a module-level logger in a test
    should not be writing to whatever database happened to exist.
    """
    if not structlog.is_configured():
        configure()
    return structlog.get_logger(name)
