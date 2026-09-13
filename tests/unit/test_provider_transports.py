"""The two provider transports, driven with no network.

What each must get right: the request shape the provider documents, usage read
from the response and never estimated, the fenced-JSON habit of small models
stripped, a schema the model rejects retried as plain JSON, and every failure
turned into a sentence rather than a traceback.

check_secrets: planted-shapes. Keys below are placeholders for nothing.
"""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest

from domain.ports.models import ModelUnavailable
from infrastructure.models.transports import anthropic as anthropic_transport
from infrastructure.models.transports import openrouter, portable_schema

PAYLOAD = {
    "model": "inclusionai/ling-3.0-flash-fin:free",
    "system": "Find evidence.",
    "messages": [{"role": "user", "content": "<<<CANDIDATE_DOCUMENT>>>\nhello\n<<<END>>>"}],
    "response_format": {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean", "minLength": 3}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    },
    "temperature": 0.0,
    "max_output_tokens": 256,
}


# --- the schema ------------------------------------------------------------------------


def test_portable_schema_keeps_shape_and_drops_validation() -> None:
    reduced = portable_schema(PAYLOAD["response_format"]["schema"])

    assert reduced["properties"]["ok"] == {"type": "boolean"}
    assert reduced["additionalProperties"] is False
    assert "minLength" not in json.dumps(reduced)


# --- OpenRouter ----------------------------------------------------------------------------


class _Http:
    """A scripted urlopen: each call pops the next (status, body)."""

    def __init__(self, *responses: tuple[int, dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def __call__(self, request: Any, timeout: float) -> Any:
        body = json.loads(request.data.decode("utf-8"))
        self.requests.append((request.full_url, body, dict(request.header_items())))
        status, payload = self.responses.pop(0)
        raw = json.dumps(payload).encode("utf-8")
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "err", {}, io.BytesIO(raw))
        return _Response(raw)


class _Response:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.raw


def _ok(text: str, **usage: int) -> tuple[int, dict[str, Any]]:
    return (
        200,
        {
            "id": "gen-1",
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 8, **usage},
        },
    )


def test_openrouter_sends_the_documented_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    http = _Http(_ok('{"ok": true}'))
    monkeypatch.setattr(openrouter.urllib.request, "urlopen", http)

    result = openrouter.make_transport("sk-or-test-key")(PAYLOAD, timeout=10)

    url, body, headers = http.requests[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-or-test-key"
    assert body["model"] == PAYLOAD["model"]
    assert body["messages"][0] == {"role": "system", "content": "Find evidence."}
    assert body["messages"][1]["role"] == "user"
    assert body["response_format"]["type"] == "json_schema"
    assert body["max_tokens"] == 256
    assert result == {
        "id": "gen-1",
        "text": '{"ok": true}',
        "usage": {"input_tokens": 120, "output_tokens": 8, "cached_input_tokens": 0},
    }


def test_openrouter_reads_cached_tokens_when_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    http = _Http(_ok('{"ok": true}', prompt_tokens_details={"cached_tokens": 100}))  # type: ignore[arg-type]
    monkeypatch.setattr(openrouter.urllib.request, "urlopen", http)

    result = openrouter.make_transport("k")(PAYLOAD, timeout=10)

    assert result["usage"]["cached_input_tokens"] == 100


def test_openrouter_strips_a_fenced_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """The free tiers wrap JSON in a code fence however firmly they are asked
    not to. The validator downstream needs the JSON, not the fence."""
    http = _Http(_ok('```json\n{"ok": true}\n```'))
    monkeypatch.setattr(openrouter.urllib.request, "urlopen", http)

    assert openrouter.make_transport("k")(PAYLOAD, timeout=10)["text"] == '{"ok": true}'


def test_openrouter_retries_as_plain_json_when_the_schema_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = _Http(
        (400, {"error": {"message": "response_format is not supported by this model"}}),
        _ok('{"ok": true}'),
    )
    monkeypatch.setattr(openrouter.urllib.request, "urlopen", http)

    result = openrouter.make_transport("k")(PAYLOAD, timeout=10)

    assert result["text"] == '{"ok": true}'
    first, second = http.requests[0][1], http.requests[1][1]
    assert "response_format" in first
    assert "response_format" not in second
    assert "JSON schema" in second["messages"][0]["content"]


@pytest.mark.parametrize(
    ("status", "fragment"),
    [(401, "rejected the API key"), (402, "no credit"), (429, "rate-limiting"), (503, "retried")],
)
def test_openrouter_failures_are_sentences(
    monkeypatch: pytest.MonkeyPatch, status: int, fragment: str
) -> None:
    http = _Http((status, {"error": {"message": "nope"}}))
    monkeypatch.setattr(openrouter.urllib.request, "urlopen", http)

    with pytest.raises(ModelUnavailable, match=fragment):
        openrouter.make_transport("k")(PAYLOAD, timeout=10)


def test_openrouter_a_different_400_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    http = _Http((400, {"error": {"message": "model not found"}}))
    monkeypatch.setattr(openrouter.urllib.request, "urlopen", http)

    with pytest.raises(ModelUnavailable, match="model not found"):
        openrouter.make_transport("k")(PAYLOAD, timeout=10)
    assert len(http.requests) == 1


# --- Anthropic -------------------------------------------------------------------------------


class _Block:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Usage:
    input_tokens = 300
    output_tokens = 12
    cache_read_input_tokens = 50


class _Message:
    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.id = "msg_1"
        self.content = [_Block(text)]
        self.usage = _Usage()
        self.stop_reason = stop_reason
        self.stop_details = None


class _Messages:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class _Client:
    def __init__(self, reply: Any) -> None:
        self.messages = _Messages(reply)

    def with_options(self, **_: Any) -> _Client:
        return self


def _make(monkeypatch: pytest.MonkeyPatch, reply: Any) -> tuple[Any, _Client]:
    client = _Client(reply)
    monkeypatch.setattr(anthropic_transport.anthropic, "Anthropic", lambda **_: client)
    return anthropic_transport.make_transport("sk-ant-test"), client


def test_anthropic_uses_structured_output_and_reads_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, client = _make(monkeypatch, _Message('{"ok": true}'))

    result = transport(PAYLOAD, timeout=10)

    call = client.messages.calls[0]
    assert call["model"] == PAYLOAD["model"]
    assert call["system"] == "Find evidence."
    assert call["max_tokens"] == 256
    assert call["output_config"]["format"]["type"] == "json_schema"
    # No sampling parameter: the current models reject it.
    assert "temperature" not in call
    assert result == {
        "id": "msg_1",
        "text": '{"ok": true}',
        "usage": {"input_tokens": 300, "output_tokens": 12, "cached_input_tokens": 50},
    }


def test_anthropic_refusal_is_a_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, _ = _make(monkeypatch, _Message("", stop_reason="refusal"))

    with pytest.raises(ModelUnavailable, match="declined"):
        transport(PAYLOAD, timeout=10)


def test_anthropic_auth_failure_names_the_admin_page(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx2 as httpx  # the SDK's own HTTP client
    from anthropic import AuthenticationError

    response = httpx.Response(401, request=httpx.Request("POST", "https://example.invalid"))
    transport, _ = _make(monkeypatch, AuthenticationError("bad key", response=response, body=None))

    with pytest.raises(ModelUnavailable, match="admin page"):
        transport(PAYLOAD, timeout=10)
