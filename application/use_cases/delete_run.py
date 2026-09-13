"""Remove one candidate's run on request.

The retention purge removes what is old; this removes what a person no longer
wants, now. Two refusals: a run the pipeline is still working on, because a
worker with its rows pulled out from under it fails somewhere nobody is
looking; and the sample candidates that ship with the system, recognised by
the content hashes of their documents, because they are the demo and the
demo stays.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from application.deps import Deps
from application.use_cases.purge import remove_run
from domain.state_machine import IN_FLIGHT


class DeleteRefused(Exception):
    """The run stays, with the reason."""


@dataclass(frozen=True)
class DeleteResult:
    run_id: UUID
    candidate_id: str
    documents_removed: int
    documents_kept: int


def is_sample(deps: Deps, run_id: UUID) -> bool:
    """Whether every document on the run is one that ships with the system."""
    if deps.sample_hashes is None:
        return False
    documents = deps.candidates.documents_for_run(run_id)
    if not documents:
        return False
    samples = deps.sample_hashes()
    return all(document.document_sha256 in samples for document in documents)


def delete_run(deps: Deps, run_id: UUID) -> DeleteResult:
    record = deps.runs.get(run_id)
    if record is None:
        raise DeleteRefused(f"There is no run {run_id}.")
    if record.status in IN_FLIGHT:
        raise DeleteRefused(
            "This candidate is still being processed. Wait for it to finish, or for "
            "the reconciler to mark it interrupted, and try again."
        )
    if is_sample(deps, run_id):
        raise DeleteRefused(
            f"{record.candidate_id} is one of the sample candidates that ship with the "
            "system. Samples cannot be deleted; upload your own documents to have "
            "something you can."
        )
    removed, kept = remove_run(deps, run_id)
    return DeleteResult(run_id, record.candidate_id, removed, kept)
