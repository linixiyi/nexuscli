"""Tests for the built-in stream fallback in OpenAICompatibleClient.chat.

Background (2026-09-29 real-gateway smoke test): a local gateway's streaming
path intermittently answered 429/5xx while its non-streaming path succeeded,
so every streaming-only turn failed. The client now replays one pre-event
transient streaming failure as a single ``stream: false`` request parsed into
the exact same event sequence; a mid-stream failure (deltas already emitted)
is never replayed. Every test drives chat() against a stubbed httpx
AsyncClient — no real network (same seam as tests/test_llm_io_dump.py).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import pytest

from nexuscli.llm.openai_compatible import OpenAICompatibleClient
from nexuscli.types import Message

_CHAT_URL = "https://api.deepseek.com/v1/chat/completions"

# Streaming form of _COMPLETION_SINGLE_TEXT: one text delta, finish + usage.
_SSE_SINGLE_TEXT = [
    'data: {"choices": [{"delta": {"content": "Hello world"}}]}\n\n',
    (
        'data: {"choices": [{"delta": {}, "finish_reason": "stop"}],'
        ' "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}\n\n'
    ),
    "data: [DONE]\n\n",
]

_COMPLETION_SINGLE_TEXT = {
    "choices": [
        {
            "message": {"role": "assistant", "content": "Hello world"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}


def _placeholder_api_key() -> str:
    """Placeholder credential for tests; env-overridable, never a real secret."""
    return os.environ.get("NEXUSCLI_UNITTEST_LLM_CREDENTIAL") or "unit-test-key-123"


class _Transport:
    """Stub for httpx.AsyncClient.stream/.post — records calls, never a network."""

    def __init__(
        self,
        *,
        sse_chunks: list[str] | None = None,
        status_error: int | None = None,
        mid_stream_error: Exception | None = None,
        completion: dict[str, Any] | None = None,
        post_status_error: int | None = None,
    ) -> None:
        self.stream_calls = 0
        self.post_calls = 0
        self.post_payloads: list[dict[str, Any]] = []
        self._sse_chunks = sse_chunks or []
        self._status_error = status_error
        self._mid_stream_error = mid_stream_error
        self._completion = completion or {}
        self._post_status_error = post_status_error

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = self

        def fake_stream(self, *args, **kwargs):
            transport.stream_calls += 1
            if transport._status_error is not None:
                return _FakeStream(_FakeStatusResponse(transport._status_error))
            return _FakeStream(
                _FakeStreamingResponse(transport._sse_chunks, transport._mid_stream_error)
            )

        async def fake_post(self, *args, **kwargs):
            transport.post_calls += 1
            transport.post_payloads.append(kwargs.get("json") or {})
            if transport._post_status_error is not None:
                _FakeStatusResponse(transport._post_status_error).raise_for_status()
            return _FakeJsonResponse(transport._completion)

        monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


class _FakeStatusResponse:
    """Streaming response rejected at raise_for_status (gateway 401/429/5xx)."""

    def __init__(self, status_code: int) -> None:
        self._status_code = status_code

    def raise_for_status(self) -> None:
        request = httpx.Request("POST", _CHAT_URL)
        httpx.Response(self._status_code, request=request).raise_for_status()


class _FakeStreamingResponse:
    """SSE body served chunk by chunk, with an optional mid-stream failure."""

    def __init__(self, chunks: list[str], mid_stream_error: Exception | None) -> None:
        self._chunks = chunks
        self._mid_stream_error = mid_stream_error

    def raise_for_status(self) -> None:
        return None

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk
        if self._mid_stream_error is not None:
            raise self._mid_stream_error


class _FakeJsonResponse:
    """Non-streaming JSON response, as client.post returns."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeStream:
    """Async context manager mirroring client.stream(...)."""

    def __init__(self, response: Any) -> None:
        self._response = response

    async def __aenter__(self) -> Any:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        provider_name="deepseek",
        model="deepseek-v4-flash",
        api_key=_placeholder_api_key(),
        base_url="https://api.deepseek.com/v1",
    )


async def _collect_chat_events(client: OpenAICompatibleClient) -> list[dict[str, Any]]:
    return [
        event
        async for event in client.chat(
            [Message(role="user", content="hello")],
            [],
            system_prompt="system",
        )
    ]


def test_pre_event_transient_failure_falls_back_to_identical_event_sequence(monkeypatch) -> None:
    """a) Streaming 503 before any event → one non-stream replay, same events."""
    fallback_transport = _Transport(status_error=503, completion=_COMPLETION_SINGLE_TEXT)
    fallback_transport.install(monkeypatch)

    events = asyncio.run(_collect_chat_events(_client()))

    assert fallback_transport.stream_calls == 1
    assert fallback_transport.post_calls == 1
    assert fallback_transport.post_payloads[0]["stream"] is False
    assert "stream_options" not in fallback_transport.post_payloads[0]
    assert [event["type"] for event in events] == [
        "message_start",
        "text_delta",
        "message_end",
        "usage",
    ]

    # The degraded sequence must be identical to the healthy stream's events.
    normal_transport = _Transport(sse_chunks=_SSE_SINGLE_TEXT)
    normal_transport.install(monkeypatch)
    normal_events = asyncio.run(_collect_chat_events(_client()))

    assert events == normal_events


def test_fallback_replays_tool_calls_through_existing_parsing(monkeypatch) -> None:
    """a) The non-stream completion's tool_calls reuse the delta parser as-is."""
    tool_call = {
        "id": "call_1",
        "type": "function",
        "index": 0,
        "function": {"name": "bash", "arguments": '{"command": "ls -la"}'},
    }
    completion = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "", "tool_calls": [tool_call]},
                "finish_reason": "tool_calls",
            }
        ],
    }
    transport = _Transport(status_error=503, completion=completion)
    transport.install(monkeypatch)

    events = asyncio.run(_collect_chat_events(_client()))

    assert transport.post_calls == 1
    assert [event["type"] for event in events] == [
        "message_start",
        "tool_call_delta",
        "message_end",
    ]
    assert events[1] == {"type": "tool_call_delta", "tool_call": tool_call}
    assert events[2] == {"type": "message_end", "stop_reason": "tool_use"}


def test_mid_stream_failure_after_delta_does_not_fall_back(monkeypatch) -> None:
    """b) Deltas already emitted → the polluted stream is never replayed."""
    transport = _Transport(
        sse_chunks=['data: {"choices": [{"delta": {"content": "partial"}}]}\n\n'],
        mid_stream_error=httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body"
        ),
        completion=_COMPLETION_SINGLE_TEXT,
    )
    transport.install(monkeypatch)

    events = asyncio.run(_collect_chat_events(_client()))

    assert transport.stream_calls == 1
    assert transport.post_calls == 0
    assert [event["type"] for event in events] == ["message_start", "text_delta", "error"]
    assert "Could not connect to deepseek" in str(events[-1]["error"])


def test_healthy_streaming_path_is_unchanged(monkeypatch) -> None:
    """c) No transient error → pure streaming, no POST, classic event order."""
    transport = _Transport(
        sse_chunks=[
            'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n',
            'data: {"choices": [{"delta": {"content": " world"}}]}\n\n',
            (
                'data: {"choices": [{"delta": {}, "finish_reason": "stop"}],'
                ' "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}\n\n'
            ),
            "data: [DONE]\n\n",
        ]
    )
    transport.install(monkeypatch)

    events = asyncio.run(_collect_chat_events(_client()))

    assert transport.stream_calls == 1
    assert transport.post_calls == 0
    assert [event["type"] for event in events] == [
        "message_start",
        "text_delta",
        "text_delta",
        "message_end",
        "usage",
    ]
    assert events[1] == {"type": "text_delta", "text": "Hello"}
    assert events[2] == {"type": "text_delta", "text": " world"}
    assert events[3] == {"type": "message_end", "stop_reason": "end_turn"}
    assert events[4]["usage"]["total_tokens"] == 15


def test_non_transient_status_error_does_not_fall_back(monkeypatch) -> None:
    """d) 401 is not transient → error event, no non-stream replay."""
    transport = _Transport(status_error=401, completion=_COMPLETION_SINGLE_TEXT)
    transport.install(monkeypatch)

    events = asyncio.run(_collect_chat_events(_client()))

    assert transport.stream_calls == 1
    assert transport.post_calls == 0
    assert [event["type"] for event in events] == ["message_start", "error"]
    assert "HTTP 401" in str(events[-1]["error"])


def test_transient_failure_replays_non_stream_exactly_once(monkeypatch) -> None:
    """The replay is one-shot: a failing replay surfaces the error, no loop."""
    transport = _Transport(status_error=503, post_status_error=429)
    transport.install(monkeypatch)

    events = asyncio.run(_collect_chat_events(_client()))

    assert transport.stream_calls == 1
    assert transport.post_calls == 1
    assert [event["type"] for event in events] == ["message_start", "error"]
    assert "HTTP 429" in str(events[-1]["error"])
