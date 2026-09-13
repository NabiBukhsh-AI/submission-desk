"""The two command-line actions the queue page can now do: pull the source,
send what is approved — and the table of what was sent.

What is asserted: pulling reads the configured source and accepts every
candidate found; an empty source accepts nothing and starts nothing; a source
that cannot be reached answers with its sentence and a 503; sending delivers
each approved run and reports in one sentence; the health line names the
adapters and never an id; demo mode refuses both.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import main as api_module
from application.deps import Deps
from application.use_cases.process_candidate import process_candidate
from application.use_cases.submit_review import submit_review
from domain.contracts.enums import ReviewAction
from domain.ports.sources import CandidateRef, DocumentRef, SourceUnavailable
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from tests.fixtures import pdfs
from tests.integration.test_api_roles import CV


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Deps]]:
    settings = settings_from_env(
        db_path=str(tmp_path / "pull.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        inbox_dir=str(tmp_path / "inbox"),
        reviewer_id="rec-9",
    )
    Path(settings.inbox_dir).mkdir()
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


def _drop_cv(deps: Deps, name: str) -> Path:
    path = Path(deps.source.root) / f"{name}.pdf"
    path.write_bytes(pdfs.text_pdf(CV))
    return path


class _Unreachable:
    source_id = "drive"
    folder_id = "shared-folder"

    def list_candidates(self, limit: int = 50) -> list[CandidateRef]:
        raise SourceUnavailable("The Drive folder is not shared with the service account.")


# --- health names the adapters ------------------------------------------------------------


def test_health_names_the_adapters_and_nothing_else(client: tuple[TestClient, Deps]) -> None:
    http, _ = client

    body = http.get("/api/health").json()

    assert body["source"] == "local"
    assert body["sinks"] == ["csv"]
    assert body["notifier"] == "console"
    assert not any(key.endswith("_id") or "token" in key for key in body)


# --- pulling --------------------------------------------------------------------------------


def test_pulling_reads_the_source_and_processes_everything_found(
    client: tuple[TestClient, Deps],
) -> None:
    http, deps = client
    _drop_cv(deps, "ana-ferreira")
    _drop_cv(deps, "ben-okoro")

    response = http.post("/api/source/pull", json={"role_id": "ai-engineer"})

    assert response.status_code == 202
    assert response.json() == {"accepted": 2, "source": "local", "role_id": "ai-engineer"}


def test_an_empty_source_accepts_nothing(client: tuple[TestClient, Deps]) -> None:
    http, _ = client

    response = http.post("/api/source/pull", json={"role_id": "ai-engineer"})

    assert response.status_code == 202
    assert response.json()["accepted"] == 0


def test_an_unreachable_source_answers_with_its_sentence(
    client: tuple[TestClient, Deps], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, deps = client
    monkeypatch.setattr(api_module, "_deps", Deps(**{**deps.__dict__, "source": _Unreachable()}))

    response = http.post("/api/source/pull", json={"role_id": "ai-engineer"})

    assert response.status_code == 503
    assert "not shared" in response.json()["detail"]


def test_pulling_against_an_unknown_role_is_refused(client: tuple[TestClient, Deps]) -> None:
    http, _ = client

    assert http.post("/api/source/pull", json={"role_id": "astronaut"}).status_code == 422


# --- sending --------------------------------------------------------------------------------


def _approved(deps: Deps) -> None:
    path = _drop_cv(deps, uuid4().hex[:8])
    candidate = CandidateRef(
        candidate_id="ana",
        documents=(DocumentRef(candidate_id="ana", filename=path.name, external_ref=str(path)),),
    )
    result = process_candidate(candidate, "ai-engineer", deps)
    submit_review(result.run_id, ReviewAction.APPROVE, deps)


def test_sending_delivers_every_approved_run(client: tuple[TestClient, Deps]) -> None:
    http, deps = client
    _approved(deps)

    response = http.post("/api/deliveries")

    assert response.status_code == 200
    body = response.json()
    assert body["delivered"] == 1
    assert body["pending_retry"] == 0
    assert "1 sent" in body["sentence"]
    rows = http.get("/api/runs", params={"filter": "Decided"}).json()
    assert [row["status"] for row in rows] == ["delivered"]


def test_the_sent_table_is_the_spreadsheet_row_plus_each_destination(
    client: tuple[TestClient, Deps],
) -> None:
    """What the page shows is what every sink was given, never the evidence."""
    http, deps = client
    _approved(deps)
    http.post("/api/deliveries")

    [row] = http.get("/api/deliveries").json()

    assert row["candidate_id"] == "ana"
    assert row["role_id"] == "ai-engineer"
    assert row["decision"] == "approve"
    assert row["reviewer_id"] == "rec-9"
    assert row["status"] == "delivered"
    assert isinstance(row["reasoning"], list) and row["reasoning"]
    [csv] = row["destinations"]
    assert csv["sink_id"] == "csv"
    assert csv["status"] == "delivered"
    assert csv["reference"].endswith("results.csv:1")
    assert not {"evidence", "quotes", "spans"} & set(row)


def test_the_sent_table_is_empty_before_anything_is_sent(client: tuple[TestClient, Deps]) -> None:
    http, deps = client
    _approved(deps)

    assert http.get("/api/deliveries").json() == []


def test_sending_with_nothing_approved_says_so(client: tuple[TestClient, Deps]) -> None:
    http, _ = client

    body = http.post("/api/deliveries").json()

    assert body["delivered"] == 0
    assert "nothing approved" in body["sentence"].lower()


# --- demo mode ------------------------------------------------------------------------------


def test_demo_mode_refuses_both(
    client: tuple[TestClient, Deps], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, deps = client
    demo = Deps(
        **{
            **deps.__dict__,
            "settings": type(deps.settings)(**{**deps.settings.__dict__, "demo_mode": True}),
        }
    )
    monkeypatch.setattr(api_module, "_deps", demo)

    assert http.post("/api/source/pull", json={"role_id": "ai-engineer"}).status_code == 403
    assert http.post("/api/deliveries").status_code == 403
