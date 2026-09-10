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
from infrastructure.calibration.embedder import LocalEmbedder
from infrastructure.calibration.index import NumpyCalibrationIndex
from infrastructure.extraction.dispatcher import Extractor
from infrastructure.extraction.ocr import TesseractEngine
from infrastructure.integrations.csv_sink import CsvSink
from infrastructure.integrations.local import LocalFolderSource
from infrastructure.integrations.slack import ConsoleNotifier
from infrastructure.models.fake import FakeModelClient
from infrastructure.models.pricing import load as load_pricing
from infrastructure.models.routing.policies import policy_for
from infrastructure.observability.metrics_sql import SqliteMetricsReader
from infrastructure.observability.redactor import Redactor
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
        calibration_top_k=number("CALIBRATION_TOP_K", 3),
        calibration_staleness_days=number("CALIBRATION_STALENESS_DAYS", 180),
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


def build_models(settings: Settings) -> object | None:
    """The model client, chosen by configuration and nothing else.

    The default is the fake, which replays recorded responses and refuses when
    it has none. That refusal is the point: a fake that invented a plausible
    answer would let a demo pass against a fixture nobody recorded, and the
    whole repository would look like it worked.

    A real provider is constructed only when one is named. Building a client
    that reads an API key at startup would make the offline claim untrue on the
    first import, before anything had been asked of it.
    """
    if settings.model_provider in ("", "fake"):
        return FakeModelClient(
            fixture_dir=_repo_root() / "tests" / "fixtures" / "llm",
        )

    # No provider adapter ships with this repository. Model naming and pricing
    # are deployment configuration, and an adapter here would name a vendor in
    # code that is meant to know only about tiers.
    return None


def build_source(settings: Settings) -> object:
    """Where candidate documents come from.

    A local folder by default. Drive is available and needs a transport and a
    folder id; absent those, the offline path is the one that works.
    """
    return LocalFolderSource(Path(settings.blob_dir).parent / "inbox")


def build_sinks(settings: Settings) -> tuple[object, ...]:
    """Every configured destination, with the CSV file first and always.

    First because it is the guarantee: it has no credentials, no network and no
    rate limit, so a run that reaches delivery always produces a file somebody
    can open. Every other sink is optional precisely because this one is not.

    Google Sheets and Slack are absent unless a transport has been supplied,
    which for this deployment means never: the repository ships offline, and an
    adapter that constructed a real client at startup would make that untrue.
    """
    return (CsvSink(Path(settings.blob_dir).parent / "deliveries" / "results.csv"),)


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

    # One repository, shared by the node that reads anchors and the node that
    # writes them, so a card written at delivery is visible to the next run.
    calibration = SqliteCalibrationRepository(db_path)
    embedder = LocalEmbedder()

    # One redactor, shared by the logs and by anything that stores a person's
    # words about another person. Two would be two policies.
    processor = Redactor()

    def redact(text: str) -> str:
        result: str = processor(None, "info", {"text": text})["text"]
        return result

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
        calibration=calibration,
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
        sinks=build_sinks(settings),
        notifier=ConsoleNotifier(),
        models=build_models(settings),
        source=build_source(settings),
        embedder=embedder,
        redactor=redact,
        calibration_index=NumpyCalibrationIndex(calibration, embedder),
        budget=BudgetGuard(
            token_ceiling=settings.token_ceiling_per_run,
            max_escalations=settings.max_escalations_per_candidate,
        ),
    )
