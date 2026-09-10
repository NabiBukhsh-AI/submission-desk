"""Where a reviewed candidate's package goes.

Every integration is optional and none can break the core. That is not a slogan:
the CSV sink is always registered, always written first, and cannot be removed,
so a run that reaches delivery always produces a file somebody can open. A
spreadsheet that is down becomes a row saying the spreadsheet is down, not a
candidate whose result vanished.

The error taxonomy is five values and the difference between them is what to do
next. TRANSIENT retries. RATE_LIMITED retries more slowly. AUTH and CONFIG do not
retry at all — a wrong credential does not become right by being asked again, and
retrying one is how an account gets locked. PERMANENT is a fact about the
request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class AdapterError(str, Enum):
    """Why a call to an outside system did not work.

    Five values, because five different things happen next. A single "failed"
    would make the retry policy a guess.
    """

    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    AUTH = "auth"
    CONFIG = "config"
    PERMANENT = "permanent"

    @property
    def retryable(self) -> bool:
        return self in (AdapterError.TRANSIENT, AdapterError.RATE_LIMITED)

    @property
    def disables_adapter(self) -> bool:
        """Whether this stops the adapter for the rest of the session.

        A missing credential and a misconfigured folder are not going to fix
        themselves between attempts, and an interface that kept retrying would
        bury the one message that says what to correct.
        """
        return self in (AdapterError.AUTH, AdapterError.CONFIG)


@dataclass(frozen=True)
class AdapterResult:
    """What one attempt at an outside system produced."""

    ok: bool
    #: What the far side calls what it stored: a row range, a file id, a path.
    #: Kept so a reviewer can be told where the result went.
    external_ref: str | None = None
    error_code: AdapterError | None = None
    #: A sentence for a person, never an exception string.
    message: str = ""
    attempts: int = 1
    latency_ms: int = 0
    #: Anything the adapter wants recorded, already free of candidate content.
    detail: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def succeeded(cls, external_ref: str, **extra: Any) -> AdapterResult:
        return cls(ok=True, external_ref=external_ref, **extra)

    @classmethod
    def failed(cls, error_code: AdapterError, message: str, **extra: Any) -> AdapterResult:
        return cls(ok=False, error_code=error_code, message=message, **extra)

    @property
    def retryable(self) -> bool:
        return bool(self.error_code and self.error_code.retryable)


@dataclass(frozen=True)
class DeliveryPayload:
    """What a sink is given.

    Assembled once by the delivery node so every sink writes the same thing, and
    deliberately narrow: a sink receives the outcome and the reasoning, not the
    evidence. A spreadsheet row containing a candidate's quotations would be a
    copy of their CV in a document with different access controls.
    """

    run_id: str
    candidate_id: str
    role_id: str
    band: str
    score: float | None
    coverage: float
    #: The rules that fired, as sentences. This is the auditable part.
    derivation: tuple[str, ...] = ()
    reviewer_id: str = ""
    reviewer_action: str = ""
    decided_at: str = ""
    #: criterion id -> resolved state, for a spreadsheet's columns.
    criterion_states: dict[str, str] = field(default_factory=dict)
    override_count: int = 0
    integrity_tier: str = "clean"
    #: What to ask the candidate, if anything.
    information_requests: tuple[str, ...] = ()


class ResultSink(Protocol):
    """Somewhere a reviewed package is written."""

    sink_id: str

    def deliver(self, payload: DeliveryPayload) -> AdapterResult:
        """Write one package. Never raises.

        A sink that raised would take down a delivery that other sinks had
        already completed, which is the failure the per-sink record exists to
        make visible.
        """
        ...

    def healthy(self) -> bool:
        """Whether this sink is worth attempting.

        False after an AUTH or CONFIG failure, so a broken credential produces
        one message rather than one per candidate.
        """
        ...
