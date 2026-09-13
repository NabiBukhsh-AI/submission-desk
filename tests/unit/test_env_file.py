"""The ``.env`` file is read, and the environment wins over it."""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.factory import load_env_file, settings_from_env


def _unset(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Absent now, and absent again afterwards.

    ``delenv`` on a name that is not there records nothing, so a value the
    loader then writes straight into ``os.environ`` would outlive the test.
    """
    for name in names:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


def test_the_file_fills_in_what_the_environment_lacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        "SLACK_CHANNEL=C0123\n"
        'DRIVE_FOLDER_ID="quoted id"  # trailing note\n'
        "export SHEETS_SPREADSHEET_ID='sheet-1'\n"
        "MODEL_PROVIDER=anthropic\n"
        "not a line\n",
        encoding="utf-8",
    )
    _unset(monkeypatch, "SLACK_CHANNEL", "DRIVE_FOLDER_ID", "SHEETS_SPREADSHEET_ID")
    monkeypatch.setenv("MODEL_PROVIDER", "fake")

    assert load_env_file(env) == 3
    settings = settings_from_env()

    assert settings.slack_channel == "C0123"
    assert settings.drive_folder_id == "quoted id"
    assert settings.sheets_spreadsheet_id == "sheet-1"
    # The shell said fake; the file does not get to change that.
    assert settings.model_provider == "fake"


def test_an_absent_file_is_nothing(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / "missing.env") == 0


def test_env_file_can_point_elsewhere_or_at_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other.env"
    other.write_text("PUBLIC_URL=https://desk.example.test/\n", encoding="utf-8")
    _unset(monkeypatch, "PUBLIC_URL")

    monkeypatch.setenv("ENV_FILE", "")
    assert load_env_file() == 0

    monkeypatch.setenv("ENV_FILE", str(other))
    assert load_env_file() == 1
    assert settings_from_env().public_url == "https://desk.example.test"
