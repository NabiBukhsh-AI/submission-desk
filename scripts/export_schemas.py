"""Write the committed JSON Schema for every contract.

The files under ``contracts/schemas/`` are the mechanism that stops a contract
from changing silently: CI regenerates them and fails on a diff, so a model
edit without a regenerated schema is a red build rather than a surprise three
phases later.

Output is deterministic, sorted, and newline-terminated, so the diff shows the
change to the contract and nothing else.

Usage:
    python scripts/export_schemas.py            # write the files
    python scripts/export_schemas.py --check    # exit 1 if they are stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from domain.contracts import EXPORTED_MODELS, Contract

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"


def render(model: type[Contract]) -> str:
    schema = model.model_json_schema(mode="serialization")
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def expected_files() -> dict[Path, str]:
    return {SCHEMA_DIR / f"{model.__name__}.json": render(model) for model in EXPORTED_MODELS}


def write(files: dict[Path, str]) -> list[Path]:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for path, content in files.items():
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
            written.append(path)
    return written


def stale(files: dict[Path, str]) -> list[Path]:
    """Schemas that do not match the models, plus any orphaned file."""
    problems = [
        path
        for path, content in files.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    if SCHEMA_DIR.exists():
        problems.extend(sorted(set(SCHEMA_DIR.glob("*.json")) - set(files)))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report stale schemas without writing, for CI and pre-commit",
    )
    args = parser.parse_args(argv)

    files = expected_files()

    if args.check:
        problems = stale(files)
        if problems:
            print("Committed schemas do not match the models:", file=sys.stderr)
            for path in problems:
                print(f"  {path.relative_to(REPO_ROOT)}", file=sys.stderr)
            print("Run: make schemas", file=sys.stderr)
            return 1
        print(f"{len(files)} schemas are current.")
        return 0

    written = write(files)
    for path in written:
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    print(f"{len(files)} schemas exported, {len(written)} changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
