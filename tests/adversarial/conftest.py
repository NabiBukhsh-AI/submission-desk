"""The adversarial corpus, built once per session.

Generated into a temporary directory rather than committed. The generator is
the readable artefact: a reviewer can see how each attack was constructed,
which a binary fixture does not offer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from domain.contracts.documents import CandidateDocument
from domain.contracts.enums import DocumentRole
from domain.contracts.source_text import SourceText
from infrastructure.extraction.dispatcher import Extractor
from infrastructure.extraction.ocr import TesseractEngine
from infrastructure.security import scan as scanner
from scripts.make_adversarial_corpus import SAMPLES, build


@pytest.fixture(scope="session")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("adversarial")
    build(out)
    return out


@pytest.fixture(scope="session")
def extractor() -> Extractor:
    return Extractor(ocr=TesseractEngine())


def document_for(path: Path) -> CandidateDocument:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    return CandidateDocument(
        document_id=uuid4(),
        candidate_id="cand-adv",
        original_filename=path.name,
        document_sha256=digest,
        mime_type="application/pdf" if path.suffix == ".pdf" else "text/plain",
        size_bytes=len(data),
        blob_path=f"data/blobs/{digest[:2]}/{digest}",
        doc_role=DocumentRole.CV,
        received_at=datetime.now(UTC),
    )


def read(path: Path, extractor: Extractor) -> tuple[CandidateDocument, SourceText]:
    document = document_for(path)
    return document, extractor.extract(document, path.read_bytes())


def scan_of(path: Path, extractor: Extractor, *, render_diff: bool = False):
    """Run every detector over one corpus document.

    The render comparison is off by default: it costs an OCR pass per page and
    is exercised on its own in ``test_render_diff``, against fixed strings, so
    the rest of the suite does not pay for it.
    """
    document, source = read(path, extractor)
    data = path.read_bytes()

    if document.mime_type == "application/pdf":
        return scanner.scan_pdf(
            data,
            source.normalized_text,
            raw_text=source.raw_text,
            ocr=extractor.ocr,
            render_diff_enabled=render_diff,
        )
    return scanner.scan_text(source.normalized_text, raw_text=source.raw_text)


#: (filename, sample) for every attack document.
ATTACKS = [(name, sample) for name, (sample, _) in SAMPLES.items() if sample.expects]

#: (filename, sample) for every document that must stay quiet.
NEGATIVES = [(name, sample) for name, (sample, _) in SAMPLES.items() if not sample.expects]


@pytest.fixture(scope="session")
def scans(corpus: Path, extractor: Extractor) -> Iterator[dict]:
    """Every document scanned once, because extraction is not free."""
    yield {name: scan_of(corpus / name, extractor) for name in SAMPLES}
