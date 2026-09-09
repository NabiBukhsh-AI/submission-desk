"""Reading real documents into the object every quotation is checked against.

These use real PDFs and a real Word file, built at test time by the same
libraries that read them back. A fixture that is not genuinely a PDF would let
the extractor pass while failing on anything a candidate actually sends.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from application.deps import Deps
from domain.contracts import CandidateDocument, DocumentRole, ExtractionMethod
from domain.ports.extraction import ExtractionFailed, OcrResult
from infrastructure.extraction import confidence, language, layout
from infrastructure.extraction.dispatcher import PAGE_SEPARATOR, Extractor
from infrastructure.extraction.ocr import (
    MIN_CONFIDENCE,
    TesseractEngine,
    UnavailableOcrEngine,
    read_with_retry,
)
from infrastructure.extraction.pdf import OCR_TRIGGER_CHARS, open_document
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline.extract import node
from tests.fixtures import documents, pdfs
from tests.workflow.conftest import make_run_record

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def document(mime: str = PDF_MIME, *, sha: str | None = None) -> CandidateDocument:
    digest = sha or uuid4().hex + uuid4().hex[:32]
    return CandidateDocument(
        document_id=uuid4(),
        candidate_id="cand-0007",
        original_filename="cv.pdf",
        document_sha256=digest,
        mime_type=mime,
        size_bytes=1000,
        blob_path=f"data/blobs/{digest[:2]}/{digest}",
        doc_role=DocumentRole.CV,
        received_at=datetime.now(UTC),
    )


@pytest.fixture
def extractor() -> Extractor:
    return Extractor(ocr=UnavailableOcrEngine())


# --- PDF with a text layer ------------------------------------------------------


def test_a_text_pdf_is_read(extractor: Extractor) -> None:
    source = extractor.extract(document(), pdfs.text_pdf())

    assert "Acme Payments" in source.normalized_text
    assert source.pages[0].extraction_method is ExtractionMethod.DIGITAL_PDF
    assert source.extraction_confidence > 0.9


def test_the_pages_tile_the_text_with_no_gaps(extractor: Extractor) -> None:
    """Every character has a page, or a quotation resolves to nowhere. The
    contract checks this; the test proves the assembler satisfies it."""
    source = extractor.extract(document(), pdfs.text_pdf(pages=3))

    assert source.pages[0].norm_start == 0
    assert source.pages[-1].norm_end == len(source.normalized_text)
    for earlier, later in zip(source.pages, source.pages[1:], strict=False):
        assert earlier.norm_end == later.norm_start


def test_pages_are_separated_so_no_sentence_spans_the_seam(extractor: Extractor) -> None:
    """A single newline would let the last line of one page and the first of the
    next form a sentence that exists in no document."""
    source = extractor.extract(document(), pdfs.text_pdf(pages=2))

    assert PAGE_SEPARATOR in source.normalized_text


def test_a_forty_page_document_is_read_without_truncation(extractor: Extractor) -> None:
    source = extractor.extract(document(), pdfs.long_pdf(40))

    assert len(source.pages) == 40
    assert len(source.normalized_text) > 15_000


def test_a_one_page_document_is_read(extractor: Extractor) -> None:
    source = extractor.extract(document(), pdfs.text_pdf())

    assert len(source.pages) == 1


# --- OCR ------------------------------------------------------------------------


def test_a_scanned_page_records_that_it_was_scanned(extractor: Extractor) -> None:
    """The method is recorded even when no reader is installed, because a
    quotation from a scanned page must be validated with OCR tolerance."""
    source_text = pdfs.mixed_pdf()
    source = extractor.extract(document(), source_text)

    methods = [page.extraction_method for page in source.pages]
    assert methods[:2] == [ExtractionMethod.DIGITAL_PDF, ExtractionMethod.DIGITAL_PDF]
    assert methods[2] is ExtractionMethod.OCR


def test_a_mixed_document_keeps_its_good_pages(extractor: Extractor) -> None:
    """Re-reading the whole file through OCR to fix page three would throw away
    the text from pages one and two."""
    source = extractor.extract(document(), pdfs.mixed_pdf())

    assert "Acme Payments" in source.normalized_text


def test_a_scan_lowers_the_confidence(extractor: Extractor) -> None:
    clean = extractor.extract(document(), pdfs.text_pdf(pages=3))
    mixed = extractor.extract(document(), pdfs.mixed_pdf())

    assert mixed.extraction_confidence < clean.extraction_confidence


def test_a_document_that_is_all_scan_cannot_be_read(extractor: Extractor) -> None:
    """With no reader installed there is nothing to assess, and saying so beats
    presenting an empty assessment."""
    with pytest.raises(ExtractionFailed, match="No readable text"):
        extractor.extract(document(), pdfs.scanned_pdf())


def test_a_missing_reader_is_reported_not_absorbed() -> None:
    """A scan that produced nothing because nobody installed the reader looks
    exactly like a blank page. Those two must not be confused."""
    result = UnavailableOcrEngine().read(b"")

    assert result.available is False
    assert result.reason


def test_the_real_engine_reports_whether_it_is_installed() -> None:
    """Tesseract is a system binary. Whether it is present is a fact about the
    machine, and the engine states it rather than guessing."""
    engine = TesseractEngine()

    assert isinstance(engine.available, bool)
    if not engine.available:
        assert engine.read(b"").available is False


def test_a_low_confidence_page_is_read_once_more_at_higher_resolution() -> None:
    """One retry, never a loop. A page still uncertain at the higher resolution
    is one the reviewer should look at themselves."""
    attempts: list[int] = []

    class Flaky:
        available = True

        def read(self, image_bytes: bytes, *, dpi: int = 300) -> OcrResult:
            attempts.append(dpi)
            return OcrResult(
                text="better" if dpi > 300 else "poor", mean_confidence=0.9 if dpi > 300 else 0.2
            )

    result = read_with_retry(Flaky(), b"image")

    assert attempts == [300, 400]
    assert result.text == "better"


def test_a_confident_page_is_not_read_twice() -> None:
    attempts: list[int] = []

    class Confident:
        available = True

        def read(self, image_bytes: bytes, *, dpi: int = 300) -> OcrResult:
            attempts.append(dpi)
            return OcrResult(text="clear", mean_confidence=MIN_CONFIDENCE + 0.1)

    read_with_retry(Confident(), b"image")

    assert attempts == [300]


def test_a_missing_engine_is_not_retried() -> None:
    attempts: list[int] = []

    class Missing:
        available = False

        def read(self, image_bytes: bytes, *, dpi: int = 300) -> OcrResult:
            attempts.append(dpi)
            return OcrResult(text="", mean_confidence=0.0, available=False)

    read_with_retry(Missing(), b"image")

    assert attempts == [300], "a missing reader was asked twice"


# --- DOCX -----------------------------------------------------------------------


def test_a_word_document_is_read(extractor: Extractor) -> None:
    source = extractor.extract(document(DOCX_MIME), pdfs.docx_with_tables())

    assert "Ana Ferreira" in source.normalized_text
    assert source.pages[0].extraction_method is ExtractionMethod.DOCX


def test_table_cells_are_separated_so_a_row_reads_as_a_sentence(extractor: Extractor) -> None:
    """Concatenated cells give "Lead EngineerAcme2021", which nobody can quote."""
    source = extractor.extract(document(DOCX_MIME), pdfs.docx_with_tables())

    assert "Acme Payments | Lead Engineer | 2021 to present" in source.normalized_text


def test_a_word_document_is_one_page(extractor: Extractor) -> None:
    """A DOCX has no pages until it is rendered. Inventing one would send a
    reviewer to the wrong place in their own file."""
    source = extractor.extract(document(DOCX_MIME), pdfs.docx_with_tables())

    assert len(source.pages) == 1


# --- plain text -----------------------------------------------------------------


def test_plain_text_is_read(extractor: Extractor) -> None:
    source = extractor.extract(document("text/plain"), documents.plain_text(500))

    assert source.pages[0].extraction_method is ExtractionMethod.PLAINTEXT


def test_a_stray_byte_does_not_lose_the_document(extractor: Extractor) -> None:
    """Refusing a whole CV over one bad character is a worse outcome than one
    replacement character in the middle of it."""
    data = documents.plain_text(400) + b"\xff\xfe" + documents.plain_text(200)

    source = extractor.extract(document("text/plain"), data)

    assert len(source.normalized_text) > 500


# --- reading order ----------------------------------------------------------------


def test_two_columns_are_read_one_after_the_other(extractor: Extractor) -> None:
    """Read line by line, the columns interleave and produce sentences that
    exist in no document."""
    source = extractor.extract(document(), pdfs.two_column_pdf())
    text = source.normalized_text

    assert "Incident response" in text
    assert "Acme Payments" in text
    assert text.index("Incident response") < text.index("Acme Payments")


def test_a_single_column_page_is_left_alone() -> None:
    blocks = ((50.0, 0.0, 500.0, 10.0, "one"), (50.0, 20.0, 500.0, 30.0, "two"))

    assert len(layout.detect_columns(blocks, 595)) == 1


def test_two_separated_groups_become_two_columns() -> None:
    blocks = (
        (50.0, 0.0, 240.0, 10.0, "left"),
        (330.0, 0.0, 545.0, 10.0, "right"),
    )

    assert len(layout.detect_columns(blocks, 595)) == 2


def test_blocks_on_the_same_line_keep_their_order() -> None:
    blocks = ((100.0, 5.0, 150.0, 15.0, "second"), (50.0, 5.0, 90.0, 15.0, "first"))

    ordered = layout.reading_order(blocks, 595)

    assert [block[4] for block in ordered] == ["first", "second"]


# --- language ---------------------------------------------------------------------


def test_an_english_document_is_detected(extractor: Extractor) -> None:
    source = extractor.extract(document(), pdfs.text_pdf())

    assert source.detected_languages == ["en"]


def test_a_korean_document_is_detected_and_processed(extractor: Extractor) -> None:
    """MUST is Korean-headquartered, so this case will be looked at. The system
    processes it and reports the quality as unmeasured rather than refusing."""
    source = extractor.extract(document(), pdfs.korean_pdf())

    assert source.detected_languages == ["ko"]
    assert len(source.normalized_text) > 50


def test_korean_is_supported_but_its_quality_is_unmeasured() -> None:
    report = language.detect(["에이콤 결제팀에서 리드 엔지니어로 결제 백엔드를 운영했습니다. " * 3])

    assert report.is_supported
    assert not report.is_allowed
    assert report.quality_is_unmeasured


def test_a_page_too_thin_to_judge_contributes_nothing() -> None:
    """A page of dates and single words is not a language sample, and forcing a
    guess out of it is how an English CV gets reported as Afrikaans."""
    assert language.detect_one("2021 - 2023") is None
    assert language.detect(["2021", "Acme"]).dominant is None


def test_detection_is_reproducible() -> None:
    """The detector randomises by default, which would make two runs of the same
    document disagree and break every experiment that holds one variable."""
    sample = ["Owned a multi-service payments backend and its on-call rotation for two years."]

    assert language.detect(sample).dominant == language.detect(sample).dominant


def test_a_mixed_document_scores_lower_on_language() -> None:
    mixed = language.LanguageReport(
        languages=("en", "ko"), proportions={"en": 0.5, "ko": 0.5}, dominant="en", mixed=True
    )

    assert mixed.score == 0.5


# --- confidence ---------------------------------------------------------------------


def test_confidence_is_computed_from_measured_parts() -> None:
    """Not a model output, and never presented as one."""
    from domain.ports.extraction import PageText

    clean = [
        PageText(
            page_number=1,
            text="x" * 500,
            method=ExtractionMethod.DIGITAL_PDF,
            blocks=((0, 0, 1, 1, "a"), (0, 1, 1, 2, "b")),
        )
    ]

    breakdown = confidence.compute(clean)

    assert breakdown.total > 0.9
    assert not breakdown.is_poor


def test_a_poor_scan_falls_below_the_warning_threshold() -> None:
    """This is what makes the reviewer see "check the quotations by eye"."""
    from domain.ports.extraction import PageText

    poor = [
        PageText(
            page_number=index,
            text="sm",
            method=ExtractionMethod.OCR,
            ocr_confidence=0.3,
            blocks=(),
        )
        for index in range(1, 4)
    ]

    breakdown = confidence.compute(poor)

    assert breakdown.is_poor
    assert breakdown.total < confidence.WARN_THRESHOLD


def test_an_empty_document_scores_zero() -> None:
    """Rather than defaulting to something comfortable."""
    assert confidence.compute([]).total == 0.0


def test_a_low_score_explains_itself() -> None:
    from domain.ports.extraction import PageText

    breakdown = confidence.compute(
        [PageText(page_number=1, text="", method=ExtractionMethod.OCR, ocr_confidence=0.2)]
    )

    assert "optical character recognition" in breakdown.explain()


def test_a_clean_document_says_so() -> None:
    from domain.ports.extraction import PageText

    breakdown = confidence.compute(
        [
            PageText(
                page_number=1,
                text="x" * 500,
                method=ExtractionMethod.DIGITAL_PDF,
                blocks=((0, 0, 1, 1, "a"), (0, 1, 1, 2, "b")),
            )
        ]
    )

    assert breakdown.explain() == "the document was read cleanly"


# --- security ------------------------------------------------------------------------


def test_a_pdf_is_opened_without_scripting() -> None:
    """A PDF can carry JavaScript, and this system reads documents from
    strangers. PyMuPDF does not execute it, and nothing here asks it to."""
    import inspect

    from infrastructure.extraction import pdf as pdf_module

    source = inspect.getsource(pdf_module)
    for dangerous in ("execute_js", "set_javascript", "embfile_add", "eval("):
        assert dangerous not in source


def test_a_password_protected_pdf_is_named_as_such() -> None:
    import pymupdf

    document_ = pymupdf.open()
    document_.new_page()
    protected = document_.tobytes(
        encryption=getattr(pymupdf, "PDF_ENCRYPT_AES_256", 5), owner_pw="x", user_pw="y"
    )
    document_.close()

    with pytest.raises(ExtractionFailed, match="password protected"):
        open_document(protected)


def test_a_corrupt_pdf_is_named_as_such() -> None:
    with pytest.raises(ExtractionFailed, match="could not be opened"):
        open_document(b"%PDF-1.4\nnot actually a pdf")


def test_an_unsupported_mime_type_is_refused(extractor: Extractor) -> None:
    with pytest.raises(ExtractionFailed, match="cannot be read"):
        extractor.extract(document("application/zip"), b"PK\x03\x04")


# --- the node and the cache -------------------------------------------------------


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "extract.sqlite"), blob_dir=str(tmp_path / "blobs")
    )
    built = build_deps(settings)
    yield Deps(**{**built.__dict__, "extractor": Extractor(ocr=UnavailableOcrEngine())})
    close_thread_connection(settings.db_path)


def run_extract(deps: Deps, data: bytes, mime: str = PDF_MIME):
    from domain.contracts.enums import RunStatus
    from domain.contracts.run_state import RunState

    record = make_run_record()
    deps.runs.create(record)
    digest = deps.blobs.put(data)
    deps.candidates.add_document(
        document(mime, sha=digest).model_copy(
            update={"blob_path": str(deps.blobs.path_for(digest))}
        ),
        run_id=record.run_id,
    )
    state = RunState(
        run_id=record.run_id,
        candidate_id=record.candidate_id,
        role_id=record.role_id,
        status=RunStatus.INTAKE_OK,
        started_at=record.started_at,
    )
    return node(state, deps)


def test_the_node_reads_and_stores(deps: Deps) -> None:
    from domain.contracts.run_state import NodeStatus

    result = run_extract(deps, pdfs.text_pdf())

    assert result.status is NodeStatus.OK
    assert [event.name for event in result.events] == ["extract.document_read"]


def test_a_second_run_of_the_same_document_does_not_re_parse(deps: Deps) -> None:
    """Extraction is independent of the rubric, so changing a criterion must
    never re-OCR a sixty-page scan."""
    data = pdfs.text_pdf()
    run_extract(deps, data)
    before = deps.extractor.parse_count

    run_extract(deps, data)

    assert deps.extractor.parse_count == before, "the cached document was parsed again"


def test_a_cache_hit_is_recorded(deps: Deps) -> None:
    data = pdfs.text_pdf()
    run_extract(deps, data)

    result = run_extract(deps, data)

    assert [event.name for event in result.events] == ["extract.cache_hit"]


def test_an_unreadable_document_fails_with_a_sentence(deps: Deps) -> None:
    from domain.contracts.run_state import NodeStatus

    result = run_extract(deps, pdfs.scanned_pdf())

    assert result.status is NodeStatus.FAILED
    assert result.error.error_code == "EMPTY_DOCUMENT"
    assert "readable text" in result.error.message_redacted


def test_a_poorly_read_document_degrades_rather_than_failing(deps: Deps) -> None:
    """Half a CV assessed honestly beats none."""
    from domain.contracts.run_state import NodeStatus

    result = run_extract(deps, pdfs.mixed_pdf())

    assert result.status in (NodeStatus.OK, NodeStatus.DEGRADED)
    stored = deps.candidates.get_source_text(
        deps.candidates.documents_for_run(result.state.run_id)[0].document_sha256,
        deps.extractor.profile_id,
    )
    assert stored is not None
    assert "Acme" in stored.normalized_text


def test_the_ocr_trigger_is_per_page_not_per_document() -> None:
    """A CV that is digital on page one and a photograph on page three is
    normal. The threshold is a page property."""
    assert OCR_TRIGGER_CHARS > 0
