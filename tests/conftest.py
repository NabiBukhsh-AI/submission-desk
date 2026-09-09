"""Shared test configuration.

The suite runs offline. No network, no API key, no provider account. Anything
that needs a real service carries the ``live`` marker and is excluded from the
default run, so the absence of credentials is never the reason a test fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the model provider to the fake for every test.

    Autouse rather than opt-in: a test that reaches a real provider because it
    forgot to set this would pass locally, cost money, and fail in CI.
    """
    monkeypatch.setenv("MODEL_PROVIDER", "fake")


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT
