"""Acquire bytes, establish identity, and refuse what cannot work.

This node exists partly *to* keep cost at zero for input that can never produce
a valid assessment. A renamed executable, a 40 MB scan, and a password-protected
PDF all stop here having spent nothing, and each leaves with a sentence a
recruiter can act on rather than a traceback.

Two rules carry the security of this stage.

The extension is never trusted. File type comes from magic bytes, so
``payload.exe`` renamed to ``resume.pdf`` is rejected as an unsupported type
rather than handed to a PDF parser.

The filename never becomes a path. Blobs are stored under the hash of their
content, so a file called ``../../etc/passwd`` is a string in a database column
and its bytes are at ``blobs/ab/abcdef...`` like everything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from application.deps import Deps
from domain.contracts.documents import CandidateDocument
from domain.contracts.enums import DocumentRole, RunStatus
from domain.contracts.errors import ErrorRecord
from domain.contracts.run_state import DomainEvent, NodeResult, NodeStatus, RunState
from domain.identity import content_key
from domain.ports.sources import DocumentRef, SourceUnavailable


class RejectionReason(str, Enum):
    """Why a document was refused. Every one is a terminal state."""

    UNSUPPORTED_TYPE = "unsupported_type"
    CORRUPT_FILE = "corrupt_file"
    ENCRYPTED = "encrypted"
    EMPTY_DOCUMENT = "empty_document"
    OVERSIZED = "oversized"
    TOO_MANY_DOCUMENTS = "too_many_documents"
    SOURCE_UNAVAILABLE = "source_unavailable"
    NO_DOCUMENTS = "no_documents"


#: What the recruiter reads. Kept in one place rather than inline at each
#: rejection, so the wording can be reviewed as a set and so no message is
#: written in the register of an exception message by accident.
REJECTION_MESSAGES: dict[RejectionReason, str] = {
    RejectionReason.UNSUPPORTED_TYPE: (
        "This file type is not supported. Please send a PDF, a Word document, or plain text."
    ),
    RejectionReason.CORRUPT_FILE: (
        "This file could not be opened. It may have been damaged in transit; "
        "ask the candidate to send it again."
    ),
    RejectionReason.ENCRYPTED: (
        "This file is password protected, so its text cannot be read. "
        "Ask the candidate for an unprotected copy."
    ),
    RejectionReason.EMPTY_DOCUMENT: (
        "This document contains almost no readable text. It may be a blank page or an image "
        "the scanner could not read."
    ),
    RejectionReason.OVERSIZED: (
        "This document is larger than this system accepts. Ask for a smaller file."
    ),
    RejectionReason.TOO_MANY_DOCUMENTS: (
        "More documents were supplied for this candidate than this system accepts. "
        "Please send the CV and at most a few supporting files."
    ),
    RejectionReason.SOURCE_UNAVAILABLE: (
        "The folder these documents come from could not be reached. "
        "You can still upload files directly."
    ),
    RejectionReason.NO_DOCUMENTS: "No documents were supplied for this candidate.",
}


@dataclass(frozen=True)
class IntakeLimits:
    """The caps, with the defaults from config/limits.yaml.

    Held as a value rather than read from a file here, because this node must
    stay testable without a configuration loader and because a limit that
    changes mid-run is a limit nobody can reason about.
    """

    max_document_bytes: int = 26_214_400
    max_pages: int = 60
    max_documents_per_candidate: int = 8
    min_document_chars: int = 200
    accepted_types: tuple[str, ...] = ("pdf", "docx", "txt", "md")


@dataclass(frozen=True)
class Rejection:
    """One refused document, with the reason and the sentence."""

    filename: str
    reason: RejectionReason

    @property
    def message(self) -> str:
        return REJECTION_MESSAGES[self.reason]


#: Share of a text sample that must be printable before it is believed to be
#: something a person wrote rather than a binary that happens to decode.
PRINTABLE_SHARE_REQUIRED = 0.9

#: Magic byte signatures. A DOCX is a zip, so its signature is a zip's, and the
#: distinction is made by looking inside for the Word content-type marker.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "pdf"),
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),
    (b"PK\x07\x08", "zip"),
)

#: Signatures of things this system will never process, listed so they can be
#: named in a log rather than lumped in with an unreadable text file.
_EXECUTABLE_SIGNATURES: tuple[bytes, ...] = (
    b"MZ",  # Windows PE
    b"\x7fELF",  # Linux ELF
    b"\xca\xfe\xba\xbe",  # Mach-O fat / Java class
    b"\xfe\xed\xfa\xce",  # Mach-O
    b"#!",  # script with a shebang
)


def sniff_type(data: bytes) -> str | None:
    """What this file actually is, from its first bytes.

    Returns None for anything unrecognised, including a plausible-looking
    extension on the wrong content. The caller decides what to do about it; this
    function does not guess.
    """
    if not data:
        return None

    if any(data.startswith(signature) for signature in _EXECUTABLE_SIGNATURES):
        return None

    for signature, kind in _SIGNATURES:
        if data.startswith(signature):
            if kind != "zip":
                return kind
            return "docx" if _looks_like_docx(data) else None

    return "txt" if _looks_like_text(data) else None


def _looks_like_docx(data: bytes) -> bool:
    """A Word file is a zip containing a specific part.

    Checked by looking for the marker rather than by opening the archive, so a
    zip bomb is refused before anything decompresses it.
    """
    marker = b"word/document.xml"
    return marker in data[:8192] or marker in data[-8192:]


def _looks_like_text(data: bytes) -> bool:
    """Decodable as UTF-8 and mostly printable.

    A file of control characters that happens to decode is not text a person
    wrote, and treating it as such would send noise to a model.
    """
    sample = data[:4096]
    try:
        decoded = sample.decode("utf-8")
    except UnicodeDecodeError:
        return False

    printable = sum(1 for character in decoded if character.isprintable() or character in "\r\n\t")
    return bool(decoded) and printable / len(decoded) > PRINTABLE_SHARE_REQUIRED


def is_encrypted_pdf(data: bytes) -> bool:
    """Whether a PDF carries an encryption dictionary.

    Detected here rather than at extraction so the run stops before a parser is
    handed a file it will fail on, and so the reviewer is told the specific
    thing that is wrong.
    """
    return b"/Encrypt" in data


def looks_corrupt(data: bytes, kind: str) -> bool:
    """Whether the file's own structure contradicts its header.

    A truncated PDF with no end-of-file marker and a zip with no central
    directory are both files that will fail later; failing now is cheaper and
    produces a better sentence.
    """
    if kind == "pdf":
        return b"%%EOF" not in data[-2048:]
    if kind == "docx":
        return b"PK\x05\x06" not in data[-66000:]
    return False


def estimate_pages(data: bytes, kind: str) -> int | None:
    """A page count good enough to enforce a cap.

    Counting ``/Type /Page`` markers overestimates on some producers and
    underestimates on others, which is why it gates a limit rather than being
    reported to anyone as a fact.
    """
    if kind != "pdf":
        return None
    return max(data.count(b"/Type /Page") - data.count(b"/Type /Pages"), 1)


def classify(data: bytes, limits: IntakeLimits) -> RejectionReason | None:
    """Everything wrong with one file, or None if it is acceptable.

    Written as a table rather than a chain of returns, so the order in which
    reasons are reported is visible in one place. Order matters to the recruiter:
    a password-protected file should be reported as such, not as corrupt,
    because the two ask them to do different things.
    """
    if len(data) > limits.max_document_bytes:
        return RejectionReason.OVERSIZED

    kind = sniff_type(data)
    if kind is None or kind not in limits.accepted_types:
        return RejectionReason.UNSUPPORTED_TYPE

    pages = estimate_pages(data, kind)
    checks: tuple[tuple[bool, RejectionReason], ...] = (
        (kind == "pdf" and is_encrypted_pdf(data), RejectionReason.ENCRYPTED),
        (looks_corrupt(data, kind), RejectionReason.CORRUPT_FILE),
        (pages is not None and pages > limits.max_pages, RejectionReason.OVERSIZED),
        (
            kind in ("txt", "md") and len(data.strip()) < limits.min_document_chars,
            RejectionReason.EMPTY_DOCUMENT,
        ),
    )

    for failed, reason in checks:
        if failed:
            return reason
    return None


def mime_for(kind: str) -> str:
    return {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "txt": "text/plain",
        "md": "text/markdown",
    }[kind]


def role_for(filename: str) -> DocumentRole:
    """A guess at what a document is, from its name.

    A guess is all this is, and it affects presentation only: nothing in the
    assessment depends on it, so being wrong costs a label rather than a result.
    """
    lowered = filename.lower()
    if "cover" in lowered or "letter" in lowered:
        return DocumentRole.COVER_LETTER
    if "portfolio" in lowered or "work" in lowered:
        return DocumentRole.PORTFOLIO
    if "cv" in lowered or "resume" in lowered or "résumé" in lowered:
        return DocumentRole.CV
    return DocumentRole.OTHER


def node(state: RunState, deps: Deps) -> NodeResult:
    """The INTAKE node.

    Returns a failed result with a reason rather than raising, so a rejected
    file is a state the reviewer can see and not an exception someone has to
    read a log to understand.
    """
    limits = IntakeLimits()
    events: list[DomainEvent] = []

    refs: tuple[DocumentRef, ...] = tuple(getattr(state, "document_refs", ()) or ())
    if not refs:
        return _rejected(state, RejectionReason.NO_DOCUMENTS, events)

    if len(refs) > limits.max_documents_per_candidate:
        return _rejected(
            state,
            RejectionReason.TOO_MANY_DOCUMENTS,
            events,
            detail={"supplied": len(refs), "limit": limits.max_documents_per_candidate},
        )

    store = deps.blobs
    accepted: list[CandidateDocument] = []
    hashes: list[str] = []

    for ref in refs:
        try:
            data = deps.source.fetch(ref)
        except SourceUnavailable:
            return _rejected(state, RejectionReason.SOURCE_UNAVAILABLE, events)

        reason = classify(data, limits)
        if reason is not None:
            events.append(
                DomainEvent(
                    name="intake.document_rejected",
                    payload={"filename": ref.filename, "reason": reason.value},
                )
            )
            return _rejected(state, reason, events, detail={"filename": ref.filename})

        kind = sniff_type(data)
        assert kind is not None, "classify accepted a file whose type could not be determined"

        digest = store.put(data)
        hashes.append(digest)
        document = CandidateDocument(
            document_id=uuid4(),
            candidate_id=state.candidate_id,
            original_filename=ref.filename,
            document_sha256=digest,
            mime_type=mime_for(kind),
            size_bytes=len(data),
            page_count=estimate_pages(data, kind),
            blob_path=str(store.path_for(digest)),
            doc_role=role_for(ref.filename),
            received_at=deps.clock.now(),
        )
        deps.candidates.add_document(document, run_id=state.run_id)
        accepted.append(document)
        events.append(
            DomainEvent(
                name="intake.document_accepted",
                payload={"sha256": digest[:12], "bytes": len(data), "kind": kind},
            )
        )

    # The real content key, now that the documents have been hashed. Until this
    # point the run carries "pending:<run_id>": the key needs the hashes, and
    # hashing means fetching every file, which is the cost the key exists to
    # avoid. A run that reached a reviewer still carrying the placeholder could
    # never be deduplicated against, so idempotency would silently never work.
    key = _content_key_for(state, deps, sorted(hashes))
    claimed = deps.runs.set_content_key(state.run_id, key)

    if not claimed:
        # Another run already holds this key, so these are documents the system
        # has seen under this configuration. The run continues — somebody asked
        # for it — and the event records that its result duplicates an earlier
        # one.
        events.append(DomainEvent(name="intake.duplicate_documents", payload={"key": key[:16]}))

    return NodeResult(
        state=state.model_copy(
            update={
                "document_hashes": tuple(sorted(hashes)),
                "content_key": key if claimed else state.content_key,
            }
        ),
        status=NodeStatus.OK,
        events=tuple(events),
        next_status=RunStatus.INTAKE_OK,
    )


def _content_key_for(state: RunState, deps: Deps, hashes: list[str]) -> str:
    """Everything that decides what a run produces, as one string.

    The same documents under the same configuration produce the same key, so a
    repeat submission is recognised rather than repeated.
    """
    return content_key(
        hashes,
        rubric_hash=state.rubric_hash or "no-rubric",
        prompt_bundle_hash=getattr(deps.prompts, "bundle_hash", "no-prompts"),
        model_tier_bindings_hash=getattr(deps.models, "tier_binding_hash", "") or "unbound",
        routing_policy_id=deps.settings.routing_policy_id,
        blind_mode=state.blind_mode,
        calibration_enabled=state.calibration_enabled,
        pipeline_version=deps.settings.pipeline_version,
    )


def _rejected(
    state: RunState,
    reason: RejectionReason,
    events: list[DomainEvent],
    detail: dict[str, object] | None = None,
) -> NodeResult:
    """A refusal, as a state rather than as an exception."""
    events.append(
        DomainEvent(name="intake.rejected", payload={"reason": reason.value, **(detail or {})})
    )
    return NodeResult(
        state=state,
        status=NodeStatus.FAILED,
        events=tuple(events),
        error=ErrorRecord(
            error_id=uuid4(),
            run_id=state.run_id,
            node="INTAKE",
            error_code=reason.value.upper(),
            error_class="IntakeRejected",
            message_redacted=REJECTION_MESSAGES[reason],
            retryable=reason is RejectionReason.SOURCE_UNAVAILABLE,
            attempt=1,
            resulting_state=RunStatus.FAILED_TERMINAL,
            occurred_at=datetime.now(UTC),
        ),
    )
