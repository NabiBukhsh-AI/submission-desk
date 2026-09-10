"""Rubric validation and hashing.

Takes an already-parsed mapping rather than a file path, because the domain
layer may not read files or import a YAML parser. The caller does the reading;
this decides whether what was read is a rubric.

It lives beside the contract it validates rather than with the rule engine,
because the adapter that reads the file needs it and adapters may not reach into
``domain.rules``. Validating a ``RoleRubric`` is a fact about the contract; what
the rubric then *means* is the rule engine, and that separation is what the
dependency test enforces. ``domain.rules`` re-exports all three names, so the
rule engine still reads as one thing from outside.

The hash is what binds a run to the exact rubric that produced it. Two runs with
the same hash were scored by the same rules, and a run whose rubric hash is not
in the repository cannot be reproduced, which is the point.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from domain.contracts.rubric import RoleRubric
from domain.errors import SubmissionDeskError


class RubricInvalid(SubmissionDeskError):
    """A rubric failed validation.

    Carries the field paths so the message can name what to fix. A recruiter
    edits these files, so "criteria.0.weight must be between 1 and 10" is worth
    the extra work over "validation error".
    """

    error_code = "RUBRIC_INVALID"

    def __init__(self, source: str, problems: list[str]) -> None:
        self.source = source
        self.problems = problems
        listed = "\n".join(f"  - {problem}" for problem in problems)
        super().__init__(f"{source} is not a valid rubric:\n{listed}")


def load_rubric(data: Mapping[str, Any], *, source: str = "<rubric>") -> RoleRubric:
    """Validate a parsed rubric, or raise with every problem named."""
    try:
        return RoleRubric.model_validate(dict(data))
    except Exception as error:
        raise RubricInvalid(source, _problems(error)) from error


def _problems(error: Exception) -> list[str]:
    """Pydantic's errors, flattened into lines a person can act on."""
    raw = getattr(error, "errors", None)
    if not callable(raw):
        return [str(error)]

    problems: list[str] = []
    for detail in raw():
        location = ".".join(str(part) for part in detail.get("loc", ())) or "<root>"
        problems.append(f"{location}: {detail.get('msg', 'invalid')}")
    return problems or [str(error)]


def rubric_hash(rubric: RoleRubric) -> str:
    """A stable fingerprint of everything that affects a score.

    Computed over the canonical JSON form with sorted keys, so reordering
    criteria in the file changes the hash (order is meaningful to a reader) but
    reformatting the YAML does not.

    ``notes_for_reviewer`` is included deliberately: it is shown above the
    scorecard, and a run should be reproducible down to what the reviewer read.
    """
    canonical = json.dumps(
        rubric.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
