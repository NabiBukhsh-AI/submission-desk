"""What the SANITIZE node is allowed to know about scanning.

The node decides what a set of findings *means* — which tier, whether to halt,
what the reviewer is told. It must not know how a PDF stores a colour, because a
node that imports a PDF library cannot be run against a fake, and the offline
suite is what makes every security claim here checkable without a network.

So the shape of a scan is declared here and the scanning happens in
``infrastructure/security/``. The node receives a ``DocumentScanner`` on Deps
like every other adapter.

``ScanResult`` carries two things beyond the findings, and both exist because a
control that did not run must not look like a control that passed.
``render_diff_ran`` says whether the strongest detector executed, and
``render_diff_skipped_reason`` says why not, in a sentence a reviewer reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from domain.contracts.integrity import IntegrityFinding


@dataclass(frozen=True)
class ScanResult:
    """What a scanner found in one document, and what it could not check."""

    findings: tuple[IntegrityFinding, ...] = ()

    #: Document properties, for the reviewer's integrity panel. Never for a
    #: prompt: metadata is the one part of a document with no reason to reach a
    #: model, so it does not.
    metadata: dict[str, str] = field(default_factory=dict)

    #: Whether the render comparison executed, and why it did not.
    render_diff_ran: bool = False
    render_diff_skipped_reason: str | None = None
    pages_compared: int = 0

    @property
    def coverage_note(self) -> str:
        """One sentence about how thoroughly this document was checked.

        Shown next to a clean result, because "we found nothing" and "we could
        not look" are different statements and a reviewer is entitled to know
        which one they are reading.
        """
        if self.render_diff_ran:
            return (
                f"Every check ran, including reading {self.pages_compared} "
                f"page(s) as images to compare against the text."
            )
        return (
            "Every check ran except the page comparison, which was skipped: "
            f"{self.render_diff_skipped_reason or 'no reason was recorded'}."
        )


class DocumentScanner(Protocol):
    """Reads a document adversarially.

    One method. The caller supplies the bytes and both renderings of the text,
    because the node already has them and re-reading the file would risk
    scanning something other than what the model will be shown.
    """

    def scan(
        self,
        data: bytes,
        *,
        normalized_text: str,
        raw_text: str,
        mime_type: str,
    ) -> ScanResult:
        """Every detector this scanner can run, over one document.

        Never raises for a malformed document: intake has already rejected what
        cannot be opened, and a scanner that failed the run on a strange file
        would be a denial of service against the recruiter rather than a
        control against the candidate.
        """
        ...
