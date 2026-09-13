"""The roles, and the rubric behind each one, for the admin page.

A rubric is the whole definition of a job: what is asked, what each answer is
worth, where the bands sit. The file in ``rubrics/`` is the shipped default;
what an admin saves here lives in the settings store and wins over the file
until it is reset. Nothing is written until the whole rubric validates, so a
half-edited role can never be the one a candidate is assessed against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from application.deps import Deps
from application.use_cases.admin import AdminRefused
from domain.contracts.rubric_loader import RubricInvalid, load_rubric, rubric_hash


@dataclass(frozen=True)
class RoleSummary:
    role_id: str
    role_title: str
    version: str
    criteria: int
    source: str


@dataclass(frozen=True)
class RubricView:
    role_id: str
    source: str
    rubric_hash: str
    data: dict[str, Any]
    yaml: str


def list_roles(deps: Deps) -> list[RoleSummary]:
    """Every role that can be run, whether it came from a file or the page."""
    if deps.rubric_loader is None:
        return []
    loader = _loader(deps)
    found = []
    for role_id in loader.available():
        try:
            rubric = loader(role_id)
        except (RubricInvalid, FileNotFoundError):
            continue
        found.append(
            RoleSummary(
                role_id=role_id,
                role_title=rubric.role_title,
                version=rubric.version,
                criteria=len(rubric.criteria),
                source=loader.source_of(role_id),
            )
        )
    return found


def describe_rubric(deps: Deps, role_id: str) -> RubricView:
    loader = _loader(deps)
    try:
        data = loader.raw(role_id)
        rubric = loader(role_id)
    except FileNotFoundError as missing:
        raise AdminRefused(f"There is no role called {role_id!r}.") from missing
    except RubricInvalid as invalid:
        raise AdminRefused(str(invalid)) from invalid
    return RubricView(
        role_id=role_id,
        source=loader.source_of(role_id),
        rubric_hash=rubric_hash(rubric),
        data=data,
        yaml=loader.as_yaml(data),
    )


def save_rubric(deps: Deps, role_id: str, data: dict[str, Any]) -> RubricView:
    """Validate the whole rubric, then store it. The id in the path is the id."""
    loader = _loader(deps)
    data = {**data, "role_id": role_id}
    try:
        load_rubric(data, source=role_id)
    except RubricInvalid as invalid:
        raise AdminRefused("\n".join(invalid.problems)) from invalid
    loader.save(role_id, data)
    return describe_rubric(deps, role_id)


def reset_rubric(deps: Deps, role_id: str) -> None:
    """Back to the file. A role that exists only in the store is removed."""
    _loader(deps).reset(role_id)


def _loader(deps: Deps) -> Any:
    if deps.rubric_loader is None:
        raise AdminRefused("No rubric loader is configured.")
    return deps.rubric_loader
