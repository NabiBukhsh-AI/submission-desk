"""The notifier is told, and told only counts.

Three events and three places: a batch finishing, a document being quarantined,
a delivery failing. Each is exercised through the real use case or node with a
recording notifier, so a refactor that stops calling the notifier fails a test
rather than silently going quiet — which is how this was found the first time.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from application.deps import Deps, Settings
from application.notify import notify
from application.use_cases.process_batch import process_batch
from application.use_cases.submit_review import submit_review
from domain.contracts.enums import ReviewAction, RunStatus
from domain.contracts.run_state import RunState
from domain.ports.notifiers import ALLOWED_FIELDS, Notification, NotificationKind
from domain.ports.sinks import AdapterError, AdapterResult
from domain.ports.sources import CandidateRef, DocumentRef
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.integrations.slack import render
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline.deliver import node as deliver
from tests.fakes.fake_transports import RecordingSink
from tests.fixtures import pdfs
from tests.workflow.conftest_review import seed_run, wire
from tests.workflow.test_end_to_end import (
    STRONG_ANSWERS,
    STRONG_CV,
    AnsweringModel,
    _FolderSource,
    _load_shipped_rubric,
)


@dataclass
class RecordingNotifier:
    notifier_id: str = "recording"
    sent: list[Notification] = field(default_factory=list)

    def healthy(self) -> bool:
        return True

    def notify(self, notification: Notification) -> AdapterResult:
        self.sent.append(notification)
        return AdapterResult.succeeded("recorded")


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "notify.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        blind_mode=False,
        reviewer_id="rec-014",
        sanitize_render_diff=False,
        public_url="https://desk.example.test",
    )
    built = wire(build_deps(settings))
    yield Deps(
        **{
            **built.__dict__,
            "rubric_loader": _load_shipped_rubric,
            "source": _FolderSource(tmp_path / "inbox"),
            "notifier": RecordingNotifier(),
        }
    )
    close_thread_connection(settings.db_path)


def _candidates(deps: Deps, count: int) -> list[CandidateRef]:
    inbox = Path(deps.source.root)
    inbox.mkdir(parents=True, exist_ok=True)
    refs = []
    for index in range(count):
        path = inbox / f"cv-{index}.pdf"
        data = pdfs.text_pdf(STRONG_CV)
        path.write_bytes(data)
        refs.append(
            CandidateRef(
                candidate_id=f"cand-{index}",
                documents=(
                    DocumentRef(
                        candidate_id=f"cand-{index}",
                        filename=path.name,
                        external_ref=str(path),
                        # What the folder source records, and what reuse is keyed on.
                        metadata={"sha256": hashlib.sha256(data).hexdigest()},
                    ),
                ),
            )
        )
    return refs


def _state_of(run) -> RunState:
    return RunState(
        run_id=run.run_id,
        candidate_id=run.candidate_id,
        role_id=run.role_id,
        status=run.status,
        started_at=run.started_at,
        integrity_tier=run.integrity_tier,
    )


# --- a batch ----------------------------------------------------------------------------


def test_a_finished_batch_is_announced_with_counts_only(deps: Deps) -> None:
    model = AnsweringModel(STRONG_CV, STRONG_ANSWERS)
    wired = Deps(**{**deps.__dict__, "models": model})

    summary = process_batch(_candidates(deps, 2), "ai-engineer", wired)

    assert summary.reviewable == 2
    [sent] = deps.notifier.sent
    assert sent.kind is NotificationKind.BATCH_READY
    assert sent.ready_count == 2
    assert sent.role_id == "ai-engineer"
    assert sent.link == "https://desk.example.test/#/queue"
    assert set(sent.as_payload()) <= ALLOWED_FIELDS
    assert "cand-0" not in render(sent)


def test_a_batch_that_was_all_reused_says_nothing(deps: Deps) -> None:
    """Nobody has new work, so nobody is interrupted."""
    model = AnsweringModel(STRONG_CV, STRONG_ANSWERS)
    wired = Deps(**{**deps.__dict__, "models": model})
    candidates = _candidates(deps, 1)
    process_batch(candidates, "ai-engineer", wired)
    deps.notifier.sent.clear()

    summary = process_batch(candidates, "ai-engineer", wired)

    assert summary.reused == 1
    assert deps.notifier.sent == []


# --- a delivery ---------------------------------------------------------------------------


def test_a_failed_delivery_names_the_sink_and_the_code(deps: Deps) -> None:
    run = seed_run(deps)
    submit_review(run.run_id, ReviewAction.APPROVE, deps)
    wired = Deps(
        **{
            **deps.__dict__,
            "sinks": (
                RecordingSink(sink_id="csv"),
                RecordingSink(sink_id="sheets", fail_with=AdapterError.TRANSIENT),
            ),
        }
    )

    result = deliver(_state_of(deps.runs.get(run.run_id)), wired)

    assert result.next_status is RunStatus.DELIVERY_PENDING_RETRY
    [sent] = deps.notifier.sent
    assert sent.kind is NotificationKind.DELIVERY_FAILED
    assert sent.failed_count == 1
    assert sent.sink_id == "sheets"
    assert sent.error_code == AdapterError.TRANSIENT.value


def test_a_clean_delivery_is_not_announced(deps: Deps) -> None:
    run = seed_run(deps)
    submit_review(run.run_id, ReviewAction.APPROVE, deps)
    wired = Deps(**{**deps.__dict__, "sinks": (RecordingSink(sink_id="csv"),)})

    result = deliver(_state_of(deps.runs.get(run.run_id)), wired)

    assert result.next_status is RunStatus.DELIVERED
    assert deps.notifier.sent == []


# --- the helper -----------------------------------------------------------------------------


def test_demo_mode_and_no_notifier_both_stay_silent(deps: Deps) -> None:
    demo = Deps(
        **{**deps.__dict__, "settings": Settings(**{**deps.settings.__dict__, "demo_mode": True})}
    )
    notify(demo, NotificationKind.QUARANTINE_DETECTED, count=1)
    absent = Deps(**{**deps.__dict__, "notifier": None})
    notify(absent, NotificationKind.QUARANTINE_DETECTED, count=1)

    assert deps.notifier.sent == []


def test_without_a_public_url_the_message_has_no_link(deps: Deps) -> None:
    quiet = Deps(
        **{**deps.__dict__, "settings": Settings(**{**deps.settings.__dict__, "public_url": ""})}
    )

    notify(quiet, NotificationKind.QUARANTINE_DETECTED, count=2)

    [sent] = deps.notifier.sent
    assert sent.link == ""
    assert "<" not in render(sent)
    assert "2 document(s)" in render(sent)
