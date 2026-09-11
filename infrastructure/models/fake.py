"""The provider that needs no account.

This is production code, not a test helper. ``make demo`` runs on it, the whole
test suite runs on it, and the evaluation harness replays through it. That is
what makes the claim "the full pipeline runs offline with no API key" true
rather than aspirational.

Fixtures are keyed identically to the response cache, so a recorded fixture and
a cached response are interchangeable. Recording a real run therefore produces a
fixture set that replays exactly, and the demo shows the same output as the run
it was recorded from.

The failing variant lives here too. Timeouts, rate limits, malformed JSON and
missing usage metadata are behaviours a provider genuinely has, and testing them
against a double that can be told to misbehave beats waiting for the real one to
do it at an inconvenient moment.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from domain.ports.models import (
    GenerationRequest,
    GenerationResult,
    ModelUnavailable,
    Usage,
)
from infrastructure.models.cache import cache_key
from infrastructure.models.repairing import validate_response

#: Token counts a fixture reports when it has none recorded. Marked as an
#: estimate in the metadata so nothing downstream presents it as measured.
FIXTURE_USAGE = Usage(input_tokens=0, output_tokens=0)


@dataclass
class FakeModelClient:
    """Replays recorded responses, keyed like the cache."""

    fixtures: dict[str, str] = field(default_factory=dict)
    fixture_dir: Path | None = None
    #: Called when no fixture matches. The default refuses, because a fake that
    #: invents a plausible answer would let a test pass against a fixture that
    #: was never recorded.
    on_missing: Callable[[GenerationRequest], GenerationResult] | None = None
    tier_binding_hash: str = ""
    calls: list[GenerationRequest] = field(default_factory=list)

    def structured_generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls.append(request)
        key = cache_key(request, tier_binding_hash=self.tier_binding_hash)

        raw = self.fixtures.get(key) or self._from_disk(key)
        if raw is None:
            if self.on_missing is not None:
                return self.on_missing(request)
            raise ModelUnavailable(
                f"No recorded response for {request.call_site} at {key[:12]}. "
                "Record one under tests/fixtures/llm/ keyed by this value, or run "
                "against a real provider."
            )

        parsed, validation_error = validate_response(raw, request.response_schema)
        return GenerationResult(
            parsed=parsed,
            raw_text=raw,
            usage=FIXTURE_USAGE,
            tier=request.tier,
            latency_ms=0,
            validation_error=validation_error,
            metadata={"source": "fixture", "usage_is_measured": False},
        )

    def record(self, request: GenerationRequest, response_json: str) -> str:
        """Store a response under the key it will be looked up by."""
        key = cache_key(request, tier_binding_hash=self.tier_binding_hash)
        self.fixtures[key] = response_json
        return key

    def _from_disk(self, key: str) -> str | None:
        if self.fixture_dir is None:
            return None
        path = self.fixture_dir / f"{key}.json"
        return path.read_text(encoding="utf-8") if path.exists() else None


@dataclass
class ScriptedModelClient:
    """Returns prepared responses in order.

    For tests that care about a sequence — invalid then valid, to prove the
    repair path runs exactly twice — rather than about content.
    """

    responses: list[str | Exception]
    usages: list[Usage] = field(default_factory=list)
    calls: list[GenerationRequest] = field(default_factory=list)

    def structured_generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls.append(request)
        index = len(self.calls) - 1

        if index >= len(self.responses):
            raise ModelUnavailable(
                f"the script has {len(self.responses)} responses and this is call {index + 1}"
            )

        nxt = self.responses[index]
        if isinstance(nxt, Exception):
            raise nxt

        parsed, validation_error = validate_response(nxt, request.response_schema)
        usage = self.usages[index] if index < len(self.usages) else Usage(10, 5)

        return GenerationResult(
            parsed=parsed,
            raw_text=nxt,
            usage=usage,
            tier=request.tier,
            latency_ms=1,
            validation_error=validation_error,
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)


@dataclass
class FailingModelClient:
    """A provider behaving badly, on demand.

    Each mode corresponds to something a real provider does. Testing against
    this beats waiting for the real one to do it during a demo.
    """

    mode: str = "timeout"
    calls: int = 0

    def structured_generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls += 1

        if self.mode == "timeout":
            raise ModelUnavailable("the provider did not respond within the timeout")
        if self.mode == "rate_limited":
            limited = ModelUnavailable("the provider is rate limiting this account")
            # The provider said when to come back. Retrying immediately earns
            # another refusal, so the delay travels with the error.
            limited.retry_after_s = 30  # type: ignore[attr-defined]
            raise limited
        if self.mode == "server_error":
            raise ModelUnavailable("the provider returned a server error")
        if self.mode == "empty":
            return _invalid(request, "")
        if self.mode == "malformed_json":
            return _invalid(request, "{not json at all")
        if self.mode == "schema_invalid":
            return _invalid(request, json.dumps({"unexpected_field": 1}))
        if self.mode == "missing_usage":
            parsed, validation_error = validate_response("{}", request.response_schema)
            return GenerationResult(
                parsed=parsed,
                raw_text="{}",
                usage=Usage(input_tokens=0, output_tokens=0),
                tier=request.tier,
                latency_ms=1,
                validation_error=validation_error,
                metadata={"usage_is_measured": False},
            )

        raise ValueError(f"unknown failure mode {self.mode!r}")


def _invalid(request: GenerationRequest, raw: str) -> GenerationResult:
    parsed, validation_error = validate_response(raw, request.response_schema)
    return GenerationResult(
        parsed=parsed,
        raw_text=raw,
        usage=Usage(input_tokens=10, output_tokens=1),
        tier=request.tier,
        latency_ms=1,
        validation_error=validation_error or "the response was empty",
    )
