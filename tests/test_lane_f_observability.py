"""Lane F (observability) user-visible-behaviour tests — testing-matrix 13.1-13.4.

These complement the unit suites (test_trace_id.py, test_telemetry.py,
test_llm_io_dump.py, test_usage_coverage.py) by asserting what a real user
gets, at the scale the matrix rows describe:

- 13.1 one request's log lines all carry the *same* trace id, with no id
  pinned by hand (the real-user flow: the id is generated lazily and
  correlates audit records with the LLM request header automatically).
- 13.2 telemetry enabled, real SDK present, but **no collector/exporter
  configured** — the honest boundary: the turn still completes, spans stay
  non-recording, nothing pretends to export.
- 13.3 ``llm.debug_dump`` on via real config loading → the factory wires it,
  and the dump line actually shows what the request looked like (the user's
  message text is findable in the payload).
- 13.4 ``/usage stats`` data survives a process restart (fresh UsageStore
  instance over the same db file) and the rendered table shows the recorded
  numbers.

No real network in machine tests: httpx.stream is stubbed with a success SSE
body (same pattern as tests/test_telemetry.py:67-99).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from rich.console import Console

from nexuscli import observability
from nexuscli.config import load_config
from nexuscli.entrypoints.commands.usage import _usage_stats_command
from nexuscli.llm import create_llm_client
from nexuscli.llm.openai_compatible import OpenAICompatibleClient
from nexuscli.llm.usage_store import UsageStore
from nexuscli.telemetry import chat_span, reset_tracer_provider
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry
from nexuscli.types import Message, Usage

# ---------------------------------------------------------------------------
# Shared httpx stub (success SSE body, pattern of tests/test_telemetry.py)
# ---------------------------------------------------------------------------

_SSE_BODY = (
    'data: {"choices":[{"delta":{"content":"done"}}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":11,"completion_tokens":7,"total_tokens":18}}\n\n'
    "data: [DONE]\n\n"
)


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    async def aiter_text(self):
        yield _SSE_BODY


class _FakeStream:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _stub_httpx_stream(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    response = _FakeResponse()

    def fake_stream(_self, method, url, *, headers=None, json=None, **_kwargs):
        captured.update({"method": method, "url": url, "headers": headers, "json": json})
        return _FakeStream(response)

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)


async def _collect_chat_events(client: OpenAICompatibleClient) -> list[dict[str, Any]]:
    return [
        event
        async for event in client.chat(
            [Message(role="user", content="hello")], [], system_prompt="system"
        )
    ]


# ---------------------------------------------------------------------------
# 13.1 trace_id: one turn, every log line, same id, nothing pinned by hand
# ---------------------------------------------------------------------------


def _registry_with_two_write_tools(executed: list[str]) -> ToolRegistry:
    """Two non-read-only tools so the success path lands in the audit log
    (executor.py only audits non-read-only calls on the allow path)."""

    def make_handler(tag: str):
        async def handler(payload, _context):
            executed.append(tag)
            return ToolResult(f"{tag} ok")

        return handler

    registry = ToolRegistry()
    for tag in ("step_one", "step_two"):
        registry.register(
            Tool(
                name=tag,
                description=f"{tag} tool",
                parameters=object_schema({}, []),
                handler=make_handler(tag),
                is_read_only=False,
                requires_approval=False,
            )
        )
    return registry


def test_one_turn_all_log_lines_share_one_auto_trace_id(tmp_path, monkeypatch) -> None:
    """No manual id: one turn's audit records + the LLM request header all
    carry the same lazily generated id (the real-user correlation flow)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    observability.set_trace_id("")  # fresh process: nothing pinned yet

    config = load_config(project_root=tmp_path)
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.policy.hitl_mode = "never"
    executed: list[str] = []
    executor = ToolExecutor(_registry_with_two_write_tools(executed))
    context = ToolContext(cwd=str(tmp_path), config=config)
    calls = [
        {"id": "call-1", "name": "step_one", "arguments": {}},
        {"id": "call-2", "name": "step_two", "arguments": {}},
    ]
    results = asyncio.run(executor.execute_all(calls, context))
    assert [r.content for r in results] == ["step_one ok", "step_two ok"]

    records = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 2, "both successful tool calls must be audited"
    ids = {record["trace_id"] for record in records}
    assert len(ids) == 1
    trace_id = ids.pop()
    assert len(trace_id) == 16 and all(c in "0123456789abcdef" for c in trace_id)

    # The LLM request of the same session reuses the audit id (x-request-id).
    captured: dict[str, Any] = {}
    _stub_httpx_stream(monkeypatch, captured)
    client = OpenAICompatibleClient(
        provider_name="openai-compatible",
        model="cbcn/glm-5.3-flash",
        api_key="placeholder-key",
        base_url="https://api.example.test/v1",
    )
    events = asyncio.run(_collect_chat_events(client))
    assert not any(event["type"] == "error" for event in events)
    assert captured["headers"]["x-request-id"] == trace_id


# ---------------------------------------------------------------------------
# 13.2 OTel span: enabled + real SDK + no collector → honest no-export boundary
# ---------------------------------------------------------------------------


def test_telemetry_enabled_without_collector_turn_completes_and_exports_nothing(
    tmp_path, monkeypatch
) -> None:
    """The real-user boundary: telemetry.enabled=true and the SDK installed,
    but no TracerProvider/exporter configured (no collector running). The
    chat must complete untouched and no span may pretend to be exported."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    reset_tracer_provider()  # production state: global OTel API, no collector

    handle = chat_span(True, provider="openai-compatible", model="cbcn/glm-5.3-flash")
    from opentelemetry.trace import NonRecordingSpan

    with handle as span:
        # No provider → the default tracer hands out NonRecordingSpan: the
        # span exists in the API path but records/exports nothing.
        assert isinstance(span._span, NonRecordingSpan)
        assert not span._span.is_recording()
        span.record_outcome("ok")  # must be a silent no-op, not an error
    assert handle._span is None

    # The same configuration through a full chat turn: telemetry on, no
    # collector — the turn completes with the normal event sequence.
    captured: dict[str, Any] = {}
    _stub_httpx_stream(monkeypatch, captured)
    client = OpenAICompatibleClient(
        provider_name="openai-compatible",
        model="cbcn/glm-5.3-flash",
        api_key="placeholder-key",
        base_url="https://api.example.test/v1",
        telemetry_enabled=True,
    )
    events = asyncio.run(_collect_chat_events(client))
    assert [event["type"] for event in events] == [
        "message_start",
        "text_delta",
        "message_end",
        "usage",
    ]


# ---------------------------------------------------------------------------
# 13.3 llm.debug_dump: config wiring + the dump shows the real request shape
# ---------------------------------------------------------------------------

_DUMP_MARKER = "lane-f-dump-marker-9f31"


def _write_project_config(tmp_path: Path, llm_overrides: dict[str, Any]) -> None:
    (tmp_path / ".nexuscli").mkdir(exist_ok=True)
    config = {
        "llm": {
            "provider": "openai-compatible",
            "base_url": "https://api.example.test/v1",
            "api_key": "placeholder-key",
            "model": "cbcn/glm-5.3-flash",
            **llm_overrides,
        }
    }
    (tmp_path / ".nexuscli" / "config.json").write_text(json.dumps(config), encoding="utf-8")


def test_debug_dump_from_config_wiring_and_dump_shows_request(tmp_path, monkeypatch) -> None:
    """Turning llm.debug_dump on in the real config file must reach the client
    (factory wiring) and the on-disk dump must let you see what the request
    looked like — the user's message text is in the payload."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    _write_project_config(tmp_path, {"debug_dump": True})

    config = load_config(project_root=tmp_path)
    assert config.llm.debug_dump is True
    client = create_llm_client(config.llm)
    assert isinstance(client, OpenAICompatibleClient)
    assert client.debug_dump is True

    _stub_httpx_stream(monkeypatch, {})
    events = asyncio.run(_chat_with_marker(client))
    assert not any(event["type"] == "error" for event in events)

    dump_path = (
        tmp_path / "home" / ".nexuscli" / "debug" / "llm" / f"{datetime.now():%Y-%m-%d}.jsonl"
    )
    records = [
        json.loads(line) for line in dump_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [r["direction"] for r in records] == ["request", "response"]
    serialized = json.dumps(records[0]["payload"], ensure_ascii=False)
    assert _DUMP_MARKER in serialized, "the request dump must show the user's message"
    assert records[0]["headers"]["authorization"] == "<redacted>"
    assert records[1]["text"] == "done"
    assert records[1]["usage"]["total_tokens"] == 18


async def _chat_with_marker(client: OpenAICompatibleClient) -> list[dict[str, Any]]:
    return [
        event
        async for event in client.chat(
            [Message(role="user", content=f"排查用 {_DUMP_MARKER}")],
            [],
            system_prompt="system",
        )
    ]


# ---------------------------------------------------------------------------
# 13.4 /usage stats: persistence across a restart + rendered numbers
# ---------------------------------------------------------------------------


def test_usage_stats_survive_restart_and_render_recorded_numbers(tmp_path, monkeypatch) -> None:
    """A turn is recorded, the process 'restarts' (brand-new UsageStore over
    the same db file), and /usage stats still shows the same numbers."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    db = tmp_path / ".nexuscli" / "usage.db"

    # Process 1: one completed turn lands in the store.
    first = UsageStore(path=db)
    first.record_turn(
        model="cbcn/glm-5.3-flash",
        provider="openai-compatible",
        usage=Usage(input_tokens=100, output_tokens=50, total_tokens=150),
        cost={"usd": {"total_cost": 0.0123}, "cny": {"total_cost": 0.088}},
    )

    # Process 2 (restart): a fresh store over the same file must see the row.
    restarted = UsageStore(path=db)
    stats = restarted.stats(days=7)
    assert stats["turns"] == 1
    assert stats["total_tokens"] == 150
    assert stats["total_cost"]["usd"] == pytest.approx(0.0123)
    assert stats["by_model"][0]["model"] == "cbcn/glm-5.3-flash"

    # And the /usage stats table the user sees renders those numbers.
    console = Console(record=True, width=140)
    _usage_stats_command("7", console)
    rendered = console.export_text()
    assert "cbcn/glm-5.3-flash" in rendered
    assert "150" in rendered
