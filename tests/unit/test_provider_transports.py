"""The Anthropic transport, driven with no network.

What it must get right: the request shape the SDK documents, streaming so a
long answer is not a slow one, usage read from the response and never
estimated, and every failure turned into a sentence rather than a traceback.

check_secrets: planted-shapes. Keys below are placeholders for nothing.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2 as httpx  # the SDK's own HTTP client
import pytest
from anthropic import AuthenticationError, NotFoundError

from domain.ports.models import ModelUnavailable
from infrastructure.models.transports import anthropic as anthropic_transport
from infrastructure.models.transports import portable_schema

PAYLOAD = {
    "model": "claude-haiku-4-5",
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


def test_portable_schema_keeps_shape_and_drops_validation() -> None:
    reduced = portable_schema(PAYLOAD["response_format"]["schema"])

    assert reduced["properties"]["ok"] == {"type": "boolean"}
    assert reduced["additionalProperties"] is False
    assert "minLength" not in json.dumps(reduced)


# --- doubles ---------------------------------------------------------------------------


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


class _Stream:
    """What ``messages.stream`` returns: a context manager with the final message."""

    def __init__(self, reply: Any) -> None:
        self.reply = reply

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get_final_message(self) -> Any:
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class _Messages:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> _Stream:
        self.calls.append(kwargs)
        return _Stream(self.reply)


class _Client:
    def __init__(self, reply: Any) -> None:
        self.messages = _Messages(reply)
        self.options: dict[str, Any] = {}

    def with_options(self, **options: Any) -> _Client:
        self.options = options
        return self


def _make(monkeypatch: pytest.MonkeyPatch, reply: Any) -> tuple[Any, _Client]:
    client = _Client(reply)
    monkeypatch.setattr(anthropic_transport.anthropic, "Anthropic", lambda **_: client)
    return anthropic_transport.make_transport("sk-ant-test"), client


def _error(cls: type, status: int) -> Exception:
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.invalid"))
    return cls("refused", response=response, body=None)


# --- the call ----------------------------------------------------------------------------


def test_the_call_is_streamed_with_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, client = _make(monkeypatch, _Message('{"ok": true}'))

    result = transport(PAYLOAD, timeout=45)

    call = client.messages.calls[0]
    assert call["model"] == PAYLOAD["model"]
    assert call["system"] == "Find evidence."
    assert call["max_tokens"] == 256
    assert call["output_config"]["format"]["type"] == "json_schema"
    # No sampling parameter: the current models reject it.
    assert "temperature" not in call
    assert client.options == {"timeout": 45}
    assert result == {
        "id": "msg_1",
        "text": '{"ok": true}',
        "usage": {"input_tokens": 300, "output_tokens": 12, "cached_input_tokens": 50},
    }


def test_a_refusal_is_a_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, _ = _make(monkeypatch, _Message("", stop_reason="refusal"))

    with pytest.raises(ModelUnavailable, match="declined"):
        transport(PAYLOAD, timeout=10)


def test_a_bad_key_names_the_admin_page(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, _ = _make(monkeypatch, _error(AuthenticationError, 401))

    with pytest.raises(ModelUnavailable, match="admin page"):
        transport(PAYLOAD, timeout=10)


def test_an_unknown_model_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure a tier left bound to a model the provider does not serve
    produces: every call 404s, and the sentence says which model."""
    transport, _ = _make(monkeypatch, _error(NotFoundError, 404))

    with pytest.raises(ModelUnavailable, match="claude-haiku-4-5"):
        transport(PAYLOAD, timeout=10)
