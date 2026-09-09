"""Where candidate documents come from.

Two methods. A source lists what is available and fetches one thing's bytes;
everything else, including what counts as a candidate and what to do with a
file, is decided by the intake node.

The narrowness is the point. Adding a source means implementing two methods
against a fake, and the domain core runs with every integration absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class DocumentRef:
    """One file a source knows about, before anything has been read.

    ``external_ref`` is whatever the source calls this file: a path, a Drive
    file id, an object key. It is opaque to everything downstream and is kept
    only so a reviewer can be told where a document came from.
    """

    candidate_id: str
    filename: str
    external_ref: str
    size_bytes: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateRef:
    """A group of files belonging to one person."""

    candidate_id: str
    documents: tuple[DocumentRef, ...]
    source_id: str = "local"


class SourceUnavailable(Exception):
    """The source could not be reached.

    Distinct from "the source has nothing": an empty folder is a fact, a
    network failure is an outage, and the interface says different things about
    each. Carries a sentence a recruiter can act on.
    """

    error_code = "SOURCE_UNAVAILABLE"
    retryable = True


class DocumentSource(Protocol):
    """A place candidate documents arrive from."""

    source_id: str

    def list_candidates(self, limit: int | None = None) -> list[CandidateRef]:
        """What is waiting to be processed.

        Never raises for an empty result. Raises ``SourceUnavailable`` only when
        the source itself could not be reached, so the interface can tell a
        quiet morning apart from an outage.
        """
        ...

    def fetch(self, ref: DocumentRef) -> bytes:
        """The bytes of one document.

        A partial read is never returned as a whole file. Implementations that
        can detect truncation raise rather than hand back something that would
        be hashed, stored, and assessed as though it were complete.
        """
        ...
