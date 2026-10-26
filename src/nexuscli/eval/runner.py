"""runner.py — Drive a real Agent through a scripted scenario, fully offline.

The runner injects a :class:`ScriptedLlmClient` (the same fake-LLM pattern the
agent tests use) and deterministic stub tools into a real ``Agent`` running in
react mode, collects the streaming events, and evaluates the scenario's
assertions against what actually happened.

Multi-round user simulation: when the scenario declares ``user_turns``, the
runner keeps driving the *same* agent after its final text — the scripted
user's next message is appended as the next user turn of the same session (the
agent's history persists across ``run()`` calls), and the fake LLM keeps
replaying ``scripted_turns`` where it left off. The conversation ends when the
user script runs out, emits its ``{"end": true}`` marker, or the scenario's
``max_rounds`` cap is reached. Trajectory assertions judge the *cumulative*
run: ``tool_sequence`` spans every round, ``final_text`` is the last round's
final answer, and ``turns`` sums the react-loop turns of all rounds (so
``max_turns`` is the whole conversation's budget).

Memory-recall track: a scenario with ``memory_eval`` never builds an agent.
The runner seeds a real ``MemoryManager`` whose SQLite file lives in a fresh
system temp directory (created and removed around the run, so nothing touches
the repo or the user home), saves the scenario's seed rows with their
adversarial metadata, and evaluates each query group against the real fused
recall: the target seed must land inside the ``top_k`` window and no
distractor seed may rank ahead of it. No agent loop ran, so the cost metrics
(``turns``, ``total_tool_calls``) stay at zero while PASS/FAIL, the
difficulty tier and the regression diff work exactly as on the agent track.

Deliberately offline: no network call, no real LLM. The config disables the
audit log, telemetry spans, context compression and skill matching, so a run
writes no usage or audit data; the pre/post-turn snapshot step follows the
normal Agent path (like the existing agent tests).
"""

from __future__ import annotations

import asyncio
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexuscli.agent import Agent
from nexuscli.config import NexusCliConfig
from nexuscli.eval.loader import (
    DEFAULT_DIFFICULTY,
    EvalScenario,
    MemoryQuerySpec,
    MemorySeedEntry,
    ScriptedTool,
)
from nexuscli.memory import MemoryManager
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.registry import ToolRegistry

# A scenario should converge within its own max_turns assertion; the agent's
# hard cap is deliberately set above it so the runner observes the real turn
# count and the assertion does the judging (an exhausted script ends the loop
# with an empty end_turn, so there is no runaway risk). The cap is per react
# loop, i.e. per round; the multi-round budget is the assertions' job.
_CAP_HEADROOM = 5
_DEFAULT_TURN_BUDGET = 20


@dataclass(slots=True)
class EvalRunResult:
    """What one scenario produced, plus the assertion verdicts.

    ``difficulty`` is copied from the scenario so the reporter can aggregate
    per tier without going back to the scenario files; it defaults to the
    loader's default tier so a hand-built result still lands in a bucket.

    ``rounds`` is the number of conversation rounds actually run (1 for a
    plain single-round scenario); ``turns`` / ``tool_sequence`` are cumulative
    across those rounds.
    """

    name: str
    passed: bool
    failure_reasons: list[str] = field(default_factory=list)
    tool_sequence: list[str] = field(default_factory=list)
    turns: int = 0
    final_text: str = ""
    difficulty: str = DEFAULT_DIFFICULTY
    rounds: int = 1


class ScriptedLlmClient:
    """Fake LLM client that replays a scenario's turns in order.

    Mirrors the fake-client contract the agent tests rely on: sync attributes
    ``model_name`` / ``provider_name`` / ``max_context_window`` and an async
    ``chat`` generator yielding the streaming event protocol.

    One instance drives the whole conversation: the turn index keeps advancing
    across rounds, so a multi-round scenario's ``scripted_turns`` is one flat
    list consumed in order (a round ends where a text-only turn ends it).
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


# The failure reason appended when a round's react loop hit its turn cap or
# the loop guard aborted: a scripted scenario must converge on its own.
_TERMINATION_REASON = "react loop terminated early before the scripted sequence ended"


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

    Scenarios are isolated: each agent-loop scenario gets its own config,
    registry and agent, each memory-recall scenario its own temp-DB
    MemoryManager, so a failing scenario cannot poison the rest of the run.
    """
    return [asyncio.run(run_scenario(scenario, cwd=cwd)) for scenario in scenarios]


async def run_scenario(scenario: EvalScenario, *, cwd: str) -> EvalRunResult:
    """Run one scenario and evaluate its assertions.

    Agent-loop scenarios run against a real react Agent (one agent instance
    drives every round of a multi-round conversation). A ``memory_eval``
    scenario takes the memory-recall track instead: no agent, no tools — the
    seed rows go into a hermetic temp-DB MemoryManager and the ranking
    assertions judge the real recall (the ``cwd`` is unused there).
    """
    if scenario.memory_eval is not None:
        return _run_memory_scenario(scenario)
    result = EvalRunResult(name=scenario.name, passed=False, difficulty=scenario.difficulty)
    tool_sequence: list[str] = []
    texts: list[str] = []
    failure_reasons: list[str] = []

    agent = _build_agent(scenario, cwd=cwd)
    message: str | None = scenario.prompt
    cursor = 0  # next unconsumed entry of the scripted-user turn list
    rounds = 0
    while message is not None:
        rounds += 1
        async for event in agent.run(message):
            event_type = event.get("type")
            if event_type == "tool_call":
                tool_sequence.append(str(event.get("name") or ""))
            elif event_type == "text_delta":
                texts.append(str(event.get("text") or ""))
            elif event_type == "warning" and _is_termination_warning(event):
                # Both react-loop termination paths (turn cap, loop-abort) mean
                # a scripted scenario did not converge on its own.
                failure_reasons.append(_TERMINATION_REASON)
            elif event_type == "error":
                failure_reasons.append(f"agent errored: {event.get('error')}")
            elif event_type == "done":
                result.turns += int(event.get("total_turns") or 0)
                result.final_text = _final_assistant_text(event.get("messages"), fallback=texts)
        message, cursor = _next_round_message(scenario, completed_rounds=rounds, cursor=cursor)

    result.rounds = rounds
    result.tool_sequence = tool_sequence
    result.failure_reasons = failure_reasons + _assertion_failures(scenario, result)
    result.passed = not result.failure_reasons
    return result


def _next_round_message(
    scenario: EvalScenario, *, completed_rounds: int, cursor: int
) -> tuple[str | None, int]:
    """The next user message after a round, or ``None`` to end the conversation.

    The conversation ends when the scenario's ``max_rounds`` cap is reached
    (the rest of the user script is truncated), the user script runs out of
    entries, or the script emits its ``{"end": true}`` marker. Otherwise the
    next entry's message starts the next round of the same session.
    """
    if scenario.max_rounds is not None and completed_rounds >= scenario.max_rounds:
        return None, cursor
    if cursor >= len(scenario.user_turns):
        return None, cursor
    user_turn = scenario.user_turns[cursor]
    if user_turn.end:
        return None, cursor
    return user_turn.message, cursor + 1


def _build_agent(scenario: EvalScenario, *, cwd: str) -> Agent:
    """Build the offline agent for one scenario — one instance for all rounds."""
    return Agent(
        llm_client=ScriptedLlmClient(scenario.turns),
        tool_registry=_scenario_registry(scenario),
        config=_eval_config(scenario),
        cwd=cwd,
        max_turns=(scenario.assertions.max_turns or _DEFAULT_TURN_BUDGET) + _CAP_HEADROOM,
    )


# ---------------------------------------------------------------------------
# Memory-recall track (memory_eval scenarios)
# ---------------------------------------------------------------------------

# Scope for the per-scenario temp DB. Every scenario builds its own manager on
# a fresh SQLite file, so scopes never collide and nothing outside the temp
# directory is touched (the loader forbids agent-loop keys on this track).
_MEMORY_EVAL_SCOPE = "eval"


def _run_memory_scenario(scenario: EvalScenario) -> EvalRunResult:
    """Run one memory-recall scenario and evaluate its ranking assertions.

    Hermetic by construction: the MemoryManager's SQLite file is created under
    a system temp directory that is removed when the run ends, so neither the
    repository nor the user home is written.
    """
    spec = scenario.memory_eval
    if spec is None:  # unreachable via the loader; keeps the type checkers honest
        raise ValueError(f"scenario {scenario.name!r} has no memory_eval spec")
    failure_reasons: list[str] = []
    with tempfile.TemporaryDirectory(prefix="nexuscli-eval-memory-") as tmp_dir:
        manager = MemoryManager(Path(tmp_dir) / "memory.db", scope=_MEMORY_EVAL_SCOPE)
        ids = [_save_memory_seed(manager, entry) for entry in spec.seed]
        for group in spec.queries:
            failure_reasons.extend(_memory_query_failures(group, ids, manager))
    return EvalRunResult(
        name=scenario.name,
        passed=not failure_reasons,
        failure_reasons=failure_reasons,
        difficulty=scenario.difficulty,
        # No agent loop ran: the cost metrics stay at zero (the reporter diffs
        # them like any other scenario's).
        tool_sequence=[],
        turns=0,
        final_text="",
        rounds=1,
    )


def _save_memory_seed(manager: MemoryManager, entry: MemorySeedEntry) -> int:
    """Save one seed row; unset optional fields fall back to ``save()`` defaults."""
    kwargs: dict[str, Any] = {}
    if entry.importance is not None:
        kwargs["importance"] = entry.importance
    if entry.kind is not None:
        kwargs["kind"] = entry.kind
    if entry.entities:
        kwargs["entities"] = list(entry.entities)
    if entry.temporal_kind is not None:
        kwargs["temporal_kind"] = entry.temporal_kind
    return manager.save(entry.content, **kwargs)


def _memory_query_failures(
    group: MemoryQuerySpec, ids: list[int], manager: MemoryManager
) -> list[str]:
    """Ranking assertions for one query group, as human-readable reasons.

    The contract: the target seed must appear inside the recall window of
    ``top_k`` results, and (when it does) no distractor seed may rank ahead of
    it. Seed references are reported as ``#index`` positions in the scenario's
    ``seed`` list; the recalled order is quoted in the miss message so a
    failure is diagnosable from the report alone.
    """
    rows = manager.recall(group.query, limit=group.top_k, mark_access=False)
    # One memory id per seed (the loader rejects duplicates that dedup would
    # merge), so ids.index() maps every recalled row back to its seed index.
    ranked = [ids.index(row.id) for row in rows]
    failures: list[str] = []
    label = f"memory query {group.query!r}"
    target_rank: int | None = None
    if group.target in ranked:
        target_rank = ranked.index(group.target) + 1
    else:
        failures.append(
            f"{label}: target seed #{group.target} missed the top-{group.top_k} "
            f"(recalled order: {_seed_indexes(ranked)})"
        )
    if target_rank is not None:
        for distractor in group.distractors:
            if distractor in ranked:
                rank = ranked.index(distractor) + 1
                if rank < target_rank:
                    failures.append(
                        f"{label}: distractor seed #{distractor} outranked target seed "
                        f"#{group.target} (rank {rank} vs {target_rank})"
                    )
    return failures


def _seed_indexes(ranked: list[int]) -> str:
    """``#2, #1, #0`` for a non-empty ranking; ``nothing recalled`` otherwise."""
    if not ranked:
        return "nothing recalled"
    return ", ".join(f"#{index}" for index in ranked)


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
    """Evaluate the scenario's assertions; human-readable reasons on miss.

    The three call-count assertions (``required_tools``, ``max_tool_calls``,
    ``max_total_tool_calls``) read the same observed ``tool_sequence`` as the
    sequence/forbidden checks: every ``tool_call`` event the agent emitted,
    including calls the loop guard intercepted instead of executing.
    """
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
    call_counts = Counter(result.tool_sequence)
    if assertions.required_tools is not None:
        for required in assertions.required_tools:
            if call_counts[required] == 0:
                failures.append(f"required tool was never called: {required}")
    if assertions.max_tool_calls is not None:
        for tool, limit in assertions.max_tool_calls.items():
            actual = call_counts[tool]
            if actual > limit:
                failures.append(f"tool {tool} was called {actual} times, limit is {limit}")
    if assertions.max_total_tool_calls is not None:
        total = len(result.tool_sequence)
        if total > assertions.max_total_tool_calls:
            failures.append(
                f"made {total} tool calls in total, limit is {assertions.max_total_tool_calls}"
            )
    if assertions.max_turns is not None and result.turns > assertions.max_turns:
        failures.append(f"took {result.turns} turns, limit is {assertions.max_turns}")
    return failures
