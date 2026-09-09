"""Migrations: numbered SQL files, applied in order, recorded once.

No Alembic. At this size a linear list of files a person can read beats a
generated one, and a migration that is not readable is a migration nobody
reviews.

Applying is idempotent. Running ``migrate`` twice is a no-op, which matters
because ``make setup`` runs it and people run ``make setup`` twice.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from domain.errors import SubmissionDeskError
from infrastructure.storage.sqlite.connection import _WRITE_LOCK, connect

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
FILENAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")


class MigrationFailed(SubmissionDeskError):
    error_code = "MIGRATION_FAILED"


def discover(directory: Path | None = None) -> list[tuple[int, str, Path]]:
    """Every migration on disk, in order.

    A file whose name does not parse is an error rather than a skip: a migration
    silently ignored because someone named it ``001-initial.sql`` is a schema
    that differs between two machines for no visible reason.
    """
    directory = directory or MIGRATIONS_DIR
    found: list[tuple[int, str, Path]] = []

    for path in sorted(directory.glob("*.sql")):
        match = FILENAME.match(path.name)
        if not match:
            raise MigrationFailed(
                f"{path.name} is not a valid migration name; expected NNN_lower_case.sql"
            )
        found.append((int(match.group(1)), match.group(2), path))

    versions = [version for version, _, _ in found]
    duplicates = sorted({v for v in versions if versions.count(v) > 1})
    if duplicates:
        raise MigrationFailed(f"duplicate migration numbers: {duplicates}")

    return found


def applied_versions(connection: sqlite3.Connection) -> set[int]:
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if tables is None:
        return set()
    rows = connection.execute("SELECT version FROM schema_version").fetchall()
    return {int(row["version"]) for row in rows}


def migrate(db_path: Path | str, *, directory: Path | None = None) -> list[int]:
    """Apply every migration not yet recorded. Returns what was applied."""
    connection = connect(db_path)
    already = applied_versions(connection)
    newly: list[int] = []

    for version, name, path in discover(directory):
        if version in already:
            continue

        sql = path.read_text(encoding="utf-8")
        # executescript commits any open transaction before it runs, so a
        # migration cannot be wrapped in one. Every statement is written as
        # CREATE ... IF NOT EXISTS instead, which makes a partially applied
        # migration safe to re-run rather than requiring a rollback that
        # sqlite3 will not give us here.
        try:
            with _WRITE_LOCK:
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_version (version, applied_at, name) VALUES (?, ?, ?)",
                    (version, datetime.now(UTC).isoformat(), name),
                )
        except sqlite3.Error as error:
            raise MigrationFailed(f"migration {version:03d}_{name} failed: {error}") from error

        newly.append(version)

    return newly


def current_version(db_path: Path | str) -> int:
    """The highest applied migration, or 0 for an empty database."""
    applied = applied_versions(connect(db_path))
    return max(applied) if applied else 0
