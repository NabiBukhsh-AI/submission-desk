"""Synthetic documents, including the ones designed to be refused.

Every byte here is generated. Nothing in this module came from a real CV, which
is the whole reason the adversarial suite can live in a public repository.
"""

from __future__ import annotations

import io
import zipfile

MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n"
    b"3 0 obj<</Type /Page /Parent 2 0 R>>endobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF\n"
)


def pdf(pages: int = 1, *, encrypted: bool = False, truncated: bool = False) -> bytes:
    """A PDF with a countable number of page objects."""
    body = b"%PDF-1.4\n"
    if encrypted:
        body += b"<</Encrypt 9 0 R>>\n"
    body += b"2 0 obj<</Type /Pages /Count %d>>endobj\n" % pages
    for index in range(pages):
        body += b"%d 0 obj<</Type /Page /Parent 2 0 R>>endobj\n" % (index + 3)
    return body if truncated else body + b"trailer<</Root 1 0 R>>\n%%EOF\n"


def docx(text: str = "Shipped an evaluation suite to production.") -> bytes:
    """A DOCX small enough to build in memory and real enough to sniff."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types"/>',
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
            f'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r>'
            "</w:p></w:body></w:document>",
        )
    return buffer.getvalue()


def zip_bomb(entries: int = 50, size: int = 5_000_000) -> bytes:
    """A small archive that expands enormously.

    Rejected because it is not a DOCX, before anything decompresses it. That
    ordering is the defence: the marker check reads bytes, it does not open the
    archive.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(entries):
            archive.writestr(f"entry-{index}.txt", b"\0" * size)
    return buffer.getvalue()


def xml_entity_bomb() -> bytes:
    """The billion-laughs shape, as a DOCX would carry it."""
    buffer = io.BytesIO()
    entities = "".join(f'<!ENTITY e{i} "&e{i - 1};&e{i - 1};">' for i in range(1, 12))
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY e0 "lol">{entities}]><d>&e11;</d>',
        )
    return buffer.getvalue()


def windows_executable() -> bytes:
    """A PE header. Renamed to .pdf in the tests, and still refused."""
    return b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200


def shell_script() -> bytes:
    return b"#!/bin/sh\nrm -rf /\n"


def plain_text(chars: int = 400) -> bytes:
    sentence = "Built and operated an evaluation suite for a production service. "
    return (sentence * (chars // len(sentence) + 1))[:chars].encode("utf-8")


def binary_noise(size: int = 4096) -> bytes:
    """Bytes that decode as nothing and are not a recognised type."""
    return bytes((index * 7 + 11) % 256 for index in range(size))
