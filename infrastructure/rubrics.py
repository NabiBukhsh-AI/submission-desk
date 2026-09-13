"""Role definitions, read from disk and from the admin page.

A rubric is a YAML file a recruiter edits. This module finds it, parses it, and
hands the validated object to the domain; it decides nothing about what the
contents mean.

A rubric saved from the admin page lives in the settings store as JSON under
``rubric:<role_id>`` and wins over the file of the same name, so a recruiter
can change a requirement without a deployment; the file is what "reset"
returns to. A role that exists only in the store is a role the admin created.

Loading is cached per path and modification time, so a run does not re-read and
re-validate the same file once per criterion, and an edit during a long session
is still picked up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from domain.contracts.rubric import RoleRubric
from domain.contracts.rubric_loader import RubricInvalid, load_rubric

#: A role id becomes a filename, so it must not be able to become a path. The
#: same rule as intake's: a name from outside never steers where we read.
_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


STORED_PREFIX = "rubric:"


class RubricLoader:
    """Reads ``<directory>/<role_id>.yaml``, or the admin's saved copy."""

    def __init__(self, directory: Path | str, store: Any = None) -> None:
        self.directory = Path(directory)
        self.store = store
        self._cache: dict[str, tuple[float | str, RoleRubric]] = {}

    def available(self) -> list[str]:
        """The roles that can be run, for a menu rather than a free-text box."""
        on_disk = (
            {path.stem for path in self.directory.glob("*.yaml") if not path.stem.startswith("_")}
            if self.directory.is_dir()
            else set()
        )
        return sorted(on_disk | set(self._stored().keys()))

    def __call__(self, role_id: str) -> RoleRubric:
        stored = self._stored().get(role_id)
        if stored is not None:
            cached = self._cache.get(role_id)
            if cached is not None and cached[0] == stored:
                return cached[1]
            rubric = load_rubric(json.loads(stored), source=f"{role_id} (admin page)")
            self._cache[role_id] = (stored, rubric)
            return rubric

        path = self._path_for(role_id)
        if not path.is_file():
            raise FileNotFoundError(role_id)

        stamp = path.stat().st_mtime
        cached = self._cache.get(role_id)
        if cached is not None and cached[0] == stamp:
            return cached[1]

        rubric = load_rubric(self.raw(role_id), source=path.name)
        self._cache[role_id] = (stamp, rubric)
        return rubric

    # --- for the admin page ---------------------------------------------------

    def raw(self, role_id: str) -> dict[str, Any]:
        """The rubric as data, from the store or the file, before validation."""
        stored = self._stored().get(role_id)
        if stored is not None:
            return dict(json.loads(stored))
        path = self._path_for(role_id)
        if not path.is_file():
            raise FileNotFoundError(role_id)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise RubricInvalid(path.name, [f"the file is not valid YAML: {error}"]) from error
        if not isinstance(data, dict):
            raise RubricInvalid(path.name, ["the file does not describe a role"])
        return data

    def source_of(self, role_id: str) -> str:
        """``admin`` when the store has a copy, else ``file``."""
        return "admin" if role_id in self._stored() else "file"

    def as_yaml(self, data: dict[str, Any]) -> str:
        """The data in the form a recruiter would edit on disk."""
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=88)

    def save(self, role_id: str, data: dict[str, Any]) -> None:
        """Store a rubric the caller has already validated."""
        if self.store is None:
            raise RubricInvalid(role_id, ["rubrics cannot be saved without a settings store"])
        self.store.set(f"{STORED_PREFIX}{role_id}", json.dumps(data, ensure_ascii=False))
        self._cache.pop(role_id, None)

    def reset(self, role_id: str) -> None:
        """Forget the stored copy; the file, if there is one, is read again."""
        if self.store is not None:
            self.store.delete(f"{STORED_PREFIX}{role_id}")
        self._cache.pop(role_id, None)

    def _stored(self) -> dict[str, str]:
        if self.store is None:
            return {}
        return {
            name[len(STORED_PREFIX) :]: value
            for name, (value, _secret) in self.store.all().items()
            if name.startswith(STORED_PREFIX)
        }

    def _path_for(self, role_id: str) -> Path:
        if not role_id or not set(role_id.lower()) <= _ALLOWED:
            raise FileNotFoundError(role_id)
        return self.directory / f"{role_id}.yaml"
