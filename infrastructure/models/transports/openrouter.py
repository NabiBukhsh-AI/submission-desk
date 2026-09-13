"""OpenRouter, through its chat-completions endpoint.

One key reaches many vendors' models. The request is the OpenAI-compatible
shape OpenRouter documents; usage comes from the response's ``usage`` object
(``prompt_tokens``, ``completion_tokens``, and cached prompt tokens when the
provider reports them).

Structured output is requested as a JSON schema. Not every model honours
``response_format`` — the free tiers in particular — so a request the model
rejects for that reason is sent once more as plain JSON, with the schema
described in the system prompt instead. The response is validated against the
contract either way; the schema request only raises the odds of a first-time
fit.

Standard library HTTP on purpose: one POST, one JSON body, no client library
to version.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from domain.ports.models import ModelUnavailable
from infrastructure.models.transports import portable_schema

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

#: HTTP statuses that mean something specific to the person reading the queue.
BAD_REQUEST, PAYMENT_REQUIRED, RATE_LIMITED = 400, 402, 429

#: A fenced block around the JSON, which smaller models add however firmly
#: they are asked not to. Stripped before validation.
FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def make_transport(api_key: str, base_url: str = "") -> Any:
    endpoint = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/chat/completions"

    def transport(payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        try:
            response = _post(endpoint, api_key, _body(payload, with_schema=True), timeout)
        except _Rejected as rejected:
            if not rejected.about_response_format:
                raise ModelUnavailable(rejected.message) from rejected
            # The model does not take a schema. Ask in words instead.
            response = _post(endpoint, api_key, _body(payload, with_schema=False), timeout)

        choices = response.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        text = message.get("content") or ""
        if isinstance(text, list):  # some providers return content parts
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
        match = FENCE.match(text)
        if match:
            text = match.group(1)

        usage = response.get("usage") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        return {
            "id": response.get("id"),
            "text": text,
            "usage": {
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
                "cached_input_tokens": int(cached),
            },
        }

    return transport


def _body(payload: dict[str, Any], *, with_schema: bool) -> dict[str, Any]:
    schema = payload.get("response_format", {}).get("schema")
    system = payload["system"]
    body: dict[str, Any] = {
        "model": payload["model"],
        "messages": [{"role": "system", "content": system}, *payload["messages"]],
        "temperature": payload.get("temperature", 0.0),
        "max_tokens": payload["max_output_tokens"],
    }
    if payload.get("seed") is not None:
        body["seed"] = payload["seed"]

    if schema and with_schema:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": portable_schema(schema), "strict": False},
        }
    elif schema:
        body["messages"][0]["content"] = (
            f"{system}\n\nRespond with a single JSON object and nothing else, matching "
            f"this JSON schema exactly:\n{json.dumps(portable_schema(schema))}"
        )
    return body


class _Rejected(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message

    @property
    def about_response_format(self) -> bool:
        return self.status == BAD_REQUEST and "response_format" in self.message.lower()


def _post(endpoint: str, api_key: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "Submission Desk",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as raw:
            return json.loads(raw.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = _detail(error)
        if error.code in (401, 403):
            raise ModelUnavailable(
                "OpenRouter rejected the API key. Check it on the admin page."
            ) from error
        if error.code == PAYMENT_REQUIRED:
            raise ModelUnavailable(
                "OpenRouter reports no credit for this request. Top up the account."
            ) from error
        if error.code == RATE_LIMITED:
            raise ModelUnavailable(
                "OpenRouter is rate-limiting requests. This will be retried."
            ) from error
        if error.code == BAD_REQUEST:
            raise _Rejected(BAD_REQUEST, detail) from error
        raise ModelUnavailable(
            f"OpenRouter could not complete the request ({error.code}). This will be retried."
        ) from error
    except urllib.error.URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise TimeoutError("the provider did not respond within the timeout") from error
        raise ModelUnavailable("OpenRouter could not be reached.") from error
    except TimeoutError as error:
        raise TimeoutError("the provider did not respond within the timeout") from error


def _detail(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read().decode("utf-8"))
        return str((body.get("error") or {}).get("message") or body)[:300]
    except Exception:
        return str(error.reason)
