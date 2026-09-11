"""The whole system, with every integration absent.

The claim: a fresh clone with no accounts, no credentials and no network runs the
complete workflow. Local files in, a CSV out, a notification printed to a
console, and a candidate reviewed and delivered in between.

This is not a convenience for testing. It is what makes every other claim in this
repository checkable by somebody who was not there: the evaluation, the
adversarial corpus, the routing comparison and the reviewer workflow all run
against fakes, so a reader can reproduce any number in the submission without
asking anybody for access to anything.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from application.deps import Deps
from application.use_cases.process_batch import process_batch
from application.use_cases.process_candidate import process_candidate
from application.use_cases.submit_review import submit_review
from domain.contracts.enums import ReviewAction, RunStatus
from domain.contracts.run_state import RunState
from domain.ports.notifiers import Notification, NotificationKind
from infrastructure.factory import build_deps, build_sinks, settings_from_env
from infrastructure.integrations.csv_sink import CsvSink
from infrastructure.integrations.slack import ConsoleNotifier
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline.deliver import node as deliver
from tests.workflow.test_end_to_end import (
    STRONG_ANSWERS,
    STRONG_CV,
    AnsweringModel,
    _FolderSource,
    _load_shipped_rubric,
    upload,
)


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    """The defaults, with nothing configured.

    No MODEL_PROVIDER beyond the fake, no DRIVE_FOLDER_ID, no
    SHEETS_SPREADSHEET_ID, no SLACK_CHANNEL, no pricing. Exactly what a fresh
    clone has.
    """
    settings = settings_from_env(
        db_path=str(tmp_path / "offline.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        blind_mode=False,
        reviewer_id="rec-014",
        sanitize_render_diff=False,
    )
    built = build_deps(settings)
    yield Deps(
        **{
            **built.__dict__,
            "rubric_loader": _load_shipped_rubric,
            "source": _FolderSource(tmp_path / "inbox"),
        }
    )
    close_thread_connection(settings.db_path)


def run_one(deps: Deps):
    candidate = upload(deps, STRONG_CV)
    model = AnsweringModel(STRONG_CV, STRONG_ANSWERS)
    wired = Deps(**{**deps.__dict__, "models": model})
    return process_candidate(candidate, "ai-engineer", wired), wired


# --- what a fresh clone has --------------------------------------------------------


def test_the_default_provider_is_the_fake() -> None:
    """A clone with no API key runs the demo. That is what the defaults are
    for."""
    assert settings_from_env().model_provider == "fake"


def test_the_csv_sink_is_always_registered() -> None:
    """Every other integration is optional because this one is not."""
    sinks = build_sinks(settings_from_env())

    assert sinks
    assert sinks[0].sink_id == "csv"


def test_the_csv_sink_is_first() -> None:
    """Written before anything that can fail, which is what makes the guarantee
    a guarantee rather than an intention."""
    ids = [sink.sink_id for sink in build_sinks(settings_from_env())]

    assert ids[0] == "csv"


def test_no_network_integration_is_configured_by_default() -> None:
    """Drive, Sheets and Slack are absent unless a transport is supplied. An
    adapter that built a real client at startup would make the offline claim
    untrue on the first import."""
    ids = {sink.sink_id for sink in build_sinks(settings_from_env())}

    assert ids == {"csv"}


def test_pricing_is_absent_and_that_is_fine(deps: Deps) -> None:
    assert deps.pricing.configured is False


# --- the whole workflow ---------------------------------------------------------------


def test_a_candidate_is_processed(deps: Deps) -> None:
    result, _ = run_one(deps)

    assert result.is_reviewable


def test_a_candidate_is_reviewed_and_delivered(deps: Deps, tmp_path: Path) -> None:
    """Upload, process, review, approve, deliver. No account anywhere."""
    result, wired = run_one(deps)

    submit_review(result.run_id, ReviewAction.APPROVE, wired)
    run = wired.runs.get(result.run_id)

    csv = CsvSink(tmp_path / "results.csv")
    delivery = deliver(
        RunState(
            run_id=run.run_id,
            candidate_id=run.candidate_id,
            role_id=run.role_id,
            status=run.status,
            started_at=run.started_at,
            integrity_tier=run.integrity_tier,
        ),
        Deps(**{**wired.__dict__, "sinks": (csv,)}),
    )

    assert delivery.next_status is RunStatus.DELIVERED
    assert run.candidate_id in csv.path.read_text(encoding="utf-8")


def test_the_result_file_is_readable_by_anything(deps: Deps, tmp_path: Path) -> None:
    """A CSV rather than a format that needs this system to open it. The point
    of the guaranteed sink is that the result outlives the tool."""
    import csv as csv_module

    result, wired = run_one(deps)
    submit_review(result.run_id, ReviewAction.APPROVE, wired)
    run = wired.runs.get(result.run_id)

    sink = CsvSink(tmp_path / "results.csv")
    deliver(
        RunState(
            run_id=run.run_id,
            candidate_id=run.candidate_id,
            role_id=run.role_id,
            status=run.status,
            started_at=run.started_at,
            integrity_tier=run.integrity_tier,
        ),
        Deps(**{**wired.__dict__, "sinks": (sink,)}),
    )

    with sink.path.open(encoding="utf-8") as handle:
        rows = list(csv_module.DictReader(handle))

    assert rows
    assert rows[0]["band"]
    assert rows[0]["reasoning"] if "reasoning" in rows[0] else rows[0]["derivation"]


def test_a_batch_runs_offline(deps: Deps) -> None:
    from domain.ports.sources import CandidateRef, DocumentRef
    from tests.fixtures import pdfs

    inbox = Path(deps.source.root)
    candidates = []
    for index in range(3):
        path = inbox / f"cv-{index}.pdf"
        path.write_bytes(pdfs.text_pdf(STRONG_CV))
        candidates.append(
            CandidateRef(
                candidate_id=f"cand-{index}",
                documents=(
                    DocumentRef(
                        candidate_id=f"cand-{index}",
                        filename=path.name,
                        external_ref=str(path),
                    ),
                ),
            )
        )

    model = AnsweringModel(STRONG_CV, STRONG_ANSWERS)
    summary = process_batch(candidates, "ai-engineer", Deps(**{**deps.__dict__, "models": model}))

    assert summary.total == 3
    assert summary.reviewable == 3


# --- notifications without a channel ------------------------------------------------------


def test_the_default_notifier_needs_nothing(deps: Deps) -> None:
    """No code path branches on whether a notifier exists, because there is
    always one."""
    assert deps.notifier is not None
    assert deps.notifier.healthy() is True


def test_a_notification_is_printed(deps: Deps) -> None:
    written: list[str] = []
    console = ConsoleNotifier(write=written.append)

    result = console.notify(
        Notification(kind=NotificationKind.BATCH_READY, ready_count=3, role_id="ai-engineer")
    )

    assert result.ok
    assert "3 candidate(s) ready" in written[0]


# --- no accidental network ------------------------------------------------------------------


def test_nothing_at_startup_needs_credentials(tmp_path: Path) -> None:
    """Building Deps must not construct a client that reads an environment
    variable and fails, or that opens a connection nobody asked for."""
    settings = settings_from_env(
        db_path=str(tmp_path / "startup.sqlite"), blob_dir=str(tmp_path / "blobs")
    )
    built = build_deps(settings)

    try:
        assert built.models is None or getattr(built.models, "provider_id", "fake") == "fake"
        assert built.source is None or built.source.source_id in ("local", "test")
        assert all(sink.sink_id == "csv" for sink in built.sinks)
    finally:
        close_thread_connection(settings.db_path)


def test_the_live_marker_exists_and_is_excluded() -> None:
    """Anything touching a real service carries the marker, and the default run
    excludes it — so the absence of credentials is never the reason a test
    fails."""
    import tomllib

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    markers = config["tool"]["pytest"]["ini_options"]["markers"]

    assert any(marker.startswith("live:") for marker in markers)


def test_the_offline_target_excludes_live_tests() -> None:
    """The Makefile is what CI and a reader both run."""
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text(encoding="utf-8")

    assert 'OFFLINE := -m "not live and not smoke"' in makefile
