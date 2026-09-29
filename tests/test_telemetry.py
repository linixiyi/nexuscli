"""Tests for the optional OpenTelemetry spans (``llm.chat`` / ``tool.call``).

The opentelemetry SDK ships as a dev extra: these tests drive the real SDK
through an InMemorySpanExporter (never a network exporter) and monkeypatch
``nexuscli.telemetry.tracing._sdk_available`` to simulate a machine without
the SDK installed. The httpx stub mirrors tests/test_llm_usage.py:30-43 —
no real LLM calls, no network traffic. Tool calls always pass prebuilt
variables (Mimosa false-positive discipline).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nexuscli import observability
from nexuscli.config import NexusCliConfig, TelemetryConfig, load_config
from nexuscli.llm import create_llm_client
from nexuscli.llm.openai_compatible import OpenAICompatibleClient
from nexuscli.telemetry import (
    chat_span,
    configure_tracer_provider,
    reset_tracer_provider,
    tool_call_span,
)
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry
from nexuscli.types import Message

# ---------------------------------------------------------------------------
# In-memory tracer provider fixture (assembly form fixed by the task brief:
# TracerProvider has no span_processor= argument — use add_span_processor).
# ---------------------------------------------------------------------------


@pytest.fixture
def span_exporter(monkeypatch, tmp_path) -> InMemorySpanExporter:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    # win32 Path.home() prefers USERPROFILE over HOME; set both (pattern of
    # tests/test_hooks.py:671-674) so the user config layer stays isolated.
    monkeypatch.setenv("USERPROFILE", str(home))
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    configure_tracer_provider(provider)
    previous_trace_id = observability.get_trace_id()
    yield exporter
    # Teardown must drop the injected provider (OTel's global provider can
    # only be set once per process) and restore the session trace id so the
    # module-level fallback never leaks across tests.
    reset_tracer_provider()
    observability.set_trace_id(previous_trace_id)


# ---------------------------------------------------------------------------
# httpx stub (local copy of the tests/test_llm_usage.py:30-43 patch point)
# ---------------------------------------------------------------------------

_SSE_FRAMES = [
    'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n',
    'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n',
    "data: [DONE]\n\n",
]


class _FakeResponse:
    """httpx response stub: no-op raise_for_status + SSE text stream."""

    def raise_for_status(self) -> None:
        return None

    async def aiter_text(self):
        for frame in _SSE_FRAMES:
            yield frame


class _FakeStream:
    """Async context manager returning the stub response."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _fake_stream(*_args: Any, **_kwargs: Any) -> _FakeStream:
    return _FakeStream(_FakeResponse())


# ---------------------------------------------------------------------------
# Client / executor helpers (shape per tests/test_llm_usage.py:260-267)
# ---------------------------------------------------------------------------


def _client(*, telemetry_enabled: bool = False) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        provider_name="deepseek",
        model="deepseek-v4-flash",
        api_key="<api-key>",  # placeholder discipline: never a real credential
        base_url="https://api.example.test/v1",
        telemetry_enabled=telemetry_enabled,
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


def _echo_registry() -> ToolRegistry:
    async def echo_ok(data, context):
        return ToolResult("echo ok")

    async def boom(data, context):
        return ToolResult("boom", is_error=True)

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="echo_ok",
            description="echo tool",
            parameters=object_schema({}, []),
            handler=echo_ok,
            is_read_only=True,
        )
    )
    registry.register(
        Tool(
            name="boom",
            description="boom tool",
            parameters=object_schema({}, []),
            handler=boom,
            is_read_only=True,
        )
    )
    return registry


def _run_two_tool_calls(tmp_path, *, telemetry_enabled: bool) -> list[ToolResult]:
    config = NexusCliConfig()
    config.telemetry.enabled = telemetry_enabled
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    executor = ToolExecutor(_echo_registry())
    ok_call = {"id": "call-1", "name": "echo_ok", "arguments": {}}
    boom_call = {"id": "call-2", "name": "boom", "arguments": {}}
    calls = [ok_call, boom_call]
    context = ToolContext(cwd=str(tmp_path), config=config)
    return asyncio.run(executor.execute_all(calls, context))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_telemetry_disabled_by_default(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    assert TelemetryConfig().enabled is False
    assert NexusCliConfig().telemetry.enabled is False
    # Keyword-only: a positional dict would be treated as project_root and
    # blow up in Path(...).resolve() (convention of tests/test_config.py:25-27).
    assert load_config(overrides={}, env={}).telemetry.enabled is False
    enabled = load_config(overrides={"telemetry": {"enabled": True}}, env={})
    assert enabled.telemetry.enabled is True
    # Unknown keys inside the section are silently dropped (_filter_known).
    bogus = load_config(overrides={"telemetry": {"enabled": True, "bogus": 1}}, env={})
    assert bogus.telemetry.enabled is True


def test_noop_when_sdk_missing(span_exporter, monkeypatch) -> None:
    # A test provider is already injected; the SDK-absent patch must still
    # keep every handle inert (zero recorded spans below).
    monkeypatch.setattr("nexuscli.telemetry.tracing._sdk_available", lambda: False)

    chat_handle = chat_span(True, provider="deepseek", model="deepseek-v4-flash")
    with chat_handle as span:
        span.record_outcome("ok")
    tool_handle = tool_call_span(True, tool_name="echo_ok")
    with tool_handle as span:
        span.record_outcome("error")

    assert len(span_exporter.get_finished_spans()) == 0


def test_chat_span_recorded_with_trace_id(span_exporter, monkeypatch) -> None:
    observability.set_trace_id("trace-w5")
    monkeypatch.setattr(httpx.AsyncClient, "stream", _fake_stream)

    events = asyncio.run(_collect_chat_events(_client(telemetry_enabled=True)))

    assert [event["type"] for event in events] == ["message_start", "text_delta", "usage"]

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "llm.chat"
    assert span.attributes["nexuscli.trace_id"] == "trace-w5"
    assert span.attributes["nexuscli.llm.model"] == "deepseek-v4-flash"
    assert span.attributes["nexuscli.llm.provider"] == "deepseek"


def test_factory_wiring_produces_chat_span(span_exporter, monkeypatch) -> None:
    # Ruling guard: the root-level switch must reach the client through the
    # exact argument shape every production call site uses —
    # create_llm_client(config.llm, telemetry_enabled=config.telemetry.enabled).
    observability.set_trace_id("trace-w5")
    monkeypatch.setattr(httpx.AsyncClient, "stream", _fake_stream)
    config = NexusCliConfig()
    config.llm.api_key = "<api-key>"  # placeholder discipline: never a real credential
    config.telemetry.enabled = True

    client = create_llm_client(config.llm, telemetry_enabled=config.telemetry.enabled)
    assert client.telemetry_enabled is True
    # Backward compatibility: the keyword default keeps the switch off.
    assert create_llm_client(config.llm).telemetry_enabled is False

    events = asyncio.run(_collect_chat_events(client))
    assert [event["type"] for event in events] == ["message_start", "text_delta", "usage"]

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "llm.chat"
    assert spans[0].attributes["nexuscli.trace_id"] == "trace-w5"


def test_tool_call_span_recorded_ok_and_error(span_exporter, tmp_path) -> None:
    results = _run_two_tool_calls(tmp_path, telemetry_enabled=True)
    assert [result.is_error for result in results] == [False, True]

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 2
    assert all(span.name == "tool.call" for span in spans)
    by_tool = {span.attributes["nexuscli.tool.name"]: span for span in spans}
    assert by_tool["echo_ok"].attributes["nexuscli.tool.outcome"] == "ok"
    assert by_tool["boom"].attributes["nexuscli.tool.outcome"] == "error"


def test_disabled_default_produces_no_spans(span_exporter, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(httpx.AsyncClient, "stream", _fake_stream)

    # Same chains as the recorded tests above, but with the config default:
    # telemetry.enabled is False on both the client and the tool config.
    events = asyncio.run(_collect_chat_events(_client()))
    assert [event["type"] for event in events] == ["message_start", "text_delta", "usage"]
    results = _run_two_tool_calls(tmp_path, telemetry_enabled=False)
    assert [result.is_error for result in results] == [False, True]

    assert len(span_exporter.get_finished_spans()) == 0


def test_sdk_missing_full_path_noop(span_exporter, monkeypatch, tmp_path) -> None:
    # Simulate a machine without the opentelemetry SDK: both real chains must
    # produce their normal events/results with zero exceptions and zero spans.
    monkeypatch.setattr("nexuscli.telemetry.tracing._sdk_available", lambda: False)
    monkeypatch.setattr(httpx.AsyncClient, "stream", _fake_stream)

    events = asyncio.run(_collect_chat_events(_client(telemetry_enabled=True)))
    assert [event["type"] for event in events] == ["message_start", "text_delta", "usage"]
    results = _run_two_tool_calls(tmp_path, telemetry_enabled=True)
    assert [result.is_error for result in results] == [False, True]

    assert len(span_exporter.get_finished_spans()) == 0
