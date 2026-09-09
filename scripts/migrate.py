"""Apply database migrations.

Idempotent: running it twice applies nothing the second time, which matters
because `make setup` runs it and people run `make setup` twice.

Usage:
    python scripts/migrate.py                    # the configured database
    python scripts/migrate.py --db path/to.db    # a specific file
    python scripts/migrate.py --status           # report without changing
"""

from __future__ import annotations

import argparse
from pathlib import Path

from infrastructure.storage.sqlite.schema import current_version, discover, migrate

DEFAULT_DB = Path("data/db/submission_desk.sqlite")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="database file")
    parser.add_argument("--status", action="store_true", help="report without applying")
    args = parser.parse_args(argv)

    available = discover()

    if args.status:
        print(f"database: {args.db}")
        print(f"applied:  {current_version(args.db)}")
        print(f"latest:   {max((v for v, _, _ in available), default=0)}")
        return 0

    applied = migrate(args.db)
    if applied:
        print(f"applied {len(applied)} migration(s): {', '.join(f'{v:03d}' for v in applied)}")
    else:
        print("database is up to date")
    print(f"schema version {current_version(args.db)} at {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
