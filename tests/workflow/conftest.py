"""Shared helpers for the workflow suite."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from domain.contracts import RunRecord, RunStatus
from tests.builders import NOW


def make_run_record(**overrides: Any) -> RunRecord:
    """A run row that satisfies every contract validator.

    Tests change one field and leave the rest, so a failure names the thing
    under test rather than a field somebody forgot.
    """
    fields: dict[str, Any] = {
        "run_id": uuid4(),
        "content_key": uuid4().hex,
        "candidate_id": "cand-0007",
        "role_id": "ai-engineer",
        "rubric_version": "1.0.0",
        "rubric_hash": "a" * 64,
        "prompt_bundle_hash": "b" * 64,
        "model_tier_bindings_hash": "c" * 64,
        "routing_policy_id": "routed",
        "blind_mode": True,
        "calibration_enabled": False,
        "calibration_status": "disabled",
        "pipeline_version": "1",
        "status": RunStatus.CREATED,
        "started_at": NOW,
    }
    fields.update(overrides)
    return RunRecord(**fields)
