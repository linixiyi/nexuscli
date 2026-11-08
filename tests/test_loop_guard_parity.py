"""Parity tests for the loop guard and the ``max_turns`` warning across the
two tool-loop implementations: the react loop (``Agent._run_react``) and the
standalone query path (``agent.query.query``, used by plan/team workers).

Covers:
- ``query()`` runs the same ``LoopGuard``: a repeated identical call past
  ``agent.loop_detection_threshold`` is NOT executed and the nudge is
  injected, one more identical call aborts the turn (same as react);
- both paths end the stream with the *same* ``warning`` event (same ``kind``
  and same message text) when the loop guard aborts — the text comes from the
  shared ``loop_abort_warning`` helper;
- both paths emit the same readable ``max_turns`` warning (shared
  ``max_turns_warning`` helper) when the turn budget runs out while the model
  still wants to keep calling tools;
- below the threshold, or with the guard disabled (threshold 0), both paths
  execute every call and never warn;
- the existing ``max_turns`` default-value semantics are unchanged
  (``AgentConfig().max_turns == 200``, ``query(max_turns=200)``).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any

from nexuscli.agent import Agent
from nexuscli.agent.loop_guard import max_turns_warning
from nexuscli.agent.query import query
from nexuscli.config import AgentConfig, NexusCliConfig, load_config
from nexuscli.tools import ToolRegistry
from nexuscli.tools.base import Tool, ToolResult


class _BoundedSameCallClient:
    """Fake LLM that requests the same tool call ``repeats`` times and then
    answers with plain text (stop_reason=end_turn)."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(
        self,
        repeats: int,
        *,
        name: str = "counter",
        arguments: str = '{"n": 1}',
    ) -> None:
        self._repeats = repeats
        self._name = name
        self._arguments = arguments
        self._requests = 0

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self._requests += 1
        if self._requests > self._repeats:
            yield {"type": "text_delta", "text": "switching strategy"}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        yield {
            "type": "tool_call_delta",
            "tool_call": {
                "index": 0,
                "id": f"call_{self._requests}",
                "function": {"name": self._name, "arguments": self._arguments},
            },
        }
        yield {"type": "message_end", "stop_reason": "tool_use"}


class _CyclingArgsClient:
    """Fake LLM that keeps calling the same tool with a different argument
    every turn, so the loop guard never fires and the turn limit is what
    stops the loop."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self, *, name: str = "counter") -> None:
        self._name = name
        self._requests = 0

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self._requests += 1
        yield {
            "type": "tool_call_delta",
            "tool_call": {
                "index": 0,
                "id": f"call_{self._requests}",
                "function": {"name": self._name, "arguments": json.dumps({"n": self._requests})},
            },
        }
        yield {"type": "message_end", "stop_reason": "tool_use"}


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
            description="counting tool for loop-guard parity tests",
            parameters={"type": "object", "properties": {"n": {"type": "integer"}}},
            handler=handler,
            is_read_only=True,
            is_concurrency_safe=True,
        )
    )
    return registry, executions


def _parity_config(monkeypatch, tmp_path) -> NexusCliConfig:
    """Config for direct Agent / query() construction; same pattern as
    tests/test_loop_detection.py and tests/test_agent_turns.py."""
    monkeypatch.setenv("NEXUSCLI_API_KEY", "test-key")
    home = tmp_path / "home"
    (home / ".nexuscli").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("nexuscli.config._home", lambda: home)
    config = load_config(project_root=tmp_path)
    # Exact event-stream assertions; compression would inject extra events.
    config.features.context_compression = False
    return config


def _run_react(
    config: NexusCliConfig, tmp_path, client: Any, *, max_turns: int = 10
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    registry, executions = _counter_registry()
    agent = Agent(
        llm_client=client,
        tool_registry=registry,
        config=config,
        cwd=str(tmp_path),
        max_turns=max_turns,
    )
    events = asyncio.run(_collect_agent(agent))
    return events, executions


async def _collect_agent(agent: Agent) -> list[dict[str, Any]]:
    return [event async for event in agent.run("keep calling the counter")]


def _run_query(
    config: NexusCliConfig, tmp_path, client: Any, *, max_turns: int = 10
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    registry, executions = _counter_registry()

    async def collect() -> list[dict[str, Any]]:
        return [
            event
            async for event in query(
                llm_client=client,
                tool_registry=registry,
                system_prompt="loop-guard parity test",
                user_message="keep calling the counter",
                history=[],
                cwd=str(tmp_path),
                config=config,
                max_turns=max_turns,
            )
        ]

    events = asyncio.run(collect())
    return events, executions


def _warnings(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] == "warning"]


def _nudged_results(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if event["type"] == "tool_result" and "Loop detected" in str(event.get("result"))
    ]


# ----------------------------------------------------------------------
# Threshold stop + nudge (query path mirrors react)
# ----------------------------------------------------------------------


def test_query_blocks_repeated_call_at_threshold_and_nudges(tmp_path, monkeypatch):
    """Threshold 2: turn 1 executes, turn 2 is intercepted with the nudge
    (the call never ran), turn 3 repeats → the turn aborts with a warning."""
    config = _parity_config(monkeypatch, tmp_path)
    config.agent.loop_detection_threshold = 2

    events, executions = _run_query(config, tmp_path, _BoundedSameCallClient(repeats=5))

    assert len(executions) == 1  # only the very first call ran
    types = [event["type"] for event in events]
    assert types[-2:] == ["warning", "done"]
    # Turn 2 got the nudge; turn 3 the abort message — the call never ran.
    nudge_texts = [str(event["result"]) for event in _nudged_results(events)]
    assert any("Change your" in text for text in nudge_texts)
    assert any("turn is ending" in text for text in nudge_texts)

    # History stays paired: every assistant tool_call got a tool answer.
    done = events[-1]
    messages = done["messages"]
    assistant_calls = sum(
        len(message.tool_calls) for message in messages if message.role == "assistant"
    )
    tool_messages = [message for message in messages if message.role == "tool"]
    assert len(tool_messages) == assistant_calls == 3


def test_react_and_query_abort_with_identical_warning(tmp_path, monkeypatch):
    """Same fake LLM, same threshold: both paths stop at the guard and end
    with the *same* warning event (kind and message text from the shared
    ``loop_abort_warning`` helper)."""
    config = _parity_config(monkeypatch, tmp_path)
    config.agent.loop_detection_threshold = 2

    react_events, react_executions = _run_react(config, tmp_path, _BoundedSameCallClient(repeats=5))
    query_events, query_executions = _run_query(config, tmp_path, _BoundedSameCallClient(repeats=5))

    # Both paths stopped at the same point: exactly one real execution.
    assert react_executions == query_executions == [{"n": 1}]

    react_warning = _warnings(react_events)[-1]
    query_warning = _warnings(query_events)[-1]
    assert react_warning["kind"] == query_warning["kind"] == "budget_exhausted"
    assert react_warning["message"] == query_warning["message"]
    assert "Loop detected" in react_warning["message"]
    assert "loop_detection_threshold=2" in react_warning["message"]


# ----------------------------------------------------------------------
# max_turns hit-limit warning parity
# ----------------------------------------------------------------------


def test_react_and_query_warn_identically_on_max_turns(tmp_path, monkeypatch):
    """With the guard quiet (every call has fresh arguments), both paths run
    to the turn limit and emit the same readable budget warning built by the
    shared ``max_turns_warning`` helper."""
    config = _parity_config(monkeypatch, tmp_path)

    react_events, react_executions = _run_react(config, tmp_path, _CyclingArgsClient(), max_turns=3)
    query_events, query_executions = _run_query(config, tmp_path, _CyclingArgsClient(), max_turns=3)

    # Every turn actually ran the tool on both paths; the limit stopped them.
    assert len(react_executions) == 3
    assert len(query_executions) == 3

    react_types = [event["type"] for event in react_events]
    query_types = [event["type"] for event in query_events]
    assert react_types[-2:] == ["warning", "done"]
    assert query_types[-2:] == ["warning", "done"]

    react_warning = _warnings(react_events)[-1]
    query_warning = _warnings(query_events)[-1]
    assert react_warning["kind"] == query_warning["kind"] == "budget_exhausted"
    assert react_warning["message"] == query_warning["message"]
    assert react_warning["message"] == max_turns_warning(3)
    assert "max_turns limit (3)" in react_warning["message"]


def test_max_turns_warning_helper_text_is_readable():
    text = max_turns_warning(7)
    assert "max_turns limit (7)" in text
    assert "agent.max_turns" in text


# ----------------------------------------------------------------------
# Below threshold / disabled guard parity
# ----------------------------------------------------------------------


def test_below_threshold_both_paths_execute_normally(tmp_path, monkeypatch):
    config = _parity_config(monkeypatch, tmp_path)
    config.agent.loop_detection_threshold = 3

    react_events, react_executions = _run_react(config, tmp_path, _BoundedSameCallClient(repeats=2))
    query_events, query_executions = _run_query(config, tmp_path, _BoundedSameCallClient(repeats=2))

    assert len(react_executions) == len(query_executions) == 2
    assert _warnings(react_events) == []
    assert _warnings(query_events) == []
    assert _nudged_results(react_events) == []
    assert _nudged_results(query_events) == []


def test_threshold_zero_disables_detection_on_both_paths(tmp_path, monkeypatch):
    config = _parity_config(monkeypatch, tmp_path)
    config.agent.loop_detection_threshold = 0

    react_events, react_executions = _run_react(config, tmp_path, _BoundedSameCallClient(repeats=5))
    query_events, query_executions = _run_query(config, tmp_path, _BoundedSameCallClient(repeats=5))

    assert len(react_executions) == len(query_executions) == 5
    assert _warnings(react_events) == []
    assert _warnings(query_events) == []
    assert _nudged_results(react_events) == []
    assert _nudged_results(query_events) == []


# ----------------------------------------------------------------------
# Unchanged default-value semantics
# ----------------------------------------------------------------------


def test_max_turns_default_semantics_unchanged():
    """The parity work must not move any default: ``AgentConfig().max_turns``
    stays 200 and ``query()`` keeps its ``max_turns=200`` signature default."""
    assert AgentConfig().max_turns == 200
    assert AgentConfig().loop_detection_threshold == 3
    assert inspect.signature(query).parameters["max_turns"].default == 200
