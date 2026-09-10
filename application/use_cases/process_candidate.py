"""Process one candidate, start to reviewable.

The entry point both the command line and the interface call. It creates the
run, drives the pipeline as far as the reviewer, and stops there.

Stopping there is the approval gate. Everything up to and including composition
happens automatically; nothing past it happens without a persisted decision, and
the runner enforces that by not being asked to go further.

Idempotency is handled before anything is spent. The same documents under the
same configuration produce the same content key, and a run that already exists
for that key is returned rather than repeated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from application.deps import Deps
from application.runner import RunOutcome, run
from domain.contracts.enums import RunStatus
from domain.contracts.run import RunRecord
from domain.contracts.run_state import RunState
from domain.identity import content_key, new_run_id
from domain.ports.sources import CandidateRef
from domain.rules import rubric_hash
from domain.state_machine import TERMINAL, is_reviewable

#: The pipeline runs to here and stops. REVIEW stages the run for a person;
#: DELIVER is downstream of their decision and is never reached from here.
STOP_AFTER = "REVIEW"

#: Bumped when a change alters what a run produces from the same inputs. Part of
#: the content key, so an old cached result is not served after the change.
PIPELINE_VERSION = "1"


@dataclass(frozen=True)
class ProcessResult:
    """What happened, and whether anything was actually done."""

    run_id: object
    status: RunStatus
    outcome: RunOutcome | None = None
    reused_existing: bool = False
    message: str = ""

    @property
    def is_reviewable(self) -> bool:
        return is_reviewable(self.status)


def process_candidate(
    candidate: CandidateRef,
    role_id: str,
    deps: Deps,
    *,
    force: bool = False,
) -> ProcessResult:
    """Take one candidate's documents as far as a reviewer.

    ``force`` starts a new run even when an identical one exists, for the case
    where somebody deliberately wants the work repeated.
    """
    existing = None if force else _existing_run(deps, candidate, role_id)
    if existing is not None:
        return ProcessResult(
            run_id=existing.run_id,
            status=existing.status,
            reused_existing=True,
            message=(
                "This candidate was already processed with this configuration on "
                f"{existing.started_at.date().isoformat()}. Open that result, or "
                "re-run to process them again."
            ),
        )

    record = _new_record(deps, candidate, role_id)
    deps.runs.create(record)

    state = RunState(
        run_id=record.run_id,
        candidate_id=record.candidate_id,
        role_id=record.role_id,
        status=RunStatus.CREATED,
        started_at=record.started_at,
        blind_mode=record.blind_mode,
        calibration_enabled=record.calibration_enabled,
        document_refs=tuple(candidate.documents),
    )

    outcome = run(state, deps, stop_after=STOP_AFTER)

    return ProcessResult(
        run_id=record.run_id,
        status=outcome.status,
        outcome=outcome,
        message=_message_for(outcome),
    )


def _existing_run(deps: Deps, candidate: CandidateRef, role_id: str) -> RunRecord | None:
    """A previous run of exactly this work, if there is one.

    Only for documents already stored: a first-time candidate has no hashes yet,
    and hashing them here would mean fetching every file before deciding whether
    to do the work, which is the cost this lookup exists to avoid.
    """
    hashes = [
        document.metadata["sha256"]
        for document in candidate.documents
        if "sha256" in document.metadata
    ]
    if not hashes:
        return None

    key = content_key(
        hashes,
        rubric_hash=_rubric_hash(deps, role_id),
        prompt_bundle_hash=_prompt_hash(deps),
        model_tier_bindings_hash=_bindings_hash(deps),
        routing_policy_id=deps.settings.routing_policy_id,
        blind_mode=deps.settings.blind_mode,
        calibration_enabled=deps.settings.calibration_enabled,
        pipeline_version=PIPELINE_VERSION,
    )

    found = deps.runs.find_by_content_key(key)
    if found is None:
        return None

    # A run that failed for an environmental reason is worth repeating; one that
    # reached a reviewer is not.
    if found.status is RunStatus.FAILED_TERMINAL:
        return None
    return found


def _new_record(deps: Deps, candidate: CandidateRef, role_id: str) -> RunRecord:
    run_id = new_run_id()
    return RunRecord(
        run_id=run_id,
        # The real key is computed once the documents are hashed at intake. Until
        # then the run id stands in, so the unique index still holds and two
        # concurrent starts cannot collide.
        content_key=f"pending:{run_id}",
        candidate_id=candidate.candidate_id,
        role_id=role_id,
        rubric_version="pending",
        rubric_hash=_rubric_hash(deps, role_id),
        prompt_bundle_hash=_prompt_hash(deps),
        model_tier_bindings_hash=_bindings_hash(deps),
        routing_policy_id=deps.settings.routing_policy_id,
        blind_mode=deps.settings.blind_mode,
        calibration_enabled=deps.settings.calibration_enabled,
        calibration_status="disabled" if not deps.settings.calibration_enabled else "pending",
        pipeline_version=PIPELINE_VERSION,
        status=RunStatus.CREATED,
        started_at=datetime.now(UTC),
    )


def _rubric_hash(deps: Deps, role_id: str) -> str:
    if deps.rubric_loader is None:
        return "no-rubric"
    try:
        return rubric_hash(deps.rubric_loader(role_id))
    except Exception:
        return "unloadable"


def _prompt_hash(deps: Deps) -> str:
    return getattr(deps.prompts, "bundle_hash", "no-prompts")


def _bindings_hash(deps: Deps) -> str:
    return getattr(deps.models, "tier_binding_hash", "") or "unbound"


def _message_for(outcome: RunOutcome) -> str:
    """One sentence about how it went, for a person rather than a log."""
    if outcome.status in TERMINAL:
        return "This candidate could not be processed. The reason is on the run."
    if outcome.failed_node:
        return "This candidate needs a closer look before anything is sent."
    return "Ready for review."
