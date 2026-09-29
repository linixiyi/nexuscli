"""Tests for the native Anthropic Messages API client.

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
from nexuscli.llm.anthropic import AnthropicClient
from nexuscli.llm.factory import create_llm_client
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


def _client() -> AnthropicClient:
    # `<api-key>` is the mandated placeholder — never a real credential.
    return AnthropicClient(model="claude-sonnet-4-5", api_key="<api-key>")


def _message_start(**usage: int) -> dict[str, Any]:
    return {
        "type": "message_start",
        "message": {"model": "claude-sonnet-4-5", "usage": usage},
    }


async def _collect_chat_events(
    client: AnthropicClient,
    messages: list[Message],
    tools: list[dict[str, Any]],
    system_prompt: str = "You are a helpful assistant.",
) -> list[dict[str, Any]]:
    return [event async for event in client.chat(messages, tools, system_prompt=system_prompt)]


def test_request_mapping_and_headers(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    _install_fake_stream(
        monkeypatch, captured, [_message_start(input_tokens=10), {"type": "message_stop"}]
    )

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
            content="",
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
    assert request["url"].endswith("/v1/messages")
    assert request["headers"]["x-api-key"] == "<api-key>"
    assert request["headers"]["anthropic-version"] == "2023-06-01"

    payload = request["json"]
    assert payload["system"] == "You are a helpful assistant."
    assert payload["max_tokens"] == 8192
    assert payload["stream"] is True
    assert payload["tools"] == [
        {
            "name": "read_file",
            "description": "Read a file",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ]
    # The tool result message becomes a user message with a tool_result block.
    assert payload["messages"][2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "call_1",
                "content": [{"type": "text", "text": "file contents"}],
            }
        ],
    }
    # Assistant tool_calls become tool_use blocks with parsed argument objects.
    assert payload["messages"][1]["role"] == "assistant"
    assert payload["messages"][1]["content"] == [
        {"type": "tool_use", "id": "call_1", "name": "read_file", "input": {"path": "a.txt"}}
    ]
    # A non-empty assistant text becomes a text block ahead of the tool_use blocks.
    formatted = _client()._format_messages(
        [
            Message(
                role="assistant",
                content="Reading it now.",
                tool_calls=[
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            )
        ]
    )
    assert [block["type"] for block in formatted[0]["content"]] == ["text", "tool_use"]


def test_stream_events_conversion(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    events = [
        _message_start(input_tokens=12),
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "hmm"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        },
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "toolu_1", "name": "read_file"},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"path": "a'},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '.txt"}'},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 7},
        },
        {"type": "message_stop"},
    ]
    _install_fake_stream(monkeypatch, captured, events)

    parsed = asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], []))

    assert [event["type"] for event in parsed] == [
        "message_start",
        "thinking_delta",
        "text_delta",
        "tool_call_delta",
        "tool_call_delta",
        "tool_call_delta",
        "message_end",
        "usage",
    ]
    assert parsed[0]["model"] == "claude-sonnet-4-5"
    assert parsed[1] == {"type": "thinking_delta", "thinking": "hmm"}
    assert parsed[2] == {"type": "text_delta", "text": "Hello"}
    # content_block_start carries the tool id/name; the two argument deltas
    # concatenate into valid JSON so agent._merge_tool_delta can accumulate.
    assert parsed[3]["tool_call"]["index"] == 1
    assert parsed[3]["tool_call"]["id"] == "toolu_1"
    assert parsed[3]["tool_call"]["function"]["name"] == "read_file"
    arguments = (
        parsed[4]["tool_call"]["function"]["arguments"]
        + parsed[5]["tool_call"]["function"]["arguments"]
    )
    assert json.loads(arguments) == {"path": "a.txt"}
    assert parsed[6]["stop_reason"] == "tool_use"


def test_usage_statistics(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    events = [
        _message_start(input_tokens=100, cache_read_input_tokens=40),
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 25},
        },
        {"type": "message_stop"},
    ]
    _install_fake_stream(monkeypatch, captured, events)

    parsed = asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], []))

    assert parsed[-1]["type"] == "usage"
    usage = parsed[-1]["usage"]
    assert usage["input_tokens"] == 100
    assert usage["cache_hit_tokens"] == 40
    assert usage["output_tokens"] == 25
    # total_tokens is filled in by Usage.__post_init__.
    assert usage["total_tokens"] == 125


def test_missing_key_yields_error_event(monkeypatch) -> None:
    def fail_stream(*args, **kwargs):
        raise AssertionError("no HTTP request may be sent without an API key")

    monkeypatch.setattr(httpx.AsyncClient, "stream", fail_stream)
    client = AnthropicClient(model="claude-sonnet-4-5", api_key="")

    parsed = asyncio.run(_collect_chat_events(client, [Message(role="user", content="hi")], []))

    assert len(parsed) == 1
    assert parsed[0]["type"] == "error"
    assert "ANTHROPIC_API_KEY" in str(parsed[0]["error"])


def test_factory_dispatch_and_env_fallback(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    client = create_llm_client(LlmConfig(provider="anthropic", model="claude-sonnet-4-5"))
    assert isinstance(client, AnthropicClient)
    assert client.api_key == ""
    assert client.base_url == "https://api.anthropic.com"
    assert client.max_context_window == 200_000

    monkeypatch.setenv("ANTHROPIC_API_KEY", "<api-key>")
    client = create_llm_client(LlmConfig(provider="anthropic", model="claude-sonnet-4-5"))
    assert client.api_key == "<api-key>"

    explicit = create_llm_client(
        LlmConfig(provider="anthropic", model="claude-sonnet-4-5", api_key="<api-key>")
    )
    assert explicit.api_key == "<api-key>"
