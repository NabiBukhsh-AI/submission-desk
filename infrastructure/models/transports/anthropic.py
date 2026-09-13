"""Anthropic, through the official SDK.

Structured output is requested with ``output_config.format``, so the first
text block is JSON matching the schema. Usage comes from ``response.usage``:
uncached input, output, and cache reads, each a number the provider counted.

The response is streamed and assembled. A profile of a dense CV is several
thousand output tokens, and a single request timeout measured from the first
byte to the last was expiring on a healthy call, after which the SDK's retry
generated the whole thing again and billed it twice. Streaming makes the
timeout a per-chunk one: a stalled connection still fails, a slow long
answer does not.

Sampling parameters are not sent. The current models reject ``temperature``,
and the determinism the evaluation depends on comes from the response cache
and the stand-in, not from a sampling knob.

A refusal is a real outcome of the current models' safety classifiers. It is
surfaced as a run-level failure naming the category, never as an empty result.
"""

from __future__ import annotations

from typing import Any

import anthropic

from domain.ports.models import ModelUnavailable
from infrastructure.models.transports import portable_schema

#: One retry inside the SDK for transient failures. The layers above own the
#: rest of the retry budget, and a transport that retried five times would
#: spend it without telling anyone.
MAX_RETRIES = 1


def make_transport(api_key: str, base_url: str = "") -> Any:
    """A transport bound to one key. The client is built once."""
    client = anthropic.Anthropic(
        api_key=api_key, base_url=base_url or None, max_retries=MAX_RETRIES
    )

    def transport(payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        schema = payload.get("response_format", {}).get("schema")
        output_config: Any = (
            {"format": {"type": "json_schema", "schema": portable_schema(schema)}}
            if schema
            else anthropic.NOT_GIVEN
        )
        try:
            with client.with_options(timeout=timeout).messages.stream(
                model=payload["model"],
                system=payload["system"],
                messages=payload["messages"],
                max_tokens=payload["max_output_tokens"],
                output_config=output_config,
            ) as stream:
                response = stream.get_final_message()
        except anthropic.AuthenticationError as error:
            raise ModelUnavailable(
                "The provider rejected the API key. Check it on the admin page."
            ) from error
        except anthropic.RateLimitError as error:
            raise ModelUnavailable(
                "The provider is rate-limiting requests. This will be retried."
            ) from error
        except anthropic.APITimeoutError as error:
            raise TimeoutError("the provider did not respond within the timeout") from error
        except anthropic.NotFoundError as error:
            raise ModelUnavailable(
                f"The provider does not serve a model called {payload['model']!r}. "
                "Check the model identifiers on the admin page."
            ) from error
        except anthropic.APIStatusError as error:
            raise ModelUnavailable(
                f"The provider refused the request ({error.status_code}). "
                "Check the model identifiers on the admin page."
            ) from error
        except anthropic.APIConnectionError as error:
            raise ModelUnavailable("The provider could not be reached.") from error

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) or "unspecified"
            raise ModelUnavailable(
                f"The provider declined to process this document (category: {category}). "
                "A person will need to read it."
            )

        text = next((block.text for block in response.content if block.type == "text"), "")
        usage = response.usage
        return {
            "id": response.id,
            "text": text,
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cached_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            },
        }

    return transport
