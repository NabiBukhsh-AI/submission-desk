"""Migrations, the blob store, and the two habits that keep storage safe.

The SQL-safety test and the traversal test are the security half of this phase.
Neither is about a bug that exists; both are about a habit that erodes, which is
why they are executable rather than written down in a style guide.
"""

from __future__ import annotations

import ast
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from infrastructure.storage.blobs import BlobStore, BlobStoreError
from infrastructure.storage.sqlite.connection import (
    AdvisoryLock,
    DatabaseBusy,
    close_thread_connection,
    connect,
)
from infrastructure.storage.sqlite.schema import (
    MigrationFailed,
    applied_versions,
    current_version,
    discover,
    migrate,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
STORAGE_DIR = REPO_ROOT / "infrastructure" / "storage"

EXPECTED_TABLES = {
    "schema_version",
    "runs",
    "documents",
    "source_texts",
    "evidence",
    "assessments",
    "reviews",
    "overrides",
    "llm_calls",
    "deliveries",
    "calibration_cards",
    "run_events",
    "llm_cache",
    "errors",
}


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "migrate.sqlite"
    yield path
    close_thread_connection(path)


# --- applying ----------------------------------------------------------------


def test_migration_applies_from_an_empty_file(db: Path) -> None:
    assert migrate(db) == [1]
    assert current_version(db) == 1


def test_migration_is_idempotent(db: Path) -> None:
    """`make setup` runs this, and people run `make setup` twice."""
    migrate(db)
    assert migrate(db) == []
    assert current_version(db) == 1


def test_every_expected_table_exists(db: Path) -> None:
    migrate(db)
    rows = connect(db).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {row["name"] for row in rows}

    assert names >= EXPECTED_TABLES


def test_the_indices_the_queries_need_exist(db: Path) -> None:
    migrate(db)
    rows = connect(db).execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    names = {row["name"] for row in rows}

    for expected in (
        "idx_runs_status",
        "idx_runs_role_started",
        "idx_evidence_run_crit",
        "idx_llm_calls_run",
        "idx_calibration_lookup",
        "idx_run_events_run_seq",
    ):
        assert expected in names


def test_an_empty_database_reports_version_zero(db: Path) -> None:
    assert current_version(db) == 0
    assert applied_versions(connect(db)) == set()


def test_the_pragmas_are_applied(db: Path) -> None:
    """Foreign keys off by default is foreign keys off."""
    migrate(db)
    connection = connect(db)

    assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_foreign_keys_are_enforced(db: Path) -> None:
    migrate(db)
    with pytest.raises(sqlite3.IntegrityError):
        connect(db).execute(
            "INSERT INTO evidence (evidence_id, run_id, criterion_id, state, claim, "
            "confidence, model_tier, prompt_version, escalation_state, span_validation) "
            "VALUES ('e', 'no-such-run', 'c', 'supported', 'x', 0.5, 'tier_cheap', 'p', "
            "'not_escalated', 'valid_exact')"
        )


# --- discovery ----------------------------------------------------------------


def test_migrations_are_discovered_in_order() -> None:
    found = discover()
    assert [version for version, _, _ in found] == sorted(version for version, _, _ in found)
    assert found[0][0] == 1


def test_a_badly_named_migration_is_an_error_not_a_skip(tmp_path: Path) -> None:
    """A migration silently ignored because someone wrote 001-initial.sql is a
    schema that differs between two machines for no visible reason."""
    (tmp_path / "001-initial.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(MigrationFailed, match="not a valid migration name"):
        discover(tmp_path)


def test_duplicate_migration_numbers_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "001_second.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(MigrationFailed, match="duplicate migration numbers"):
        discover(tmp_path)


# --- SQL safety ---------------------------------------------------------------


def _sql_building_fstrings(path: Path) -> list[tuple[int, int, str]]:
    """Any f-string or % format whose text is a SQL statement.

    Walks the AST rather than grepping, so a formatted string spanning several
    lines is still caught and a SQL keyword inside a comment is not.

    The pattern requires a statement shape rather than a single keyword. An
    error message reading "if nothing is running, delete the lock file" is
    English, and a check that flags it is a check people learn to silence.
    """
    offenders: list[tuple[int, int, str]] = []
    statement = re.compile(
        r"\bSELECT\b[\s\S]*\bFROM\b"
        r"|\bINSERT\s+INTO\b"
        r"|\bUPDATE\b[\s\S]*\bSET\b"
        r"|\bDELETE\s+FROM\b"
        r"|\bVALUES\s*\(",
        re.IGNORECASE,
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    for node in ast.walk(tree):
        rendered = ""
        if isinstance(node, ast.JoinedStr):
            rendered = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mod)
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
        ):
            rendered = node.left.value

        if rendered and statement.search(rendered):
            start = getattr(node, "lineno", 0)
            offenders.append((start, getattr(node, "end_lineno", None) or start, rendered[:70]))
    return offenders


def test_no_sql_is_built_by_string_formatting() -> None:
    """Parameterised queries only.

    One exception is allowed and is marked in the source: an IN clause whose
    placeholder count comes from len(values), never from the values themselves.
    """
    offenders: list[str] = []
    for path in sorted(STORAGE_DIR.rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for start, end, text in _sql_building_fstrings(path):
            # Wide enough to reach a comment block explaining the exception,
            # since the formatter decides where the statement itself begins.
            window = " ".join(lines[max(0, start - 9) : min(len(lines), end + 1)])
            if "count only" in window:
                continue
            offenders.append(f"{path.name}:{start}: {text!r}")

    assert offenders == [], "SQL built by string formatting:\n" + "\n".join(offenders)


def test_the_in_clause_exception_interpolates_only_placeholders() -> None:
    """The single formatted query builds "?,?,?" from a count. Reading it back
    here keeps the exception honest if someone edits it later."""
    source = (STORAGE_DIR / "sqlite" / "repositories.py").read_text(encoding="utf-8")
    assert 'placeholders = ",".join("?" for _ in statuses)' in source


def test_no_sql_outside_the_storage_package() -> None:
    """A query in a use case or a page is a query nobody can swap out."""
    keywords = re.compile(r"\b(SELECT\s+\*|INSERT\s+INTO|UPDATE\s+\w+\s+SET)\b", re.IGNORECASE)
    offenders = []

    for package in ("domain", "application", "pipeline", "app", "eval"):
        for path in sorted((REPO_ROOT / package).rglob("*.py")):
            if keywords.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []


# --- the blob store -----------------------------------------------------------


def test_a_blob_round_trips(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    digest = store.put(b"a candidate's cv")

    assert store.get(digest) == b"a candidate's cv"
    assert store.exists(digest)


def test_the_same_bytes_are_stored_once(tmp_path: Path) -> None:
    """Re-uploading the same CV is a no-op, not a second copy."""
    store = BlobStore(tmp_path / "blobs")
    first = store.put(b"identical")
    before = store.path_for(first).stat().st_mtime_ns

    second = store.put(b"identical")

    assert first == second
    assert store.path_for(first).stat().st_mtime_ns == before


def test_a_blob_lands_under_its_hash(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    digest = store.put(b"content")

    assert store.path_for(digest).name == digest
    assert store.path_for(digest).parent.name == digest[:2]


@pytest.mark.parametrize(
    "attempt",
    ["../../etc/passwd", "..\\..\\windows\\system32", "resume.pdf", "", "/absolute/path"],
)
def test_a_filename_can_never_become_a_path(tmp_path: Path, attempt: str) -> None:
    """Uploaded filenames are metadata. There is no input to the store that
    produces a path outside it, because the only accepted input is a hash."""
    store = BlobStore(tmp_path / "blobs")

    with pytest.raises(BlobStoreError, match="64 lowercase hex characters"):
        store.path_for(attempt)


def test_an_uppercase_hash_is_rejected(tmp_path: Path) -> None:
    """Two spellings of one address would store one document twice."""
    store = BlobStore(tmp_path / "blobs")
    with pytest.raises(BlobStoreError):
        store.path_for("A" * 64)


def test_reading_a_missing_blob_says_so(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    with pytest.raises(BlobStoreError, match="no document stored"):
        store.get("f" * 64)


def test_a_partial_write_leaves_nothing_behind(tmp_path: Path) -> None:
    """Written beside the target and renamed, so a crash mid-write cannot leave
    a truncated file at an address that claims to be complete."""
    store = BlobStore(tmp_path / "blobs")
    store.put(b"complete")

    assert list((tmp_path / "blobs").rglob("*.partial")) == []


# --- the advisory lock --------------------------------------------------------


def test_a_second_process_is_refused_immediately(tmp_path: Path) -> None:
    """A person staring at a frozen page learns nothing. A person told the
    command line is using the database knows what to do."""
    lock_path = tmp_path / ".lock"

    with (
        AdvisoryLock(lock_path),
        pytest.raises(DatabaseBusy, match="another process is using this database"),
    ):
        AdvisoryLock(lock_path).acquire()


def test_the_lock_is_released_on_exit(tmp_path: Path) -> None:
    lock_path = tmp_path / ".lock"

    with AdvisoryLock(lock_path):
        pass

    AdvisoryLock(lock_path).acquire()
    assert lock_path.exists()


def test_the_lock_names_who_holds_it(tmp_path: Path) -> None:
    """A stale lock file left by a crash is reported with its owner rather than
    silently stolen: breaking someone else's lock is how two writers meet."""
    lock_path = tmp_path / ".lock"
    AdvisoryLock(lock_path).acquire(owner="cli")

    with pytest.raises(DatabaseBusy, match="cli"):
        AdvisoryLock(lock_path).acquire()


def test_the_message_says_how_to_recover(tmp_path: Path) -> None:
    lock_path = tmp_path / ".lock"
    AdvisoryLock(lock_path).acquire()

    with pytest.raises(DatabaseBusy, match="If nothing is running, delete"):
        AdvisoryLock(lock_path).acquire()
