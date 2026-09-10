"""The composition root.

The only module permitted to construct a concrete adapter. Everything else
receives protocol-typed handles on ``Deps``, which is what lets the entire
pipeline run against fakes with no network, no API key, and no account.

Keeping this in one file is what makes that claim checkable: to know what a run
will actually talk to, read this function, not the whole tree.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from application.budget import BudgetGuard
from application.deps import Deps, Settings, SystemClock
from infrastructure.extraction.dispatcher import Extractor
from infrastructure.extraction.ocr import TesseractEngine
from infrastructure.models.pricing import load as load_pricing
from infrastructure.models.routing.policies import policy_for
from infrastructure.observability.metrics_sql import SqliteMetricsReader
from infrastructure.prompts.registry import PromptRegistry
from infrastructure.rubrics import RubricLoader
from infrastructure.security.scan import Scanner
from infrastructure.storage.blobs import BlobStore
from infrastructure.storage.sqlite.repositories import (
    SqliteCalibrationRepository,
    SqliteCandidateRepository,
    SqliteCostRepository,
    SqliteDeliveryRepository,
    SqliteErrorRepository,
    SqliteEventRepository,
    SqliteEvidenceRepository,
    SqliteLlmCacheRepository,
    SqliteReviewRepository,
    SqliteRunRepository,
)
from infrastructure.storage.sqlite.schema import migrate


def _repo_root() -> Path:
    """Where the rubrics and prompts live.

    Resolved from this file rather than the working directory, so the pipeline
    behaves the same whether it is started from the repository root, from a
    test, or from a service manager.
    """
    return Path(__file__).resolve().parents[1]


def settings_from_env(**overrides: object) -> Settings:
    """Read configuration once, at startup.

    Defaults are the offline ones: the fake model provider, blind mode on,
    calibration off. A fresh clone with no .env runs the demo, which is the
    whole point of the defaults being where they are.
    """

    def flag(name: str, default: bool) -> bool:
        raw = os.environ.get(name, "").strip().lower()
        return default if raw == "" else raw in ("1", "true", "yes", "on")

    def number(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        return int(raw) if raw.isdigit() else default

    def _fraction(name: str, default: float) -> float:
        raw = os.environ.get(name, "").strip()
        try:
            return float(raw) if raw else default
        except ValueError:
            return default

    resolved = Settings(
        db_path=os.environ.get("DATABASE_PATH") or "data/db/submission_desk.sqlite",
        blob_dir=os.environ.get("BLOB_DIR") or "data/blobs",
        routing_policy_id=os.environ.get("MODEL_ROUTING_POLICY") or "routed",
        model_provider=os.environ.get("MODEL_PROVIDER") or "fake",
        blind_mode=flag("BLIND_MODE", True),
        calibration_enabled=flag("CALIBRATION_ENABLED", False),
        demo_mode=flag("DEMO_MODE", False),
        token_ceiling_per_run=number("TOKEN_CEILING_PER_RUN", 120_000),
        max_escalations_per_candidate=number("MAX_ESCALATIONS_PER_CANDIDATE", 3),
        stale_run_minutes=number("STALE_RUN_MINUTES", 15),
        extraction_confidence_warn=_fraction("EXTRACTION_CONFIDENCE_WARN", 0.6),
        assess_concurrency=number("ASSESS_CONCURRENCY", 4),
        assess_chunk_k=number("ASSESS_CHUNK_K", 6),
        assess_max_input_tokens=number("ASSESS_MAX_INPUT_TOKENS", 12_000),
        structure_max_input_tokens=number("STRUCTURE_MAX_INPUT_TOKENS", 24_000),
        reviewer_id=os.environ.get("REVIEWER_ID") or "",
        log_spans=flag("LOG_SPANS", False),
        sanitize_render_diff=flag("SANITIZE_RENDER_DIFF", True),
        render_diff_max_pages=number("RENDER_DIFF_MAX_PAGES", 10),
        suspect_confidence=_fraction("SUSPECT_CONFIDENCE", 0.75),
        sanitize_classify=flag("SANITIZE_CLASSIFY", False),
    )

    if overrides:
        resolved = replace(resolved, **overrides)  # type: ignore[arg-type]
    return resolved


def build_deps(settings: Settings | None = None, *, migrate_db: bool = True) -> Deps:
    """Construct everything a run needs.

    The repositories are the only concrete adapters at this phase. The model
    client, extractor, sinks, and notifier are wired in as their phases land;
    until then they are None on Deps, and a node that needs one is not yet
    written.
    """
    settings = settings or settings_from_env()

    db_path = Path(settings.db_path)
    if migrate_db:
        migrate(db_path)

    # One reader, shared. The scanner rasterises pages through the same engine
    # the extractor uses, so a machine with no Tesseract degrades both in the
    # same way rather than in two different ones.
    extractor = Extractor(ocr=TesseractEngine())

    return Deps(
        settings=settings,
        clock=SystemClock(),
        runs=SqliteRunRepository(db_path),
        candidates=SqliteCandidateRepository(db_path),
        evidence=SqliteEvidenceRepository(db_path),
        reviews=SqliteReviewRepository(db_path),
        costs=SqliteCostRepository(db_path),
        errors=SqliteErrorRepository(db_path),
        deliveries=SqliteDeliveryRepository(db_path),
        calibration=SqliteCalibrationRepository(db_path),
        llm_cache=SqliteLlmCacheRepository(db_path),
        events=SqliteEventRepository(db_path),
        blobs=BlobStore(settings.blob_dir),
        extractor=extractor,
        scanner=Scanner(
            ocr=extractor.ocr,
            render_diff_enabled=settings.sanitize_render_diff,
            render_diff_max_pages=settings.render_diff_max_pages,
        ),
        prompts=PromptRegistry.load(_repo_root() / "prompts"),
        router=policy_for(settings.routing_policy_id),
        rubric_loader=RubricLoader(_repo_root() / "rubrics"),
        pricing=load_pricing(_repo_root() / "config" / "pricing.yaml"),
        metrics=SqliteMetricsReader(db_path),
        budget=BudgetGuard(
            token_ceiling=settings.token_ceiling_per_run,
            max_escalations=settings.max_escalations_per_candidate,
        ),
    )
