"""Content-addressed storage for uploaded documents.

A file is stored under the hash of its bytes and nothing else. That gives
deduplication for free, makes re-uploading the same CV a no-op rather than a
second copy, and means a filename can never decide where anything lands.

The last point is the security one. Uploaded filenames are metadata: they are
displayed and stored in a column, and they never touch a path.
"""

from __future__ import annotations

import contextlib
import hashlib
from pathlib import Path

from domain.errors import SubmissionDeskError

#: Characters in a sha256 hex digest.
SHA256_HEX_LENGTH = 64


class BlobStoreError(SubmissionDeskError):
    error_code = "BLOB_STORE_ERROR"


class BlobStore:
    """Documents on disk, addressed by hash.

    The two-character prefix directory keeps any one directory small enough to
    list on a machine with a few thousand candidates on it.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def path_for(self, document_sha256: str) -> Path:
        """Where a hash lives. Rejects anything that is not a hash.

        The guard is what makes traversal structurally impossible rather than
        merely unlikely: there is no input to this function that produces a path
        outside the root, because the only accepted input is 64 hex characters.
        """
        if len(document_sha256) != SHA256_HEX_LENGTH or not all(
            character in "0123456789abcdef" for character in document_sha256
        ):
            raise BlobStoreError("a blob address is 64 lowercase hex characters")
        return self.root / document_sha256[:2] / document_sha256

    def put(self, data: bytes) -> str:
        """Store bytes, returning their hash.

        Writing a hash that already exists is a no-op, not an overwrite: two
        files with the same hash are the same file, and rewriting would churn
        the disk for nothing.
        """
        digest = hashlib.sha256(data).hexdigest()
        destination = self.path_for(digest)

        if destination.exists():
            return digest

        destination.parent.mkdir(parents=True, exist_ok=True)
        # Write beside the target and rename, so a crash mid-write cannot leave
        # a truncated file sitting at an address that claims to be complete.
        staging = destination.with_suffix(".partial")
        staging.write_bytes(data)
        staging.replace(destination)
        return digest

    def put_file(self, source: Path | str) -> str:
        return self.put(Path(source).read_bytes())

    def get(self, document_sha256: str) -> bytes:
        path = self.path_for(document_sha256)
        if not path.exists():
            raise BlobStoreError(f"no document stored under {document_sha256[:12]}")
        return path.read_bytes()

    def exists(self, document_sha256: str) -> bool:
        return self.path_for(document_sha256).exists()

    def delete(self, document_sha256: str) -> bool:
        """Remove the bytes at a hash. Goes through ``path_for``, so the same
        guard that stops a read escaping the root stops a delete."""
        path = self.path_for(document_sha256)
        if not path.exists():
            return False
        path.unlink()
        # An empty shard directory is noise, not state. Removing it is safe
        # because the next write recreates it.
        with contextlib.suppress(OSError):
            path.parent.rmdir()
        return True

    def size(self, document_sha256: str) -> int:
        return self.path_for(document_sha256).stat().st_size
