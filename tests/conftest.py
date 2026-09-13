"""Shared test configuration.

The suite runs offline. No network, no API key, no provider account. Anything
that needs a real service carries the ``live`` marker and is excluded from the
default run, so the absence of credentials is never the reason a test fails.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Never read the developer's .env. Set at import, not in a fixture, because
# the API module reads settings when it is imported, which is before any
# fixture runs; a real Slack token or a Drive folder in the file would turn
# an offline test into a live one.
os.environ.setdefault("ENV_FILE", "")


@pytest.fixture(autouse=True)
def offline_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the model provider to the fake for every test.

    Autouse rather than opt-in: a test that reaches a real provider because it
    forgot to set this would pass locally, cost money, and fail in CI.
    """
    monkeypatch.setenv("MODEL_PROVIDER", "fake")


@pytest.fixture(autouse=True, scope="session")
def logs_outside_the_tree(tmp_path_factory: pytest.TempPathFactory) -> None:
    """The event log is written wherever LOG_DIR points; for the suite that is
    a temporary directory, not data/logs."""
    os.environ.setdefault("LOG_DIR", str(tmp_path_factory.mktemp("logs")))


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT
