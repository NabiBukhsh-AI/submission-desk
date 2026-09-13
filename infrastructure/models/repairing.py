"""One repair attempt, and then it stops.

A model that returns malformed JSON usually returns valid JSON when shown the
error. A model that returns malformed JSON twice is not going to be fixed by
asking a third time, and a loop that keeps asking is how a five-day sprint
produces a provider bill nobody can explain.

So: exactly one repair, at the same tier. Escalation to a stronger tier is a
separate decision made by the routing layer above, which is why this class has
no idea that tiers other than the one it was given exist.

The repair prompt carries the prior output and the exact validation error, and
deliberately does not resend the document. A sixty-page CV resent for a missing
comma would cost more than the original call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from domain.ports.models import (
    BlockKind,
    GenerationRequest,
    GenerationResult,
    ModelClient,
    PromptBlock,
    Usage,
)
from infrastructure.observability.logging import get_logger

#: One. Not a default, not configurable at the call site: the architecture caps
#: total attempts per call site at three, and this layer owns exactly one of
#: them.
MAX_REPAIRS = 1


@dataclass
class RepairingClient:
    """Validates, and gives the model one chance to correct itself."""

    inner: ModelClient
    repair_template: str = ""

    @property
    def tier_binding_hash(self) -> str:
        """The inner client's fingerprint, passed through so the run record
        names what was bound whether or not repair sits in front of it."""
        return str(getattr(self.inner, "tier_binding_hash", "") or "")

    def structured_generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.monotonic()
        first = self.inner.structured_generate(request)

        if first.ok:
            return first

        if first.validation_error is None:
            # The call failed for a reason repair cannot fix: a timeout, a rate
            # limit, an unreachable provider. Retrying the content would not
            # help, and the layer that handles transport already tried.
            return first

        get_logger().info(
            "model.repair", site=request.call_site, why=(first.validation_error or "")[:160]
        )
        repaired = self.inner.structured_generate(self._repair_request(request, first))
        elapsed = int((time.monotonic() - started) * 1000)

        combined = Usage(
            input_tokens=first.usage.input_tokens + repaired.usage.input_tokens,
            output_tokens=first.usage.output_tokens + repaired.usage.output_tokens,
            cached_input_tokens=first.usage.cached_input_tokens
            + repaired.usage.cached_input_tokens,
            provider_request_id=repaired.usage.provider_request_id,
        )

        return GenerationResult(
            parsed=repaired.parsed,
            raw_text=repaired.raw_text,
            usage=combined,
            tier=request.tier,
            latency_ms=elapsed,
            validation_error=repaired.validation_error,
            attempts=first.attempts + repaired.attempts,
            repaired=True,
        )

    def _repair_request(
        self, request: GenerationRequest, failed: GenerationResult
    ) -> GenerationRequest:
        """Ask again, showing the error and not the document.

        The original blocks are replaced by a single repair block. That is the
        whole reason repair is cheap: the model already read the document, and
        what it needs now is its own output and the complaint about it.
        """
        template = self.repair_template or DEFAULT_REPAIR_TEMPLATE
        body = template.format(
            schema_name=request.response_schema.__name__,
            previous_output=failed.raw_text[:4000],
            validation_error=(failed.validation_error or "")[:2000],
        )

        return GenerationRequest(
            call_site=request.call_site,
            tier=request.tier,
            system_prompt=request.system_prompt,
            user_blocks=(PromptBlock(kind=BlockKind.REPAIR, content=body),),
            response_schema=request.response_schema,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            timeout_s=request.timeout_s,
            seed=request.seed,
            nonce=request.nonce,
        )


DEFAULT_REPAIR_TEMPLATE = """Your previous response did not match the required schema.

Schema: {schema_name}

What you returned:
{previous_output}

What was wrong with it:
{validation_error}

Return only corrected JSON matching the schema. Do not explain the correction,
do not add fields that are not in the schema, and do not invent content to fill
a field you cannot support from what you were shown.
"""


def validate_response(
    raw_text: str, schema: type[BaseModel]
) -> tuple[BaseModel | None, str | None]:
    """Parse a response, returning either the object or the complaint.

    Returns rather than raises, because an invalid response is an ordinary
    event on this path and the repair layer needs the error text to send back.
    """
    try:
        return schema.model_validate_json(raw_text), None
    except ValidationError as error:
        return None, _summarise(error)
    except ValueError as error:
        return None, f"the response was not valid JSON: {error}"


def _summarise(error: ValidationError) -> str:
    """Pydantic's errors, flattened into something a model can act on.

    The full error object is verbose and mostly noise to a model. Naming the
    field and the problem is what gets the second attempt right.
    """
    lines = []
    for detail in error.errors()[:10]:
        location = ".".join(str(part) for part in detail.get("loc", ())) or "<root>"
        lines.append(f"{location}: {detail.get('msg', 'invalid')}")
    return "\n".join(lines)
