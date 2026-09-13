"""The EXTRACT node.

Reads every accepted document into the object every later quotation is checked
against, and caches the result on the document hash rather than on the run.

That cache key matters more than it looks. Extraction is independent of the
rubric, so changing a criterion, a weight, or a prompt must never re-OCR a
sixty-page scan. Keying the cache on (document hash, profile id) is what makes
a rubric edit free.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from application.deps import Deps
from domain.contracts.enums import RunStatus
from domain.contracts.errors import ErrorRecord
from domain.contracts.run_state import DomainEvent, NodeResult, NodeStatus, RunState
from domain.ports.extraction import ExtractionFailed
from domain.provenance.normalization import normalize_source


def node(state: RunState, deps: Deps) -> NodeResult:
    """Read the candidate's documents.

    A document that cannot be read at all fails the run with the reason. A
    document that read poorly does not: it continues with a lower confidence and
    the reviewer is told, because half a CV assessed honestly beats none.
    """
    documents = deps.candidates.documents_for_run(state.run_id)
    if not documents:
        return _failed(state, "No documents were stored for this candidate.", "NO_DOCUMENTS")

    events: list[DomainEvent] = []
    profile_id = deps.source_profile_id
    poor_quality: list[str] = []
    ocr_pages = 0

    for document in documents:
        cached = deps.candidates.get_source_text(document.document_sha256, profile_id)
        if cached is not None:
            events.append(
                DomainEvent(
                    name="extract.cache_hit",
                    payload={"sha256": document.document_sha256[:12]},
                )
            )
            continue

        try:
            data = deps.blobs.get(document.document_sha256)
            source = normalize_source(deps.extractor.extract(document, data))
        except ExtractionFailed as failure:
            return _failed(state, str(failure), failure.error_code)

        deps.candidates.put_source_text(source, document_sha256=document.document_sha256)

        page_methods = [page.extraction_method.value for page in source.pages]
        ocr_pages += page_methods.count("ocr")
        if source.extraction_confidence < deps.settings.extraction_confidence_warn:
            poor_quality.append(document.original_filename)

        events.append(
            DomainEvent(
                name="extract.document_read",
                payload={
                    "sha256": document.document_sha256[:12],
                    "pages": len(source.pages),
                    "methods": sorted(set(page_methods)),
                    "confidence": source.extraction_confidence,
                    "chars": len(source.normalized_text),
                    "languages": source.detected_languages,
                },
            )
        )

    if ocr_pages:
        events.append(DomainEvent(name="extract.ocr_pages", payload={"count": ocr_pages}))

    if poor_quality:
        events.append(
            DomainEvent(name="extract.low_confidence", payload={"documents": poor_quality})
        )
        return NodeResult(
            state=state.degraded_by("extraction_quality"),
            status=NodeStatus.DEGRADED,
            events=tuple(events),
            next_status=RunStatus.EXTRACTED,
        )

    return NodeResult(
        state=state,
        status=NodeStatus.OK,
        events=tuple(events),
        next_status=RunStatus.EXTRACTED,
    )


def _failed(state: RunState, message: str, error_code: str) -> NodeResult:
    return NodeResult(
        state=state,
        status=NodeStatus.FAILED,
        events=(DomainEvent(name="extract.failed", payload={"reason": error_code}),),
        error=ErrorRecord(
            error_id=uuid4(),
            run_id=state.run_id,
            node="EXTRACT",
            error_code=error_code,
            error_class="ExtractionFailed",
            message_redacted=message,
            retryable=False,
            attempt=1,
            resulting_state=RunStatus.FAILED_TERMINAL,
            occurred_at=datetime.now(UTC),
        ),
    )
