"""Reading candidate documents from a shared folder.

Read-only, one folder, and never a write. The scope is the security control: a
credential that can only read one folder cannot delete a recruiter's drive
however wrong the code is, and no amount of testing provides that guarantee as
cheaply as not asking for the permission.

A partial fetch is discarded rather than returned. A truncated PDF that reached
extraction would be hashed, stored, and assessed as though it were the whole
document, and the candidate would be judged on the half that arrived.

The transport is injected. This module knows about folders, retries and
truncation; it does not know about any particular HTTP client, which is what
lets the whole thing be tested against a fake with no account.
"""

from __future__ import annotations

import os
import time
from typing import Any, Protocol

from domain.ports.sinks import AdapterError, AdapterResult
from domain.ports.sources import CandidateRef, DocumentRef, SourceUnavailable
from infrastructure.integrations.retrying import TokenBucket, classify_status, with_retries

#: What this adapter asks for. Read-only, and worth reading twice before it is
#: ever widened.
SCOPE = "https://www.googleapis.com/auth/drive.readonly"

#: Calls per second. Provider quotas are shared across an account, so a batch
#: that saturates one takes down whatever else that account is doing.
DEFAULT_QPS = 4.0


class DriveTransportError(Exception):
    """What a transport raises, carrying the status it saw."""

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or f"HTTP {status}")
        self.status = status


class DriveTransport(Protocol):
    """The two calls this adapter makes.

    Narrow on purpose: adding a source means implementing two methods against a
    fake, and a wider interface would be a wider blast radius for a credential.
    """

    def list_files(self, folder_id: str) -> list[dict[str, Any]]:
        """Files in one folder: id, name, size, and mime type."""
        ...

    def download(self, file_id: str) -> tuple[bytes, int | None]:
        """The bytes, and the size the far side said they would be.

        Two values so truncation is detectable. One value would make a short
        read indistinguishable from a short file.
        """
        ...


class DriveSource:
    """Candidate documents from one Drive folder."""

    source_id = "drive"

    def __init__(
        self,
        transport: DriveTransport,
        *,
        folder_id: str = "",
        qps: float = DEFAULT_QPS,
        sleep: Any = time.sleep,
    ) -> None:
        self.transport = transport
        self.folder_id = folder_id or os.environ.get("DRIVE_FOLDER_ID", "")
        self.bucket = TokenBucket(rate_per_second=qps)
        self._sleep = sleep
        self._disabled_reason: str | None = None

    def healthy(self) -> bool:
        return self._disabled_reason is None

    def list_candidates(self, limit: int | None = None) -> list[CandidateRef]:
        """What is waiting, grouped by the name in front of the first separator.

        An empty folder is a fact and returns an empty list. Only an unreachable
        folder raises, so the interface can tell a quiet morning from an outage.
        """
        if not self.folder_id:
            raise SourceUnavailable(
                "No Drive folder is configured. Set DRIVE_FOLDER_ID, or upload files directly."
            )

        result = with_retries(self._list, sleep=self._sleep)
        if not result.ok:
            self._maybe_disable(result)
            raise SourceUnavailable(result.message)

        grouped: dict[str, list[DocumentRef]] = {}
        for entry in result.detail.get("files", []):
            candidate_id = candidate_id_for(str(entry.get("name", "")))
            grouped.setdefault(candidate_id, []).append(
                DocumentRef(
                    candidate_id=candidate_id,
                    filename=str(entry.get("name", "document")),
                    external_ref=str(entry.get("id", "")),
                    size_bytes=_int_or_none(entry.get("size")),
                    metadata={"mime_type": str(entry.get("mimeType", ""))},
                )
            )

        refs = [
            CandidateRef(candidate_id=candidate_id, documents=tuple(documents), source_id="drive")
            for candidate_id, documents in sorted(grouped.items())
        ]
        return refs[:limit] if limit else refs

    def fetch(self, ref: DocumentRef) -> bytes:
        """One document's bytes, whole or not at all."""
        result = with_retries(lambda: self._download(ref), sleep=self._sleep)

        if not result.ok:
            self._maybe_disable(result)
            raise SourceUnavailable(result.message)

        data: bytes = result.detail["data"]
        return data

    # --- the two calls -------------------------------------------------------

    def _list(self) -> AdapterResult:
        self.bucket.take(sleep=self._sleep)
        try:
            files = self.transport.list_files(self.folder_id)
        except Exception as error:
            return failure_from(error, "this folder could not be listed")
        return AdapterResult.succeeded(self.folder_id, detail={"files": files})

    def _download(self, ref: DocumentRef) -> AdapterResult:
        self.bucket.take(sleep=self._sleep)
        try:
            data, expected = self.transport.download(ref.external_ref)
        except Exception as error:
            return failure_from(error, f"{ref.filename} could not be downloaded")

        if expected is not None and len(data) != expected:
            # Transient on purpose: a short read is usually a dropped connection
            # and usually succeeds next time. What must not happen is returning
            # it, because everything downstream would treat it as a whole file.
            return AdapterResult.failed(
                AdapterError.TRANSIENT,
                f"{ref.filename} arrived incomplete ({len(data)} of {expected} bytes) "
                "and was discarded.",
            )

        return AdapterResult.succeeded(ref.external_ref, detail={"data": data})

    def _maybe_disable(self, result: AdapterResult) -> None:
        if result.error_code and result.error_code.disables_adapter:
            self._disabled_reason = result.message


def failure_from(error: Exception, what: str) -> AdapterResult:
    """An exception from the transport, as a decision about what to do next."""
    status = getattr(error, "status", None)
    if status is None:
        return AdapterResult.failed(
            AdapterError.TRANSIENT,
            f"Google Drive could not be reached, so {what}. It may work shortly.",
        )

    code = classify_status(int(status))
    return AdapterResult.failed(code, message_for(code, what) + said(error))


def said(error: Exception) -> str:
    """What the far side answered, appended so the sentence can be acted on.

    "Access was refused" covers a key file that is not there, a key Google
    no longer recognises, and a folder nobody shared; the transport's message
    tells them apart, and without it the person reading has to guess.
    """
    detail = str(error).strip()
    if not detail or detail.startswith("HTTP "):
        return ""
    return f" ({detail.rstrip('.')})"


def message_for(code: AdapterError, what: str) -> str:
    """What a recruiter reads, never a status line."""
    return {
        AdapterError.AUTH: (
            f"Access to Google Drive was refused, so {what}. The credentials need "
            "renewing, or the folder has not been shared with this system."
        ),
        AdapterError.CONFIG: (
            f"The configured Drive folder was not found, so {what}. Check DRIVE_FOLDER_ID."
        ),
        AdapterError.RATE_LIMITED: (
            f"Google Drive is asking for fewer requests, so {what} yet. This will retry on its own."
        ),
        AdapterError.TRANSIENT: (
            f"Google Drive had a problem, so {what}. This will retry on its own."
        ),
        AdapterError.PERMANENT: f"Google Drive refused the request, so {what}.",
    }[code]


def candidate_id_for(filename: str) -> str:
    """Which candidate a file belongs to, guessed from its name.

    A guess, and the interface shows it as one. Filenames are how people name
    things, not how systems identify them.
    """
    stem = filename.rsplit(".", 1)[0].strip().lower()
    for separator in ("_", "-", " "):
        if separator in stem:
            return stem.split(separator)[0] or stem
    return stem or "candidate"


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
