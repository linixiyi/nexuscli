"""Tests for the native Google Gemini API client (provider="google").

Every HTTP interaction is mocked (httpx.AsyncClient.stream is replaced);
no test in this file performs a real network request. The only credential
literal allowed anywhere below is the ``<api-key>`` placeholder.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from nexuscli.config import LlmConfig
from nexuscli.llm.base import is_transient_api_error
from nexuscli.llm.factory import create_llm_client
from nexuscli.llm.google import GoogleGeminiClient
from nexuscli.llm.model_profiles import (
    DEFAULT_MODEL_PROFILES,
    PROVIDER_API_KEY_ENVS,
    PROVIDER_DEFAULTS,
)
from nexuscli.types import Message


class _FakeStreamResponse:
    """Async context-manager stand-in for httpx.Response streaming SSE."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    async def __aenter__(self) -> _FakeStreamResponse:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_text(self) -> AsyncIterator[str]:
        for event in self._events:
            yield f"data: {json.dumps(event)}\n\n"


def _install_fake_stream(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> None:
    def fake_stream(client, method, url, headers=None, json=None, **kwargs):
        captured.append({"method": method, "url": url, "headers": headers, "json": json})
        return _FakeStreamResponse(events)

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)


def _install_failing_stream(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def fake_stream(client, method, url, headers=None, json=None, **kwargs):
        captured.append({"method": method, "url": url, "headers": headers, "json": json})
        raise exc

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    return captured


def _client(**overrides: Any) -> GoogleGeminiClient:
    kwargs: dict[str, Any] = {"model": "gemini-3-pro", "api_key": "<api-key>"}
    kwargs.update(overrides)
    # `<api-key>` is the mandated placeholder — never a real credential.
    return GoogleGeminiClient(**kwargs)


def _chunk(
    parts: list[dict[str, Any]],
    *,
    finish_reason: str | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate: dict[str, Any] = {"content": {"role": "model", "parts": parts}}
    if finish_reason:
        candidate["finishReason"] = finish_reason
    chunk: dict[str, Any] = {"candidates": [candidate]}
    if usage:
        chunk["usageMetadata"] = usage
    return chunk


async def _collect_chat_events(
    client: GoogleGeminiClient,
    messages: list[Message],
    tools: list[dict[str, Any]],
    system_prompt: str = "You are a helpful assistant.",
) -> list[dict[str, Any]]:
    return [event async for event in client.chat(messages, tools, system_prompt=system_prompt)]


# ---------------------------------------------------------------------------
# Request mapping: URL, headers, payload shape.
# ---------------------------------------------------------------------------


def test_request_mapping_and_headers(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    _install_fake_stream(monkeypatch, captured, [_chunk([{"text": "ok"}], usage={})])

    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }
    ]
    messages = [
        Message(role="user", content="List the files"),
        Message(
            role="assistant",
            content="Reading it now.",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'},
                }
            ],
        ),
        Message(role="tool", content="file contents", tool_call_id="call_1"),
    ]

    asyncio.run(_collect_chat_events(_client(), messages, tools))

    request = captured[0]
    assert request["method"] == "POST"
    assert request["url"].endswith("/v1beta/models/gemini-3-pro:streamGenerateContent?alt=sse")
    # The key travels in the header, never in a `?key=` query parameter.
    assert request["headers"]["x-goog-api-key"] == "<api-key>"
    assert "key=" not in request["url"]

    payload = request["json"]
    assert payload["systemInstruction"] == {"parts": [{"text": "You are a helpful assistant."}]}
    assert payload["generationConfig"] == {"temperature": 0.7, "maxOutputTokens": 8192}
    assert payload["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ]
        }
    ]
    # The user turn keeps role "user"; the assistant turn becomes role "model"
    # with text ahead of the functionCall part (parsed argument object).
    assert payload["contents"][0] == {"role": "user", "parts": [{"text": "List the files"}]}
    assert payload["contents"][1]["role"] == "model"
    assert payload["contents"][1]["parts"] == [
        {"text": "Reading it now."},
        {"functionCall": {"name": "read_file", "args": {"path": "a.txt"}}},
    ]
    # The tool result becomes a user content with a functionResponse part whose
    # name is resolved from the paired assistant tool_calls entry.
    assert payload["contents"][2] == {
        "role": "user",
        "parts": [
            {
                "functionResponse": {
                    "name": "read_file",
                    "response": {"result": "file contents"},
                }
            }
        ],
    }


def test_consecutive_tool_results_merge_with_resolved_names(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    _install_fake_stream(monkeypatch, captured, [_chunk([{"text": "ok"}], usage={})])

    messages = [
        Message(role="user", content="run both"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                },
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                },
            ],
        ),
        Message(role="tool", content="contents", tool_call_id="call_1"),
        Message(role="tool", content="exit 0", tool_call_id="call_2"),
    ]

    asyncio.run(_collect_chat_events(_client(), messages, []))

    contents = captured[0]["json"]["contents"]
    # Both tool results merge into ONE user content, in call order, each named
    # from its paired assistant tool_call.
    assert len(contents) == 3
    assert contents[2]["role"] == "user"
    assert contents[2]["parts"] == [
        {"functionResponse": {"name": "read_file", "response": {"result": "contents"}}},
        {"functionResponse": {"name": "bash", "response": {"result": "exit 0"}}},
    ]


# ---------------------------------------------------------------------------
# Image input: capability comes from the tri-state field, never the name.
# ---------------------------------------------------------------------------


def _image_message() -> Message:
    return Message(
        role="user",
        content=[
            {"type": "text", "text": "what is this?"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,QUJD"},
                "metadata": {"source": "shot.png", "width": 10, "height": 10},
            },
        ],
    )


def test_vision_capable_inline_data_mapping() -> None:
    client = _client(vision_capable=True)

    assert client.supports_images is True
    parts = client._format_parts(_image_message().content)

    assert parts == [
        {"text": "what is this?"},
        {"inlineData": {"mimeType": "image/png", "data": "QUJD"}},
    ]


def test_unknown_capability_is_conservative_even_for_vision_named_models() -> None:
    # No model-name heuristic on this provider: "vision" in the name proves
    # nothing; an unset capability field means images are degraded to text.
    client = _client(model="gemini-3-vision-flash", vision_capable=None)

    assert client.supports_images is False
    parts = client._format_parts(_image_message().content)

    assert parts[1] == {"text": "[Image omitted: shot.png, 10x10]"}
    assert not any("inlineData" in part for part in parts)


def test_vision_explicitly_disabled_degrades_to_text() -> None:
    client = _client(vision_capable=False)

    parts = client._format_parts(_image_message().content)

    assert client.supports_images is False
    assert parts[1] == {"text": "[Image omitted: shot.png, 10x10]"}


def test_remote_image_url_degrades_even_when_vision_capable() -> None:
    # REST inlineData has no remote-URL form (fileData is GCS/YouTube only)
    # and the client performs no secondary fetches.
    client = _client(vision_capable=True)
    content = [
        {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
    ]

    parts = client._format_parts(content)

    assert parts == [{"text": "[Image omitted: remote image URLs are not supported here]"}]


# ---------------------------------------------------------------------------
# Stream parsing: events, tool calls, stop reasons, usage.
# ---------------------------------------------------------------------------


def test_stream_events_conversion(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    events = [
        _chunk([{"text": "plan", "thought": True}]),
        _chunk([{"text": "Hello"}]),
        _chunk([{"functionCall": {"name": "read_file", "args": {"path": "a.txt"}}}]),
        _chunk(
            [],
            finish_reason="STOP",
            usage={"promptTokenCount": 12, "candidatesTokenCount": 5, "totalTokenCount": 17},
        ),
    ]
    _install_fake_stream(monkeypatch, captured, events)

    parsed = asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], []))

    assert [event["type"] for event in parsed] == [
        "message_start",
        "thinking_delta",
        "text_delta",
        "tool_call_delta",
        "message_end",
        "usage",
    ]
    assert parsed[0] == {"type": "message_start", "model": "gemini-3-pro"}
    assert parsed[1] == {"type": "thinking_delta", "thinking": "plan"}
    assert parsed[2] == {"type": "text_delta", "text": "Hello"}
    # Gemini sends no call id and no argument string: the parser assigns a
    # deterministic id/index and passes the args object through (the agent's
    # _merge_tool_delta serializes dict arguments itself).
    assert parsed[3]["tool_call"] == {
        "index": 0,
        "id": "call_0",
        "type": "function",
        "function": {"name": "read_file", "arguments": {"path": "a.txt"}},
    }
    # finishReason STOP after a functionCall part maps onto tool_use.
    assert parsed[4] == {"type": "message_end", "stop_reason": "tool_use"}


def test_finish_reason_mapping(monkeypatch) -> None:
    cases = [
        ("STOP", "end_turn"),
        ("MAX_TOKENS", "max_tokens"),
        ("SAFETY", "stop_sequence"),
        ("PROHIBITED_CONTENT", "stop_sequence"),
    ]
    for finish_reason, expected in cases:
        captured: list[dict[str, Any]] = []
        _install_fake_stream(
            monkeypatch, captured, [_chunk([{"text": "hi"}], finish_reason=finish_reason)]
        )
        parsed = asyncio.run(
            _collect_chat_events(_client(), [Message(role="user", content="hi")], [])
        )
        ends = [event for event in parsed if event["type"] == "message_end"]
        assert len(ends) == 1
        assert ends[0]["stop_reason"] == expected, finish_reason


def test_multiple_tool_calls_get_distinct_indices(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    events = [
        _chunk(
            [
                {"functionCall": {"name": "read_file", "args": {"path": "a.txt"}}},
                {"functionCall": {"name": "bash", "args": {"command": "ls"}}},
            ]
        ),
        _chunk([], finish_reason="STOP"),
    ]
    _install_fake_stream(monkeypatch, captured, events)

    parsed = asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], []))

    deltas = [event["tool_call"] for event in parsed if event["type"] == "tool_call_delta"]
    assert [(delta["index"], delta["id"]) for delta in deltas] == [(0, "call_0"), (1, "call_1")]
    assert [delta["function"]["name"] for delta in deltas] == ["read_file", "bash"]
    assert deltas[0]["function"]["arguments"] == {"path": "a.txt"}
    assert deltas[1]["function"]["arguments"] == {"command": "ls"}


def test_usage_statistics_single_emission(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    # Streamed usageMetadata is cumulative; only the final value may surface.
    events = [
        _chunk([{"text": "Hel"}], usage={"promptTokenCount": 100, "totalTokenCount": 100}),
        _chunk(
            [{"text": "lo"}],
            usage={
                "promptTokenCount": 100,
                "candidatesTokenCount": 25,
                "cachedContentTokenCount": 40,
                "thoughtsTokenCount": 5,
                "totalTokenCount": 125,
            },
        ),
    ]
    _install_fake_stream(monkeypatch, captured, events)

    parsed = asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], []))

    usage_events = [event for event in parsed if event["type"] == "usage"]
    assert len(usage_events) == 1
    usage = usage_events[0]["usage"]
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 25
    assert usage["cache_hit_tokens"] == 40
    assert usage["reasoning_tokens"] == 5
    assert usage["total_tokens"] == 125


# ---------------------------------------------------------------------------
# Error translation.
# ---------------------------------------------------------------------------


def test_missing_key_yields_error_event(monkeypatch) -> None:
    def fail_stream(*args, **kwargs):
        raise AssertionError("no HTTP request may be sent without an API key")

    monkeypatch.setattr(httpx.AsyncClient, "stream", fail_stream)
    client = GoogleGeminiClient(model="gemini-3-pro", api_key="")

    parsed = asyncio.run(_collect_chat_events(client, [Message(role="user", content="hi")], []))

    assert len(parsed) == 1
    assert parsed[0]["type"] == "error"
    assert "GEMINI_API_KEY" in str(parsed[0]["error"])


def test_http_status_error_carries_response_body(monkeypatch) -> None:
    request = httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/x")
    response = httpx.Response(
        400,
        text=json.dumps(
            {
                "error": {
                    "code": 400,
                    "message": "API key not valid. Please pass a valid API key.",
                    "status": "INVALID_ARGUMENT",
                }
            }
        ),
        request=request,
    )
    _install_failing_stream(
        monkeypatch,
        httpx.HTTPStatusError("Client error '400'", request=request, response=response),
    )

    parsed = asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], []))

    # message_start is emitted before the request (same seam as the
    # OpenAI-compatible client), so the error lands second.
    assert [event["type"] for event in parsed] == ["message_start", "error"]
    message = str(parsed[-1]["error"])
    assert "google API returned HTTP 400" in message
    assert "API key not valid" in message
    # The phrasing stays inside the shared transient-error classifier.
    assert is_transient_api_error(message.replace("HTTP 400", "HTTP 429"))


def test_timeout_and_connection_errors_translate(monkeypatch) -> None:
    captured = _install_failing_stream(monkeypatch, httpx.ReadTimeout("timed out"))

    parsed = asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], []))

    assert len(captured) == 1
    assert [event["type"] for event in parsed] == ["message_start", "error"]
    assert "timed out after 120s" in str(parsed[-1]["error"])

    captured = _install_failing_stream(monkeypatch, httpx.ConnectError("connection refused"))
    parsed = asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], []))

    assert len(captured) == 1
    assert [event["type"] for event in parsed] == ["message_start", "error"]
    assert "Could not connect to google" in str(parsed[-1]["error"])
    assert "https://generativelanguage.googleapis.com" in str(parsed[-1]["error"])


# ---------------------------------------------------------------------------
# Factory dispatch and env fallback.
# ---------------------------------------------------------------------------


def test_factory_dispatch_and_env_fallback(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    client = create_llm_client(LlmConfig(provider="google", model="gemini-3-pro"))
    assert isinstance(client, GoogleGeminiClient)
    assert client.api_key == ""
    assert client.base_url == "https://generativelanguage.googleapis.com"
    # Context window comes from the ModelProfile table entry.
    assert client.max_context_window == 1_048_576
    # Unknown capability (no llm.vision) is conservatively image-less — no
    # model-name heuristic.
    assert client.supports_images is False

    monkeypatch.setenv("GEMINI_API_KEY", "<api-key>")
    client = create_llm_client(LlmConfig(provider="google", model="gemini-3-pro"))
    assert client.api_key == "<api-key>"

    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setenv("GOOGLE_API_KEY", "<api-key>")
    client = create_llm_client(LlmConfig(provider="google", model="gemini-3-pro"))
    assert client.api_key == "<api-key>"

    explicit = create_llm_client(
        LlmConfig(provider="google", model="gemini-3-pro", api_key="<api-key>")
    )
    assert explicit.api_key == "<api-key>"


def test_factory_unknown_model_takes_conservative_defaults(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    client = create_llm_client(
        LlmConfig(provider="google", model="gemini-99-future", api_key="<api-key>")
    )
    assert isinstance(client, GoogleGeminiClient)
    # No ModelProfile entry → conservative context fallback, not a guess.
    assert client.max_context_window == 128_000
    assert client.supports_images is False

    overridden = create_llm_client(
        LlmConfig(
            provider="google",
            model="gemini-99-future",
            api_key="<api-key>",
            context_window=500_000,
            vision=True,
        )
    )
    assert overridden.max_context_window == 500_000
    assert overridden.supports_images is True


def test_model_profiles_carry_google_defaults() -> None:
    profiles = {
        profile.model: profile for profile in DEFAULT_MODEL_PROFILES if profile.provider == "google"
    }
    assert set(profiles) >= {"gemini-3-pro", "gemini-3-flash"}
    for profile in profiles.values():
        assert profile.context_window == 1_048_576
        # Capability is a declared field on the profile, not a name heuristic.
        assert profile.vision is True
        assert profile.api_key_env == "GEMINI_API_KEY"

    assert PROVIDER_DEFAULTS["google"] == (
        "Google Gemini",
        "https://generativelanguage.googleapis.com",
        1_048_576,
    )
    assert PROVIDER_API_KEY_ENVS["google"] == ("GEMINI_API_KEY", "GOOGLE_API_KEY")
