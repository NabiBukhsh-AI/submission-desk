"""Candidates from a folder on disk.

The default source, and the one that always works. Google Drive is optional; a
folder is not, because a recruiter with a Drive outage still has files and still
has a deadline.

Grouping is deliberately simple and explained to the user rather than clever:
one subfolder per candidate, or, for loose files, a shared filename prefix
before the first underscore or dash. A rule a person can predict beats one that
is usually right.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from domain.ports.sources import CandidateRef, DocumentRef, SourceUnavailable

#: Extensions listed here are offered to intake. Intake still sniffs the bytes:
#: this only decides what to bother reading, never what to accept.
OFFERED_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md"})


class LocalFolderSource:
    """Reads candidate documents from a directory tree."""

    source_id = "local"

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def list_candidates(self, limit: int | None = None) -> list[CandidateRef]:
        """Group the folder's files into candidates.

        A missing folder is an outage, not an empty result: a recruiter whose
        path is wrong should be told that, rather than shown a queue with
        nothing in it and left to conclude the system is broken.
        """
        if not self.root.exists():
            raise SourceUnavailable(
                f"The folder {self.root} does not exist. Check the path, or upload files directly."
            )
        if not self.root.is_dir():
            raise SourceUnavailable(f"{self.root} is a file, not a folder of candidates.")

        candidates = [*self._from_subfolders(), *self._from_loose_files()]
        candidates.sort(key=lambda candidate: candidate.candidate_id)
        return candidates[:limit] if limit else candidates

    def fetch(self, ref: DocumentRef) -> bytes:
        """Read one document.

        The reference is resolved against the root and checked to be inside it,
        so a reference carrying a traversal cannot read outside the folder the
        recruiter pointed at.
        """
        path = Path(ref.external_ref).resolve()
        root = self.root.resolve()

        if not path.is_relative_to(root):
            raise SourceUnavailable(f"{ref.filename} is outside the folder being processed.")
        if not path.exists():
            raise SourceUnavailable(f"{ref.filename} is no longer in the folder.")

        return path.read_bytes()

    # --- grouping ---------------------------------------------------------

    def _from_subfolders(self) -> list[CandidateRef]:
        """One subfolder, one candidate. The rule a recruiter will use."""
        found: list[CandidateRef] = []
        for directory in sorted(path for path in self.root.iterdir() if path.is_dir()):
            documents = tuple(
                self._ref(directory.name, path)
                for path in sorted(directory.rglob("*"))
                if path.is_file() and path.suffix.lower() in OFFERED_SUFFIXES
            )
            if documents:
                found.append(CandidateRef(candidate_id=directory.name, documents=documents))
        return found

    def _from_loose_files(self) -> list[CandidateRef]:
        """Files sitting in the root, grouped by the prefix before _ or -.

        ``ana-cv.pdf`` and ``ana-cover.pdf`` are one candidate. ``cv.pdf`` alone
        is a candidate named for its own stem. Neither is guessed at from
        content, because a wrong guess here silently merges two people.
        """
        groups: dict[str, list[Path]] = {}
        for path in sorted(self.root.iterdir()):
            if not path.is_file() or path.suffix.lower() not in OFFERED_SUFFIXES:
                continue
            stem = path.stem
            prefix = stem.split("_")[0].split("-")[0] or stem
            groups.setdefault(prefix, []).append(path)

        return [
            CandidateRef(
                candidate_id=prefix,
                documents=tuple(self._ref(prefix, path) for path in paths),
            )
            for prefix, paths in sorted(groups.items())
        ]

    def _ref(self, candidate_id: str, path: Path) -> DocumentRef:
        """One file, with its hash.

        Hashed at listing time because the bytes are already on this disk and
        reading them costs nothing worth avoiding. The hash is what lets the
        use case notice that this exact document under this exact
        configuration was already processed, and reuse that run instead of
        starting another — which is what makes `make seed` safe to run twice.
        A remote source cannot afford this and leaves the metadata empty.
        """
        return DocumentRef(
            candidate_id=candidate_id,
            filename=path.name,
            external_ref=str(path.resolve()),
            size_bytes=path.stat().st_size,
            metadata={"sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
        )
