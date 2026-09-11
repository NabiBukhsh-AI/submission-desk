"""The HTTP interface. Same rule as the pages: it renders what a use case returns.

Every route is one use case call and a JSON shape built from contracts with
``model_dump(mode="json")``. Nothing here computes a band, resolves a criterion
or moves a run, and the architecture test that keeps the Streamlit pages honest
walks this file too.

No authentication. Anybody who can reach the port can review; the runbook and
the threat model say so, and a deployment beyond one reviewer on one machine
puts an identity layer in front of this process.

    uvicorn app.api.main:app --reload         # development
    make api                                  # the same, from the Makefile
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.components.status_chip import BAND_LABELS, CHIPS, band_label, chip_for
from app.passages import passage_around, source_for
from app.vocabulary import (
    DETECTOR_LABELS,
    EMPTY_MESSAGES,
    QUEUE_FILTERS,
    REASON_LABELS,
    SEVERITY_LABELS,
    STATE_LABELS,
)
from application.deps import Deps
from application.recovery import reconcile
from application.use_cases.process_batch import process_batch
from application.use_cases.recompute_recommendation import recompute
from application.use_cases.submit_review import ReviewRejected, submit_review
from domain.contracts.enums import ReviewAction
from domain.contracts.review import Override
from domain.ports.sources import CandidateRef, DocumentRef
from domain.security import banner_for
from infrastructure.factory import build_deps, settings_from_env

app = FastAPI(title="Submission Desk", docs_url="/api/docs", openapi_url="/api/openapi.json")

# The Vite dev server and a static build served from anywhere. Wide open on
# purpose: there is no credential to protect, and a stricter list would be
# security theatre in front of an unauthenticated API.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_deps: Deps | None = None


def deps() -> Deps:
    """Built once, on first request, the way the Streamlit app builds it."""
    global _deps  # noqa: PLW0603 - one process, one container, built lazily
    if _deps is None:
        _deps = build_deps(settings_from_env())
        reconcile(_deps)
    return _deps


def _json(value: Any) -> Any:
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


# --- read -------------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    settings = deps().settings
    return {
        "ok": True,
        "demo_mode": settings.demo_mode,
        "reviewer_configured": bool(settings.reviewer_id),
        "provider": settings.model_provider,
    }


@app.get("/api/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every sentence the interface shows, so no client invents its own."""
    return {
        "chips": {
            status.value: {
                "label": chip.label,
                "needs_attention": chip.needs_attention,
                "help": chip.help,
            }
            for status, chip in CHIPS.items()
        },
        "bands": {band.value: label for band, label in BAND_LABELS.items()},
        "states": {state.value: label for state, label in STATE_LABELS.items()},
        "reasons": {reason.value: label for reason, label in REASON_LABELS.items()},
        "detectors": DETECTOR_LABELS,
        "severities": {severity.value: label for severity, label in SEVERITY_LABELS.items()},
        "filters": {
            name: [status.value for status in statuses] for name, statuses in QUEUE_FILTERS.items()
        },
        "empty": EMPTY_MESSAGES,
    }


@app.get("/api/runs")
def runs(filter: str = "Needs attention", limit: int = 200) -> list[dict[str, Any]]:
    statuses = QUEUE_FILTERS.get(filter)
    if statuses is None:
        raise HTTPException(404, f"no filter named {filter!r}")
    return [_run_row(run) for run in deps().runs.list_by_status(statuses, limit=limit)]


def _run_row(run: Any) -> dict[str, Any]:
    chip = chip_for(run.status)
    return {
        **_json(run),
        "chip": {"label": chip.label, "needs_attention": chip.needs_attention, "help": chip.help},
        "band_label": band_label(run.final_band),
    }


@app.get("/api/runs/{run_id}")
def run_detail(run_id: UUID) -> dict[str, Any]:
    """Everything the review page shows, in one round trip."""
    wired = deps()
    run = wired.runs.get(run_id)
    if run is None:
        raise HTTPException(404, "There is no run with that id.")
    if wired.rubric_loader is None:
        raise HTTPException(500, "No rubrics are configured.")

    rubric = wired.rubric_loader(run.role_id)
    reports = wired.candidates.integrity_reports_for_run(run_id)
    return {
        "run": _run_row(run),
        "rubric": _json(rubric),
        "banner": {"tier": run.integrity_tier.value, "message": banner_for(run.integrity_tier)},
        "recommendation": _json(wired.evidence.recommendation_for_run(run_id)),
        "assessments": [_json(item) for item in wired.evidence.assessments_for_run(run_id)],
        "documents": [_json(item) for item in wired.candidates.documents_for_run(run_id)],
        "reports": [_json(item) for item in reports],
    }


@app.get("/api/runs/{run_id}/passages/{evidence_id}")
def passage(run_id: UUID, evidence_id: UUID) -> dict[str, Any]:
    """The quotation in its paragraph: "show me where"."""
    wired = deps()
    item = next(
        (
            evidence
            for assessment in wired.evidence.assessments_for_run(run_id)
            for evidence in assessment.evidence
            if evidence.evidence_id == evidence_id
        ),
        None,
    )
    if item is None or item.provenance is None or not item.verbatim_span:
        raise HTTPException(
            404, "This item did not quote the document, so there is nothing to show."
        )

    documents = wired.candidates.documents_for_run(run_id)
    source = source_for(wired, documents, item.provenance.document_id)
    if source is None:
        raise HTTPException(
            410, "The document's text is no longer stored, so the passage cannot be shown."
        )

    before, quoted, after = passage_around(source, item, item.provenance)
    return {"page": item.provenance.page_start, "before": before, "quoted": quoted, "after": after}


# --- write ------------------------------------------------------------------------------


class Preview(BaseModel):
    overrides: list[Override] = []


@app.post("/api/runs/{run_id}/preview")
def preview(run_id: UUID, body: Preview) -> dict[str, Any]:
    """What the band becomes if these corrections are applied. Nothing is saved."""
    wired = deps()
    before = wired.evidence.recommendation_for_run(run_id)
    if before is None:
        raise HTTPException(404, "No recommendation was produced for this candidate.")
    try:
        result = recompute(run_id, wired, body.overrides)
    except LookupError as missing:
        raise HTTPException(404, str(missing)) from missing
    return {
        "before": _json(before),
        "after": _json(result.recommendation),
        "changed": result.changed,
    }


class Decision(BaseModel):
    action: ReviewAction
    overrides: list[Override] = []
    comments: str | None = None
    elapsed_seconds: int = 0
    trust_rating: int | None = None
    expected_version: int | None = None


@app.post("/api/runs/{run_id}/decision")
def decide(run_id: UUID, body: Decision) -> dict[str, Any]:
    try:
        result = submit_review(
            run_id,
            body.action,
            deps(),
            overrides=body.overrides,
            comments=body.comments or None,
            elapsed_seconds=body.elapsed_seconds,
            trust_rating=body.trust_rating,
            expected_version=body.expected_version,
        )
    except ReviewRejected as refused:
        raise HTTPException(409, refused.message) from refused
    return {
        "status": result.status.value,
        "band": result.band.value if result.band else None,
        "message": result.message,
    }


@app.post("/api/uploads", status_code=202)
async def upload(
    files: Annotated[list[UploadFile], File()],
    candidate_ids: Annotated[list[str], Form()],
    role_id: Annotated[str, Form()] = "ai-engineer",
) -> dict[str, Any]:
    """Accept documents and process them in the background.

    One ``candidate_ids`` entry per file, in the same order, so the client's
    corrected grouping is what is processed. Processing runs on a thread and
    the queue shows progress by status; a request that blocked for a minute
    per candidate would be timed out by the first proxy it met.
    """
    wired = deps()
    if wired.settings.demo_mode:
        raise HTTPException(
            403,
            "Demo mode is on, so uploads are switched off and only the synthetic "
            "candidates are read. Unset DEMO_MODE to process real documents.",
        )
    if len(files) != len(candidate_ids):
        raise HTTPException(422, "One candidate id per file, in the same order.")

    inbox = Path(wired.settings.blob_dir).parent / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    grouped: dict[str, list[DocumentRef]] = {}
    for upload_file, candidate_id in zip(files, candidate_ids, strict=True):
        payload = await upload_file.read()
        name = candidate_id.strip() or "candidate"
        refs = grouped.setdefault(name, [])
        # Written under the candidate id rather than the uploaded name: a
        # filename from outside never decides where bytes land.
        target = inbox / f"{name}-{len(refs)}{Path(upload_file.filename or '').suffix}"
        target.write_bytes(payload)
        refs.append(
            DocumentRef(
                candidate_id=name,
                filename=upload_file.filename or target.name,
                external_ref=str(target),
                size_bytes=len(payload),
            )
        )

    candidates = [
        CandidateRef(candidate_id=name, documents=tuple(refs)) for name, refs in grouped.items()
    ]
    threading.Thread(
        target=process_batch, args=(candidates, role_id, wired), daemon=True, name="process-batch"
    ).start()
    return {"accepted": len(candidates), "candidate_ids": list(grouped)}
