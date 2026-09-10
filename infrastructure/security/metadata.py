"""Document properties, read for inspection and never for content.

Metadata is the one part of a document with no reason to reach a model. Nobody
writes their experience into an XMP field, so anything found there is either
noise or an attempt to talk to whatever parses the file.

So this module returns values to the detectors and to the reviewer, and there is
no path from here into a prompt. That is a property of the code rather than a
convention: nothing in ``pipeline/`` passes a metadata value to a
``GenerationRequest``, and a test walks the source to confirm it.
"""

from __future__ import annotations

from typing import Any

#: PDF's own names for the fields worth reading, mapped onto the names the
#: detectors use. Producer and creator-tool strings are deliberately absent:
#: they say which program wrote the file, and scanning them would fire on any
#: application whose name reads like a verb.
PDF_FIELDS: dict[str, str] = {
    "title": "title",
    "subject": "subject",
    "keywords": "keywords",
    "author": "author",
    "creator": "creator",
}

#: DOCX core properties, same idea.
DOCX_FIELDS: tuple[str, ...] = (
    "title",
    "subject",
    "keywords",
    "author",
    "comments",
    "category",
    "content_status",
)

#: No legitimate document property is longer than this. A value beyond it is
#: either a payload or a bug, and truncating bounds what a detector scans and
#: what a reviewer is shown.
MAX_VALUE_CHARS = 4000


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:MAX_VALUE_CHARS]


def from_pdf(document: Any) -> dict[str, str]:
    """Properties from an open PDF, including XMP if it is there.

    Takes the opened document rather than a path so the caller controls the
    lifetime of the handle, and so this can be tested against a stub.
    """
    raw = getattr(document, "metadata", None) or {}
    found = {name: _clean(raw.get(key)) for key, name in PDF_FIELDS.items()}

    xmp = _xmp_of(document)
    if xmp:
        found["xmp"] = xmp

    return {name: value for name, value in found.items() if value}


def _xmp_of(document: Any) -> str:
    """The XMP packet as text, if the file carries one.

    Returned whole rather than parsed. Parsing it would mean running an XML
    parser over attacker-controlled bytes to gain nothing: the detectors match
    patterns, and a pattern matches the same in raw XMP as in a parsed field.
    """
    try:
        packet = document.get_xml_metadata()
    except Exception:
        return ""
    return _clean(packet)


def from_docx(document: Any) -> dict[str, str]:
    """Core and custom properties from an open DOCX."""
    core = getattr(document, "core_properties", None)
    if core is None:
        return {}

    found = {name: _clean(getattr(core, name, None)) for name in DOCX_FIELDS}
    return {name: value for name, value in found.items() if value}


def describe(metadata: dict[str, str]) -> str:
    """The properties as one block, for the reviewer's integrity panel.

    Shown, never sent. A reviewer looking at a quarantined document wants to see
    exactly what was in the file, including the part that was aimed at the
    machine.
    """
    if not metadata:
        return "This document carries no descriptive properties."
    return "\n".join(f"{name}: {value}" for name, value in sorted(metadata.items()))
