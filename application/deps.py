"""Everything a node is allowed to touch.

One frozen dataclass of protocol-typed handles. A node reaches the outside world
through this and through nothing else, which is what makes the whole pipeline
runnable against fakes with no network and no API key.

The types here are protocols, so nothing in this module knows that SQLite,
Google Drive, or any provider exists. The concrete classes are chosen in
``infrastructure/factory.py`` and nowhere else.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from domain.contracts.enums import ModelTier
from domain.ports.repositories import (
    BlobStore,
    CalibrationRepository,
    CandidateRepository,
    CostRepository,
    DeliveryRepository,
    ErrorRepository,
    EventRepository,
    EvidenceRepository,
    LlmCacheRepository,
    ReviewRepository,
    RunRepository,
)
from domain.provenance.normalization import PROFILE_ID as NORMALIZATION_PROFILE


class Clock(Protocol):
    """Time, injected.

    Not because reading a clock is dangerous, but because a test that cannot
    control time either sleeps or asserts nothing about durations, and both of
    those are how a suite gets slow and vague.
    """

    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class Settings:
    """Configuration, resolved once at startup.

    Held as a plain frozen dataclass rather than read from the environment at
    each use, so a run's behaviour cannot change halfway through because
    something else edited a variable.
    """

    db_path: str = "data/db/submission_desk.sqlite"
    blob_dir: str = "data/blobs"
    #: Where candidate documents are read from. Empty means the inbox beside
    #: the blob store. In demo mode this is overridden with the synthetic
    #: corpus, and a value pointing anywhere else refuses to start.
    inbox_dir: str = ""
    #: Real pilot documents, outside the repository. Never set in demo mode.
    pilot_data_dir: str = ""
    source_adapter: str = "local"
    pipeline_version: str = "1"
    routing_policy_id: str = "routed"
    #: fake | anthropic. Anything else refuses at the first call.
    model_provider: str = "fake"
    #: The active provider's credential. Never logged, never returned to a
    #: browser; the doctor reports presence only.
    model_api_key: str = ""
    model_api_base_url: str = ""
    #: Provider model identifiers bound to the two tiers. Empty means unbound.
    model_cheap_id: str = ""
    model_strong_id: str = ""
    #: Prices per million tokens, as decimal strings. Empty means unpriced,
    #: which renders as "not configured" and never as zero.
    price_cheap_input: str = ""
    price_cheap_output: str = ""
    price_strong_input: str = ""
    price_strong_output: str = ""
    #: Browser origins allowed to call the API with a session cookie.
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)
    blind_mode: bool = True
    calibration_enabled: bool = False
    demo_mode: bool = False
    token_ceiling_per_run: int = 120_000
    max_escalations_per_candidate: int = 3
    stale_run_minutes: int = 15
    #: How long a finished run is kept before `purge` removes it and its documents.
    retention_days: int = 30
    extraction_confidence_warn: float = 0.6
    reviewer_id: str = ""
    log_spans: bool = False

    #: Which tier each call site starts at. The routing policy may raise this;
    #: nothing lowers it. Named by tier, never by model, so a binding swap
    #: changes config/models.yaml and nothing else.
    structure_tier: ModelTier = ModelTier.CHEAP
    assess_tier: ModelTier = ModelTier.CHEAP
    structure_max_input_tokens: int = 24_000
    assess_max_input_tokens: int = 12_000
    assess_chunk_k: int = 6

    #: Criteria assessed in parallel. Safe because criteria are independent, and
    #: results are restored to rubric order before anything reads them.
    assess_concurrency: int = 4

    # --- security -----------------------------------------------------------
    #
    # The render comparison is the only detector with a cost, and it is the
    # strongest one. It is switched off by page count rather than by taste: a
    # sixty-page document would take minutes for a check that matters most on
    # page one of a CV.

    sanitize_render_diff: bool = True
    render_diff_max_pages: int = 10
    #: A HIGH finding at or above this halts the run; below it, a person is
    #: told instead. Never resolves to "assume clean" in either direction.
    suspect_confidence: float = 0.75
    #: The second opinion on a borderline finding. Off by default because the
    #: deterministic detectors decide, and a classifier that never runs cannot
    #: be persuaded by the text it was asked to examine.
    sanitize_classify: bool = False
    sanitize_tier: ModelTier = ModelTier.CHEAP

    # --- calibration ---------------------------------------------------------
    #
    # Off by default, because it plausibly helps and has not been measured. The
    # honest place for such a feature is behind a flag with an experiment
    # attached, not switched on because it sounds sensible.

    #: How many past decisions are offered. Three: two is not a range, and four
    #: starts to read like a pattern to match rather than a reference.
    calibration_top_k: int = 3
    #: How old a decision may be before it stops being a useful anchor. Roles
    #: drift and rubrics change; last year's decision met a different bar.
    calibration_staleness_days: int = 180


@dataclass(frozen=True)
class Deps:
    """The handles a node receives.

    Every field is a protocol or a value. Adding a concrete class here would
    make the pipeline untestable offline, which is the one property the whole
    evaluation depends on.
    """

    settings: Settings
    clock: Clock

    runs: RunRepository
    candidates: CandidateRepository
    evidence: EvidenceRepository
    reviews: ReviewRepository
    costs: CostRepository
    errors: ErrorRepository
    deliveries: DeliveryRepository
    calibration: CalibrationRepository
    llm_cache: LlmCacheRepository
    events: EventRepository
    blobs: BlobStore

    #: Filled in by later phases. Declared here so the shape of the finished
    #: system is visible from the start rather than assembled by accretion.
    models: Any = None
    router: Any = None
    extractor: Any = None
    source: Any = None
    sinks: tuple[Any, ...] = ()
    notifier: Any = None
    budget: Any = None
    prompts: Any = None
    scanner: Any = None
    pricing: Any = None
    metrics: Any = None
    calibration_index: Any = None
    embedder: Any = None
    #: Removes identifiers from text before it is written down. Injected rather
    #: than imported, because a node that reached for the concrete processor
    #: could not be run against a fake and would put an infrastructure import
    #: inside the pipeline.
    redactor: Callable[[str], str] | None = None
    rubric_loader: Callable[[str], Any] | None = None
    #: Operator settings and the admin account, written from the admin page.
    settings_store: Any = None
    #: Seals secrets at rest and hashes passwords. A port, so the use cases
    #: that manage the admin account never see a cipher.
    secrets: Any = None

    @property
    def source_profile_id(self) -> str:
        """The key a document's text is stored under: how the extractor
        assembled it, plus how the domain normalised it. A change to either
        re-reads the document rather than validating spans against the wrong
        text."""
        return f"{self.extractor.profile_id}+{NORMALIZATION_PROFILE}"
