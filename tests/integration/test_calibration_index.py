"""The index, against a real database and a real embedder.

The unit tests check the rules. This checks that a card survives a round trip
through SQLite and still ranks the way it did in memory — which is where an
embedding stored as the wrong type, or a float silently widened, would show up.

It also covers the default: calibration is off, and a run with it off must be
identical to a run in a deployment that never had the feature.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from application.deps import Deps
from domain.contracts.calibration import CalibrationCard
from domain.contracts.enums import Band, CriterionState, RunStatus
from domain.contracts.run_state import RunState
from domain.ports.calibration import CalibrationQuery, CalibrationStatus, embedding_text
from infrastructure.calibration.embedder import DIMENSIONS, LocalEmbedder, unpack
from infrastructure.calibration.index import NumpyCalibrationIndex
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from pipeline.calibrate import STATUS_MESSAGES
from pipeline.calibrate import node as calibrate

EMBEDDER = LocalEmbedder()

BACKEND = "Lead Engineer, 2021 to present. Technologies: python, kubernetes, postgres"
FRONTEND = "Design Lead, 2019 to 2023. Technologies: figma, typescript, css"
DATA = "Data Engineer, 2020 to present. Technologies: spark, airflow, python"


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "index.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        calibration_enabled=True,
    )
    yield build_deps(settings)
    close_thread_connection(settings.db_path)


def store(
    deps: Deps,
    summary: str,
    *,
    role_id: str = "ai-engineer",
    band: Band = Band.ADVANCE,
    days_ago: int = 5,
    rubric_version: str = "1.0.0",
) -> CalibrationCard:
    states = {"python-depth": CriterionState.MET}
    card = CalibrationCard(
        card_id=uuid4(),
        role_id=role_id,
        rubric_version=rubric_version,
        anonymized_summary=summary,
        criterion_states=states,
        final_band=band,
        decided_at=datetime.now(UTC) - timedelta(days=days_ago),
        embedding=EMBEDDER.embed(embedding_text(summary, states)) or b"",
        source_run_id=uuid4(),
    )
    deps.calibration.add(card)
    return card


def search(deps: Deps, summary: str, **kwargs):
    index = NumpyCalibrationIndex(deps.calibration, EMBEDDER)
    return index.search(
        CalibrationQuery(
            role_id=kwargs.pop("role_id", "ai-engineer"),
            rubric_version=kwargs.pop("rubric_version", "1.0.0"),
            summary=summary,
            criterion_states={"python-depth": "met"},
            **kwargs,
        )
    )


# --- the round trip ---------------------------------------------------------------


def test_a_card_survives_storage(deps: Deps) -> None:
    stored = store(deps, BACKEND)

    back = deps.calibration.for_role("ai-engineer", "1.0.0")

    assert len(back) == 1
    assert back[0].card_id == stored.card_id
    assert back[0].anonymized_summary == BACKEND


def test_an_embedding_survives_storage(deps: Deps) -> None:
    """A BLOB read back as the wrong type, or a float silently widened, would
    make every similarity meaningless and nothing would raise."""
    stored = store(deps, BACKEND)

    back = deps.calibration.for_role("ai-engineer", "1.0.0")[0]

    assert back.embedding == stored.embedding
    assert len(unpack(back.embedding)) == DIMENSIONS


def test_the_source_run_survives_storage(deps: Deps) -> None:
    stored = store(deps, BACKEND)

    back = deps.calibration.for_role("ai-engineer", "1.0.0")[0]

    assert back.source_run_id == stored.source_run_id


# --- searching --------------------------------------------------------------------


def test_the_closest_decision_comes_first(deps: Deps) -> None:
    store(deps, FRONTEND)
    store(deps, BACKEND)

    result = search(deps, "Lead Engineer. Technologies: python, kubernetes")

    assert result.status is CalibrationStatus.APPLIED
    assert result.matches[0].card.anonymized_summary == BACKEND


def test_a_related_decision_scores_above_an_unrelated_one(deps: Deps) -> None:
    store(deps, FRONTEND)
    store(deps, DATA)

    result = search(deps, "Engineer. Technologies: python, airflow")
    ranked = {match.card.anonymized_summary: match.similarity for match in result.matches}

    assert ranked[DATA] > ranked.get(FRONTEND, -1.0)


def test_at_most_three_are_returned(deps: Deps) -> None:
    for index in range(6):
        store(deps, f"{BACKEND} team {index}")

    result = search(deps, BACKEND)

    assert len(result.matches) <= 3


def test_the_corpus_size_is_reported(deps: Deps) -> None:
    """ "No matches" and "no corpus" look the same to a reader and mean different
    things to somebody deciding whether this is worth keeping."""
    store(deps, BACKEND)
    store(deps, FRONTEND)

    result = search(deps, BACKEND)

    assert result.considered == 2


def test_an_empty_corpus_reports_itself(deps: Deps) -> None:
    result = search(deps, BACKEND)

    assert result.status is CalibrationStatus.EMPTY_CORPUS


def test_another_role_is_never_returned(deps: Deps) -> None:
    """Through the real database, because a filter that worked in memory and not
    in SQL would be the same bug in a different place."""
    store(deps, BACKEND, role_id="data-scientist")

    result = search(deps, BACKEND, role_id="ai-engineer")

    assert result.matches == ()


def test_an_old_decision_is_excluded_and_counted(deps: Deps) -> None:
    store(deps, BACKEND, days_ago=400)

    result = search(deps, BACKEND, staleness_days=180)

    assert result.excluded_stale == 1
    assert result.matches == ()


# --- the node -----------------------------------------------------------------------


def state_for(deps: Deps, **updates) -> RunState:
    base = RunState(
        run_id=uuid4(),
        candidate_id="cand-0007",
        role_id="ai-engineer",
        status=RunStatus.STRUCTURED,
        started_at=datetime.now(UTC),
        calibration_enabled=True,
    )
    return base.model_copy(update=updates)


def test_the_node_records_a_status(deps: Deps) -> None:
    result = calibrate(state_for(deps), deps)

    assert result.state.calibration_status
    assert result.events[0].name == "calibrate.searched"


def test_the_node_proceeds_with_an_empty_corpus(deps: Deps) -> None:
    """An aid that could fail an assessment would be worse than no aid."""
    result = calibrate(state_for(deps), deps)

    assert result.state.calibration_status == "empty_corpus"
    assert result.error is None


def test_the_node_proceeds_when_the_index_is_missing(deps: Deps) -> None:
    without = Deps(**{**deps.__dict__, "calibration_index": None})

    result = calibrate(state_for(without), without)

    assert result.state.calibration_status == "index_unavailable"
    assert result.error is None


def test_a_missing_index_degrades_rather_than_passes_silently(deps: Deps) -> None:
    """The reviewer should be told this candidate was assessed without the
    reference others got."""
    without = Deps(**{**deps.__dict__, "calibration_index": None})

    result = calibrate(state_for(without), without)

    assert result.status.value == "degraded"


# --- off by default -------------------------------------------------------------------


def test_calibration_is_off_by_default() -> None:
    """It plausibly helps and has not been measured. The honest place for such a
    feature is behind a flag with an experiment attached."""
    assert settings_from_env().calibration_enabled is False


def test_a_disabled_deployment_records_disabled(tmp_path: Path) -> None:
    settings = settings_from_env(
        db_path=str(tmp_path / "off.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        calibration_enabled=False,
    )
    off = build_deps(settings)
    try:
        result = calibrate(state_for(off), off)

        assert result.state.calibration_status == "disabled"
        assert result.state.calibration_block is None
    finally:
        close_thread_connection(settings.db_path)


def test_a_disabled_run_records_disabled_even_where_the_feature_is_on(
    deps: Deps,
) -> None:
    """Both the run and the deployment have to want it, so a calibrated result
    and an uncalibrated one can be told apart afterwards."""
    result = calibrate(state_for(deps, calibration_enabled=False), deps)

    assert result.state.calibration_status == "disabled"


def test_a_disabled_run_searches_nothing(deps: Deps) -> None:
    store(deps, BACKEND)

    result = calibrate(state_for(deps, calibration_enabled=False), deps)

    assert result.state.calibration_block is None


# --- what a reviewer reads ----------------------------------------------------------------


def test_every_status_has_a_sentence() -> None:
    """A status code shown to a person is a defect."""
    for status in CalibrationStatus:
        assert status in STATUS_MESSAGES
        assert STATUS_MESSAGES[status].endswith(".")
        assert STATUS_MESSAGES[status][0].isupper()


def test_no_status_message_reads_as_a_code() -> None:
    for message in STATUS_MESSAGES.values():
        assert "_" not in message
        for jargon in ("null", "none", "corpus", "index", "embedding"):
            assert jargon not in message.lower() or "record of past decisions" in message
