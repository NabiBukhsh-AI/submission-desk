"""Identity and integrity of one uploaded file.

Created at INTAKE and immutable thereafter. The filename is metadata and never
a path: blobs are stored under a content hash, so a file called
``../../etc/passwd`` is a string in a database column and nothing more.
"""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from domain.contracts.base import Contract, require_utc
from domain.contracts.enums import DocumentRole

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class CandidateDocument(Contract):
    document_id: UUID
    candidate_id: str = Field(min_length=1, max_length=128)
    original_filename: str = Field(min_length=1, max_length=512)
    document_sha256: str
    mime_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1)
    page_count: int | None = Field(default=None, ge=1)
    blob_path: str = Field(min_length=1)
    doc_role: DocumentRole
    received_at: datetime

    _utc = field_validator("received_at")(require_utc)

    @field_validator("document_sha256")
    @classmethod
    def _hash_shape(cls, value: str) -> str:
        if not SHA256_PATTERN.match(value):
            raise ValueError("document_sha256 must be 64 lowercase hex characters")
        return value

    @model_validator(mode="after")
    def _blob_path_is_content_addressed(self) -> CandidateDocument:
        """The blob location is derived from the hash, never from the filename.

        Checked as a suffix rather than a whole path, because the store root is
        configurable and the invariant is the addressing scheme, not the root.
        """
        expected_suffix = f"{self.document_sha256[:2]}/{self.document_sha256}"
        if not self.blob_path.replace("\\", "/").endswith(expected_suffix):
            raise ValueError(f"blob_path must end with {expected_suffix}, derived from the hash")
        return self
