"""Role definitions, read from disk.

A rubric is a YAML file a recruiter edits. This module finds it, parses it, and
hands the validated object to the domain; it decides nothing about what the
contents mean.

Loading is cached per path and modification time, so a run does not re-read and
re-validate the same file once per criterion, and an edit during a long session
is still picked up.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from domain.contracts.rubric import RoleRubric
from domain.contracts.rubric_loader import RubricInvalid, load_rubric

#: A role id becomes a filename, so it must not be able to become a path. The
#: same rule as intake's: a name from outside never steers where we read.
_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


class RubricLoader:
    """Reads ``<directory>/<role_id>.yaml``."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self._cache: dict[str, tuple[float, RoleRubric]] = {}

    def available(self) -> list[str]:
        """The roles that can be run, for a menu rather than a free-text box."""
        if not self.directory.is_dir():
            return []
        return sorted(
            path.stem for path in self.directory.glob("*.yaml") if not path.stem.startswith("_")
        )

    def __call__(self, role_id: str) -> RoleRubric:
        path = self._path_for(role_id)
        if not path.is_file():
            raise FileNotFoundError(role_id)

        stamp = path.stat().st_mtime
        cached = self._cache.get(role_id)
        if cached is not None and cached[0] == stamp:
            return cached[1]

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise RubricInvalid(path.name, [f"the file is not valid YAML: {error}"]) from error

        if not isinstance(data, dict):
            raise RubricInvalid(path.name, ["the file does not describe a role"])

        rubric = load_rubric(data, source=path.name)
        self._cache[role_id] = (stamp, rubric)
        return rubric

    def _path_for(self, role_id: str) -> Path:
        if not role_id or not set(role_id.lower()) <= _ALLOWED:
            raise FileNotFoundError(role_id)
        return self.directory / f"{role_id}.yaml"
