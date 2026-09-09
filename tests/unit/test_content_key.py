"""Two identities, and what each one is allowed to depend on.

The content key names a result. Every input that can change the outcome must
change it, and nothing else may, or the run cache either misses when it should
hit (wasted money) or hits when it should miss (a stale answer presented as
fresh).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from domain.identity import blob_path, content_key, new_run_id

BASE: dict[str, Any] = {
    "rubric_hash": "a" * 64,
    "prompt_bundle_hash": "b" * 64,
    "model_tier_bindings_hash": "c" * 64,
    "routing_policy_id": "routed",
    "blind_mode": True,
    "calibration_enabled": False,
    "pipeline_version": "1",
}
DOCS = ["1" * 64, "2" * 64]


def key(documents: list[str] | None = None, **overrides: Any) -> str:
    return content_key(documents if documents is not None else DOCS, **{**BASE, **overrides})


def test_the_same_inputs_give_the_same_key() -> None:
    assert key() == key()


def test_the_key_is_a_full_sha256() -> None:
    assert len(key()) == 64


def test_document_order_does_not_matter() -> None:
    """Uploading the same two files in the other order is the same run."""
    assert key(documents=list(reversed(DOCS))) == key(documents=DOCS)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rubric_hash", "d" * 64),
        ("prompt_bundle_hash", "d" * 64),
        ("model_tier_bindings_hash", "d" * 64),
        ("routing_policy_id", "all_cheap"),
        ("blind_mode", False),
        ("calibration_enabled", True),
        ("pipeline_version", "2"),
    ],
)
def test_every_input_changes_the_key(field: str, value: Any) -> None:
    """Anything that can change the answer must change the key.

    A prompt edit that did not change the key would serve a cached result
    produced by the old prompt, and the evaluation would silently compare a
    change against itself.
    """
    assert key(**{field: value}) != key()


def test_adding_a_document_changes_the_key() -> None:
    assert key(documents=[*DOCS, "3" * 64]) != key()


def test_removing_a_document_changes_the_key() -> None:
    assert key(documents=DOCS[:1]) != key()


def test_a_different_document_changes_the_key() -> None:
    assert key(documents=["9" * 64, DOCS[1]]) != key()


def test_component_boundaries_cannot_be_shifted() -> None:
    """Concatenating without a separator would let two different configurations
    collide: "ab" + "c" and "a" + "bc" are the same string."""
    first = content_key([], **{**BASE, "routing_policy_id": "ab", "pipeline_version": "c"})
    second = content_key([], **{**BASE, "routing_policy_id": "a", "pipeline_version": "bc"})
    assert first != second


def test_the_key_ignores_the_run_id_and_the_clock() -> None:
    """Re-running the same work tomorrow is a cache hit, not a second bill.

    The key takes no run id and no timestamp at all, so this holds by
    construction rather than by discipline.
    """
    import inspect

    parameters = set(inspect.signature(content_key).parameters)
    assert "run_id" not in parameters
    assert not any("time" in name or "date" in name for name in parameters)


# --- run ids -----------------------------------------------------------------


def test_a_run_id_is_a_uuid_version_seven() -> None:
    run_id = new_run_id()
    assert isinstance(run_id, UUID)
    assert run_id.version == 7
    assert run_id.variant == "specified in RFC 4122"


def test_run_ids_are_unique() -> None:
    assert len({new_run_id() for _ in range(500)}) == 500


def test_run_ids_sort_by_time() -> None:
    """Sortable matters during an incident: the queue and the operations page
    read in insertion order without a secondary sort."""
    earlier = new_run_id(timestamp_ms=1_700_000_000_000, entropy=b"\xff" * 10)
    later = new_run_id(timestamp_ms=1_700_000_001_000, entropy=b"\x00" * 10)

    assert str(earlier) < str(later)


def test_a_run_id_is_reproducible_when_pinned() -> None:
    first = new_run_id(timestamp_ms=1_700_000_000_000, entropy=b"\x01" * 10)
    second = new_run_id(timestamp_ms=1_700_000_000_000, entropy=b"\x01" * 10)
    assert first == second


def test_a_run_id_needs_enough_entropy() -> None:
    with pytest.raises(ValueError, match="10 bytes of entropy"):
        new_run_id(entropy=b"\x01")


# --- blob paths --------------------------------------------------------------


def test_a_blob_path_is_derived_from_the_hash() -> None:
    digest = "ab" + "c" * 62
    assert blob_path(digest) == f"data/blobs/ab/{digest}"


def test_a_blob_path_rejects_anything_that_is_not_a_hash() -> None:
    """There is no input that produces a path outside the store, because the
    only accepted input is a hash."""
    for attempt in ("../../etc/passwd", "", "short", "x" * 65):
        with pytest.raises(ValueError, match="64 hex characters"):
            blob_path(attempt)
