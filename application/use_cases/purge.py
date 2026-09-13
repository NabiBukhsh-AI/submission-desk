"""Remove what the retention period says should be gone.

Candidate documents are kept for as long as a decision might need to be
revisited and not a day longer. The period is configuration; this is the thing
that acts on it, and it acts on finished runs only: a run still open is not old,
it is in progress.

Two things go, in order. The run and every row recorded against it, in one
statement that cascades. Then the bytes and the cached extraction for each of
its documents, but only when no other run still refers to them — a document
uploaded twice is stored once, and deleting it under the second run's feet would
turn a purge into a data-loss bug.

Dry run first. A purge that cannot be previewed is a purge somebody runs with a
typo in the retention period.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

from application.deps import Deps

#: How long a finished run is kept, unless configuration says otherwise.
DEFAULT_RETENTION_DAYS = 30


@dataclass
class PurgeSummary:
    """What was removed, or what would be."""

    retention_days: int
    dry_run: bool
    runs: list[UUID] = field(default_factory=list)
    blobs_removed: int = 0
    blobs_kept: int = 0

    def sentence(self) -> str:
        verb = "Would remove" if self.dry_run else "Removed"
        if not self.runs:
            return f"Nothing is older than {self.retention_days} day(s)."
        kept = f", {self.blobs_kept} document(s) kept because another run uses them"
        return (
            f"{verb} {len(self.runs)} run(s) older than {self.retention_days} day(s) "
            f"and {self.blobs_removed} document(s){kept if self.blobs_kept else ''}."
        )


def purge(
    deps: Deps,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    dry_run: bool = True,
    limit: int = 500,
) -> PurgeSummary:
    """Remove finished runs older than ``retention_days``, and their documents."""
    if retention_days < 1:
        raise ValueError("retention must be at least one day")

    cutoff = deps.clock.now() - timedelta(days=retention_days)
    summary = PurgeSummary(retention_days=retention_days, dry_run=dry_run)

    for run in deps.runs.list_finished_before(cutoff, limit=limit):
        documents = deps.candidates.documents_for_run(run.run_id)
        summary.runs.append(run.run_id)

        if dry_run:
            summary.blobs_removed += len(documents)
            continue

        removed, kept = remove_run(deps, run.run_id)
        summary.blobs_removed += removed
        summary.blobs_kept += kept

    return summary


def remove_run(deps: Deps, run_id: UUID) -> tuple[int, int]:
    """The run and every row against it, then the documents nothing else uses.

    Returns how many documents were removed and how many were kept because
    another run still refers to them.
    """
    documents = deps.candidates.documents_for_run(run_id)
    deps.runs.delete(run_id)

    removed = kept = 0
    for document in documents:
        sha = document.document_sha256
        if deps.candidates.sha_referenced(sha):
            kept += 1
            continue
        deps.candidates.delete_source_texts(sha)
        if deps.blobs.delete(sha):
            removed += 1
    return removed, kept
