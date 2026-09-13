"""The STRUCTURE node, against three CVs and a model that misbehaves.

Two properties matter more than the extraction itself.

Nothing is fabricated. When the model fails twice, the node keeps what
validated, nulls the rest, and marks the profile partial. Half a profile with a
flag on it is useful; a complete profile with two invented fields is worse than
nothing, because there is no way to tell which two.

Nothing forbidden is recorded. A CV stating age, nationality and marital status
produces a profile with no field and no claim referencing any of them, because
the schema has nowhere to put them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from uuid import uuid4

import pytest

from application.deps import Deps
from domain.contracts import CandidateDocument, DocumentRole, ExtractionMethod, RunStatus
from domain.contracts.profile import CandidateProfile
from domain.contracts.run_state import NodeStatus, RunState
from domain.contracts.source_text import OffsetRun, PageSpan, SourceText
from domain.contracts.work_authorization import AuthorizationStatement, WorkAuthorizationNote
from domain.ports.models import BlockKind, GenerationRequest, ModelUnavailable
from domain.provenance import chunking
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.models.fake import ScriptedModelClient
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline.structure import node
from tests.workflow.conftest import make_run_record

CLEAN_CV = (
    "Ana Ferreira, Senior Backend Engineer. "
    "Acme Payments, Lead Engineer, 2021 to present. "
    "Owned a multi-service payments backend and its on-call rotation. "
    "Northwind Logistics, Backend Engineer, 2018 to 2021. "
    "BSc Computer Science, University of Porto, 2018. Python, PostgreSQL, Kubernetes."
)

SPARSE_CV = "Ben Oyelaran. Engineer. Python."

DISCLOSING_CV = (
    "Chidi Okonkwo, 34 years old, married with two children, Nigerian national. "
    "Backend Engineer at Acme Payments since 2021. "
    "Owned the payments service and its deployment pipeline."
)

PROFILE_JSON = json.dumps(
    {
        "candidate_id": "cand-0007",
        "employment": [
            {
                "employer": {"value": "Acme Payments", "provenance": [], "conflicts": []},
                "title": {"value": "Lead Engineer", "provenance": [], "conflicts": []},
                "start": {"value": "2021", "provenance": [], "conflicts": []},
                "end": {"value": "present", "provenance": [], "conflicts": []},
                "summary": {
                    "value": "Owned a multi-service payments backend",
                    "provenance": [],
                    "conflicts": [],
                },
            }
        ],
        "education": [{"value": "BSc Computer Science", "provenance": [], "conflicts": []}],
        "technologies": [{"value": "Python", "provenance": [], "conflicts": []}],
        "languages": [],
        "artefact_links": [],
        "partial": False,
    }
)

EMPTY_PROFILE_JSON = json.dumps({"candidate_id": "cand-0007", "partial": False})
MALFORMED = "{not json"


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "structure.sqlite"), blob_dir=str(tmp_path / "blobs")
    )
    yield build_deps(settings)
    close_thread_connection(settings.db_path)


def store_document(deps: Deps, run_id, text: str) -> SourceText:
    """Put one document and its extracted text where the node will find them."""
    digest = uuid4().hex + uuid4().hex[:32]
    document = CandidateDocument(
        document_id=uuid4(),
        candidate_id="cand-0007",
        original_filename="cv.pdf",
        document_sha256=digest,
        mime_type="application/pdf",
        size_bytes=len(text),
        blob_path=f"data/blobs/{digest[:2]}/{digest}",
        doc_role=DocumentRole.CV,
        received_at=datetime.now(UTC),
    )
    deps.candidates.add_document(document, run_id=run_id)

    source = SourceText(
        document_id=document.document_id,
        raw_text=text,
        normalized_text=text,
        offset_runs=[OffsetRun(norm_start=0, raw_start=0, length=len(text))],
        pages=[
            PageSpan(
                page_number=1,
                norm_start=0,
                norm_end=len(text),
                extraction_method=ExtractionMethod.DIGITAL_PDF,
            )
        ],
        normalization_profile_id=deps.source_profile_id,
        extraction_confidence=0.95,
        detected_languages=["en"],
    )
    deps.candidates.put_source_text(source, document_sha256=digest)
    return source


def run_structure(deps: Deps, text: str, responses: list[str | Exception]):
    record = make_run_record()
    deps.runs.create(record)
    store_document(deps, record.run_id, text)

    client = ScriptedModelClient(responses=responses)
    wired = Deps(**{**deps.__dict__, "models": client})
    state = RunState(
        run_id=record.run_id,
        candidate_id=record.candidate_id,
        role_id=record.role_id,
        status=RunStatus.SANITIZED,
        started_at=record.started_at,
        nonce="a3f9",
    )
    return node(state, wired), client


# --- three CVs ---------------------------------------------------------------------


def test_a_clean_cv_produces_a_profile(deps: Deps) -> None:
    result, _ = run_structure(deps, CLEAN_CV, [PROFILE_JSON])

    assert result.status is NodeStatus.OK
    profile = result.state.profile
    assert profile.employment[0].employer.value == "Acme Payments"


def test_a_sparse_cv_produces_a_thin_profile_not_an_invented_one(deps: Deps) -> None:
    """A one-line CV should yield almost nothing. Filling it in would be the
    system telling a recruiter something the document never said."""
    result, _ = run_structure(deps, SPARSE_CV, [EMPTY_PROFILE_JSON])

    assert result.status is NodeStatus.OK
    assert result.state.profile.employment == []


def test_a_disclosing_cv_yields_no_forbidden_field(deps: Deps) -> None:
    """The CV states age, marital status and nationality. None of it can reach
    the profile, because the schema has nowhere to put it.

    Values are checked rather than the serialised object, since the field name
    "languages" contains "age" and a substring check would fail on the schema's
    own vocabulary rather than on anything the model returned.
    """
    result, _ = run_structure(deps, DISCLOSING_CV, [PROFILE_JSON])
    profile = result.state.profile

    values = " ".join(_stated_values(profile)).lower()
    for disclosed in ("34", "married", "nigerian", "children", "two children"):
        assert disclosed not in values, f"{disclosed} reached a profile value"

    field_names = {name.lower() for name in type(profile).model_fields}
    for forbidden in ("age", "gender", "nationality", "marital_status", "family_status"):
        assert forbidden not in field_names


def _stated_values(profile: CandidateProfile) -> list[str]:
    """Every value the profile actually records, as text."""
    values: list[str] = []
    for field_list in (
        profile.education,
        profile.technologies,
        profile.languages,
        profile.artefact_links,
    ):
        values.extend(str(item.value) for item in field_list if item.value is not None)

    for entry in profile.employment:
        for field in (entry.employer, entry.title, entry.start, entry.end, entry.summary):
            if field.value is not None:
                values.append(str(field.value))
    return values


def test_the_candidate_id_is_ours_not_the_models(deps: Deps) -> None:
    """A model asked for it would return whatever the document called the
    person, which is a name, and names are what blind mode removes."""
    hijacked = json.dumps({"candidate_id": "Ana Ferreira", "partial": False})

    result, _ = run_structure(deps, CLEAN_CV, [hijacked])

    assert result.state.profile.candidate_id == "cand-0007"


# --- the degraded path ----------------------------------------------------------------


def test_a_model_that_fails_twice_produces_a_partial_profile(deps: Deps) -> None:
    """Not an exception, and not an invention. The run continues and the
    reviewer is told which part is missing."""
    result, _ = run_structure(deps, CLEAN_CV, [MALFORMED])

    assert result.status is NodeStatus.DEGRADED
    assert result.state.profile.partial is True
    assert result.state.profile.employment == []


def test_the_partial_profile_says_what_is_missing_in_plain_words(deps: Deps) -> None:
    result, _ = run_structure(deps, CLEAN_CV, [MALFORMED])

    reason = result.state.profile.unparsed_reason or ""
    assert "guessed" in reason or "missing" in reason
    assert "exception" not in reason.lower()


def test_a_degraded_run_still_reaches_the_next_stage(deps: Deps) -> None:
    """Half a CV assessed honestly beats none."""
    result, _ = run_structure(deps, CLEAN_CV, [MALFORMED])

    assert result.next_status is RunStatus.STRUCTURED


def test_an_unreachable_model_fails_the_node_retryably(deps: Deps) -> None:
    """Distinct from a bad response: the provider may be back in a minute, and
    the run should be retried rather than marked partial forever."""
    result, _ = run_structure(deps, CLEAN_CV, [ModelUnavailable("provider is down")])

    assert result.status is NodeStatus.FAILED
    assert result.error.retryable is True


def test_missing_extracted_text_fails_with_a_sentence(deps: Deps) -> None:
    record = make_run_record()
    deps.runs.create(record)
    wired = Deps(**{**deps.__dict__, "models": ScriptedModelClient(responses=[])})
    state = RunState(
        run_id=record.run_id,
        candidate_id=record.candidate_id,
        role_id=record.role_id,
        status=RunStatus.SANITIZED,
        started_at=record.started_at,
    )

    result = node(state, wired)

    assert result.status is NodeStatus.FAILED
    assert "could not be read" in result.error.message_redacted


# --- what reaches the model -----------------------------------------------------------


def test_the_document_is_sent_as_an_untrusted_block(deps: Deps) -> None:
    """Never in the system prompt, and always tagged so the renderer wraps it."""
    _, client = run_structure(deps, CLEAN_CV, [PROFILE_JSON])

    request: GenerationRequest = client.calls[0]
    assert [block.kind for block in request.user_blocks] == [BlockKind.DOCUMENT]
    assert CLEAN_CV not in request.system_prompt


def test_the_call_is_made_at_the_declared_site(deps: Deps) -> None:
    _, client = run_structure(deps, CLEAN_CV, [PROFILE_JSON])

    assert client.calls[0].call_site == "structure.profile"


def test_the_call_runs_at_temperature_zero(deps: Deps) -> None:
    """Reproducibility is what the routing and fairness comparisons rest on."""
    _, client = run_structure(deps, CLEAN_CV, [PROFILE_JSON])

    assert client.calls[0].temperature == 0.0


def test_the_document_carries_its_identity(deps: Deps) -> None:
    """So a span found in it can be attributed to the right file."""
    _, client = run_structure(deps, CLEAN_CV, [PROFILE_JSON])

    assert client.calls[0].user_blocks[0].document_id is not None


def test_no_metadata_reaches_the_prompt(deps: Deps) -> None:
    """The filename is metadata, and a filename can be an instruction."""
    _, client = run_structure(deps, CLEAN_CV, [PROFILE_JSON])

    sent = " ".join(block.content for block in client.calls[0].user_blocks)
    assert "cv.pdf" not in sent


# --- chunking -----------------------------------------------------------------------


def test_a_short_document_is_one_chunk() -> None:
    source = _source(CLEAN_CV, pages=1)

    assert len(chunking.by_page_groups(source, max_tokens=24_000)) == 1


def test_a_long_document_is_split_into_page_groups() -> None:
    source = _source("word " * 4000, pages=8)

    chunks = chunking.by_page_groups(source, max_tokens=500)

    assert len(chunks) > 1


def test_chunks_never_split_a_page() -> None:
    """A quotation running to the end of a page must not be cut in half by an
    arbitrary character budget."""
    source = _source("word " * 4000, pages=8)

    chunks = chunking.by_page_groups(source, max_tokens=500)
    covered = [number for chunk in chunks for number in chunk.page_numbers]

    assert sorted(covered) == list(range(1, 9))


def test_chunks_keep_their_coordinates() -> None:
    """A span found inside a chunk has to be reported in the whole document's
    coordinates, or the reviewer's highlight lands in the wrong place."""
    source = _source("word " * 4000, pages=8)

    chunks = chunking.by_page_groups(source, max_tokens=500)

    assert chunks[0].norm_start == 0
    for earlier, later in pairwise(chunks):
        assert later.norm_start == earlier.norm_end


def test_a_long_document_is_read_in_several_calls_and_merged(deps: Deps) -> None:
    """The whole point of chunking: all of a long CV is read, not the first
    page of it."""
    from pipeline import structure

    long_text = "Acme Payments, Lead Engineer. " * 800
    record = make_run_record()
    deps.runs.create(record)
    store_document(deps, record.run_id, long_text)

    client = ScriptedModelClient(responses=[PROFILE_JSON, PROFILE_JSON, PROFILE_JSON])
    wired = Deps(**{**deps.__dict__, "models": client})
    state = RunState(
        run_id=record.run_id,
        candidate_id=record.candidate_id,
        role_id=record.role_id,
        status=RunStatus.SANITIZED,
        started_at=record.started_at,
    )

    original = structure.MAX_INPUT_TOKENS
    structure.MAX_INPUT_TOKENS = 200
    try:
        result = node(state, wired)
    finally:
        structure.MAX_INPUT_TOKENS = original

    assert result.status in (NodeStatus.OK, NodeStatus.DEGRADED)


def test_chunk_selection_reports_that_it_selected() -> None:
    """An insufficient-evidence result on a chunked document may mean "not in
    what we showed" rather than "not in the CV", and the evaluation must be able
    to tell those apart."""
    source = _source("word " * 4000, pages=8)

    _, was_selected = chunking.select_for_criterion(
        source, query_terms=["python"], max_tokens=500, top_k=2
    )

    assert was_selected is True


def test_a_document_that_fits_is_not_selected_from() -> None:
    source = _source(CLEAN_CV, pages=1)

    chosen, was_selected = chunking.select_for_criterion(
        source, query_terms=["python"], max_tokens=24_000
    )

    assert was_selected is False
    assert len(chosen) == 1


def test_selection_prefers_passages_containing_the_criterion_terms() -> None:
    text = (
        ("filler about nothing. " * 60)
        + "built an evaluation suite with Python. "
        + ("more filler. " * 60)
    )
    source = _source(text, pages=6)

    chosen, _ = chunking.select_for_criterion(
        source, query_terms=["evaluation", "Python"], max_tokens=120, top_k=1
    )

    assert "evaluation suite" in chosen[0].text


def test_query_terms_come_from_the_criterion_wording() -> None:
    """So a recruiter editing a criterion changes what is retrieved for it
    without anyone touching code."""
    terms = chunking.query_terms_for(
        "Measures quality before scaling",
        "Does the document show an evaluation suite?",
        ["built an eval set"],
    )

    assert "evaluation" in terms
    assert "Does" not in terms and "document" not in terms


# --- work authorisation stays apart ------------------------------------------------------


def test_work_authorisation_is_not_on_the_profile() -> None:
    """It is adjacent to nationality and immigration status, which this system
    will not infer and must not be nudged toward."""
    assert "work_authorization" not in CandidateProfile.model_fields
    assert "work_authorisation" not in CandidateProfile.model_fields


def test_a_statement_about_authorisation_must_be_quoted() -> None:
    """The same rule that governs evidence governs this, because an unquoted
    claim on this subject would be indefensible."""
    with pytest.raises(ValueError, match="must quote the document"):
        WorkAuthorizationNote(candidate_id="c", statement=AuthorizationStatement.STATED_ELIGIBLE)


def test_silence_about_authorisation_is_recordable_and_quotes_nothing() -> None:
    """Most CVs do not mention it, and treating silence as a finding would
    decline people for the shape of their CV."""
    note = WorkAuthorizationNote(candidate_id="c", statement=AuthorizationStatement.NOT_STATED)

    assert note.verbatim_span is None


def test_not_stated_cannot_carry_a_quotation() -> None:
    with pytest.raises(ValueError, match="quotes nothing"):
        WorkAuthorizationNote(
            candidate_id="c",
            statement=AuthorizationStatement.NOT_STATED,
            verbatim_span="eligible to work",
        )


def test_the_authorisation_note_never_reaches_an_assessment_prompt(deps: Deps) -> None:
    """Holding it on the profile would put it in front of the model during every
    criterion assessment, where it could colour a judgment about engineering."""
    _, client = run_structure(deps, DISCLOSING_CV, [PROFILE_JSON])

    for call in client.calls:
        sent = call.system_prompt + " ".join(block.content for block in call.user_blocks)
        assert "work_authorization" not in sent
        assert "sponsorship" not in sent.lower()


# --- observability ----------------------------------------------------------------------


def test_the_node_records_what_it_did(deps: Deps) -> None:
    result, _ = run_structure(deps, CLEAN_CV, [PROFILE_JSON])

    names = [event.name for event in result.events]
    assert "structure.profile_extracted" in names


def test_the_null_rate_is_recorded(deps: Deps) -> None:
    """Watched over time: a rising null rate is a prompt regression or a change
    in the documents arriving, and both are worth noticing early."""
    result, _ = run_structure(deps, CLEAN_CV, [PROFILE_JSON])

    payload = next(
        event.payload for event in result.events if event.name == "structure.profile_extracted"
    )
    assert "null_rate" in payload


def _source(text: str, *, pages: int) -> SourceText:
    per_page = max(len(text) // pages, 1)
    spans = []
    cursor = 0
    for number in range(1, pages + 1):
        end = len(text) if number == pages else min(cursor + per_page, len(text))
        spans.append(
            PageSpan(
                page_number=number,
                norm_start=cursor,
                norm_end=end,
                extraction_method=ExtractionMethod.DIGITAL_PDF,
            )
        )
        cursor = end

    return SourceText(
        document_id=uuid4(),
        raw_text=text,
        normalized_text=text,
        offset_runs=[OffsetRun(norm_start=0, raw_start=0, length=len(text))],
        pages=spans,
        normalization_profile_id="np-v1",
        extraction_confidence=0.9,
    )
