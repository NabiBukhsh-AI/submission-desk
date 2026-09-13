"""The STRUCTURE node.

Turns prose into structured employment history, with every field carrying the
text it came from, and degrades rather than inventing when it cannot.

The failure behaviour is the interesting part. When validation fails twice, this
node does not raise and does not guess: it keeps the fields that validated,
nulls the rest, marks the profile partial, and lets the run continue as
DEGRADED. Half a profile with a flag on it is useful to a reviewer. A complete
profile with two invented fields is worse than nothing, because there is no way
to tell which two.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from application.deps import Deps
from domain.contracts.enums import RunStatus
from domain.contracts.errors import ErrorRecord
from domain.contracts.profile import CandidateProfile, EmploymentEntry, ProvenancedField
from domain.contracts.run_state import DomainEvent, NodeResult, NodeStatus, RunState
from domain.contracts.source_text import SourceText
from domain.ports.models import BlockKind, GenerationRequest, ModelUnavailable, PromptBlock
from domain.provenance import chunking
from domain.provenance.merge import merge_profiles

#: Input ceiling for one structuring call, in tokens. A document above it is
#: read in page groups and merged.
MAX_INPUT_TOKENS = 24_000


def node(state: RunState, deps: Deps) -> NodeResult:
    """Read the candidate's documents into a profile."""
    documents = deps.candidates.documents_for_run(state.run_id)
    sources = _sources_for(deps, documents)

    if not sources:
        return _failed(state, "The documents could not be read into text.", "NO_SOURCE_TEXT")

    events: list[DomainEvent] = []
    profiles: list[CandidateProfile] = []
    degraded_reasons: list[str] = []

    for source in sources:
        try:
            profile, chunk_count, repaired = _profile_from(state, deps, source)
        except ModelUnavailable as unavailable:
            return _failed(state, str(unavailable), "MODEL_UNAVAILABLE", retryable=True)

        profiles.append(profile)
        if profile.partial:
            degraded_reasons.append(profile.unparsed_reason or "partial_profile")

        events.append(
            DomainEvent(
                name="structure.profile_extracted",
                payload={
                    "chunks": chunk_count,
                    "repaired": repaired,
                    "partial": profile.partial,
                    "null_rate": round(_null_rate(profile), 3),
                    "employment_entries": len(profile.employment),
                },
            )
        )

    merged = merge_profiles(profiles)
    conflicts = _conflict_count(merged)
    if conflicts:
        events.append(DomainEvent(name="structure.conflicts", payload={"count": conflicts}))

    carried = state.model_copy(update={"profile": merged, "profile_partial": merged.partial})

    if merged.partial or degraded_reasons:
        events.append(DomainEvent(name="structure.partial", payload={"reasons": degraded_reasons}))
        return NodeResult(
            state=carried.degraded_by("partial_profile"),
            status=NodeStatus.DEGRADED,
            events=tuple(events),
            next_status=RunStatus.STRUCTURED,
        )

    return NodeResult(
        state=carried,
        status=NodeStatus.OK,
        events=tuple(events),
        next_status=RunStatus.STRUCTURED,
    )


def _profile_from(
    state: RunState, deps: Deps, source: SourceText
) -> tuple[CandidateProfile, int, bool]:
    """Extract one document's profile, chunking it if it will not fit."""
    chunks = chunking.by_page_groups(source, max_tokens=MAX_INPUT_TOKENS)
    prompt = deps.prompts.get("structure/profile")
    partials: list[CandidateProfile] = []
    repaired = False

    for chunk in chunks:
        request = GenerationRequest(
            call_site="structure.profile",
            tier=deps.settings.structure_tier,
            system_prompt=prompt.render(),
            user_blocks=(
                PromptBlock(
                    kind=BlockKind.DOCUMENT,
                    content=chunk.text,
                    document_id=source.document_id,
                ),
            ),
            response_schema=CandidateProfile,
            temperature=0.0,
            nonce=state.nonce or "",
        )

        result = deps.models.structured_generate(request)
        repaired = repaired or result.repaired

        if result.ok and isinstance(result.parsed, CandidateProfile):
            partials.append(_with_candidate_id(result.parsed, state.candidate_id))
            continue

        # Repair already had its one attempt inside the model layer. What
        # arrives here has failed twice, and the honest response is a profile
        # that says so rather than a profile with invented fields.
        partials.append(
            CandidateProfile(
                candidate_id=state.candidate_id,
                partial=True,
                unparsed_reason=(
                    "This document could not be read into a structured profile. "
                    "The quotations below still come from the document; the summary "
                    "fields are missing rather than guessed."
                ),
            )
        )

    return merge_profiles(partials), len(chunks), repaired


def _with_candidate_id(profile: CandidateProfile, candidate_id: str) -> CandidateProfile:
    """The candidate id is ours, not the model's.

    A model asked for it would return whatever the document called the person,
    which is a name, and names are exactly what blind mode removes.
    """
    return profile.model_copy(update={"candidate_id": candidate_id})


def _sources_for(deps: Deps, documents: list[Any]) -> list[SourceText]:
    profile_id = deps.source_profile_id
    found: list[SourceText] = []
    for document in documents:
        source = deps.candidates.get_source_text(document.document_sha256, profile_id)
        if source is not None:
            found.append(source)
    return found


def _null_rate(profile: CandidateProfile) -> float:
    """How much of the profile came back empty.

    Watched over time rather than acted on per run: a rising null rate is a
    prompt regression or a change in the documents arriving, and both are worth
    noticing before someone reports that the system has got worse.
    """
    fields: list[ProvenancedField[Any]] = [
        *profile.education,
        *profile.technologies,
        *profile.languages,
        *profile.artefact_links,
    ]
    for entry in profile.employment:
        fields.extend([entry.employer, entry.title, entry.start, entry.end, entry.summary])

    if not fields:
        return 1.0
    return sum(1 for field in fields if field.value is None) / len(fields)


def _conflict_count(profile: CandidateProfile) -> int:
    total = sum(len(field.conflicts) for field in profile.education)
    total += sum(len(field.conflicts) for field in profile.technologies)
    for entry in profile.employment:
        for field in (entry.employer, entry.title, entry.start, entry.end, entry.summary):
            total += len(field.conflicts)
    return total


def _failed(
    state: RunState, message: str, error_code: str, *, retryable: bool = False
) -> NodeResult:
    return NodeResult(
        state=state,
        status=NodeStatus.FAILED,
        events=(DomainEvent(name="structure.failed", payload={"reason": error_code}),),
        error=ErrorRecord(
            error_id=uuid4(),
            run_id=state.run_id,
            node="STRUCTURE",
            error_code=error_code,
            error_class="StructureFailed",
            message_redacted=message,
            retryable=retryable,
            attempt=1,
            resulting_state=RunStatus.MANUAL_REVIEW_REQUIRED,
            occurred_at=datetime.now(UTC),
        ),
    )


def employment_entry(**fields: Any) -> EmploymentEntry:
    """Build an entry with every field provenanced, for tests and fixtures."""
    return EmploymentEntry(
        **{
            name: value if isinstance(value, ProvenancedField) else ProvenancedField(value=value)
            for name, value in fields.items()
        }
    )
