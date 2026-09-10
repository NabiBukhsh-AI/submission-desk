"""Turning a summary into a vector, or failing quietly.

Two implementations. A provider-backed one for a deployment that has an
embedding model configured, and a deterministic local one that needs nothing.

The local embedder is not a placeholder. Calibration is off by default and has
not been shown to help, so spending an API call per candidate on it before the
experiment has run would be paying for a feature nobody has justified. A hashed
bag-of-words is a poor semantic model and a perfectly good similarity signal over
short structured summaries — which is what these are, because they are built from
named fields rather than written by anybody.

Both fail the same way: they return None, the node records EMBEDDING_FAILED, and
the run proceeds without anchors. Calibration is an aid; a run that cannot get
one is a run without an aid, not a failed run.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
from typing import Any, Protocol

#: Vector width. Small enough that a thousand cards is a few megabytes, wide
#: enough that unrelated summaries do not collide into the same direction.
DIMENSIONS = 256

#: How a vector is stored in SQLite. Little-endian float32, which numpy reads
#: back with one call and which any other language can decode from the format
#: string alone.
PACK_FORMAT = "<%df"

#: Words that carry no signal about a career and would otherwise dominate a
#: bag-of-words vector, because every summary has them.
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "as",
        "is",
        "was",
        "were",
        "be",
        "been",
    }
)


class Embedder(Protocol):
    """Turns text into a vector, or returns None."""

    embedder_id: str

    def embed(self, text: str) -> bytes | None:
        """The vector as packed bytes, or None if it could not be produced.

        Never raises. A calibration failure must degrade an assessment rather
        than fail it, and an exception here would travel up through a node that
        exists to be optional.
        """
        ...


class LocalEmbedder:
    """A hashed bag of words, normalised to unit length.

    Deterministic across machines and versions, which is what makes the index
    reproducible: the same summary produces the same vector on a laptop in March
    and in CI in June, so a similarity score in a report can be checked.

    A hashing trick rather than a vocabulary, because a vocabulary would have to
    be built, stored, versioned, and kept in step with the cards — and every one
    of those is a way for the index to quietly stop meaning anything.
    """

    embedder_id = "local-hashed-v1"

    def __init__(self, dimensions: int = DIMENSIONS) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> bytes | None:
        tokens = tokenize(text)
        if not tokens:
            return None

        weights = [0.0] * self.dimensions
        for token in tokens:
            index, sign = _bucket(token, self.dimensions)
            weights[index] += sign

        return pack(_normalised(weights))


class ProviderEmbedder:
    """Whatever the configured provider offers, behind the same interface.

    Wrapped rather than used directly so that a provider outage produces None
    and a status, in exactly the same shape as an empty summary would.
    """

    embedder_id = "provider"

    def __init__(self, client: Any, *, call_site: str = "calibrate.embed") -> None:
        self.client = client
        self.call_site = call_site

    def embed(self, text: str) -> bytes | None:
        embed = getattr(self.client, "embed", None)
        if embed is None:
            return None

        try:
            vector = embed(text, call_site=self.call_site)
        except Exception:
            return None

        if not vector:
            return None
        return pack(_normalised([float(value) for value in vector]))


def tokenize(text: str) -> list[str]:
    """Words worth counting.

    Lowercased, stripped of stopwords, and long enough to mean something. "on"
    and "the" appear in every summary and would make every vector point the same
    way.
    """
    words = re.findall(r"[a-z0-9+#.]{2,}", text.lower())
    return [word for word in words if word not in STOPWORDS]


def _bucket(token: str, dimensions: int) -> tuple[int, float]:
    """Which dimension a token lands in, and with which sign.

    The sign comes from a different bit of the same hash. Signed hashing keeps
    collisions from always adding: two unrelated words in the same bucket cancel
    about as often as they reinforce, so a collision costs precision rather than
    inventing similarity.
    """
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "little")
    return value % dimensions, 1.0 if (value >> 63) & 1 else -1.0


def _normalised(weights: list[float]) -> list[float]:
    """Unit length, so cosine similarity is a dot product.

    A summary listing four roles must not score higher against everything than
    one listing two, and normalising is what removes length from the comparison.
    """
    magnitude = math.sqrt(sum(value * value for value in weights))
    if magnitude == 0:
        return weights
    return [value / magnitude for value in weights]


def pack(vector: list[float]) -> bytes:
    return struct.pack(PACK_FORMAT % len(vector), *vector)


def unpack(data: bytes) -> list[float]:
    count = len(data) // 4
    return list(struct.unpack(PACK_FORMAT % count, data))
