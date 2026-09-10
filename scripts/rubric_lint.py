"""Check every rubric before a run depends on it.

Reads the YAML, validates it through the same model the pipeline uses, and
prints the rubric hash. Run it after editing a rubric: a validation failure at
lint time costs a second, and the same failure at run time costs a candidate's
place in the queue.

The file reading lives here rather than in the domain layer, which may not
import a YAML parser. What is validated and how is decided in
``domain/contracts/rubric_loader.py``; this only feeds it.

Usage:
    python scripts/rubric_lint.py                 # every rubric in rubrics/
    python scripts/rubric_lint.py path/to.yaml    # named files
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from domain.contracts import RoleRubric
from domain.rules import RubricInvalid, load_rubric, rubric_hash

REPO_ROOT = Path(__file__).resolve().parents[1]
RUBRIC_DIR = REPO_ROOT / "rubrics"
SCHEMA_PATH = RUBRIC_DIR / "_schema.json"


def parse(path: Path) -> dict[str, Any]:
    """Read one YAML rubric.

    A YAML error is reported like a validation error, because to the person
    editing the file it is one.
    """
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise RubricInvalid(path.name, [f"could not be parsed as YAML: {error}"]) from error

    if not isinstance(loaded, dict):
        raise RubricInvalid(path.name, ["the file must contain a mapping at the top level"])
    return loaded


def check(path: Path) -> tuple[bool, str]:
    try:
        rubric = load_rubric(parse(path), source=path.name)
    except RubricInvalid as error:
        return False, str(error)

    blockers = sum(1 for c in rubric.criteria if c.kind.value == "blocker")
    return True, (
        f"{path.name}: ok. {len(rubric.criteria)} criteria, {blockers} blocker(s), "
        f"coverage gate {rubric.min_coverage:.0%}, hash {rubric_hash(rubric)[:12]}"
    )


def write_schema() -> None:
    """Regenerate the editor schema from the contract.

    Kept generated rather than hand-written so the two cannot drift: the schema
    a person's editor validates against is the schema the pipeline enforces.
    """
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema = RoleRubric.model_json_schema(mode="serialization")
    SCHEMA_PATH.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="*", type=Path, help="rubric files; default is rubrics/*.yaml"
    )
    parser.add_argument(
        "--write-schema", action="store_true", help="regenerate rubrics/_schema.json"
    )
    args = parser.parse_args(argv)

    if args.write_schema:
        write_schema()
        print(f"wrote {SCHEMA_PATH.relative_to(REPO_ROOT)}")

    paths = args.paths or sorted(RUBRIC_DIR.glob("*.yaml"))
    if not paths:
        print("no rubrics found", file=sys.stderr)
        return 1

    failed = 0
    for path in paths:
        ok, message = check(path)
        print(message, file=sys.stdout if ok else sys.stderr)
        failed += 0 if ok else 1

    if failed:
        print(f"\n{failed} of {len(paths)} rubrics are invalid.", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
