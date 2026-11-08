"""Tests for the single ``agent.max_turns`` turn budget across agent loops.

Every loop — the react tool loop, plan task workers (``PlanExecuteAgent``)
and team workers (``AgentOrchestrator`` subagents, react and nested plan) —
reads the same ``config.agent.max_turns`` field; no path carries its own
magic number (the old worker ``max_turns=8`` and the ``Agent._run_plan``
lift of ``max_task_turns`` to 200 are both gone).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from nexuscli.agent import Agent, PlanExecuteAgent
from nexuscli.agent.orchestrator import (
    AgentMessage,
    AgentMessageType,
    AgentOrchestrator,
    AgentRole,
    SubAgent,
)
from nexuscli.config import NexusCliConfig, load_config
from nexuscli.plan import ExecutionPlan, Task, TaskType
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.types import Message


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() into a temp home.

    Plan runs write graph checkpoints under ``~/.nexuscli/plan_runs`` and
    snapshots go to ``~/.nexuscli/snapshots``. A HOME-only patch leaks onto
    the real home on Windows (Path.home() reads USERPROFILE there), so triple
    patch — same convention as tests/test_plan.py.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


class FakeClient:
    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        yield {"type": "text_delta", "text": "{}"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


_DONE_EVENT: dict[str, Any] = {
    "messages": [Message(role="assistant", content="ok")],
    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    "total_turns": 1,
}


def _config(tmp_path, monkeypatch) -> NexusCliConfig:
    """Config for direct agent construction; the API key comes from the
    environment (same pattern as tests/test_agent_turns.py) so no credential
    literal lives in the source."""
    monkeypatch.setenv("NEXUSCLI_API_KEY", "test-key")
    return load_config(project_root=tmp_path)


def _orchestrator(tmp_path, config: NexusCliConfig) -> AgentOrchestrator:
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    config.policy.hitl_mode = "never"
    return AgentOrchestrator(
        llm_client=FakeClient(),
        tool_registry=registry,
        config=config,
        cwd=str(tmp_path),
    )


async def _drain(stream) -> list[dict[str, Any]]:
    return [event async for event in stream]


def _fake_query(captured: list[dict[str, Any]]):
    async def fake_query(**kwargs):
        captured.append(kwargs)
        yield dict(_DONE_EVENT)

    return fake_query


def _fake_plan_run(captured: list[int]):
    async def fake_run(self, message):  # noqa: ARG001
        captured.append(self.max_task_turns)
        yield dict(_DONE_EVENT)

    return fake_run


def test_team_react_worker_budget_comes_from_config(tmp_path, monkeypatch, isolated_home):
    """Team worker 的 query() 回合预算来自 agent.max_turns，不再写死 8。"""
    config = _config(tmp_path, monkeypatch)
    config.agent.max_turns = 7
    orchestrator = _orchestrator(tmp_path, config)

    captured: list[dict[str, Any]] = []
    monkeypatch.setattr("nexuscli.agent.orchestrator.query", _fake_query(captured))

    asyncio.run(orchestrator.workers[0].execute(AgentMessage.task("orchestrator", "do A")))

    assert captured and captured[0]["max_turns"] == 7


def test_plan_task_budget_comes_from_config(tmp_path, monkeypatch, isolated_home):
    """Plan 任务 worker 的 query() 回合预算来自 agent.max_turns，不再写死 8。"""
    config = _config(tmp_path, monkeypatch)
    config.agent.max_turns = 9
    agent = PlanExecuteAgent(
        llm_client=FakeClient(),
        tool_registry=ToolRegistry(),
        config=config,
        cwd=str(tmp_path),
    )
    assert agent.max_task_turns == 9

    plan = ExecutionPlan(id="plan_1", goal="demo")
    plan.add_task(Task("task_1", "do a", TaskType.ANALYSIS))
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr("nexuscli.agent.plan_execute.query", _fake_query(captured))

    asyncio.run(_drain(agent._execute_task_stream(plan, plan.get_task("task_1"))))

    assert captured and captured[0]["max_turns"] == 9


def test_plan_task_budget_defaults_to_config_value(tmp_path, monkeypatch, isolated_home):
    """不传 max_task_turns 时回落到 config.agent.max_turns 的默认值 200。"""
    config = _config(tmp_path, monkeypatch)
    agent = PlanExecuteAgent(
        llm_client=FakeClient(),
        tool_registry=ToolRegistry(),
        config=config,
        cwd=str(tmp_path),
    )

    assert config.agent.max_turns == 200
    assert agent.max_task_turns == 200


def test_plan_task_explicit_budget_still_wins(tmp_path, monkeypatch, isolated_home):
    """显式传入的 max_task_turns 仍然生效（调用方/测试需要不同预算时）。"""
    config = _config(tmp_path, monkeypatch)
    config.agent.max_turns = 9
    agent = PlanExecuteAgent(
        llm_client=FakeClient(),
        tool_registry=ToolRegistry(),
        config=config,
        cwd=str(tmp_path),
        max_task_turns=4,
    )

    assert agent.max_task_turns == 4


def test_run_plan_does_not_lift_task_budget(tmp_path, monkeypatch, isolated_home):
    """Agent._run_plan 不再把 max_task_turns 抬到 200：预算只来自 agent.max_turns。"""
    config = _config(tmp_path, monkeypatch)
    config.agent.max_turns = 11
    agent = Agent(
        llm_client=FakeClient(),
        tool_registry=ToolRegistry(),
        config=config,
        cwd=str(tmp_path),
        mode="plan",
    )

    captured: list[int] = []
    monkeypatch.setattr(
        "nexuscli.agent.plan_execute.PlanExecuteAgent.run", _fake_plan_run(captured)
    )

    asyncio.run(_drain(agent.run("plan this")))

    assert captured == [11]


def test_team_nested_plan_worker_budget_comes_from_config(tmp_path, monkeypatch, isolated_home):
    """Team 的嵌套 plan worker（mode=plan）同样读 agent.max_turns。"""
    config = _config(tmp_path, monkeypatch)
    config.agent.max_turns = 13
    worker = SubAgent(
        name="worker-plan",
        role=AgentRole.WORKER,
        llm_client=FakeClient(),
        tool_registry=ToolRegistry(),
        config=config,
        cwd=str(tmp_path),
    )

    captured: list[int] = []
    monkeypatch.setattr(
        "nexuscli.agent.plan_execute.PlanExecuteAgent.run", _fake_plan_run(captured)
    )

    result = asyncio.run(
        worker.execute(AgentMessage.task("orchestrator", "complex task"), mode="plan")
    )

    assert captured == [13]
    assert result.type == AgentMessageType.RESULT
