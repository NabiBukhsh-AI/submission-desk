"""Connections, pragmas, transactions, and the two locks.

SQLite is the whole database. What that buys is zero setup, transactional
integrity, one file to hand over, and a database a reviewer can open with any
browser during handover. What it costs is one writer at a time, which is why
this module exists: the concurrency story has to be explicit rather than hoped
for.

Two locks, for two different races.

The process lock serialises write transactions between threads in this process.
The advisory file lock stops the command line and the interface from writing at
the same time, and fails fast with a sentence a person can act on rather than
blocking until a timeout nobody is watching.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from domain.errors import SubmissionDeskError

#: Serialises writes across threads in this process. Reads run concurrently
#: under WAL and do not take it.
_WRITE_LOCK = threading.Lock()

#: One connection per thread. SQLite connections are not safe to share, and the
#: alternative, a pool, is machinery this workload does not need.
_LOCAL = threading.local()


class DatabaseBusy(SubmissionDeskError):
    """Another process holds the write lock."""

    error_code = "DATABASE_BUSY"
    retryable = True


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open the database for this thread, applying the pragmas that matter.

    ``WAL`` so a long read does not block a write. ``foreign_keys`` because
    referential integrity that is off by default is referential integrity that
    is off. ``synchronous=NORMAL`` because WAL makes FULL redundant for this
    durability requirement, and the write throughput matters more during a batch
    than the last few milliseconds of a power-cut window.
    """
    key = f"conn:{db_path}"
    existing: sqlite3.Connection | None = getattr(_LOCAL, key, None)
    if existing is not None:
        return existing

    path = Path(db_path)
    if path.parent and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA synchronous=NORMAL")

    setattr(_LOCAL, key, connection)
    return connection


def close_thread_connection(db_path: Path | str) -> None:
    """Release this thread's connection, for tests and for shutdown."""
    key = f"conn:{db_path}"
    connection = getattr(_LOCAL, key, None)
    if connection is not None:
        connection.close()
        delattr(_LOCAL, key)


@contextmanager
def write_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One transaction, one lock, all or nothing.

    The runner's transaction boundary is a single node, so a crash leaves the
    database at a node boundary and the run resumes from there. That is the
    whole resumability story, and it depends on this being atomic.
    """
    with _WRITE_LOCK:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")


class AdvisoryLock:
    """A lock file that stops two processes writing at once.

    Exclusive creation is the whole mechanism: the filesystem decides the winner,
    which is enough for one recruiter running one command line beside one
    interface. It fails immediately rather than waiting, because a person
    staring at a frozen page learns nothing, and a person told "the command line
    is using the database" knows exactly what to do.

    A lock left behind by a crash is reported with its owner's process id rather
    than silently stolen: breaking someone else's lock is how two writers end up
    in the same file.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._acquired = False

    def acquire(self, *, owner: str = "submission-desk") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            held_by = self._read_holder()
            raise DatabaseBusy(
                f"another process is using this database ({held_by}). "
                f"Close it and try again. If nothing is running, delete {self.path}."
            ) from error

        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(f"{owner} pid={os.getpid()}\n")
        self._acquired = True

    def release(self) -> None:
        if self._acquired and self.path.exists():
            self.path.unlink()
        self._acquired = False

    def _read_holder(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8").strip() or "unknown"
        except OSError:
            return "unknown"

    def __enter__(self) -> AdvisoryLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
