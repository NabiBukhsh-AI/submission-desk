"""Committed schemas must match the models.

This is the mechanism that stops a contract from changing silently. A model edit
without a regenerated schema fails here, in the same change rather than three
phases later when something downstream breaks for no visible reason.
"""

from __future__ import annotations

import json

import pytest

from domain.contracts import EXPORTED_MODELS, Contract
from scripts.export_schemas import SCHEMA_DIR, expected_files, render, stale


def test_every_exported_model_has_a_committed_schema() -> None:
    missing = [
        model.__name__
        for model in EXPORTED_MODELS
        if not (SCHEMA_DIR / f"{model.__name__}.json").exists()
    ]
    assert missing == [], f"run `make schemas`; missing: {missing}"


def test_committed_schemas_match_the_models() -> None:
    problems = [path.name for path in stale(expected_files())]
    assert problems == [], f"run `make schemas`; stale or orphaned: {problems}"


def test_no_orphaned_schema_files() -> None:
    """A deleted contract leaves a file behind that would otherwise look current."""
    exported = {f"{model.__name__}.json" for model in EXPORTED_MODELS}
    committed = {path.name for path in SCHEMA_DIR.glob("*.json")}
    assert committed - exported == set()


def test_export_is_deterministic() -> None:
    """Two renders must be byte-identical, or every run produces a diff and the
    check becomes noise that people learn to ignore."""
    for model in EXPORTED_MODELS:
        assert render(model) == render(model)


def test_schemas_are_valid_json_and_newline_terminated() -> None:
    for path in SCHEMA_DIR.glob("*.json"):
        content = path.read_text(encoding="utf-8")
        json.loads(content)
        assert content.endswith("\n"), f"{path.name} has no trailing newline"


@pytest.mark.parametrize("model", EXPORTED_MODELS, ids=lambda m: m.__name__)
def test_every_contract_forbids_unknown_fields_and_is_frozen(model: type[Contract]) -> None:
    assert model.model_config.get("extra") == "forbid", f"{model.__name__} accepts unknown fields"
    assert model.model_config.get("frozen") is True, f"{model.__name__} is mutable"
