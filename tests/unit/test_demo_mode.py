"""Demo mode is safe by construction, not by discipline.

Three properties, each tested at the composition root rather than in a page:

- With DEMO_MODE on and any path to real documents configured, the system
  refuses to start. Not warns: refuses, before a database is opened.
- With DEMO_MODE on, there is no sink and no notifier. Not disabled — absent,
  so nothing in the process holds a handle that could send.
- With DEMO_MODE on, the document source is the synthetic corpus and nothing
  the inbox setting says can change that.

And the run-time consequence: an approved run in demo mode stays approved,
says why nothing was sent, and is never queued for a retry that would mark it
delivered.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from application.deps import Deps
from infrastructure.factory import (
    DEMO_CORPUS,
    SAMPLES_DIR,
    DemoModeViolation,
    build_deps,
    build_notifier,
    build_sinks,
    build_source,
    enforce_demo_mode,
    settings_from_env,
)
from infrastructure.storage.sqlite.connection import close_thread_connection


def demo_settings(tmp_path: Path, **overrides: object):
    return settings_from_env(
        db_path=str(tmp_path / "demo.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        demo_mode=True,
        **overrides,
    )


@pytest.fixture
def demo(tmp_path: Path) -> Iterator[Deps]:
    settings = demo_settings(tmp_path)
    yield build_deps(settings)
    close_thread_connection(settings.db_path)


# --- refusing to start ---------------------------------------------------------------


def test_a_pilot_data_path_refuses_to_start(tmp_path: Path) -> None:
    """The headline. One environment variable pointing at real documents and
    demo mode will not construct anything."""
    settings = demo_settings(tmp_path, pilot_data_dir=str(tmp_path / "real"))

    with pytest.raises(DemoModeViolation, match="PILOT_DATA_DIR"):
        build_deps(settings)


def test_an_inbox_outside_samples_refuses_to_start(tmp_path: Path) -> None:
    settings = demo_settings(tmp_path, inbox_dir=str(tmp_path / "inbox"))

    with pytest.raises(DemoModeViolation, match="INBOX_DIR"):
        build_deps(settings)


def test_a_remote_source_refuses_to_start(tmp_path: Path) -> None:
    """Drive is a path to real documents that happens not to be a path."""
    settings = demo_settings(tmp_path, source_adapter="drive")

    with pytest.raises(DemoModeViolation, match="SOURCE_ADAPTER"):
        build_deps(settings)


def test_the_refusal_happens_before_the_database_exists(tmp_path: Path) -> None:
    """Refused at configuration, not at first use. A database created by a
    startup that then refused would be a database with nothing in it, and a
    confusing one."""
    settings = demo_settings(tmp_path, pilot_data_dir=str(tmp_path / "real"))

    with pytest.raises(DemoModeViolation):
        build_deps(settings)

    assert not Path(settings.db_path).exists()


def test_an_inbox_inside_samples_is_allowed(tmp_path: Path) -> None:
    settings = demo_settings(tmp_path, inbox_dir=str(SAMPLES_DIR / "synthetic"))

    enforce_demo_mode(settings)


def test_the_refusal_names_what_to_change(tmp_path: Path) -> None:
    """Somebody hitting this at a demo needs the fix, not a stack trace."""
    settings = demo_settings(tmp_path, pilot_data_dir="/srv/pilot")

    with pytest.raises(DemoModeViolation) as raised:
        enforce_demo_mode(settings)

    assert "Unset" in str(raised.value)


def test_nothing_is_checked_when_demo_mode_is_off(tmp_path: Path) -> None:
    settings = settings_from_env(
        db_path=str(tmp_path / "x.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        pilot_data_dir="/srv/pilot",
        source_adapter="drive",
    )

    enforce_demo_mode(settings)


# --- what is absent ---------------------------------------------------------------------


def test_there_are_no_sinks(demo: Deps) -> None:
    """Absent, not disabled. An empty tuple cannot be re-enabled by a flag."""
    assert demo.sinks == ()


def test_there_is_no_notifier(demo: Deps) -> None:
    assert demo.notifier is None


def test_the_csv_guarantee_is_suspended_too(tmp_path: Path) -> None:
    """The CSV sink is 'always first and not removable' in every other mode.
    Demo mode is the exception, and it is an exception in the factory rather
    than a flag on the sink."""
    assert build_sinks(demo_settings(tmp_path)) == ()
    assert build_notifier(demo_settings(tmp_path)) is None


def test_outside_demo_mode_the_csv_sink_is_present(tmp_path: Path) -> None:
    settings = settings_from_env(db_path=str(tmp_path / "x.sqlite"), blob_dir=str(tmp_path / "b"))

    assert len(build_sinks(settings)) == 1
    assert build_notifier(settings) is not None


# --- what is read -------------------------------------------------------------------------


def test_the_source_is_the_synthetic_corpus(demo: Deps) -> None:
    assert Path(demo.source.root).resolve() == DEMO_CORPUS.resolve()


def test_the_inbox_setting_cannot_move_it(tmp_path: Path) -> None:
    """Even a permitted inbox (inside samples) does not redirect the source:
    demo mode reads the synthetic corpus, full stop."""
    settings = demo_settings(tmp_path, inbox_dir=str(SAMPLES_DIR / "adversarial"))

    assert Path(build_source(settings).root).resolve() == DEMO_CORPUS.resolve()


def test_the_corpus_is_under_the_committable_tree() -> None:
    """data/samples/ is the one directory under data/ the PII scanner allows
    into history. The demo corpus has to be there, or `make seed` would
    produce files that could not be committed."""
    assert DEMO_CORPUS.resolve().is_relative_to(SAMPLES_DIR.resolve())


# --- the doctor says so -------------------------------------------------------------------


def test_doctor_reports_demo_mode_as_a_warning(demo: Deps) -> None:
    from infrastructure.doctor import Level, check_demo_mode

    check = check_demo_mode(demo.settings)

    assert check.level is Level.WARN
    assert "sink" in check.detail
