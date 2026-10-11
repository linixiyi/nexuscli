"""Tests for the ReAct tool-loop repetition detection (``agent.loop_detection_threshold``).

Covers:
- the pure fingerprint/transition helpers in ``nexuscli.agent.loop_guard``
  (stable serialization, consecutive-count reset, disabled mode);
- threshold reached → the repeated call is NOT executed and a nudge message
  is injected to the model (and the model can then finish normally);
- below the threshold → every call executes normally, no nudge, no warning;
- threshold 0 → detection fully disabled;
- different arguments → the consecutive counter resets;
- one more identical repeat after the nudge → the turn aborts with an
  explicit ``warning`` event (same event protocol as the ``max_turns``
  warning) and the history stays valid;
- config parsing of ``agent.loop_detection_threshold`` (default 3, 0 = off,
  negative clamped to 0, non-numeric falls back to the default).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from nexuscli.agent import Agent
from nexuscli.agent.loop_guard import (
    DEFAULT_LOOP_THRESHOLD,
    LoopGuard,
    LoopState,
    next_loop_state,
    tool_call_fingerprint,
)
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


class _VaryingArgsClient:
    """Fake LLM that cycles through a list of argument strings, then finishes."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self, arguments: list[str], *, name: str = "counter") -> None:
        self._arguments = arguments
        self._name = name
        self._index = 0

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        if self._index >= len(self._arguments):
            yield {"type": "text_delta", "text": "all done"}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        arguments = self._arguments[self._index]
        self._index += 1
        yield {
            "type": "tool_call_delta",
            "tool_call": {
                "index": 0,
                "id": f"call_{self._index}",
                "function": {"name": self._name, "arguments": arguments},
            },
        }
        yield {"type": "message_end", "stop_reason": "tool_use"}


def _counter_registry() -> tuple[ToolRegistry, list[dict[str, Any]]]:
    """Registry with one ``counter`` tool that records every execution."""
    executions: list[dict[str, Any]] = []

    async def handler(data: dict[str, Any], context: Any) -> ToolResult:  # noqa: ARG001
        executions.append(dict(data))
        return ToolResult(content="ok")

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="counter",
            description="counting tool for loop-detection tests",
            parameters={"type": "object", "properties": {"n": {"type": "integer"}}},
            handler=handler,
            is_read_only=True,
            is_concurrency_safe=True,
        )
    )
    return registry, executions


def _agent_config(monkeypatch, tmp_path) -> NexusCliConfig:
    """Direct Agent construction config; same pattern as tests/test_agent_turns.py."""
    monkeypatch.setenv("NEXUSCLI_API_KEY", "test-key")
    home = tmp_path / "home"
    (home / ".nexuscli").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("nexuscli.config._home", lambda: home)
    config = load_config(project_root=tmp_path)
    # Exact event-stream assertions; compression would inject extra events.
    config.features.context_compression = False
    return config


async def _collect(agent: Agent, message: str) -> list[dict[str, Any]]:
    return [event async for event in agent.run(message)]


def _load_config_with_agent_section(
    tmp_path, monkeypatch, agent_section: dict[str, Any] | None
) -> NexusCliConfig:
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".nexuscli").mkdir(parents=True, exist_ok=True)
    (project / ".nexuscli").mkdir(parents=True, exist_ok=True)
    if agent_section is not None:
        (project / ".nexuscli" / "config.json").write_text(
            json.dumps({"agent": agent_section}),
            encoding="utf-8",
        )
    monkeypatch.setattr("nexuscli.config._home", lambda: home)
    return load_config(project_root=project)


# ----------------------------------------------------------------------
# Pure helpers (loop_guard.py)
# ----------------------------------------------------------------------


def test_fingerprint_is_stable_across_key_order():
    assert tool_call_fingerprint(
        "read_file", {"path": "a.txt", "line": 3}
    ) == tool_call_fingerprint("read_file", {"line": 3, "path": "a.txt"})


def test_fingerprint_distinguishes_name_and_arguments():
    assert tool_call_fingerprint("read_file", {"path": "a.txt"}) != tool_call_fingerprint(
        "write_file", {"path": "a.txt"}
    )
    assert tool_call_fingerprint("read_file", {"path": "a.txt"}) != tool_call_fingerprint(
        "read_file", {"path": "b.txt"}
    )


def test_next_loop_state_counts_consecutive_repeats_and_resets():
    state = LoopState()
    state, first = next_loop_state(state, "counter(n=1)", threshold=3)
    assert (first.repeat_count, first.blocked, first.abort, first.nudged) == (
        1,
        False,
        False,
        False,
    )

    state, second = next_loop_state(state, "counter(n=1)", threshold=3)
    assert (second.repeat_count, second.blocked, second.abort, second.nudged) == (
        2,
        False,
        False,
        False,
    )

    state, third = next_loop_state(state, "counter(n=1)", threshold=3)
    assert (third.repeat_count, third.blocked, third.abort, third.nudged) == (3, True, False, True)

    state, fourth = next_loop_state(state, "counter(n=1)", threshold=3)
    assert (fourth.repeat_count, fourth.blocked, fourth.abort) == (4, True, True)

    # A new fingerprint resets the counter and the nudge.
    state, other = next_loop_state(state, "counter(n=2)", threshold=3)
    assert (other.repeat_count, other.blocked, other.abort, other.nudged) == (
        1,
        False,
        False,
        False,
    )


def test_loop_guard_threshold_zero_disables_detection():
    guard = LoopGuard(threshold=0)
    for _ in range(10):
        decision = guard.record("counter(n=1)")
        assert not decision.blocked
        assert not decision.abort


def test_loop_guard_default_threshold_is_three():
    assert DEFAULT_LOOP_THRESHOLD == 3
    assert AgentConfig().loop_detection_threshold == 3


# ----------------------------------------------------------------------
# React-loop integration
# ----------------------------------------------------------------------


def test_threshold_reached_blocks_call_and_injects_nudge(tmp_path, monkeypatch):
    """The third identical call is not executed; the model receives the nudge
    and can then finish the turn normally (no warning, no abort)."""
    registry, executions = _counter_registry()
    agent = Agent(
        llm_client=_BoundedSameCallClient(repeats=3),
        tool_registry=registry,
        config=_agent_config(monkeypatch, tmp_path),
        cwd=str(tmp_path),
        max_turns=10,
    )
    agent.config.agent.loop_detection_threshold = 3

    events = asyncio.run(_collect(agent, "call the counter repeatedly"))

    # Turns 1-2 executed; turn 3 blocked; turn 4 the model answers with text.
    assert len(executions) == 2
    types = [event["type"] for event in events]
    assert "warning" not in types
    assert types[-1] == "done"

    nudged = [event for event in events if event["type"] == "tool_result"]
    assert len(nudged) == 3
    assert "Loop detected" in nudged[-1]["result"]
    assert "not executed" in nudged[-1]["result"]

    # The nudge reaches the model as a tool-role message in the history.
    tool_messages = [message for message in agent.history if message.role == "tool"]
    assert any("Loop detected" in str(message.content) for message in tool_messages)


def test_below_threshold_executes_every_call_normally(tmp_path, monkeypatch):
    registry, executions = _counter_registry()
    agent = Agent(
        llm_client=_BoundedSameCallClient(repeats=2),
        tool_registry=registry,
        config=_agent_config(monkeypatch, tmp_path),
        cwd=str(tmp_path),
        max_turns=10,
    )
    agent.config.agent.loop_detection_threshold = 3

    events = asyncio.run(_collect(agent, "call the counter twice"))

    assert len(executions) == 2
    types = [event["type"] for event in events]
    assert "warning" not in types
    tool_results = [event for event in events if event["type"] == "tool_result"]
    assert all(event["result"] == "ok" for event in tool_results)


def test_threshold_zero_disables_detection(tmp_path, monkeypatch):
    registry, executions = _counter_registry()
    agent = Agent(
        llm_client=_BoundedSameCallClient(repeats=5),
        tool_registry=registry,
        config=_agent_config(monkeypatch, tmp_path),
        cwd=str(tmp_path),
        max_turns=10,
    )
    agent.config.agent.loop_detection_threshold = 0

    events = asyncio.run(_collect(agent, "call the counter five times"))

    assert len(executions) == 5
    types = [event["type"] for event in events]
    assert "warning" not in types
    assert not any("Loop detected" in str(event.get("result")) for event in events)


def test_changed_arguments_reset_the_counter(tmp_path, monkeypatch):
    """Alternating arguments never accumulate: every fingerprint is new."""
    registry, executions = _counter_registry()
    agent = Agent(
        llm_client=_VaryingArgsClient(['{"n": 1}', '{"n": 1}', '{"n": 2}', '{"n": 1}']),
        tool_registry=registry,
        config=_agent_config(monkeypatch, tmp_path),
        cwd=str(tmp_path),
        max_turns=10,
    )
    agent.config.agent.loop_detection_threshold = 3

    events = asyncio.run(_collect(agent, "vary the arguments"))

    assert len(executions) == 4
    types = [event["type"] for event in events]
    assert "warning" not in types
    assert types[-1] == "done"


def test_repeat_after_nudge_terminates_turn_with_warning(tmp_path, monkeypatch):
    """Threshold 2: turn 1 executes, turn 2 is nudged, turn 3 repeats the
    same fingerprint → the turn aborts with an explicit warning event."""
    registry, executions = _counter_registry()
    agent = Agent(
        llm_client=_BoundedSameCallClient(repeats=5),
        tool_registry=registry,
        config=_agent_config(monkeypatch, tmp_path),
        cwd=str(tmp_path),
        max_turns=10,
    )
    agent.config.agent.loop_detection_threshold = 2

    events = asyncio.run(_collect(agent, "keep calling the counter"))

    assert len(executions) == 1  # only the very first call ran
    types = [event["type"] for event in events]
    assert types[-2:] == ["warning", "done"]
    warning = events[-2]
    assert "Loop detected" in str(warning["message"])
    assert "loop_detection_threshold=2" in str(warning["message"])

    # History stays valid: every assistant tool_call has a tool answer.
    assistant_calls = sum(
        len(message.tool_calls) for message in agent.history if message.role == "assistant"
    )
    tool_messages = [message for message in agent.history if message.role == "tool"]
    assert len(tool_messages) == assistant_calls == 3
    # The abort message explains the termination to the model.
    assert any("turn is ending" in str(message.content) for message in tool_messages)


def test_max_turns_warning_is_suppressed_when_loop_guard_aborts(tmp_path, monkeypatch):
    """If the abort lands on the last allowed turn (turn 3 with threshold 2),
    only the loop-detection warning is emitted, not the max_turns one."""
    registry, executions = _counter_registry()
    agent = Agent(
        llm_client=_BoundedSameCallClient(repeats=5),
        tool_registry=registry,
        config=_agent_config(monkeypatch, tmp_path),
        cwd=str(tmp_path),
        max_turns=3,
    )
    agent.config.agent.loop_detection_threshold = 2

    events = asyncio.run(_collect(agent, "keep calling the counter"))

    warnings = [event for event in events if event["type"] == "warning"]
    assert len(warnings) == 1
    assert "max_turns" not in str(warnings[0]["message"])
    assert "loop_detection_threshold=2" in str(warnings[0]["message"])


# ----------------------------------------------------------------------
# Config parsing
# ----------------------------------------------------------------------


def test_loop_detection_threshold_config_default_and_override(tmp_path, monkeypatch):
    config = _load_config_with_agent_section(tmp_path, monkeypatch, None)
    assert config.agent.loop_detection_threshold == 3

    config = _load_config_with_agent_section(tmp_path, monkeypatch, {"loop_detection_threshold": 7})
    assert config.agent.loop_detection_threshold == 7


def test_loop_detection_threshold_zero_disables_and_negative_clamps(tmp_path, monkeypatch):
    config = _load_config_with_agent_section(tmp_path, monkeypatch, {"loop_detection_threshold": 0})
    assert config.agent.loop_detection_threshold == 0

    config = _load_config_with_agent_section(
        tmp_path, monkeypatch, {"loop_detection_threshold": -7}
    )
    assert config.agent.loop_detection_threshold == 0


def test_loop_detection_threshold_non_numeric_falls_back(tmp_path, monkeypatch):
    config = _load_config_with_agent_section(
        tmp_path, monkeypatch, {"loop_detection_threshold": "abc", "bogus": True}
    )
    assert config.agent.loop_detection_threshold == 3
