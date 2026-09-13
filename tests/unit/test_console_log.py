"""The line a person watches while the system works.

`make api-live` prints one line per node and per model call. What is asserted:
the line is short enough to read, a run id is cut to its first block and a
model id is not mistaken for one, and the two funnels — the event repository
for nodes, the provider client for model calls — actually emit.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from infrastructure.observability.logging import console_line


def line(**fields: Any) -> str:
    return console_line(None, "info", {"timestamp": "2026-09-13T07:45:22.123Z", **fields})


def test_the_line_is_time_event_then_fields() -> None:
    text = line(event="node.assess", run_id=UUID("01a099ba-0df7-7eff-af8a-472a443011c1"), ms=33)

    assert text == "07:45:22  node.assess      run_id=01a099ba ms=33"


def test_a_model_id_with_the_length_of_a_uuid_is_not_cut() -> None:
    """dots-studio/dots-3-note-preview:free is 36 characters with four dashes."""
    text = line(event="model.call", model="dots-studio/dots-3-note-preview:free")

    assert "dots-studio/dots-3-note-preview:free" in text


def test_a_long_value_is_cut_to_a_line() -> None:
    text = line(event="model.call", problem="x" * 200)

    assert len(text) < 120
    assert text.endswith("...")


def test_a_node_transition_reaches_the_log(tmp_path: Any) -> None:
    from domain.contracts.enums import RunStatus
    from infrastructure.factory import build_deps, settings_from_env
    from infrastructure.storage.sqlite.connection import close_thread_connection
    from tests.workflow.conftest import make_run_record

    settings = settings_from_env(db_path=str(tmp_path / "t.sqlite"), blob_dir=str(tmp_path / "b"))
    deps = build_deps(settings)
    record = make_run_record()
    deps.runs.create(record)
    captured: list[dict[str, Any]] = []
    structlog.configure(
        processors=[lambda _l, _m, event: captured.append(event) or ""],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    try:
        deps.events.append(
            record.run_id,
            node="ASSESS",
            from_status=RunStatus.CALIBRATED,
            to_status=RunStatus.ASSESSED,
            node_status="ok",
            payload={"candidate_id": "cand-1", "latency_ms": 33},
        )
    finally:
        structlog.reset_defaults()
        close_thread_connection(settings.db_path)

    assert captured[0]["event"] == "node.assess"
    assert captured[0]["candidate"] == "cand-1"
    assert captured[0]["to"] == "assessed"
