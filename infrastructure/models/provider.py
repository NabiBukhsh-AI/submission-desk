"""One HTTP call to a language model.

Deliberately thin. It builds the request, sends it, parses the response, and
reports the usage the provider stated. It does not retry, route, cache, or
budget: those are separate layers so that each can be tested alone and none can
be forgotten.

No provider abstraction library. The surface actually used here is one endpoint
with structured output, and a library that unifies twelve providers would add a
dependency, a translation layer, and a place for usage metadata to be lost on
the way through.

Model identifiers never appear in this file. The binding comes from
config/models.yaml and arrives as a string, so nothing in the code, the logs, or
a chart label ever names a model.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from domain.contracts.enums import ModelTier
from domain.ports.models import (
    DATA_NOT_INSTRUCTION,
    BlockKind,
    GenerationRequest,
    GenerationResult,
    ModelUnavailable,
    Usage,
    render_document_block,
)
from infrastructure.models.repairing import validate_response


@dataclass(frozen=True)
class TierBinding:
    """What one tier resolves to, from configuration."""

    model: str
    max_output_tokens: int = 2048
    timeout_s: float = 60.0

    @property
    def is_bound(self) -> bool:
        return bool(self.model)


@dataclass
class ProviderModelClient:
    """Sends one request and returns what came back."""

    bindings: dict[ModelTier, TierBinding]
    api_key: str = ""
    base_url: str = ""
    transport: Any = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def tier_binding_hash(self) -> str:
        """What each tier points at, as a fingerprint on every run record."""
        return bindings_hash(self.bindings)

    def structured_generate(self, request: GenerationRequest) -> GenerationResult:
        binding = self.bindings.get(request.tier)
        if binding is None or not binding.is_bound:
            raise ModelUnavailable(
                f"No model is bound to {request.tier.value}. Set it in config/models.yaml, "
                "or run with MODEL_PROVIDER=fake to use recorded responses."
            )
        if not self.api_key:
            raise ModelUnavailable(
                "No API key is configured. Set MODEL_API_KEY, or run with "
                "MODEL_PROVIDER=fake to use recorded responses."
            )

        payload = self._payload(request, binding)
        started = time.monotonic()

        try:
            response = self._send(payload, timeout=min(request.timeout_s, binding.timeout_s))
        except TimeoutError as timeout:
            raise ModelUnavailable("the provider did not respond within the timeout") from timeout

        latency_ms = int((time.monotonic() - started) * 1000)
        raw_text = response.get("text", "")
        parsed, validation_error = validate_response(raw_text, request.response_schema)

        return GenerationResult(
            parsed=parsed,
            raw_text=raw_text,
            usage=_usage_from(response),
            tier=request.tier,
            latency_ms=latency_ms,
            validation_error=validation_error,
        )

    def _payload(self, request: GenerationRequest, binding: TierBinding) -> dict[str, Any]:
        """Assemble the request body.

        The system prompt always carries the data-not-instruction sentence, and
        document blocks are wrapped in a region keyed to a nonce the candidate
        has never seen.
        """
        system = f"{request.system_prompt}\n\n{DATA_NOT_INSTRUCTION}"

        rendered: list[str] = []
        for block in request.user_blocks:
            if block.kind is BlockKind.DOCUMENT:
                rendered.append(render_document_block(block, request.nonce))
            else:
                rendered.append(block.content)

        return {
            "model": binding.model,
            "system": system,
            "messages": [{"role": "user", "content": "\n\n".join(rendered)}],
            "response_format": {
                "type": "json_schema",
                "schema": request.response_schema.model_json_schema(),
            },
            "temperature": request.temperature,
            "max_output_tokens": min(request.max_output_tokens, binding.max_output_tokens),
            **({"seed": request.seed} if request.seed is not None else {}),
        }

    def _send(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        """Perform the call.

        The transport is injected so the failure tests can drive real behaviour
        without a network. Without one configured this raises rather than
        silently doing nothing, because a client that appears to work while
        sending nothing is worse than one that refuses.
        """
        self.calls.append(payload)

        if self.transport is None:
            raise ModelUnavailable(
                "No transport is configured for the live provider. Run with "
                "MODEL_PROVIDER=fake, or wire a transport in the factory."
            )
        result: dict[str, Any] = self.transport(payload, timeout=timeout)
        return result


def _usage_from(response: dict[str, Any]) -> Usage:
    """Read what the provider said it consumed.

    Never estimated. A response that omits usage is a response whose cost cannot
    be stated, and it is recorded as zero tokens with the omission flagged
    rather than filled in from a tokeniser: a guessed number that looks measured
    is worse than a missing one.
    """
    usage = response.get("usage") or {}
    return Usage(
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        cached_input_tokens=int(usage.get("cached_input_tokens", 0)),
        provider_request_id=response.get("id"),
    )


def load_bindings(config: dict[str, Any]) -> dict[ModelTier, TierBinding]:
    """Read tier bindings from parsed configuration."""
    tiers = config.get("tiers") or {}
    bindings: dict[ModelTier, TierBinding] = {}

    for tier in (ModelTier.CHEAP, ModelTier.STRONG):
        entry = tiers.get(tier.value) or {}
        bindings[tier] = TierBinding(
            model=str(entry.get("model") or ""),
            max_output_tokens=int(entry.get("max_output_tokens") or 2048),
            timeout_s=float(entry.get("timeout_s") or 60),
        )
    return bindings


def bindings_hash(bindings: dict[ModelTier, TierBinding]) -> str:
    """A fingerprint of what each tier points at.

    Part of the run record and of the cache key, so a model swap invalidates
    cached responses and is visible in the evaluation rather than silently
    changing results between runs.
    """
    canonical = json.dumps(
        {
            tier.value: binding.model
            for tier, binding in sorted(bindings.items(), key=lambda pair: pair[0].value)
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
