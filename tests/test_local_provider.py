"""Tests for the local LLM gateway clients (provider="ollama"/"llamacpp"/"local").

Every test drives real HTTP against a threaded fake gateway bound to a random
loopback port (127.0.0.1:0) — no test reaches the real network, and the only
credential literal below is the ``<api-key>`` placeholder. Real HTTP rather
than a stubbed httpx transport is deliberate: the assertions include exact
request headers (no ``Authorization`` without a configured key) and the SSE
wire path local servers (Ollama / llama.cpp) actually speak.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from nexuscli.config import LlmConfig
from nexuscli.llm.factory import (
    LLAMACPP_BASE_URL,
    LOCAL_FALLBACK_CONTEXT_WINDOW,
    OLLAMA_BASE_URL,
    create_llm_client,
)
from nexuscli.llm.local import LocalProviderClient
from nexuscli.llm.openai_compatible import ModelFetchError
from nexuscli.types import Message

# One text delta per chunk, then finish + usage on the final chunk (the shape
# Ollama's /v1 endpoint emits).
_DEFAULT_CHAT_CHUNKS = [
    {"choices": [{"delta": {"content": "Hello "}}]},
    {"choices": [{"delta": {"content": "world"}}]},
    {
        "choices": [{"delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11},
    },
]


class _GatewayHandler(BaseHTTPRequestHandler):
    """OpenAI-compatible wire behaviour: SSE streaming + JSON non-streaming."""

    def do_GET(self) -> None:
        gateway: _FakeGateway = self.server  # type: ignore[assignment]
        gateway.requests.append(
            {
                "method": "GET",
                "path": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "payload": {},
            }
        )
        if self.path.split("?")[0].rstrip("/") == "/v1/models":
            self._send_json(200, gateway.models_payload)
        else:
            self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        gateway: _FakeGateway = self.server  # type: ignore[assignment]
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}") if length else {}
        gateway.requests.append(
            {
                "method": "POST",
                "path": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "payload": payload,
            }
        )
        if self.path.split("?")[0].rstrip("/") != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": "not found"}})
            return
        if payload.get("stream") is False:
            # The non-streaming replay path parses a plain completion JSON.
            self._send_json(200, gateway.completion)
            return
        if gateway.fail_streaming_once:
            gateway.fail_streaming_once = False
            self._send_json(503, {"error": {"code": 503, "message": "overloaded"}})
            return
        self._send_sse(gateway.chat_chunks)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, chunks: list[dict[str, Any]]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args: object) -> None:
        pass


class _FakeGateway(ThreadingHTTPServer):
    """One-shot OpenAI-compatible gateway on a random loopback port."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _GatewayHandler)
        self.requests: list[dict[str, Any]] = []
        self.chat_chunks: list[dict[str, Any]] = list(_DEFAULT_CHAT_CHUNKS)
        self.models_payload: dict[str, Any] = {
            "object": "list",
            "data": [
                {"id": "qwen2.5-coder:latest", "object": "model"},
                {"id": "llama3.2:latest", "object": "model"},
            ],
        }
        self.completion: dict[str, Any] = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "fallback reply"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
        }
        self.fail_streaming_once = False

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/v1"


@pytest.fixture()
def gateway():
    server = _FakeGateway()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def refused_port():
    """A bound-but-never-listening loopback port: connects are refused.

    The socket stays open (bind without listen) for the fixture's lifetime,
    so no other process can claim the port mid-test.
    """
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


def _client(gateway: _FakeGateway, **overrides: Any) -> LocalProviderClient:
    kwargs: dict[str, Any] = {
        "provider_name": "ollama",
        "model": "qwen2.5-coder:latest",
        "api_key": "",
        "base_url": gateway.base_url,
    }
    kwargs.update(overrides)
    return LocalProviderClient(**kwargs)


async def _collect_chat_events(
    client: LocalProviderClient,
    messages: list[Message] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    return [
        event
        async for event in client.chat(
            messages if messages is not None else [Message(role="user", content="hi")],
            tools or [],
            system_prompt="You are a helpful assistant.",
        )
    ]


# ---------------------------------------------------------------------------
# Factory dispatch: defaults, alias, context window, pricing seam.
# ---------------------------------------------------------------------------


def test_factory_defaults_for_ollama_and_llamacpp(monkeypatch) -> None:
    monkeypatch.delenv("NEXUSCLI_OFFLINE_DEMO", raising=False)

    client = create_llm_client(LlmConfig(provider="ollama", model="qwen2.5-coder:latest"))
    assert isinstance(client, LocalProviderClient)
    assert client.base_url == OLLAMA_BASE_URL
    # No key configured → the factory leaves api_key empty (the client must
    # then send no Authorization header).
    assert client.api_key == ""
    assert client.max_context_window == LOCAL_FALLBACK_CONTEXT_WINDOW
    # Local models never inherit cloud builtin prices — a name collision
    # ("deepseek-coder" on Ollama) must not fabricate costs.
    assert client.price_profile is None

    llama = create_llm_client(LlmConfig(provider="llamacpp", model="qwen2.5-coder:latest"))
    assert isinstance(llama, LocalProviderClient)
    assert llama.base_url == LLAMACPP_BASE_URL

    priced = create_llm_client(
        LlmConfig(
            provider="ollama",
            model="qwen2.5-coder:latest",
            prices={"usd": {"input_cache_hit": 0.0, "input_cache_miss": 0.05, "output": 0.1}},
        )
    )
    assert priced.price_profile is not None

    overridden = create_llm_client(
        LlmConfig(
            provider="ollama",
            model="qwen2.5-coder:latest",
            base_url="http://127.0.0.1:9999/v1",
            context_window=8192,
        )
    )
    assert overridden.base_url == "http://127.0.0.1:9999/v1"
    assert overridden.max_context_window == 8192


def test_factory_local_alias_requires_base_url(monkeypatch) -> None:
    monkeypatch.delenv("NEXUSCLI_OFFLINE_DEMO", raising=False)

    # Endpoint-ambiguous alias: no default endpoint to guess.
    with pytest.raises(ValueError, match="base_url"):
        create_llm_client(LlmConfig(provider="local", model="m"))

    unified = create_llm_client(
        LlmConfig(provider="local", model="m", base_url="http://127.0.0.1:8000/v1")
    )
    assert isinstance(unified, LocalProviderClient)
    assert unified.base_url == "http://127.0.0.1:8000/v1"
    assert unified.api_key == ""


# ---------------------------------------------------------------------------
# chat(): real HTTP against the fake gateway.
# ---------------------------------------------------------------------------


def test_chat_without_key_hits_gateway_and_omits_authorization(gateway) -> None:
    events = asyncio.run(_collect_chat_events(_client(gateway)))

    request = gateway.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert "authorization" not in request["headers"]
    assert request["headers"]["content-type"] == "application/json"
    payload = request["payload"]
    assert payload["model"] == "qwen2.5-coder:latest"
    assert payload["messages"][0] == {
        "role": "system",
        "content": "You are a helpful assistant.",
    }
    assert payload["stream"] is True

    assert [event["type"] for event in events] == [
        "message_start",
        "text_delta",
        "text_delta",
        "message_end",
        "usage",
    ]
    assert events[1] == {"type": "text_delta", "text": "Hello "}
    assert events[2] == {"type": "text_delta", "text": "world"}
    assert events[3] == {"type": "message_end", "stop_reason": "end_turn"}
    assert events[4]["usage"]["total_tokens"] == 11


def test_chat_with_key_sends_bearer_and_round_trips_tools(gateway) -> None:
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
    gateway.chat_chunks = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path": "a.txt"}',
                                },
                            }
                        ]
                    }
                }
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]

    events = asyncio.run(_collect_chat_events(_client(gateway, api_key="<api-key>"), tools=tools))

    request = gateway.requests[0]
    # A configured key IS sent as a bearer token (Ollama auth / proxy cases).
    assert request["headers"]["authorization"] == "Bearer <api-key>"
    assert request["payload"]["tools"] == tools
    assert request["payload"]["tool_choice"] == "auto"

    assert [event["type"] for event in events] == [
        "message_start",
        "tool_call_delta",
        "message_end",
    ]
    assert events[1]["tool_call"]["id"] == "call_1"
    assert events[1]["tool_call"]["function"]["name"] == "read_file"
    assert events[1]["tool_call"]["function"]["arguments"] == '{"path": "a.txt"}'
    assert events[-1] == {"type": "message_end", "stop_reason": "tool_use"}


def test_connection_failure_yields_readable_hint(refused_port) -> None:
    client = LocalProviderClient(
        provider_name="ollama",
        model="qwen2.5-coder:latest",
        api_key="",
        base_url=f"http://127.0.0.1:{refused_port}/v1",
    )

    events = asyncio.run(_collect_chat_events(client))

    assert [event["type"] for event in events] == ["message_start", "error"]
    message = str(events[-1]["error"])
    assert f"http://127.0.0.1:{refused_port}/v1" in message
    assert "ollama serve" in message


def test_transient_streaming_failure_replays_non_streaming(gateway) -> None:
    # The built-in OpenAI-compatible fallback must survive the local subclass
    # untouched: one pre-event 503 → one stream:false replay, same events.
    gateway.fail_streaming_once = True

    events = asyncio.run(_collect_chat_events(_client(gateway)))

    assert len(gateway.requests) == 2
    first, second = gateway.requests
    assert first["payload"]["stream"] is True
    assert second["payload"]["stream"] is False
    assert "stream_options" not in second["payload"]
    assert [event["type"] for event in events] == [
        "message_start",
        "text_delta",
        "message_end",
        "usage",
    ]
    assert events[1] == {"type": "text_delta", "text": "fallback reply"}
    assert events[-1]["usage"]["total_tokens"] == 9


# ---------------------------------------------------------------------------
# Model listing probe: no key, readable failure.
# ---------------------------------------------------------------------------


def test_list_models_without_key(gateway) -> None:
    ids = asyncio.run(_client(gateway).list_models())

    assert ids == ["llama3.2:latest", "qwen2.5-coder:latest"]
    request = gateway.requests[0]
    assert request["path"] == "/v1/models"
    assert "authorization" not in request["headers"]


def test_list_models_connection_failure_is_readable(refused_port) -> None:
    client = LocalProviderClient(
        provider_name="llamacpp",
        model="qwen2.5-coder:latest",
        api_key="",
        base_url=f"http://127.0.0.1:{refused_port}/v1",
    )

    with pytest.raises(ModelFetchError) as excinfo:
        asyncio.run(client.list_models())

    reason = excinfo.value.reason
    assert f"http://127.0.0.1:{refused_port}/v1" in reason
    assert "llama-server" in reason
