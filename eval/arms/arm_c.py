"""Arm C: the system, through the same door everybody else uses.

It calls ``process_candidate``. Not a copy of the pipeline, not a trimmed
version, not a special evaluation path — the identical use case the interface
calls when a recruiter clicks a button.

That is the whole discipline of this file. An evaluation harness with its own
pipeline measures the harness, and the divergence appears exactly when somebody
changes the real one and forgets the copy, which is to say at the moment the
number matters most. So this module is thin, and the thinness is the feature.
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from application.deps import Deps
from application.use_cases.process_candidate import process_candidate
from domain.contracts.enums import Band, CriterionState
from domain.ports.sources import CandidateRef, DocumentRef
from infrastructure.integrations.local import LocalFolderSource


@dataclass
class ArmCResult:
    """One case, run through the real system."""

    case_id: str
    run_id: UUID | None = None
    band: Band | None = None
    criterion_states: dict[str, CriterionState] = field(default_factory=dict)
    cost_usd: Any = None
    latency_ms: int = 0
    escalations: int = 0
    invalid_spans: int = 0
    evidence_items: int = 0
    criteria_assessed: int = 0
    integrity_flagged: bool = False
    status: str = ""
    notes: str = ""


def run_case(
    case: Any,
    deps: Deps,
    *,
    documents_root: Path,
) -> ArmCResult:
    """One candidate, all the way through, exactly as production does it.

    Everything read back afterwards comes from the repositories rather than from
    the in-memory state, because that is what a report is made of: if a figure
    only exists in a variable, it is not a figure anybody can check later.
    """
    started = time.monotonic()

    # The source is rooted at the case documents. Not a convenience: the local
    # source refuses to read outside its own root, which is the traversal
    # defence working correctly, and pointing it at the benchmark folder is how
    # the benchmark reads its own files rather than how the defence is bypassed.
    wired = Deps(**{**deps.__dict__, "source": LocalFolderSource(documents_root)})

    candidate = _candidate_for(case, documents_root)
    result = process_candidate(candidate, case.role_id, wired)
    elapsed = int((time.monotonic() - started) * 1000)

    run = wired.runs.get(result.run_id)
    recommendation = wired.evidence.recommendation_for_run(result.run_id)
    assessments = wired.evidence.assessments_for_run(result.run_id)
    accepted = wired.evidence.evidence_for_run(result.run_id, rejected=False)
    rejected = wired.evidence.evidence_for_run(result.run_id, rejected=True)

    return ArmCResult(
        case_id=case.case_id,
        run_id=result.run_id,
        band=getattr(recommendation, "band", None),
        criterion_states=dict(getattr(recommendation, "criterion_states", {}) or {}),
        cost_usd=getattr(run, "total_cost_usd", None),
        latency_ms=elapsed,
        escalations=getattr(run, "escalation_count", 0),
        # The measured hallucination rate: quotations the validator could not
        # find, over quotations offered.
        invalid_spans=len(rejected),
        evidence_items=len(accepted) + len(rejected),
        criteria_assessed=len(assessments),
        integrity_flagged=_flagged(run),
        status=result.status.value,
        notes=result.message,
    )


def _candidate_for(case: Any, root: Path) -> CandidateRef:
    """The case's documents, as the source would describe them."""
    return CandidateRef(
        candidate_id=case.case_id,
        documents=tuple(
            DocumentRef(
                candidate_id=case.case_id,
                filename=Path(name).name,
                external_ref=str(root / name),
            )
            for name in case.documents
        ),
        source_id="eval",
    )


def _flagged(run: Any) -> bool:
    """Whether the documents were treated as anything other than clean."""
    tier = getattr(run, "integrity_tier", None)
    return bool(tier) and getattr(tier, "value", str(tier)) != "clean"


def calls_the_real_use_case() -> bool:
    """A statement this module makes about itself, checked by a test.

    Trivial to read and easy to lose: somebody optimising the harness inlines
    two nodes to skip an extraction, and from then on the evaluation measures a
    system nobody ships.
    """
    source = inspect.getsource(run_case)
    return "process_candidate(" in source
