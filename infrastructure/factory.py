"""The composition root.

The only module permitted to construct a concrete adapter. Everything else
receives protocol-typed handles on ``Deps``, which is what lets the entire
pipeline run against fakes with no network, no API key, and no account.

Keeping this in one file is what makes that claim checkable: to know what a run
will actually talk to, read this function, not the whole tree.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from application.budget import BudgetGuard
from application.deps import Deps, Settings, SystemClock
from domain.contracts.enums import ModelTier
from infrastructure.calibration.embedder import LocalEmbedder
from infrastructure.calibration.index import NumpyCalibrationIndex
from infrastructure.extraction.dispatcher import Extractor
from infrastructure.extraction.ocr import TesseractEngine
from infrastructure.integrations.csv_sink import CsvSink
from infrastructure.integrations.local import LocalFolderSource
from infrastructure.integrations.slack import ConsoleNotifier
from infrastructure.models.cache import ResponseCache
from infrastructure.models.fake import FakeModelClient
from infrastructure.models.pricing import Pricing, TierPrice
from infrastructure.models.pricing import load as load_pricing
from infrastructure.models.provider import ProviderModelClient, bindings_hash, load_bindings
from infrastructure.models.repairing import RepairingClient
from infrastructure.models.routing.policies import policy_for
from infrastructure.models.stand_in import for_rubrics
from infrastructure.models.transports import anthropic as anthropic_transport
from infrastructure.observability.metrics_sql import SqliteMetricsReader
from infrastructure.observability.redactor import Redactor
from infrastructure.prompts.registry import PromptRegistry
from infrastructure.rubrics import RubricLoader
from infrastructure.secrets import LocalSecrets, app_secret
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
    SqliteSettingsRepository,
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
        inbox_dir=os.environ.get("INBOX_DIR") or "",
        pilot_data_dir=os.environ.get("PILOT_DATA_DIR") or "",
        source_adapter=(os.environ.get("SOURCE_ADAPTER") or "local").strip().lower(),
        routing_policy_id=os.environ.get("MODEL_ROUTING_POLICY") or "routed",
        model_provider=(os.environ.get("MODEL_PROVIDER") or "fake").strip().lower(),
        model_api_key=os.environ.get("MODEL_API_KEY", "").strip(),
        model_api_base_url=os.environ.get("MODEL_API_BASE_URL", "").strip(),
        model_cheap_id=os.environ.get("MODEL_CHEAP_ID", "").strip(),
        model_strong_id=os.environ.get("MODEL_STRONG_ID", "").strip(),
        cors_origins=tuple(
            origin.strip()
            for origin in (os.environ.get("CORS_ORIGINS") or "http://localhost:5173").split(",")
            if origin.strip()
        ),
        blind_mode=flag("BLIND_MODE", True),
        calibration_enabled=flag("CALIBRATION_ENABLED", False),
        calibration_top_k=number("CALIBRATION_TOP_K", 3),
        calibration_staleness_days=number("CALIBRATION_STALENESS_DAYS", 180),
        demo_mode=flag("DEMO_MODE", False),
        token_ceiling_per_run=number("TOKEN_CEILING_PER_RUN", 120_000),
        max_escalations_per_candidate=number("MAX_ESCALATIONS_PER_CANDIDATE", 3),
        stale_run_minutes=number("STALE_RUN_MINUTES", 15),
        retention_days=number("RETENTION_DAYS", 30),
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


#: Settings the admin page may write, by the name stored, and how each is read
#: back onto ``Settings``. Everything not listed stays environment-only.
STORED_STRINGS = {
    "model_provider": "model_provider",
    "model_cheap_id": "model_cheap_id",
    "model_strong_id": "model_strong_id",
    "model_api_base_url": "model_api_base_url",
    "price_cheap_input": "price_cheap_input",
    "price_cheap_output": "price_cheap_output",
    "price_strong_input": "price_strong_input",
    "price_strong_output": "price_strong_output",
    "reviewer_id": "reviewer_id",
}
STORED_FLAGS = {"blind_mode": "blind_mode"}
STORED_NUMBERS = {"retention_days": "retention_days"}
#: One sealed key per provider, so switching providers does not lose the other.
STORED_KEYS = {"anthropic": "anthropic_api_key"}


def stored_settings(settings: Settings, store: object, secrets: object) -> Settings:
    """The settings with what the admin page saved laid over the environment.

    The store wins where it has a value; an empty table changes nothing. The
    active provider's key is opened here, in the composition root, so the
    plaintext exists in one process and reaches nothing that logs.
    """
    rows = store.all()  # type: ignore[attr-defined]
    values = {name: value for name, (value, _secret) in rows.items()}
    changes: dict[str, object] = {}

    for name, field in STORED_STRINGS.items():
        if name in values:
            changes[field] = values[name].strip()
    for name, field in STORED_FLAGS.items():
        if name in values:
            changes[field] = values[name].strip().lower() in ("1", "true", "yes", "on")
    for name, field in STORED_NUMBERS.items():
        if name in values and values[name].strip().isdigit():
            changes[field] = int(values[name])

    provider = str(changes.get("model_provider", settings.model_provider)).strip().lower()
    sealed = values.get(STORED_KEYS.get(provider, ""))
    if sealed:
        changes["model_api_key"] = secrets.open(sealed)  # type: ignore[attr-defined]

    return replace(settings, **changes) if changes else settings  # type: ignore[arg-type]


def pricing_for(settings: Settings, fallback: Pricing) -> Pricing:
    """Prices from the admin page when it has set any, else the file.

    Both directions of a tier or neither: half a price is the estimate the
    pricing module exists to refuse. A rate of zero is honoured — the free
    tiers exist — because it was typed, not assumed.
    """

    def decimal(text: str) -> Decimal | None:
        try:
            return Decimal(text.strip()) if text.strip() else None
        except InvalidOperation:
            return None

    tiers = {
        ModelTier.CHEAP.value: TierPrice(
            input_per_million=decimal(settings.price_cheap_input),
            output_per_million=decimal(settings.price_cheap_output),
        ),
        ModelTier.STRONG.value: TierPrice(
            input_per_million=decimal(settings.price_strong_input),
            output_per_million=decimal(settings.price_strong_output),
        ),
    }
    if not any(price.configured for price in tiers.values()):
        return fallback

    fingerprint = "|".join(
        f"{name}:{price.input_per_million}/{price.output_per_million}"
        for name, price in sorted(tiers.items())
    )
    return Pricing(
        tiers=tiers,
        source=f"admin@{hashlib.sha256(fingerprint.encode()).hexdigest()[:16]}",
        version="admin",
    )


#: The providers a deployment can name, and the transport each one uses.
PROVIDERS = {"anthropic": anthropic_transport.make_transport}


def build_models(
    settings: Settings,
    rubric_loader: RubricLoader | None = None,
    *,
    cache_store: object | None = None,
    prompts: PromptRegistry | None = None,
    transport: object | None = None,
) -> object:
    """The model client stack, chosen by configuration and nothing else.

    Innermost, the client. The default is the fake, which replays recorded
    responses; a call with no recorded fixture goes to the deterministic
    stand-in, which quotes the document or says it cannot and marks its usage
    as unmeasured, so nothing downstream presents a size estimate as a token
    count. That is what lets a fresh clone reach a reviewable candidate with no
    key. It is not a model: every result it produces is a claim about the
    pipeline, and the evaluation report says so at the top.

    With the ``anthropic`` provider the client is
    the provider adapter with that provider's transport, the tier bindings
    from the settings (the admin page, the environment, or config/models.yaml,
    in that order) and the key from the settings. A provider this file does
    not know refuses at the first call with a sentence saying so, rather than
    guessing at a wire format.

    Around the client, in order: one schema repair at the same tier, and, for
    the live provider only, the response cache. The cache is not applied to
    the offline client on purpose — a deterministic stand-in gains nothing
    from it, and the fairness control arm measures the system's consistency
    with itself, which a cache would answer for it.
    """
    repair_template = ""
    if prompts is not None:
        repair_template = prompts.get("repair/schema_repair").body

    if settings.model_provider in ("", "fake"):
        loader = rubric_loader or RubricLoader(_repo_root() / "rubrics")
        stand_in = for_rubrics(loader(role) for role in loader.available())
        offline = FakeModelClient(
            fixture_dir=_repo_root() / "tests" / "fixtures" / "llm",
            on_missing=stand_in.structured_generate,
        )
        return RepairingClient(inner=offline, repair_template=repair_template)

    # Bindings from the settings first, then the committed file, so the admin
    # page wins over config/models.yaml and neither has to be edited to use
    # the other.
    bindings = load_bindings(_read_yaml(_repo_root() / "config" / "models.yaml"))
    chosen = {ModelTier.CHEAP: settings.model_cheap_id, ModelTier.STRONG: settings.model_strong_id}
    for tier, model_id in chosen.items():
        if model_id:
            bindings[tier] = replace(bindings[tier], model=model_id)

    if transport is None and settings.model_provider in PROVIDERS and settings.model_api_key:
        transport = PROVIDERS[settings.model_provider](
            settings.model_api_key, settings.model_api_base_url
        )

    live = ProviderModelClient(
        bindings=bindings,
        api_key=settings.model_api_key,
        base_url=settings.model_api_base_url,
        transport=transport,
    )
    fingerprint = bindings_hash(bindings)
    repaired = RepairingClient(inner=live, repair_template=repair_template)
    if cache_store is None:
        return repaired
    return ResponseCache(inner=repaired, store=cache_store, tier_binding_hash=fingerprint)


def _read_yaml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


class DemoModeViolation(RuntimeError):
    """Demo mode was asked to read documents from somewhere real.

    Raised at startup, before any adapter exists. A demo that could be pointed
    at a pilot folder by one environment variable would be safe by discipline,
    and the whole point of the mode is that it is safe by construction.
    """


#: The only documents demo mode will read. Generated by
#: ``scripts/make_synthetic_corpus.py``; every name in it is invented.
SAMPLES_DIR = _repo_root() / "data" / "samples"
DEMO_CORPUS = SAMPLES_DIR / "synthetic"


def inbox_for(settings: Settings) -> Path:
    """Where the local source reads from, demo mode aside."""
    if settings.inbox_dir:
        return Path(settings.inbox_dir)
    return Path(settings.blob_dir).parent / "inbox"


def enforce_demo_mode(settings: Settings) -> None:
    """Refuse to start if demo mode is on and any real document path is set.

    Checked against the configuration rather than the adapters, so it runs
    before anything that could read a file exists. Every path that could name
    real documents is listed here; a new one has to be added to this function,
    which is the point of the function.
    """
    if not settings.demo_mode:
        return

    if settings.pilot_data_dir:
        raise DemoModeViolation(
            "DEMO_MODE is on and PILOT_DATA_DIR is set. Demo mode reads only the "
            "synthetic corpus under data/samples/ and will not start while a path "
            "to real documents is configured. Unset one of them."
        )

    if settings.source_adapter != "local":
        raise DemoModeViolation(
            f"DEMO_MODE is on and SOURCE_ADAPTER is {settings.source_adapter!r}. "
            "Demo mode reads only the local synthetic corpus. Set SOURCE_ADAPTER=local "
            "or unset DEMO_MODE."
        )

    if settings.inbox_dir and not _is_within(Path(settings.inbox_dir), SAMPLES_DIR):
        raise DemoModeViolation(
            f"DEMO_MODE is on and INBOX_DIR points at {settings.inbox_dir}, which is "
            "outside data/samples/. Demo mode will not read documents from there. "
            "Unset INBOX_DIR or DEMO_MODE."
        )


def _is_within(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def build_source(settings: Settings) -> object:
    """Where candidate documents come from.

    A local folder by default. Drive is available and needs a transport and a
    folder id; absent those, the offline path is the one that works.

    In demo mode the folder is the synthetic corpus and nothing else, whatever
    the inbox setting says. ``enforce_demo_mode`` has already refused any
    setting that disagrees, so this is the second of two locks.
    """
    if settings.demo_mode:
        return LocalFolderSource(DEMO_CORPUS)
    return LocalFolderSource(inbox_for(settings))


def build_sinks(settings: Settings) -> tuple[object, ...]:
    """Every configured destination, with the CSV file first and always.

    First because it is the guarantee: it has no credentials, no network and no
    rate limit, so a run that reaches delivery always produces a file somebody
    can open. Every other sink is optional precisely because this one is not.

    Google Sheets and Slack are absent unless a transport has been supplied,
    which for this deployment means never: the repository ships offline, and an
    adapter that constructed a real client at startup would make that untrue.

    In demo mode there are no sinks at all. Not disabled, not stubbed: absent,
    so that nothing in the process holds a handle that could send.
    """
    if settings.demo_mode:
        return ()
    return (CsvSink(Path(settings.blob_dir).parent / "deliveries" / "results.csv"),)


def build_notifier(settings: Settings) -> object | None:
    """The console by default; nothing in demo mode, for the reason above."""
    if settings.demo_mode:
        return None
    return ConsoleNotifier()


def build_deps(settings: Settings | None = None, *, migrate_db: bool = True) -> Deps:
    """Construct everything a run needs.

    The repositories are the only concrete adapters at this phase. The model
    client, extractor, sinks, and notifier are wired in as their phases land;
    until then they are None on Deps, and a node that needs one is not yet
    written.
    """
    settings = settings or settings_from_env()

    # A refusal here is the demo mode guarantee; everything after it is
    # construction. Before the database is touched, so a refused start leaves
    # nothing behind. The admin page cannot set a path, so the stored
    # settings read below cannot change this answer.
    enforce_demo_mode(settings)

    db_path = Path(settings.db_path)
    if migrate_db:
        migrate(db_path)

    # What the admin page saved, laid over the environment, before the model
    # client is built so the stored key reaches it.
    settings_store = SqliteSettingsRepository(db_path)
    secrets = LocalSecrets(app_secret(db_path))
    settings = stored_settings(settings, settings_store, secrets)

    # One reader, shared. The scanner rasterises pages through the same engine
    # the extractor uses, so a machine with no Tesseract degrades both in the
    # same way rather than in two different ones.
    extractor = Extractor(ocr=TesseractEngine())

    # One repository, shared by the node that reads anchors and the node that
    # writes them, so a card written at delivery is visible to the next run.
    calibration = SqliteCalibrationRepository(db_path)
    embedder = LocalEmbedder()

    # One rubric loader, shared by the pipeline and by the stand-in model that
    # answers when no fixture is recorded, so both read the same criteria. The
    # admin page's saved rubrics come from the same store as its settings.
    rubric_loader = RubricLoader(_repo_root() / "rubrics", store=settings_store)
    prompts = PromptRegistry.load(_repo_root() / "prompts")
    llm_cache = SqliteLlmCacheRepository(db_path)

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
        llm_cache=llm_cache,
        events=SqliteEventRepository(db_path),
        blobs=BlobStore(settings.blob_dir),
        extractor=extractor,
        scanner=Scanner(
            ocr=extractor.ocr,
            render_diff_enabled=settings.sanitize_render_diff,
            render_diff_max_pages=settings.render_diff_max_pages,
        ),
        prompts=prompts,
        router=policy_for(settings.routing_policy_id),
        rubric_loader=rubric_loader,
        pricing=pricing_for(settings, load_pricing(_repo_root() / "config" / "pricing.yaml")),
        metrics=SqliteMetricsReader(db_path),
        sinks=build_sinks(settings),
        notifier=build_notifier(settings),
        models=build_models(settings, rubric_loader, cache_store=llm_cache, prompts=prompts),
        source=build_source(settings),
        embedder=embedder,
        redactor=redact,
        calibration_index=NumpyCalibrationIndex(calibration, embedder),
        budget=BudgetGuard(
            token_ceiling=settings.token_ceiling_per_run,
            max_escalations=settings.max_escalations_per_candidate,
        ),
        settings_store=settings_store,
        secrets=secrets,
    )
