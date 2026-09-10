"""Process several candidates, and keep going when one of them fails.

A batch that stops at the first bad document is a batch a recruiter cannot use:
one corrupt PDF in twenty would hold up the other nineteen. So each candidate is
independent, failures are collected rather than raised, and the summary says what
happened to every one of them.

Sequential on purpose. Criteria run in parallel inside a run, where they are
independent by construction; candidates are not parallelised because the win is
smaller, the failure modes are worse, and a recruiter watching a progress bar
cares more about the first result appearing than about the last one arriving
sooner.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from application.deps import Deps
from application.use_cases.process_candidate import ProcessResult, process_candidate
from domain.contracts.enums import RunStatus
from domain.ports.sources import CandidateRef

#: What a batch reports per candidate when the run itself could not start.
UNEXPECTED = "This candidate could not be processed. The others in this batch were unaffected."


@dataclass
class BatchSummary:
    """What happened to each candidate, and to the batch as a whole."""

    results: list[ProcessResult] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results) + len(self.failures)

    @property
    def reviewable(self) -> int:
        return sum(1 for result in self.results if result.is_reviewable)

    @property
    def quarantined(self) -> int:
        return sum(1 for result in self.results if result.status is RunStatus.QUARANTINED)

    @property
    def reused(self) -> int:
        return sum(1 for result in self.results if result.reused_existing)

    def sentence(self) -> str:
        """One line for the recruiter, naming every outcome that occurred.

        Written as a list of clauses rather than a template with blanks, so a
        batch where nothing was quarantined does not say "0 quarantined" and
        make somebody wonder what that means.
        """
        if not self.total:
            return "There was nothing to process."

        parts = [f"{self.reviewable} ready to review"]
        if self.quarantined:
            parts.append(f"{self.quarantined} quarantined")
        if self.reused:
            parts.append(f"{self.reused} already done")
        if self.failures:
            parts.append(f"{len(self.failures)} could not be read")

        return f"Processed {self.total} candidate(s): " + ", ".join(parts) + "."


def process_batch(
    candidates: Sequence[CandidateRef],
    role_id: str,
    deps: Deps,
    *,
    force: bool = False,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> BatchSummary:
    """Take each candidate as far as a reviewer, independently.

    ``on_progress`` is called before each candidate with (done, total, id), so
    an interface can show movement without this module knowing what a progress
    bar is.
    """
    summary = BatchSummary()
    total = len(candidates)

    for index, candidate in enumerate(candidates):
        if on_progress is not None:
            on_progress(index, total, candidate.candidate_id)

        try:
            summary.results.append(process_candidate(candidate, role_id, deps, force=force))
        except Exception as error:
            # The runner already converts a node's exception into a failed run.
            # Reaching here means the run could not be created at all, which is
            # rare and worth recording rather than crashing the batch.
            summary.failures.append((candidate.candidate_id, _reason(error)))

    if on_progress is not None:
        on_progress(total, total, "")

    return summary


def _reason(error: Exception) -> str:
    """A sentence for the recruiter, never a traceback.

    An adapter that raised something with a message written for a person keeps
    it; anything else is replaced, because an exception string is written for
    whoever is debugging and this is read by whoever is hiring.
    """
    message = getattr(error, "message", None)
    if isinstance(message, str) and message and not message.startswith("'"):
        return message
    return UNEXPECTED
