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
    pipeline_version: str = "1"
    routing_policy_id: str = "routed"
    model_provider: str = "fake"
    blind_mode: bool = True
    calibration_enabled: bool = False
    demo_mode: bool = False
    token_ceiling_per_run: int = 120_000
    max_escalations_per_candidate: int = 3
    stale_run_minutes: int = 15
    reviewer_id: str = ""
    log_spans: bool = False


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
    rubric_loader: Callable[[str], Any] | None = None
