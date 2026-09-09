"""Two identities, deliberately.

``run_id`` names an execution. ``content_key`` names a result. Keeping them
apart is what makes re-running safe: the same documents under the same
configuration produce the same content key, so the second run is a lookup rather
than a bill.

Every input that can change the outcome is in the key, and nothing else is. A
different run id, a different day, a different machine: none of those change the
answer, so none of them change the key.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Iterable
from uuid import UUID

#: Separator between key components. A byte that cannot occur in a hex digest,
#: a slug, or a semver string, so "ab" + "c" and "a" + "bc" cannot collide.
_SEPARATOR = "\x1f"


#: Bytes of randomness a version 7 uuid carries after its timestamp.
UUID7_ENTROPY_BYTES = 10

#: Characters in a sha256 hex digest.
SHA256_HEX_LENGTH = 64


def content_key(
    document_hashes: Iterable[str],
    *,
    rubric_hash: str,
    prompt_bundle_hash: str,
    model_tier_bindings_hash: str,
    routing_policy_id: str,
    blind_mode: bool,
    calibration_enabled: bool,
    pipeline_version: str,
) -> str:
    """The semantic identity of a run.

    Document hashes are sorted, so uploading the same two files in the other
    order is the same run rather than a second one.

    Every argument is keyword-only past the first. These are eight similar
    strings and booleans, and a positional call site that transposed two of them
    would produce a plausible key for the wrong configuration.
    """
    parts = [
        *sorted(document_hashes),
        rubric_hash,
        prompt_bundle_hash,
        model_tier_bindings_hash,
        routing_policy_id,
        str(blind_mode),
        str(calibration_enabled),
        pipeline_version,
    ]
    joined = _SEPARATOR.join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def new_run_id(*, timestamp_ms: int | None = None, entropy: bytes | None = None) -> UUID:
    """A time-sortable unique id, in the shape of UUID version 7.

    Sortable matters more than it looks: the queue, the operations page, and
    every "what happened around then" query read in insertion order without a
    secondary sort, and a run id that sorts by time makes a database browser
    usable during an incident.

    The clock and the randomness are injectable so tests can produce a fixed id.
    This is the one place in the domain layer that reads a clock by default, and
    it is doing so to mint an identifier rather than to make a decision.
    """
    milliseconds = timestamp_ms if timestamp_ms is not None else time.time_ns() // 1_000_000
    random_bytes = entropy if entropy is not None else os.urandom(UUID7_ENTROPY_BYTES)
    if len(random_bytes) < UUID7_ENTROPY_BYTES:
        raise ValueError("a version 7 uuid needs 10 bytes of entropy")

    value = bytearray(16)
    value[0:6] = milliseconds.to_bytes(6, "big")
    value[6:16] = random_bytes[:UUID7_ENTROPY_BYTES]

    # Version 7 in the high nibble of byte 6, variant 10 in the top bits of byte 8.
    value[6] = (value[6] & 0x0F) | 0x70
    value[8] = (value[8] & 0x3F) | 0x80

    return UUID(bytes=bytes(value))


def blob_path(document_sha256: str, *, root: str = "data/blobs") -> str:
    """Where one document's bytes live.

    Derived from the content hash and never from the filename, so an upload
    called ``../../etc/passwd`` is stored under its hash like everything else.
    The two-character prefix keeps directories small enough to list.
    """
    if len(document_sha256) != SHA256_HEX_LENGTH:
        raise ValueError("a document hash is 64 hex characters")
    return f"{root}/{document_sha256[:2]}/{document_sha256}"
