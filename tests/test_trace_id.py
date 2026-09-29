"""Tests for the session-level trace id (audit records + LLM request header).

Local copies of the executor/audit fixtures mirror tests/test_hooks.py; the
httpx mock mirrors tests/test_llm_usage.py but with a success SSE stub.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from nexuscli import observability
from nexuscli.config import NexusCliConfig
from nexuscli.llm.openai_compatible import OpenAICompatibleClient
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry
from nexuscli.types import Message

# ---------------------------------------------------------------------------
# Executor fixtures (shape copied from tests/test_hooks.py:259-323)
# ---------------------------------------------------------------------------


def _registry_with_mutate_tool(executed: list[str]) -> ToolRegistry:
    registry = ToolRegistry()

    async def mutate(payload, _context):
        executed.append(str(payload["value"]))
        return ToolResult(f"mutated {payload['value']}")

    registry.register(
        Tool(
            name="mutate",
            description="Mutate test state",
            parameters=object_schema({"value": {"type": "string"}}, ["value"]),
            required_keys=["value"],
            handler=mutate,
            is_read_only=False,
            requires_approval=True,
        )
    )
    return registry


def _run_mutate(config: NexusCliConfig, context: ToolContext) -> ToolResult:
    executed: list[str] = []
    registry = _registry_with_mutate_tool(executed)
    executor = ToolExecutor(registry)
    call = {"id": "call-1", "name": "mutate", "arguments": {"value": "ok"}}
    return asyncio.run(executor.execute_all([call], context))[0]


def _mutate_config(tmp_path: Path) -> NexusCliConfig:
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    return config


def _audit_records(tmp_path: Path) -> list[dict[str, Any]]:
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert lines, "expected audit records"
    return [json.loads(line) for line in lines]


def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    # win32 Path.home() prefers USERPROFILE over HOME; set both (same pattern
    # as tests/test_permissions.py:262-265) so the real profile is never touched.
    monkeypatch.setenv("USERPROFILE", str(home))


# ---------------------------------------------------------------------------
# httpx mock (pattern from tests/test_llm_usage.py:30-49, success SSE stub)
# ---------------------------------------------------------------------------

_SSE_BODY = (
    'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n'
    "data: [DONE]\n\n"
)


class _FakeStreamResponse:
    """Async-context-manager response stub with an SSE text stream."""

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    async def __aenter__(self) -> _FakeStreamResponse:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk


def _capture_stream(captured: dict[str, Any]):
    def fake_stream(_self, method, url, *, headers=None, json=None, **_kwargs):
        captured.update({"method": method, "url": url, "headers": headers, "json": json})
        return _FakeStreamResponse([_SSE_BODY])

    return fake_stream


async def _collect_chat_events(client: OpenAICompatibleClient) -> list[dict[str, Any]]:
    return [
        event
        async for event in client.chat(
            [Message(role="user", content="hello")],
            [],
            system_prompt="system",
        )
    ]


def _client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        provider_name="deepseek",
        model="deepseek-v4-flash",
        api_key="<api-key>",  # placeholder discipline: never a real credential
        base_url="https://api.example.test/v1",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_trace_id_stable_within_session_and_lazy_generated() -> None:
    # Isolate from any id an earlier test's executor run may have generated.
    observability.set_trace_id("")
    first = observability.get_trace_id()
    assert first == observability.get_trace_id()
    assert observability.get_trace_id() == first

    fresh = observability.new_trace_id()
    assert len(fresh) == 16
    assert all(char in "0123456789abcdef" for char in fresh)
    assert fresh != observability.get_trace_id()

    assert observability.set_trace_id("fixed123") == "fixed123"
    assert observability.get_trace_id() == "fixed123"

    async def read() -> str:
        return observability.get_trace_id()

    async def gather_both() -> tuple[str, str]:
        # Tasks copy the caller's context; the ContextVar holds "" here, so
        # the module-level fallback is what keeps both readers consistent —
        # the concurrency face of "same session, same id".
        return await asyncio.gather(read(), read())

    observability.set_trace_id("")
    sibling_a, sibling_b = asyncio.run(gather_both())
    assert sibling_a == sibling_b


def test_audit_records_carry_trace_id(tmp_path, monkeypatch) -> None:
    _isolate_home(tmp_path, monkeypatch)
    observability.set_trace_id("trace-abc")
    config = _mutate_config(tmp_path)
    result = _run_mutate(config, ToolContext(cwd=str(tmp_path), config=config))
    assert result.is_error  # requires_approval without callback → deny

    records = _audit_records(tmp_path)
    assert records
    for record in records:
        # New field present on every record...
        assert record["trace_id"] == "trace-abc"
        # ...while the pre-existing fields survive (backward-compat anchor).
        assert record["tool_name"] == "mutate"
        assert record["outcome"]
        assert record["approver"]
        assert record["cwd"]
        assert record["timestamp"]


def test_llm_request_carries_same_trace_id(monkeypatch, tmp_path) -> None:
    _isolate_home(tmp_path, monkeypatch)
    observability.set_trace_id("trace-abc")
    captured: dict[str, Any] = {}
    monkeypatch.setattr(httpx.AsyncClient, "stream", _capture_stream(captured))

    events = asyncio.run(_collect_chat_events(_client()))

    assert not any(event["type"] == "error" for event in events)
    assert captured["headers"]["x-request-id"] == "trace-abc"
    assert captured["headers"]["authorization"] == "Bearer <api-key>"


def test_audit_and_llm_share_id_end_to_end(tmp_path, monkeypatch) -> None:
    _isolate_home(tmp_path, monkeypatch)
    observability.set_trace_id("trace-end2end")

    # One tool call through the executor (lands in the audit log)...
    config = _mutate_config(tmp_path)
    _run_mutate(config, ToolContext(cwd=str(tmp_path), config=config))
    # ...then one chat call with the captured request headers.
    captured: dict[str, Any] = {}
    monkeypatch.setattr(httpx.AsyncClient, "stream", _capture_stream(captured))
    events = asyncio.run(_collect_chat_events(_client()))
    assert not any(event["type"] == "error" for event in events)

    audit_trace = _audit_records(tmp_path)[0]["trace_id"]
    header_trace = captured["headers"]["x-request-id"]
    assert audit_trace == header_trace == "trace-end2end"


def test_no_trace_id_header_when_empty(monkeypatch) -> None:
    # Explicitly empty id (monkeypatched; production get_trace_id always
    # lazily generates a non-empty one) must not emit the header at all.
    monkeypatch.setattr("nexuscli.llm.openai_compatible.get_trace_id", lambda: "")
    captured: dict[str, Any] = {}
    monkeypatch.setattr(httpx.AsyncClient, "stream", _capture_stream(captured))

    asyncio.run(_collect_chat_events(_client()))

    assert "x-request-id" not in captured["headers"]
