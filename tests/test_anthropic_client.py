"""Tests for the native Anthropic Messages API client.

Every HTTP interaction is mocked (httpx.AsyncClient.stream is replaced);
no test in this file performs a real network request. The only credential
literal allowed anywhere below is the ``<api-key>`` placeholder.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from nexuscli.agent import Agent
from nexuscli.agent.agent import MAX_TOKENS_TOOL_RESULT_MESSAGE
from nexuscli.config import LlmConfig, load_config
from nexuscli.llm.anthropic import AnthropicClient
from nexuscli.llm.factory import create_llm_client
from nexuscli.tools import ToolRegistry
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


class _BreakingStreamResponse(_FakeStreamResponse):
    """Streams a frame prefix, then the transport drops mid-stream."""

    def __init__(self, prefix: list[dict[str, Any]], exc: Exception) -> None:
        super().__init__(prefix)
        self._exc = exc

    async def aiter_text(self) -> AsyncIterator[str]:
        async for frame in super().aiter_text():
            yield frame
        raise self._exc


def _install_fake_stream(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> None:
    def fake_stream(client, method, url, headers=None, json=None, **kwargs):
        captured.append({"method": method, "url": url, "headers": headers, "json": json})
        return _FakeStreamResponse(events)

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)


def _install_scripted_stream(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[dict[str, Any]],
    attempts: list[Any],
) -> dict[str, int]:
    """Per-attempt script for the retry tests.

    Each entry describes one HTTP attempt: an exception instance (raised
    when the attempt starts), a ``(prefix_events, exception)`` tuple
    (frames streamed, then the transport drops mid-stream), or an event
    list (a successful attempt). Extra attempts beyond the script reuse
    the last entry.
    """
    state = {"count": 0}

    def fake_stream(client, method, url, headers=None, json=None, **kwargs):
        index = state["count"]
        state["count"] += 1
        captured.append({"method": method, "url": url, "headers": headers, "json": json})
        script = attempts[min(index, len(attempts) - 1)]
        if isinstance(script, Exception):
            raise script
        if isinstance(script, tuple):
            prefix, exc = script
            return _BreakingStreamResponse(prefix, exc)
        return _FakeStreamResponse(script)

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    return state


def _patch_sleep(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    """Record retry backoff delays instead of actually waiting."""

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("nexuscli.llm.anthropic.asyncio.sleep", fake_sleep)


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


async def _collect_agent_events(agent: Agent, message: str) -> list[dict[str, Any]]:
    return [event async for event in agent.run(message)]


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
    # F12 prompt caching: the system prompt travels as one ephemeral-tagged
    # text block; the (single) tool entry carries the ephemeral breakpoint.
    assert payload["system"] == [
        {
            "type": "text",
            "text": "You are a helpful assistant.",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert payload["max_tokens"] == 8192
    assert payload["stream"] is True
    assert payload["tools"] == [
        {
            "name": "read_file",
            "description": "Read a file",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            "cache_control": {"type": "ephemeral"},
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


# ---------------------------------------------------------------------------
# F12: transport-level stream retry (4 attempts, 1/2/4s backoff; semantic
# errors never retry; retry attempts publish nothing until they succeed).
# ---------------------------------------------------------------------------


def _success_events() -> list[dict[str, Any]]:
    return [
        _message_start(input_tokens=5),
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 2},
        },
        {"type": "message_stop"},
    ]


def test_transport_drop_retries_once_and_publishes_final_attempt_once(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    sleeps: list[float] = []
    _patch_sleep(monkeypatch, sleeps)
    _install_scripted_stream(
        monkeypatch,
        captured,
        [
            httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body"
            ),
            _success_events(),
        ],
    )

    parsed = asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], []))

    assert len(captured) == 2
    assert sleeps == [1.0]
    types = [event["type"] for event in parsed]
    # The successful attempt's full sequence is replayed exactly once:
    # message_end/usage appear once, its deltas are not duplicated.
    assert types == ["message_start", "text_delta", "message_end", "usage"]
    assert types.count("message_end") == 1
    assert types.count("usage") == 1


def test_retry_backoff_sequence_three_drops_then_success(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    sleeps: list[float] = []
    _patch_sleep(monkeypatch, sleeps)
    _install_scripted_stream(
        monkeypatch,
        captured,
        [
            httpx.RemoteProtocolError("drop 1"),
            httpx.ReadError("drop 2"),
            httpx.ConnectError("drop 3"),
            _success_events(),
        ],
    )

    parsed = asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], []))

    # Reviewed parameters: 4 attempts so the documented 1/2/4s backoff
    # sequence is fully reachable.
    assert len(captured) == 4
    assert sleeps == [1.0, 2.0, 4.0]
    assert [event["type"] for event in parsed].count("message_end") == 1


def test_retry_exhausted_surfaces_existing_request_error(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    sleeps: list[float] = []
    _patch_sleep(monkeypatch, sleeps)
    _install_scripted_stream(
        monkeypatch,
        captured,
        [
            httpx.RemoteProtocolError("drop 1"),
            httpx.RemoteProtocolError("drop 2"),
            httpx.RemoteProtocolError("drop 3"),
            httpx.RemoteProtocolError("drop 4"),
        ],
    )

    parsed = asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], []))

    assert len(captured) == 4
    assert sleeps == [1.0, 2.0, 4.0]
    assert len(parsed) == 1
    assert parsed[0]["type"] == "error"
    assert "Could not connect to anthropic" in str(parsed[0]["error"])


def test_semantic_errors_are_never_retried(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    sleeps: list[float] = []
    _patch_sleep(monkeypatch, sleeps)
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(500, request=request)
    _install_scripted_stream(
        monkeypatch,
        captured,
        [
            httpx.HTTPStatusError(
                "Server error '500 Internal Server Error'", request=request, response=response
            )
        ],
    )

    parsed = asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], []))

    # HTTP status errors are semantic failures: one attempt, no backoff.
    assert len(captured) == 1
    assert sleeps == []
    assert parsed[0]["type"] == "error"

    # Timeouts surface immediately too.
    timeout_captured: list[dict[str, Any]] = []
    _install_scripted_stream(monkeypatch, timeout_captured, [httpx.ReadTimeout("timed out")])
    parsed = asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], []))
    assert len(timeout_captured) == 1
    assert sleeps == []
    assert parsed[0]["type"] == "error"


def test_failed_retry_attempt_events_are_never_published(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    sleeps: list[float] = []
    _patch_sleep(monkeypatch, sleeps)
    prefix = [
        _message_start(input_tokens=5),
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}},
    ]
    success_events = [
        _message_start(input_tokens=5),
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": " there"},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 3},
        },
        {"type": "message_stop"},
    ]
    _install_scripted_stream(
        monkeypatch,
        captured,
        [
            (prefix, httpx.RemoteProtocolError("drop after prefix")),
            (
                [
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "DISCARD"},
                    }
                ],
                httpx.ReadError("drop again"),
            ),
            success_events,
        ],
    )

    parsed = asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], []))

    texts = [event.get("text") for event in parsed if event["type"] == "text_delta"]
    # Attempt-1's prefix was published live; the failed attempt-2's delta
    # never leaks (silent collection); the final attempt replays its full
    # text — the documented display jump (prefix followed by the complete
    # message) is the accepted tradeoff.
    assert texts == ["Hi", "Hi", " there"]
    assert "DISCARD" not in texts
    types = [event["type"] for event in parsed]
    assert types.count("message_end") == 1
    assert types.count("usage") == 1
    assert sleeps == [1.0, 2.0]


# ---------------------------------------------------------------------------
# F12: prompt caching.
# ---------------------------------------------------------------------------


def test_cache_control_on_system_and_last_tool(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    _install_fake_stream(
        monkeypatch, captured, [_message_start(input_tokens=1), {"type": "message_stop"}]
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Run a command",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], tools))

    payload = captured[0]["json"]
    assert payload["system"] == [
        {
            "type": "text",
            "text": "You are a helpful assistant.",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert [tool["name"] for tool in payload["tools"]] == ["read_file", "bash"]
    # Only the final tool definition carries the cache breakpoint.
    assert "cache_control" not in payload["tools"][0]
    assert payload["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    # The caller's tool dicts are not mutated.
    assert "cache_control" not in tools[1]


def test_usage_cache_creation_maps_to_cache_miss(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    events = [
        _message_start(
            input_tokens=100,
            cache_creation_input_tokens=30,
            cache_read_input_tokens=40,
        ),
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 25},
        },
        {"type": "message_stop"},
    ]
    _install_fake_stream(monkeypatch, captured, events)

    parsed = asyncio.run(_collect_chat_events(_client(), [Message(role="user", content="hi")], []))

    usage = parsed[-1]["usage"]
    assert usage["cache_hit_tokens"] == 40
    # cache_creation_input_tokens (cache writes) maps onto cache_miss_tokens.
    assert usage["cache_miss_tokens"] == 30
    assert usage["input_tokens"] == 100
    assert usage["total_tokens"] == 125


# ---------------------------------------------------------------------------
# F12: thinking blocks passed back verbatim (signature included).
# ---------------------------------------------------------------------------


def test_assistant_thinking_blocks_passed_back_verbatim() -> None:
    thinking = [{"type": "thinking", "thinking": "check the path first", "signature": "sig-abc123"}]
    # Message gains the `thinking_blocks` field in a follow-up round (outside
    # this lane's file allowlist); a stand-in with the same attribute shape
    # exercises the client-side pass-through mechanism.
    message = SimpleNamespace(
        role="assistant",
        content="Reading it now.",
        tool_calls=[],
        thinking_blocks=thinking,
    )

    formatted = _client()._format_messages([message])

    assert formatted[0]["role"] == "assistant"
    # Verbatim at the head of the assistant content, before the text block.
    assert formatted[0]["content"] == [
        {"type": "thinking", "thinking": "check the path first", "signature": "sig-abc123"},
        {"type": "text", "text": "Reading it now."},
    ]

    with_tool_call = SimpleNamespace(
        role="assistant",
        content="",
        tool_calls=[
            {
                "id": "call_9",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }
        ],
        thinking_blocks=thinking,
    )
    formatted = _client()._format_messages([with_tool_call])
    assert [block["type"] for block in formatted[0]["content"]] == ["thinking", "tool_use"]
    assert formatted[0]["content"][0] == thinking[0]

    # Plain assistant messages (no thinking_blocks attribute) keep the
    # existing shape: a bare text block.
    formatted = _client()._format_messages([Message(role="assistant", content="plain")])
    assert formatted[0]["content"] == "plain"


# ---------------------------------------------------------------------------
# F12: max_tokens cutoff inside a tool_use block — the react loop
# synthesizes is_error tool_results instead of executing truncated calls.
# ---------------------------------------------------------------------------


class _MaxTokensThenEndClient:
    """Fake LLM: turn 1 cuts off mid tool-call (stop_reason=max_tokens),
    turn 2 finishes naturally."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.calls += 1
        if self.calls == 1:
            yield {
                "type": "tool_call_delta",
                "tool_call": {
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "read_file", "arguments": '{"path": "no'},
                },
            }
            yield {"type": "message_end", "stop_reason": "max_tokens"}
        else:
            yield {"type": "text_delta", "text": "recovered"}
            yield {"type": "message_end", "stop_reason": "end_turn"}


def test_react_loop_synthesizes_error_results_on_max_tokens_tool_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUSCLI_API_KEY", "test-key")
    home = tmp_path / "home"
    (home / ".nexuscli").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("nexuscli.config._home", lambda: home)
    config = load_config(project_root=tmp_path)
    config.features.context_compression = False

    agent = Agent(
        llm_client=_MaxTokensThenEndClient(),
        tool_registry=ToolRegistry(),
        config=config,
        cwd=str(tmp_path),
        max_turns=3,
    )

    events = asyncio.run(_collect_agent_events(agent, "read a file"))

    tool_results = [event for event in events if event["type"] == "tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0]["name"] == "read_file"
    assert tool_results[0]["is_error"] is True
    # The synthetic message proves the truncated call was never executed:
    # an executor run (even failing on the empty registry) would produce a
    # different result content.
    assert tool_results[0]["result"] == MAX_TOKENS_TOOL_RESULT_MESSAGE
    # The loop continues after synthesis and the second turn completes.
    done = events[-1]
    assert done["type"] == "done"
    assert done["total_turns"] == 2
    # Pairing invariant: the assistant tool_use keeps its paired tool_result
    # message in history.
    roles = [(item.role, item.tool_call_id) for item in agent.history]
    assert roles == [
        ("user", None),
        ("assistant", None),
        ("tool", "call_1"),
        ("assistant", None),
    ]
    assert agent.history[1].tool_calls[0]["id"] == "call_1"
