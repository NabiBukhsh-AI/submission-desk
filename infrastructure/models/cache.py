"""Keying a response on everything that determined it.

Two things rest on this. An evaluation run is reproducible, so a comparison
between two configurations measures the configuration rather than the weather.
And a week of demos does not re-bill the same prompts, which during a five-day
sprint is the difference between a trivial spend and an embarrassing one.

The key includes the tier binding, the system prompt, every rendered block, the
schema, the temperature, and the seed. Anything that could change the answer is
in it; nothing else is. A different run id or a different day is the same call,
which is exactly what makes the second run free.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from domain.ports.models import GenerationRequest, GenerationResult, ModelClient, Usage

#: Separator between key components, chosen so no component can contain it and
#: two different requests cannot produce one string by accident.
_SEPARATOR = "\x1f"


def cache_key(request: GenerationRequest, *, tier_binding_hash: str = "") -> str:
    """The identity of a response.

    The schema is included by name and by field shape, so adding a field to a
    contract invalidates every cached response that was produced under the old
    one rather than serving answers that no longer fit.
    """
    schema = request.response_schema
    schema_fingerprint = getattr(schema, "__name__", str(schema))
    if schema is not None:
        schema_fingerprint += json.dumps(
            schema.model_json_schema(), sort_keys=True, separators=(",", ":")
        )

    parts = [
        tier_binding_hash,
        request.call_site,
        request.tier.value,
        request.system_prompt,
        *(f"{block.kind.value}:{block.content}" for block in request.user_blocks),
        schema_fingerprint,
        f"{request.temperature:.4f}",
        str(request.seed),
    ]
    return hashlib.sha256(_SEPARATOR.join(parts).encode("utf-8")).hexdigest()


@dataclass
class ResponseCache:
    """Serves a stored response, or asks the client beneath it.

    A cached result is marked as such and carries the usage the original call
    reported, with the tokens attributed to the cache rather than to the
    provider. A cache hit that reported fresh usage would double-count spend and
    make the cost figure wrong in the direction that flatters it.
    """

    inner: ModelClient
    store: Any
    tier_binding_hash: str = ""
    enabled: bool = True

    def structured_generate(self, request: GenerationRequest) -> GenerationResult:
        if not self.enabled:
            return self.inner.structured_generate(request)

        key = cache_key(request, tier_binding_hash=self.tier_binding_hash)
        stored = self.store.get(key)

        if stored is not None:
            response_json, usage_json = stored
            usage = json.loads(usage_json)
            parsed = (
                request.response_schema.model_validate_json(response_json)
                if request.response_schema is not None
                else None
            )
            return GenerationResult(
                parsed=parsed,
                raw_text=response_json,
                usage=Usage(
                    input_tokens=0,
                    output_tokens=0,
                    cached_input_tokens=usage.get("input_tokens", 0),
                    provider_request_id=None,
                ),
                tier=request.tier,
                latency_ms=0,
                from_cache=True,
            )

        result = self.inner.structured_generate(request)

        # Only a valid response is stored. Caching a failure would make one bad
        # answer permanent, and the repair path would never get a second chance.
        if result.ok and result.parsed is not None:
            self.store.put(
                key,
                result.parsed.model_dump_json(),
                json.dumps(
                    {
                        "input_tokens": result.usage.input_tokens,
                        "output_tokens": result.usage.output_tokens,
                        "cached_input_tokens": result.usage.cached_input_tokens,
                    }
                ),
            )

        return result
