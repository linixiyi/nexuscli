"""Parity tests for the provider context-overflow retry and the F12
``max_tokens`` truncation protection across the two tool-loop implementations:
the react loop (``Agent._run_react``) and the standalone query path
(``agent.query.query``, used by plan/team workers).

Both paths now run the shared ``stream_llm_turn_with_overflow_retry`` and
``max_tokens_blocked_results`` helpers. Covers:

- the task script scenario — a fake provider answering **overflow →
  length-truncated tool call → normal response in sequence**: both paths
  compact the history once, retry, synthesize is_error tool_results for the
  truncated call (it never executes), then complete normally with byte-wide
  identical event streams;
- the react-native ``max_tokens`` spelling fires the same protection;
- truncated calls are never recorded as loop-guard repetitions (a repeated
  truncated call keeps synthesizing errors instead of tripping the guard);
- the retry happens exactly once: a second overflow, a non-overflow error,
  or compression disabled surfaces the provider error on both paths.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from nexuscli.agent import Agent
from nexuscli.agent.query import MAX_TOKENS_TOOL_RESULT_MESSAGE, query
from nexuscli.config import NexusCliConfig, load_config
from nexuscli.tools import ToolRegistry
from nexuscli.tools.base import Tool, ToolResult
from nexuscli.types import Message

# Both paths must see the same system prompt so ContextWindowManager.prepare()
# estimates (and therefore every event) match byte for byte.
PARITY_SYSTEM_PROMPT = "overflow parity test system prompt"


class _OverflowLengthThenOkClient:
    """Fake LLM: first chat reports a provider context overflow, the second
    returns a tool call cut off by the output token limit (raw
    finish_reason=length), the third answers normally."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    # Wide window keeps prepare() below the compression threshold, so the
    # only compaction in the stream is the overflow recovery itself.
    max_context_window = 100_000

    def __init__(self) -> None:
        self.calls = 0
        self.call_first_contents: list[Any] = []
        self.call_message_counts: list[int] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.calls += 1
        self.call_first_contents.append(messages[0].content if messages else None)
        self.call_message_counts.append(len(messages))
        if self.calls == 1:
            yield {"type": "error", "error": RuntimeError("maximum context length exceeded")}
        elif self.calls == 2:
            yield {
                "type": "tool_call_delta",
                "tool_call": {
                    "index": 0,
                    "id": "call_truncated",
                    "function": {"name": "counter", "arguments": '{"n": '},
                },
            }
            yield {"type": "message_end", "stop_reason": "length"}
        else:
            yield {"type": "text_delta", "text": "recovered"}
            yield {"type": "message_end", "stop_reason": "end_turn"}


class _MaxTokensThenOkClient:
    """Fake LLM: turn 1 cuts off mid tool-call with the react-native
    stop_reason=max_tokens, turn 2 finishes naturally."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 100_000

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.calls += 1
        if self.calls == 1:
            yield {
                "type": "tool_call_delta",
                "tool_call": {
                    "index": 0,
                    "id": "call_truncated",
                    "function": {"name": "counter", "arguments": '{"n": '},
                },
            }
            yield {"type": "message_end", "stop_reason": "max_tokens"}
        else:
            yield {"type": "text_delta", "text": "recovered"}
            yield {"type": "message_end", "stop_reason": "end_turn"}


class _AlwaysTruncatedClient:
    """Fake LLM that returns the same truncated tool call (stop_reason=length)
    every turn, so a wrong loop-guard recording would abort the turn."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 100_000

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        yield {
            "type": "tool_call_delta",
            "tool_call": {
                "index": 0,
                "id": "call_truncated",
                "function": {"name": "counter", "arguments": '{"n": '},
            },
        }
        yield {"type": "message_end", "stop_reason": "length"}


class _AlwaysOverflowClient:
    """Fake LLM that fails every chat with provider overflow phrasing."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 100_000

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.calls += 1
        yield {
            "type": "error",
            "error": RuntimeError("prompt is too long: 900 tokens > 800 maximum"),
        }


class _NonOverflowErrorClient:
    """Fake LLM that fails every chat with an unrelated provider error."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 100_000

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.calls += 1
        yield {"type": "error", "error": RuntimeError("model_not_found: no such model")}


def _counter_registry() -> tuple[ToolRegistry, list[dict[str, Any]]]:
    """Fresh registry with one ``counter`` tool that records every execution."""
    executions: list[dict[str, Any]] = []

    async def handler(data: dict[str, Any], context: Any) -> ToolResult:  # noqa: ARG001
        executions.append(dict(data))
        return ToolResult(content="ok")

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="counter",
            description="counting tool for overflow parity tests",
            parameters={"type": "object", "properties": {"n": {"type": "integer"}}},
            handler=handler,
            is_read_only=True,
            is_concurrency_safe=True,
        )
    )
    return registry, executions


def _seed_history() -> list[Message]:
    """Short-but-multi-turn history: enough for overflow recovery to
    summarize the older side (the run adds one fresh user message on top)."""
    return [Message(role="user", content=f"turn {i} of a long working session") for i in range(4)]


def _parity_config(monkeypatch, tmp_path) -> NexusCliConfig:
    """Config for direct Agent / query() construction; same pattern as
    tests/test_loop_guard_parity.py. Compression stays ON — the overflow
    retry requires it."""
    monkeypatch.setenv("NEXUSCLI_API_KEY", "test-key")
    home = tmp_path / "home"
    (home / ".nexuscli").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("nexuscli.config._home", lambda: home)
    return load_config(project_root=tmp_path)


def _run_react(
    config: NexusCliConfig, tmp_path, client: Any, *, max_turns: int = 10
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    registry, executions = _counter_registry()
    agent = Agent(
        llm_client=client,
        tool_registry=registry,
        config=config,
        cwd=str(tmp_path),
        system_prompt=PARITY_SYSTEM_PROMPT,
        max_turns=max_turns,
    )
    agent.history = _seed_history()

    async def collect() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        events = [event async for event in agent.run("continue the session")]
        return events, executions

    return asyncio.run(collect())


def _run_query(
    config: NexusCliConfig, tmp_path, client: Any, *, max_turns: int = 10
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    registry, executions = _counter_registry()

    async def collect() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        events = [
            event
            async for event in query(
                llm_client=client,
                tool_registry=registry,
                system_prompt=PARITY_SYSTEM_PROMPT,
                user_message="continue the session",
                history=_seed_history(),
                cwd=str(tmp_path),
                config=config,
                max_turns=max_turns,
            )
        ]
        return events, executions

    return asyncio.run(collect())


def _freeze(value: Any) -> Any:
    """Hashable projection of event payloads (exceptions and Messages have
    no value equality of their own)."""
    if isinstance(value, BaseException):
        return f"{type(value).__name__}: {value}"
    if isinstance(value, Message):
        return (
            "message",
            value.role,
            value.content,
            value.name,
            value.tool_call_id,
            json.dumps(value.tool_calls, sort_keys=True, ensure_ascii=False),
        )
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _stream_digest(events: list[dict[str, Any]]) -> tuple[Any, ...]:
    return tuple(_freeze(dict(event)) for event in events)


def _tool_results(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] == "tool_result"]


def _errors(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] == "error"]


# ----------------------------------------------------------------------
# The task scenario: overflow -> length-truncated call -> normal reply
# ----------------------------------------------------------------------


def test_overflow_then_length_truncation_then_normal_parity(tmp_path, monkeypatch):
    config = _parity_config(monkeypatch, tmp_path)
    assert config.features.context_compression  # the retry path requires it

    react_client = _OverflowLengthThenOkClient()
    query_client = _OverflowLengthThenOkClient()
    react_events, react_executions = _run_react(config, tmp_path, react_client)
    query_events, query_executions = _run_query(config, tmp_path, query_client)

    # Both paths made the same three provider calls: overflow, length, normal.
    assert react_client.calls == query_client.calls == 3
    # The truncated call never executed on either path.
    assert react_executions == query_executions == []

    # The retried request starts at the rolling summary, not the raw history.
    for client in (react_client, query_client):
        assert "conversation-summary" in str(client.call_first_contents[1])
        assert client.call_message_counts[1] < client.call_message_counts[0]

    # Byte-wide identical event streams on both paths.
    assert _stream_digest(react_events) == _stream_digest(query_events)

    # The turn recovered: no error, the F12 synthesis ran, then the reply.
    types = [event["type"] for event in react_events]
    assert "error" not in types
    assert types[-1] == "done"
    notices = [
        event["text"]
        for event in react_events
        if event["type"] == "text_delta" and "compacting" in event["text"]
    ]
    assert notices == [
        "\nProvider reported a context-length overflow; compacting the "
        "conversation history and retrying once.\n"
    ]
    results = _tool_results(react_events)
    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert results[0]["result"] == MAX_TOKENS_TOOL_RESULT_MESSAGE
    done = react_events[-1]
    assert done["total_turns"] == 2
    assert done["messages"][-1].content == "recovered"


def test_max_tokens_spelling_gets_same_protection(tmp_path, monkeypatch):
    """The react-native ``max_tokens`` stop reason fires the shared F12
    protection identically on both paths (no overflow involved)."""
    config = _parity_config(monkeypatch, tmp_path)

    react_events, react_executions = _run_react(config, tmp_path, _MaxTokensThenOkClient())
    query_events, query_executions = _run_query(config, tmp_path, _MaxTokensThenOkClient())

    assert react_executions == query_executions == []
    assert _stream_digest(react_events) == _stream_digest(query_events)

    results = _tool_results(query_events)
    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert results[0]["result"] == MAX_TOKENS_TOOL_RESULT_MESSAGE
    done = query_events[-1]
    assert done["type"] == "done"
    assert done["total_turns"] == 2
    assert done["messages"][-1].content == "recovered"


def test_truncated_calls_are_not_loop_guard_repetitions(tmp_path, monkeypatch):
    """F12-blocked calls never ran, so they must not feed the loop guard:
    the same truncated call repeating past the threshold keeps synthesizing
    errors until max_turns, instead of aborting with the loop-guard message."""
    config = _parity_config(monkeypatch, tmp_path)
    config.agent.loop_detection_threshold = 2

    react_events, react_executions = _run_react(
        config, tmp_path, _AlwaysTruncatedClient(), max_turns=3
    )
    query_events, query_executions = _run_query(
        config, tmp_path, _AlwaysTruncatedClient(), max_turns=3
    )

    # The guard must stay silent the whole way: every tool_result is the
    # synthetic F12 message and the stream ends on the max_turns warning.
    assert react_executions == query_executions == []
    assert _stream_digest(react_events) == _stream_digest(query_events)
    results = _tool_results(query_events)
    assert len(results) == 3
    assert all(result["result"] == MAX_TOKENS_TOOL_RESULT_MESSAGE for result in results)
    assert all("Loop detected" not in str(result["result"]) for result in results)
    warnings = [event for event in query_events if event["type"] == "warning"]
    assert len(warnings) == 1
    assert warnings[0]["kind"] == "budget_exhausted"
    assert "max_turns limit (3)" in warnings[0]["message"]


# ----------------------------------------------------------------------
# Final failure paths: never more than one retry
# ----------------------------------------------------------------------


def test_second_overflow_surfaces_provider_error_parity(tmp_path, monkeypatch):
    config = _parity_config(monkeypatch, tmp_path)

    react_client = _AlwaysOverflowClient()
    query_client = _AlwaysOverflowClient()
    react_events, _ = _run_react(config, tmp_path, react_client)
    query_events, _ = _run_query(config, tmp_path, query_client)

    # Exactly one compaction retry: the second overflow fails the turn.
    assert react_client.calls == query_client.calls == 2
    assert _stream_digest(react_events) == _stream_digest(query_events)
    for events in (react_events, query_events):
        errors = _errors(events)
        assert len(errors) == 1
        assert "prompt is too long" in str(errors[0]["error"])
        assert errors[0]["error_code"] == "llm_error"
        assert [event["type"] for event in events if event["type"] == "done"] == []


def test_non_overflow_error_surfaces_immediately_parity(tmp_path, monkeypatch):
    config = _parity_config(monkeypatch, tmp_path)

    react_client = _NonOverflowErrorClient()
    query_client = _NonOverflowErrorClient()
    react_events, _ = _run_react(config, tmp_path, react_client)
    query_events, _ = _run_query(config, tmp_path, query_client)

    assert react_client.calls == query_client.calls == 1
    assert _stream_digest(react_events) == _stream_digest(query_events)
    for events in (react_events, query_events):
        errors = _errors(events)
        assert len(errors) == 1
        assert "model_not_found" in str(errors[0]["error"])


def test_overflow_retry_disabled_without_compression_parity(tmp_path, monkeypatch):
    config = _parity_config(monkeypatch, tmp_path)
    config.features.context_compression = False

    react_client = _OverflowLengthThenOkClient()
    query_client = _OverflowLengthThenOkClient()
    react_events, _ = _run_react(config, tmp_path, react_client)
    query_events, _ = _run_query(config, tmp_path, query_client)

    # Without compression the overflow surfaces directly: a single call, no
    # retry, no truncation dance.
    assert react_client.calls == query_client.calls == 1
    assert _stream_digest(react_events) == _stream_digest(query_events)
    for events in (react_events, query_events):
        errors = _errors(events)
        assert len(errors) == 1
        assert "maximum context length" in str(errors[0]["error"])


# ----------------------------------------------------------------------
# Shared-constant seam
# ----------------------------------------------------------------------


def test_max_tokens_message_constant_reexported_from_agent():
    """The F12 message moved to query.py (the lower layer); agent.py must
    keep re-exporting it — existing importers read it from there."""
    from nexuscli.agent import agent as agent_module

    assert agent_module.MAX_TOKENS_TOOL_RESULT_MESSAGE is MAX_TOKENS_TOOL_RESULT_MESSAGE
