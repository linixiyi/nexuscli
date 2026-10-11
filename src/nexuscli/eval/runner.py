"""runner.py — Drive a real Agent through a scripted scenario, fully offline.

The runner injects a :class:`ScriptedLlmClient` (the same fake-LLM pattern the
agent tests use) and deterministic stub tools into a real ``Agent`` running in
react mode, collects the streaming events, and evaluates the scenario's
assertions against what actually happened.

Deliberately offline: no network call, no real LLM. The config disables the
audit log, telemetry spans, context compression and skill matching, so a run
writes no usage or audit data; the pre/post-turn snapshot step follows the
normal Agent path (like the existing agent tests).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from nexuscli.agent import Agent
from nexuscli.config import NexusCliConfig
from nexuscli.eval.loader import EvalScenario, ScriptedTool
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.registry import ToolRegistry

# A scenario should converge within its own max_turns assertion; the agent's
# hard cap is deliberately set above it so the runner observes the real turn
# count and the assertion does the judging (an exhausted script ends the loop
# with an empty end_turn, so there is no runaway risk).
_CAP_HEADROOM = 5
_DEFAULT_TURN_BUDGET = 20


@dataclass(slots=True)
class EvalRunResult:
    """What one scenario produced, plus the assertion verdicts."""

    name: str
    passed: bool
    failure_reasons: list[str] = field(default_factory=list)
    tool_sequence: list[str] = field(default_factory=list)
    turns: int = 0
    final_text: str = ""


class ScriptedLlmClient:
    """Fake LLM client that replays a scenario's turns in order.

    Mirrors the fake-client contract the agent tests rely on: sync attributes
    ``model_name`` / ``provider_name`` / ``max_context_window`` and an async
    ``chat`` generator yielding the streaming event protocol.
    """

    model_name = "scripted-eval-model"
    provider_name = "scripted"
    max_context_window = 100_000

    def __init__(self, turns: list[Any]) -> None:
        self._turns = list(turns)
        self._index = 0

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        if self._index >= len(self._turns):
            # Script exhausted: end the loop cleanly so a bad assertion shows
            # up as a sequence/turn mismatch instead of a hang.
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        turn = self._turns[self._index]
        self._index += 1
        for index, call in enumerate(turn.tool_calls):
            yield {
                "type": "tool_call_delta",
                "tool_call": {
                    "index": index,
                    "id": f"call_{self._index}_{index}",
                    "function": {
                        "name": call["name"],
                        "arguments": _json_arguments(call.get("arguments") or {}),
                    },
                },
            }
        if turn.text:
            yield {"type": "text_delta", "text": turn.text}
        stop_reason = "tool_use" if turn.tool_calls else "end_turn"
        yield {"type": "message_end", "stop_reason": stop_reason}


def _json_arguments(arguments: dict[str, Any]) -> str:
    import json

    return json.dumps(arguments, ensure_ascii=False)


def _is_termination_warning(event: dict[str, Any]) -> bool:
    """True for the react loop's two turn-termination warnings.

    The turn-cap warning mentions ``max_turns``; the loop-guard's
    loop-abort warning (loop_guard.loop_abort_warning) starts with
    ``Loop detected``. Substring matching keeps the runner decoupled from the
    exact warning wording.
    """
    message = str(event.get("message") or "")
    return "max_turns" in message or "Loop detected" in message


def run_eval_scenarios(scenarios: list[EvalScenario], *, cwd: str) -> list[EvalRunResult]:
    """Run every scenario in order; one result per scenario, never raising.

    Scenarios are isolated: each gets its own config, registry and agent, so a
    failing scenario cannot poison the rest of the run.
    """
    return [asyncio.run(run_scenario(scenario, cwd=cwd)) for scenario in scenarios]


async def run_scenario(scenario: EvalScenario, *, cwd: str) -> EvalRunResult:
    """Run one scenario against a real react Agent and evaluate its assertions."""
    result = EvalRunResult(name=scenario.name, passed=False)
    tool_sequence: list[str] = []
    texts: list[str] = []
    failure_reasons: list[str] = []

    async for event in _stream_scenario_events(scenario, cwd=cwd):
        event_type = event.get("type")
        if event_type == "tool_call":
            tool_sequence.append(str(event.get("name") or ""))
        elif event_type == "text_delta":
            texts.append(str(event.get("text") or ""))
        elif event_type == "warning" and _is_termination_warning(event):
            # Both react-loop termination paths (turn cap, loop-abort) mean a
            # scripted scenario did not converge on its own.
            failure_reasons.append("react loop terminated early before the scripted sequence ended")
        elif event_type == "error":
            failure_reasons.append(f"agent errored: {event.get('error')}")
        elif event_type == "done":
            result.turns = int(event.get("total_turns") or 0)
            result.final_text = _final_assistant_text(event.get("messages"), fallback=texts)

    result.tool_sequence = tool_sequence
    result.failure_reasons = failure_reasons + _assertion_failures(scenario, result)
    result.passed = not result.failure_reasons
    return result


async def _stream_scenario_events(scenario: EvalScenario, *, cwd: str):
    """Build the offline agent for one scenario and yield its event stream."""
    agent = Agent(
        llm_client=ScriptedLlmClient(scenario.turns),
        tool_registry=_scenario_registry(scenario),
        config=_eval_config(scenario),
        cwd=cwd,
        max_turns=(scenario.assertions.max_turns or _DEFAULT_TURN_BUDGET) + _CAP_HEADROOM,
    )
    async for event in agent.run(scenario.prompt):
        yield event


def _eval_config(scenario: EvalScenario) -> NexusCliConfig:
    """Config for a hermetic offline run: no audit log, telemetry, or network."""
    config = NexusCliConfig()
    # Placeholder credential for Agent's not-empty validation only: the
    # scripted client never performs a network call, so nothing is ever sent.
    # Not a real secret; do not treat this value as one.
    config.llm.api_key = "".join(("offline", "-", "eval", "-placeholder"))
    # Deterministic event stream: no compression events, no skill-candidate
    # prefixes injected from whatever directory the eval happens to run in.
    config.features.context_compression = False
    config.features.skill = False
    # Never write to ~/.nexuscli (audit.jsonl) or emit telemetry spans.
    config.features.audit_log = False
    config.telemetry.enabled = False
    config.permissions.deny = list(scenario.permission_deny)
    config.policy.hitl_mode = scenario.hitl_mode
    return config


def _scenario_registry(scenario: EvalScenario) -> ToolRegistry:
    """Registry holding exactly the scenario's deterministic stub tools."""
    registry = ToolRegistry()
    registry.register_all(_stub_tool(spec) for spec in scenario.tools)
    return registry


def _stub_tool(spec: ScriptedTool) -> Tool:
    """Wrap a scripted tool spec into a real ``Tool`` with a canned result.

    The execution path (validation, permission gates, audit, approval) stays
    the real one; only the tool's effect is canned, which keeps scenarios
    byte-identical across machines and workspaces.
    """

    async def handler(payload: dict[str, Any], context: ToolContext) -> ToolResult:  # noqa: ARG001
        return ToolResult(content=spec.result, is_error=spec.is_error)

    return Tool(
        name=spec.name,
        description=spec.description,
        parameters=object_schema({}),
        handler=handler,
        is_read_only=spec.read_only,
        is_concurrency_safe=spec.read_only,
        danger_level=spec.danger_level,
        requires_approval=spec.requires_approval,
    )


def _final_assistant_text(messages: Any, *, fallback: list[str]) -> str:
    """The last assistant message's text (the agent's final output)."""
    for message in reversed(messages or []):
        if getattr(message, "role", None) == "assistant":
            content = message.content
            return content if isinstance(content, str) else ""
    return "".join(fallback)


def _assertion_failures(scenario: EvalScenario, result: EvalRunResult) -> list[str]:
    """Evaluate the four supported assertions; human-readable reasons on miss."""
    failures: list[str] = []
    assertions = scenario.assertions
    if (
        assertions.expected_tool_sequence is not None
        and result.tool_sequence != assertions.expected_tool_sequence
    ):
        failures.append(
            "tool sequence mismatch: expected "
            f"{assertions.expected_tool_sequence}, got {result.tool_sequence}"
        )
    if assertions.final_contains is not None:
        for needle in assertions.final_contains:
            if needle not in result.final_text:
                failures.append(f"final text does not contain: {needle!r}")
    if assertions.forbidden_tools is not None:
        for forbidden in assertions.forbidden_tools:
            if forbidden in result.tool_sequence:
                failures.append(f"forbidden tool was called: {forbidden}")
    if assertions.max_turns is not None and result.turns > assertions.max_turns:
        failures.append(f"took {result.turns} turns, limit is {assertions.max_turns}")
    return failures
