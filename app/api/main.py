"""The HTTP interface. Same rule as the pages: it renders what a use case returns.

Every route is one use case call and a JSON shape built from contracts with
``model_dump(mode="json")``. Nothing here computes a band, resolves a criterion
or moves a run, and the architecture test that keeps the Streamlit pages honest
walks this file too.

One admin, and every route past ``/api/health`` and ``/api/auth`` requires
that admin's session cookie. The account, the sessions and the settings the
admin page writes are use cases in ``application/use_cases/admin.py``; this
file sets and reads the cookie and nothing more. Transport security is the
deployment's: the cookie is marked secure when the request arrived over HTTPS.

    uvicorn app.api.main:app --reload         # development
    make api                                  # the same, from the Makefile
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.components.status_chip import BAND_LABELS, CHIPS, band_label, chip_for
from app.passages import passage_around, source_for
from app.vocabulary import (
    BAND_HELP,
    DETECTOR_LABELS,
    EMPTY_MESSAGES,
    GLOSSARY,
    HUMAN_REASONS,
    KIND_HELP,
    QUEUE_FILTERS,
    REASON_LABELS,
    SEVERITY_LABELS,
    SPAN_VALIDATION_HELP,
    STATE_HELP,
    STATE_LABELS,
)
from application.deps import Deps
from application.recovery import reconcile
from application.use_cases import admin, rubrics
from application.use_cases.delete_run import DeleteRefused, delete_run, is_sample
from application.use_cases.deliver_approved import deliver_approved
from application.use_cases.list_deliveries import list_deliveries
from application.use_cases.process_batch import process_batch
from application.use_cases.reassess import candidates_from_runs
from application.use_cases.recompute_recommendation import recompute
from application.use_cases.retry_deliveries import retry_deliveries
from application.use_cases.submit_review import ReviewRejected, submit_review
from domain.contracts.enums import Band, ReviewAction
from domain.contracts.review import Override
from domain.ports.sources import CandidateRef, DocumentRef, SourceUnavailable
from domain.security import banner_for
from infrastructure.factory import build_deps, settings_from_env

app = FastAPI(title="Submission Desk", docs_url="/api/docs", openapi_url="/api/openapi.json")

# The session travels as a cookie, so the browser origins that may send it are
# named. The Vite dev server is the default; a deployment lists its own in
# CORS_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings_from_env().cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSION_COOKIE = "sd_session"

_deps: Deps | None = None


def deps() -> Deps:
    """Built once, on first request, the way the Streamlit app builds it."""
    global _deps  # noqa: PLW0603 - one process, one container, built lazily
    if _deps is None:
        _deps = build_deps(settings_from_env())
        reconcile(_deps)
        # A fixed admin account from the environment, before the first request.
        problem = admin.ensure_admin(_deps)
        if problem:
            print(problem, file=sys.stderr)
    return _deps


def rebuild() -> Deps:
    """After the admin page saves: the next request reads the new settings."""
    global _deps  # noqa: PLW0603
    _deps = None
    return deps()


def current_user(request: Request) -> str:
    """The guard. Every route past health and auth depends on it."""
    user = admin.session_user(deps(), request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    return user


Guarded = Depends(current_user)


# --- auth -------------------------------------------------------------------------------


class Credentials(BaseModel):
    username: str
    password: str


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict[str, Any]:
    """Whether the account exists yet, and who this session belongs to."""
    wired = deps()
    return {
        "setup_required": admin.setup_required(wired),
        "user": admin.session_user(wired, request.cookies.get(SESSION_COOKIE)),
    }


@app.post("/api/auth/setup", status_code=201)
def auth_setup(body: Credentials, request: Request, response: Response) -> dict[str, Any]:
    """Create the one admin account, then sign it in. Refused once it exists."""
    try:
        admin.create_admin(deps(), body.username, body.password)
        token = admin.login(deps(), body.username, body.password)
    except admin.AdminRefused as refused:
        raise HTTPException(409, str(refused)) from refused
    _set_session(request, response, token)
    return {"user": body.username.strip()}


@app.post("/api/auth/login")
def auth_login(body: Credentials, request: Request, response: Response) -> dict[str, Any]:
    try:
        token = admin.login(deps(), body.username, body.password)
    except admin.AdminRefused as refused:
        raise HTTPException(401, str(refused)) from refused
    _set_session(request, response, token)
    return {"user": body.username.strip()}


@app.post("/api/auth/logout", status_code=204)
def auth_logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def _set_session(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=admin.SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        # Secure only when the request itself came over TLS; a cookie that is
        # never sent over plain HTTP would lock the local demo out.
        secure=request.url.scheme == "https",
        path="/",
    )


# --- admin ------------------------------------------------------------------------------


class SettingsBody(BaseModel):
    changes: dict[str, str] = {}
    keys: dict[str, str] = {}


@app.get("/api/admin/settings")
def admin_settings(_user: str = Guarded) -> dict[str, Any]:
    view = admin.describe_settings(deps())
    return {
        "values": view.values,
        "keys_set": view.keys_set,
        "demo_mode": view.demo_mode,
        "effective_provider": view.effective_provider,
    }


@app.put("/api/admin/settings")
def admin_save(body: SettingsBody, _user: str = Guarded) -> dict[str, Any]:
    try:
        admin.save_settings(deps(), body.changes, body.keys)
    except admin.AdminRefused as refused:
        raise HTTPException(422, str(refused)) from refused
    try:
        rebuild()
    except Exception as failure:
        raise HTTPException(
            422, f"Saved, but the system could not start with these settings: {failure}"
        ) from failure
    return admin_settings(_user)


@app.delete("/api/admin/keys/{provider}", status_code=204)
def admin_clear_key(provider: str, _user: str = Guarded) -> None:
    try:
        admin.clear_key(deps(), provider)
    except admin.AdminRefused as refused:
        raise HTTPException(404, str(refused)) from refused
    rebuild()


@app.post("/api/admin/probe")
def admin_probe(_user: str = Guarded) -> list[dict[str, Any]]:
    """One tiny call per tier through the configured provider, and what it cost."""
    return [
        {
            "ok": result.ok,
            "message": result.message,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "tier": result.tier,
        }
        for result in admin.probe_provider(deps())
    ]


# --- roles ------------------------------------------------------------------------------


@app.get("/api/roles")
def roles(_user: str = Guarded) -> list[dict[str, Any]]:
    """Every role a candidate can be assessed against."""
    return [role.__dict__ for role in rubrics.list_roles(deps())]


@app.get("/api/admin/rubrics/{role_id}")
def rubric_detail(role_id: str, _user: str = Guarded) -> dict[str, Any]:
    try:
        return rubrics.describe_rubric(deps(), role_id).__dict__
    except admin.AdminRefused as refused:
        raise HTTPException(404, str(refused)) from refused


class RubricBody(BaseModel):
    data: dict[str, Any]


@app.put("/api/admin/rubrics/{role_id}")
def rubric_save(role_id: str, body: RubricBody, _user: str = Guarded) -> dict[str, Any]:
    """Store a rubric once the whole of it validates; in force on the next run."""
    try:
        view = rubrics.save_rubric(deps(), role_id, body.data)
    except admin.AdminRefused as refused:
        raise HTTPException(422, str(refused)) from refused
    rebuild()
    return view.__dict__


@app.delete("/api/admin/rubrics/{role_id}", status_code=204)
def rubric_reset(role_id: str, _user: str = Guarded) -> None:
    """Back to the shipped file; a role the admin created is removed."""
    rubrics.reset_rubric(deps(), role_id)
    rebuild()


@app.delete("/api/runs/{run_id}", status_code=204)
def run_delete(run_id: UUID, _user: str = Guarded) -> None:
    """Remove a candidate's run and the documents nothing else uses."""
    try:
        delete_run(deps(), run_id)
    except DeleteRefused as refused:
        raise HTTPException(409, str(refused)) from refused


class Pull(BaseModel):
    role_id: str


@app.post("/api/source/pull", status_code=202)
def pull(body: Pull, _user: str = Guarded) -> dict[str, Any]:
    """Read every candidate in the configured source and process them.

    The web form of ``submission-desk process``: the same listing, the same
    batch, on a thread. A source that cannot be reached answers with the
    adapter's sentence, which names the share or the setting to fix.
    """
    wired = deps()
    if wired.settings.demo_mode:
        raise HTTPException(403, "Demo mode is on, so nothing new is processed.")
    if body.role_id not in {role.role_id for role in rubrics.list_roles(wired)}:
        raise HTTPException(422, f"There is no role called {body.role_id!r}.")
    try:
        candidates = wired.source.list_candidates(limit=50)
    except SourceUnavailable as unavailable:
        raise HTTPException(503, str(unavailable)) from unavailable
    if candidates:
        threading.Thread(
            target=process_batch,
            args=(candidates, body.role_id, wired),
            daemon=True,
            name="pull-batch",
        ).start()
    return {
        "accepted": len(candidates),
        "source": getattr(wired.source, "source_id", "local"),
        "role_id": body.role_id,
    }


@app.post("/api/deliveries")
def deliver(_user: str = Guarded) -> dict[str, Any]:
    """Send every approved package, then retry any that half-failed.

    The web form of ``submission-desk deliver`` and ``retry-deliveries``, in
    that order. Synchronous: a delivery is one append per sink, and the
    sentence that comes back is the point of pressing the button.
    """
    wired = deps()
    if wired.settings.demo_mode:
        raise HTTPException(403, "Demo mode is on, so nothing is sent anywhere.")
    sent = deliver_approved(wired)
    retried = retry_deliveries(wired)
    sentence = sent.sentence()
    if retried.attempted:
        sentence = f"{sentence} {retried.sentence()}"
    return {
        "delivered": len(sent.delivered) + len(retried.completed),
        "pending_retry": len(sent.pending_retry) + len(retried.still_failing),
        "skipped": len(sent.skipped) + len(retried.refused),
        "sentence": sentence,
    }


@app.get("/api/deliveries")
def sent(_user: str = Guarded) -> list[dict[str, Any]]:
    """Every run that reached delivery: the row each destination was given,
    and what each destination did with it. The spreadsheet's table, here."""
    return [
        {
            "run_id": row.payload.run_id,
            "candidate_id": row.payload.candidate_id,
            "role_id": row.payload.role_id,
            "band": row.payload.band,
            "band_label": band_label(_band_enum(row.payload.band)),
            "score": row.payload.score,
            "coverage": row.payload.coverage,
            "reviewer_id": row.payload.reviewer_id,
            "decision": row.payload.reviewer_action,
            "decided_at": row.payload.decided_at,
            "corrections": row.payload.override_count,
            "integrity": row.payload.integrity_tier,
            "reasoning": list(row.payload.derivation),
            "status": row.status.value,
            "destinations": [item.__dict__ for item in row.destinations],
        }
        for row in list_deliveries(deps())
    ]


def _band_enum(value: str) -> Band | None:
    try:
        return Band(value)
    except ValueError:
        return None


class Reassess(BaseModel):
    run_ids: list[UUID]
    role_id: str


@app.post("/api/runs/reassess", status_code=202)
def reassess(body: Reassess, _user: str = Guarded) -> dict[str, Any]:
    """Run the chosen candidates' stored documents against a role."""
    wired = deps()
    if wired.settings.demo_mode:
        raise HTTPException(403, "Demo mode is on, so nothing new is processed.")
    if body.role_id not in {role.role_id for role in rubrics.list_roles(wired)}:
        raise HTTPException(422, f"There is no role called {body.role_id!r}.")
    try:
        candidates = candidates_from_runs(wired, body.run_ids)
    except admin.AdminRefused as refused:
        raise HTTPException(404, str(refused)) from refused
    threading.Thread(
        target=process_batch,
        args=(candidates, body.role_id, wired),
        daemon=True,
        name="reassess-batch",
    ).start()
    return {"accepted": len(candidates), "role_id": body.role_id}


def _json(value: Any) -> Any:
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


# --- read -------------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    wired = deps()
    settings = wired.settings
    return {
        "ok": True,
        "demo_mode": settings.demo_mode,
        "reviewer_configured": bool(settings.reviewer_id),
        "provider": settings.model_provider,
        # Names only — which adapters are wired, never an id or a token — so
        # the queue can offer "pull from Drive" and "send approved" when they
        # would do something.
        "source": getattr(wired.source, "source_id", "local"),
        "sinks": [getattr(sink, "sink_id", "?") for sink in wired.sinks or ()],
        "notifier": getattr(wired.notifier, "notifier_id", "none"),
    }


@app.get("/api/vocabulary")
def vocabulary(_user: str = Guarded) -> dict[str, Any]:
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
        "band_help": {band.value: text for band, text in BAND_HELP.items()},
        "state_help": {state.value: text for state, text in STATE_HELP.items()},
        "kinds": {kind.value: text for kind, text in KIND_HELP.items()},
        "span_validation": {check.value: text for check, text in SPAN_VALIDATION_HELP.items()},
        "human_reasons": HUMAN_REASONS,
        "glossary": GLOSSARY,
        "detectors": DETECTOR_LABELS,
        "severities": {severity.value: label for severity, label in SEVERITY_LABELS.items()},
        "filters": {
            name: [status.value for status in statuses] for name, statuses in QUEUE_FILTERS.items()
        },
        "empty": EMPTY_MESSAGES,
    }


@app.get("/api/runs")
def runs(
    filter: str = "Needs attention", limit: int = 200, _user: str = Guarded
) -> list[dict[str, Any]]:
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
        # One of the candidates that ship with the system: shown as such, and
        # the interface does not offer to delete it.
        "sample": is_sample(deps(), run.run_id),
    }


@app.get("/api/runs/{run_id}")
def run_detail(run_id: UUID, _user: str = Guarded) -> dict[str, Any]:
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
def passage(run_id: UUID, evidence_id: UUID, _user: str = Guarded) -> dict[str, Any]:
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
def preview(run_id: UUID, body: Preview, _user: str = Guarded) -> dict[str, Any]:
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
def decide(run_id: UUID, body: Decision, _user: str = Guarded) -> dict[str, Any]:
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
    _user: str = Guarded,
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
    if role_id not in {role.role_id for role in rubrics.list_roles(wired)}:
        raise HTTPException(422, f"There is no role called {role_id!r}.")

    grouped: dict[str, list[DocumentRef]] = {}
    for upload_file, candidate_id in zip(files, candidate_ids, strict=True):
        payload = await upload_file.read()
        name = candidate_id.strip() or "candidate"
        refs = grouped.setdefault(name, [])
        # Into the content-addressed store, under its hash, and referenced by
        # that hash: intake reads it from there whatever the configured source
        # is, so an upload works the same whether the source is a folder or a
        # Drive account. A filename from outside never decides where bytes land.
        digest = wired.blobs.put(payload)
        refs.append(
            DocumentRef(
                candidate_id=name,
                filename=upload_file.filename or f"{name}-{len(refs)}",
                external_ref=f"upload:{digest}",
                size_bytes=len(payload),
                metadata={"sha256": digest},
            )
        )

    candidates = [
        CandidateRef(candidate_id=name, documents=tuple(refs)) for name, refs in grouped.items()
    ]
    threading.Thread(
        target=process_batch, args=(candidates, role_id, wired), daemon=True, name="process-batch"
    ).start()
    return {"accepted": len(candidates), "candidate_ids": list(grouped)}


# --- the frontend, when it has been built ------------------------------------------------
#
# One origin for the page and the API: the session cookie is same-site, no
# CORS list has to name the deployment, and one process serves the whole thing.
# The API routes above take precedence; everything else is the built page,
# whose hash router handles the rest. Absent in development, where Vite serves
# the page and proxies /api here.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
