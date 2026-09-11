"""The HTTP interface renders what the use cases return, and nothing else.

Each route is exercised once against a real database with one seeded run. What
is asserted is the contract the frontend reads: the fields it needs are there,
the words come from the shared vocabulary, a decision goes through
``submit_review`` with all its refusals intact, and demo mode refuses uploads.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import main as api_module
from application.deps import Deps
from domain.contracts.enums import RunStatus
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from tests.workflow.conftest_review import seed_run, wire


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Deps]]:
    """The app bound to a temporary database, with a reviewer configured."""
    settings = settings_from_env(
        db_path=str(tmp_path / "api.sqlite"), blob_dir=str(tmp_path / "blobs"), reviewer_id="rec-9"
    )
    deps = wire(build_deps(settings))
    monkeypatch.setattr(api_module, "_deps", deps)
    yield TestClient(api_module.app), deps
    close_thread_connection(settings.db_path)


def test_health_says_what_the_deployment_is(client: tuple[TestClient, Deps]) -> None:
    http, _ = client
    body = http.get("/api/health").json()

    assert body["ok"] is True
    assert body["reviewer_configured"] is True
    assert body["demo_mode"] is False


def test_the_vocabulary_is_the_shared_one(client: tuple[TestClient, Deps]) -> None:
    """Every sentence the interface shows comes from here, so a second client
    cannot invent a gentler banner or a raw enum value."""
    http, _ = client
    body = http.get("/api/vocabulary").json()

    assert body["chips"]["quarantined"]["label"] == "Documents flagged"
    assert body["bands"]["insufficient_information"] == "Not enough information"
    assert body["states"]["insufficient_evidence"] == "Not addressed"
    assert "D-INSTR-IMPERATIVE" in body["detectors"]
    assert "Needs attention" in body["filters"]


def test_the_queue_lists_a_seeded_run_with_its_chip(client: tuple[TestClient, Deps]) -> None:
    http, deps = client
    run = seed_run(deps)

    rows = http.get("/api/runs", params={"filter": "Needs attention"}).json()

    assert [row["run_id"] for row in rows] == [str(run.run_id)]
    assert rows[0]["chip"]["needs_attention"] is True
    assert rows[0]["band_label"] == "Advance"


def test_an_unknown_filter_is_refused(client: tuple[TestClient, Deps]) -> None:
    http, _ = client
    assert http.get("/api/runs", params={"filter": "Whatever"}).status_code == 404


def test_the_detail_carries_everything_the_review_page_shows(
    client: tuple[TestClient, Deps],
) -> None:
    http, deps = client
    run = seed_run(deps)

    body = http.get(f"/api/runs/{run.run_id}").json()

    assert body["run"]["candidate_id"] == run.candidate_id
    assert body["rubric"]["criteria"]
    assert body["banner"]["tier"] == "clean"
    assert body["recommendation"]["band"] == "advance"
    assert body["recommendation"]["derivation"]
    assert len(body["assessments"]) == 4


def test_a_missing_run_is_a_404_with_a_sentence(client: tuple[TestClient, Deps]) -> None:
    http, _ = client
    response = http.get("/api/runs/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["detail"].endswith(".")


def test_a_preview_recomputes_without_saving(client: tuple[TestClient, Deps]) -> None:
    http, deps = client
    run = seed_run(deps)
    override = {
        "criterion_id": "evaluation-practice",
        "previous_state": "met",
        "new_state": "not_met",
        "reason_code": "evidence_misread",
        "reason_text": "The quotation does not mean that.",
    }

    body = http.post(f"/api/runs/{run.run_id}/preview", json={"overrides": [override]}).json()

    assert body["changed"] == ["evaluation-practice"]
    assert body["before"]["band"] == "advance"
    assert body["after"]["band"] != "advance"
    # Nothing was written: the stored recommendation is unchanged.
    assert http.get(f"/api/runs/{run.run_id}").json()["recommendation"]["band"] == "advance"


def test_a_decision_goes_through_the_use_case(client: tuple[TestClient, Deps]) -> None:
    http, deps = client
    run = seed_run(deps)

    response = http.post(
        f"/api/runs/{run.run_id}/decision",
        json={
            "action": "approve",
            "elapsed_seconds": 30,
            "trust_rating": 4,
            "expected_version": run.version,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert deps.runs.get(run.run_id).status is RunStatus.APPROVED
    assert deps.reviews.get_for_run(run.run_id).elapsed_seconds == 30


def test_a_stale_decision_is_refused_with_the_reviewers_message(
    client: tuple[TestClient, Deps],
) -> None:
    """Optimistic concurrency reaches the browser as a 409 and a sentence."""
    http, deps = client
    run = seed_run(deps)
    http.post(
        f"/api/runs/{run.run_id}/decision",
        json={"action": "approve", "expected_version": run.version},
    )

    response = http.post(
        f"/api/runs/{run.run_id}/decision",
        json={"action": "reject", "expected_version": run.version},
    )

    assert response.status_code == 409
    assert (
        "decide" in response.json()["detail"].lower()
        or "reload" in response.json()["detail"].lower()
    )


def test_an_invalid_override_is_refused_before_anything_runs(
    client: tuple[TestClient, Deps],
) -> None:
    """The contract refuses an override that changes nothing, at the edge."""
    http, deps = client
    run = seed_run(deps)
    same = {
        "criterion_id": "evaluation-practice",
        "previous_state": "met",
        "new_state": "met",
        "reason_code": "other",
        "reason_text": "Nothing changed here.",
    }

    assert (
        http.post(f"/api/runs/{run.run_id}/preview", json={"overrides": [same]}).status_code == 422
    )


def test_uploads_are_refused_in_demo_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_from_env(
        db_path=str(tmp_path / "demo.sqlite"), blob_dir=str(tmp_path / "blobs"), demo_mode=True
    )
    monkeypatch.setattr(api_module, "_deps", build_deps(settings))
    try:
        response = TestClient(api_module.app).post(
            "/api/uploads", files=[("files", ("cv.txt", b"hello"))], data={"candidate_ids": ["a"]}
        )
        assert response.status_code == 403
        assert "Demo mode" in response.json()["detail"]
    finally:
        close_thread_connection(settings.db_path)


def test_one_candidate_id_per_file_is_enforced(client: tuple[TestClient, Deps]) -> None:
    http, _ = client
    response = http.post(
        "/api/uploads",
        files=[("files", ("a.txt", b"x")), ("files", ("b.txt", b"y"))],
        data={"candidate_ids": ["only-one"]},
    )

    assert response.status_code == 422
