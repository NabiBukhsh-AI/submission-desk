"""Roles the admin can see and change, and candidates run again against one.

What is asserted: every shipped rubric is a role; a rubric the admin saves
wins over the file and is in force on the next run, with a different hash; a
rubric that does not validate is refused whole; reset returns to the file; a
run's stored documents can be assessed against another role without the
inbox, and the same role again is recognised rather than repeated.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import main as api_module
from application.deps import Deps
from application.use_cases.process_batch import process_batch
from application.use_cases.process_candidate import process_candidate
from application.use_cases.reassess import candidates_from_runs
from domain.ports.sources import CandidateRef, DocumentRef
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from tests.fixtures import pdfs

CV = (
    "Ana Ferreira. Built the React front end for the customer portal, launched to "
    "customers in 2022. Designed the REST API in Node.js. Wrote unit and integration "
    "tests. I am eligible to work in the United Kingdom without sponsorship."
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Deps]]:
    settings = settings_from_env(
        db_path=str(tmp_path / "roles.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        inbox_dir=str(tmp_path / "inbox"),
        reviewer_id="rec-9",
    )
    Path(settings.inbox_dir).mkdir()
    # The API rebuilds its container from the environment after a save.
    monkeypatch.setenv("DATABASE_PATH", settings.db_path)
    monkeypatch.setenv("BLOB_DIR", settings.blob_dir)
    monkeypatch.setenv("INBOX_DIR", settings.inbox_dir)
    monkeypatch.setenv("REVIEWER_ID", "rec-9")
    deps = build_deps(settings)
    monkeypatch.setattr(api_module, "_deps", deps)
    http = TestClient(api_module.app)
    http.post("/api/auth/setup", json={"username": "admin", "password": "correct horse battery"})
    yield http, deps
    close_thread_connection(settings.db_path)


def _candidate(deps: Deps) -> CandidateRef:
    path = Path(deps.source.root) / f"{uuid4().hex[:8]}.pdf"
    path.write_bytes(pdfs.text_pdf(CV))
    return CandidateRef(
        candidate_id="ana",
        documents=(DocumentRef(candidate_id="ana", filename=path.name, external_ref=str(path)),),
    )


# --- roles ---------------------------------------------------------------------------


def test_every_shipped_rubric_is_a_role(client: tuple[TestClient, Deps]) -> None:
    http, _ = client

    roles = {role["role_id"]: role for role in http.get("/api/roles").json()}

    assert set(roles) >= {"ai-engineer", "fullstack-developer", "flutter-mobile-developer"}
    assert roles["fullstack-developer"]["role_title"] == "Full Stack Developer"
    assert roles["fullstack-developer"]["source"] == "file"
    assert roles["fullstack-developer"]["criteria"] == 9


def test_the_rubric_is_shown_as_data_and_as_yaml(client: tuple[TestClient, Deps]) -> None:
    http, _ = client

    body = http.get("/api/admin/rubrics/ai-engineer").json()

    assert body["data"]["role_title"] == "AI Engineer"
    assert body["data"]["criteria"][0]["id"] == "production-llm-delivery"
    assert "role_title: AI Engineer" in body["yaml"]
    assert body["source"] == "file"


def test_a_saved_rubric_wins_over_the_file_and_changes_the_hash(
    client: tuple[TestClient, Deps],
) -> None:
    http, _ = client
    before = http.get("/api/admin/rubrics/ai-engineer").json()
    data = before["data"]
    data["criteria"][0]["weight"] = 9
    data["criteria"][0]["label"] = "Has shipped a language-model feature (edited)"

    saved = http.put("/api/admin/rubrics/ai-engineer", json={"data": data})

    assert saved.status_code == 200, saved.text
    assert saved.json()["source"] == "admin"
    assert saved.json()["rubric_hash"] != before["rubric_hash"]
    # The running system reads the saved copy, not the file.
    rubric = api_module.deps().rubric_loader("ai-engineer")
    assert rubric.criteria[0].weight == 9
    assert "(edited)" in rubric.criteria[0].label


def test_a_rubric_that_does_not_validate_is_refused_whole(
    client: tuple[TestClient, Deps],
) -> None:
    http, _ = client
    data = http.get("/api/admin/rubrics/ai-engineer").json()["data"]
    data["criteria"][0]["weight"] = 50

    response = http.put("/api/admin/rubrics/ai-engineer", json={"data": data})

    assert response.status_code == 422
    assert "weight" in response.json()["detail"]
    assert http.get("/api/admin/rubrics/ai-engineer").json()["source"] == "file"


def test_reset_returns_to_the_file(client: tuple[TestClient, Deps]) -> None:
    http, _ = client
    data = http.get("/api/admin/rubrics/ai-engineer").json()["data"]
    data["role_title"] = "AI Engineer (Contract)"
    http.put("/api/admin/rubrics/ai-engineer", json={"data": data})

    assert http.delete("/api/admin/rubrics/ai-engineer").status_code == 204

    body = http.get("/api/admin/rubrics/ai-engineer").json()
    assert body["source"] == "file"
    assert body["data"]["role_title"] == "AI Engineer"


def test_a_new_role_can_be_created_from_an_existing_one(client: tuple[TestClient, Deps]) -> None:
    http, _ = client
    data = http.get("/api/admin/rubrics/fullstack-developer").json()["data"]
    data["role_title"] = "Frontend Developer"

    saved = http.put("/api/admin/rubrics/frontend-developer", json={"data": data})

    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["role_id"] == "frontend-developer"
    roles = {role["role_id"]: role for role in http.get("/api/roles").json()}
    assert roles["frontend-developer"]["source"] == "admin"
    assert roles["frontend-developer"]["role_title"] == "Frontend Developer"


# --- assessing again -------------------------------------------------------------------


def test_stored_documents_run_against_another_role_without_the_inbox(
    client: tuple[TestClient, Deps],
) -> None:
    http, deps = client
    first = process_candidate(_candidate(deps), "ai-engineer", deps)
    for path in Path(deps.source.root).iterdir():
        path.unlink()  # the inbox is gone; the blob store is what remains

    candidates = candidates_from_runs(deps, [first.run_id])
    summary = process_batch(candidates, "fullstack-developer", deps)

    assert summary.reviewable == 1
    rows = http.get("/api/runs", params={"filter": "Needs attention"}).json()
    assert {row["role_id"] for row in rows} == {"ai-engineer", "fullstack-developer"}


def test_the_same_role_again_is_recognised_not_repeated(client: tuple[TestClient, Deps]) -> None:
    _, deps = client
    first = process_candidate(_candidate(deps), "ai-engineer", deps)

    summary = process_batch(candidates_from_runs(deps, [first.run_id]), "ai-engineer", deps)

    assert summary.reused == 1


def test_the_route_accepts_the_batch_and_names_the_role(client: tuple[TestClient, Deps]) -> None:
    http, deps = client
    first = process_candidate(_candidate(deps), "ai-engineer", deps)

    response = http.post(
        "/api/runs/reassess",
        json={"run_ids": [str(first.run_id)], "role_id": "flutter-mobile-developer"},
    )

    assert response.status_code == 202
    assert response.json() == {"accepted": 1, "role_id": "flutter-mobile-developer"}


def test_an_unknown_role_is_refused(client: tuple[TestClient, Deps]) -> None:
    http, deps = client
    first = process_candidate(_candidate(deps), "ai-engineer", deps)

    response = http.post(
        "/api/runs/reassess", json={"run_ids": [str(first.run_id)], "role_id": "astronaut"}
    )

    assert response.status_code == 422
