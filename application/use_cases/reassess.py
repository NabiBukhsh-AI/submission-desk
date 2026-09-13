"""Run candidates the system already holds against another role.

A recruiter picks runs in the queue and a role; each run's documents become a
candidate again and go through the pipeline for that role. The bytes come from
the blob store — they were hashed and kept at intake — so nothing is re-read
from the inbox, and the same documents against the same configuration are
recognised as already done rather than paid for twice.
"""

from __future__ import annotations

from uuid import UUID

from application.deps import Deps
from application.use_cases.admin import AdminRefused
from domain.ports.sources import CandidateRef, DocumentRef


def candidates_from_runs(deps: Deps, run_ids: list[UUID]) -> list[CandidateRef]:
    """One candidate per run, carrying the stored documents by hash."""
    candidates: list[CandidateRef] = []
    for run_id in run_ids:
        record = deps.runs.get(run_id)
        if record is None:
            raise AdminRefused(f"There is no run {run_id}.")
        documents = deps.candidates.documents_for_run(run_id)
        if not documents:
            raise AdminRefused(f"Run {run_id} has no stored documents to assess again.")
        candidates.append(
            CandidateRef(
                candidate_id=record.candidate_id,
                documents=tuple(
                    DocumentRef(
                        candidate_id=record.candidate_id,
                        filename=document.original_filename,
                        external_ref=document.blob_path,
                        size_bytes=document.size_bytes,
                        # The hash is what intake reads the bytes by, and what
                        # lets an identical run be found instead of repeated.
                        metadata={"sha256": document.document_sha256},
                    )
                    for document in documents
                ),
            )
        )
    return candidates
