"""Task 0034: ``--mode plan`` 假完成 — plan 只读闸拒绝的任务不得标 COMPLETED。

plan 模式（``config.policy.plan_mode``）下：

- planner 收到只读规划指令，不得把写文件/改文件/写盘命令任务排进计划
  （指令经 plan_execute 拥有的 client 缝隙注入；plan/planner.py 在
  boundary 外）；
- worker 仍被只读闸拒绝时，任务必须是 blocked（FAILED + 拒绝原因），即使
  worker 的文本自称完成；
- 最终摘要不得声称成功；``-p --mode plan`` 的进程退出码非 0；
- ``--yes`` 只影响审批，不绕过 plan 只读闸。

非 plan 模式的 plan 运行保持原行为（无指令、写任务照常执行并完成）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
import typer

import nexuscli.agent.plan_execute as plan_execute_module
import nexuscli.entrypoints.cli as cli
from nexuscli.agent import PlanExecuteAgent
from nexuscli.agent.plan_execute import _PLAN_MODE_PLANNING_DIRECTIVE
from nexuscli.config import NexusCliConfig, load_config
from nexuscli.plan import (
    ExecutionPlan,
    PlanCheckpointStore,
    PlanStatus,
    Task,
    TaskStatus,
    TaskType,
)
from nexuscli.tools.base import Tool, ToolResult, object_schema
from nexuscli.tools.registry import ToolRegistry


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() into a temp home (mirrors tests/test_plan.py)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return str(content)


# Long enough and multi-step ("先…然后…") so _is_simple_goal never shortcuts
# the planner LLM round-trip.
_GOAL = "先把总结内容写入 release.md，然后读取 README.md 总结项目结构"

_PLAN_WITH_WRITE_TASKS_JSON = (
    '{"summary":"写任务计划","tasks":['
    '{"id":"w","description":"创建 release.md 并写入总结内容","type":"FILE_WRITE",'
    '"dependencies":[]},'
    '{"id":"r","description":"读取 README.md 第一行","type":"FILE_READ",'
    '"dependencies":[]}]}'
)

_SINGLE_ANALYSIS_TASK_JSON = (
    '{"summary":"s","tasks":['
    '{"id":"a","description":"分析项目结构","type":"ANALYSIS","dependencies":[]}]}'
)


class FakeClient:
    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        yield {"type": "text_delta", "text": "{}"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


class _RecordingPlanClient(FakeClient):
    """Records the user turns of every chat; workers just claim success."""

    def __init__(self):
        self.planner_prompts: list[str] = []
        self.worker_prompts: list[str] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        body = _message_text(messages[-1].content)
        if "请为以下目标创建执行计划" in body:
            self.planner_prompts.append(body)
            yield {"type": "text_delta", "text": _PLAN_WITH_WRITE_TASKS_JSON}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        self.worker_prompts.append(body)
        yield {"type": "text_delta", "text": "任务已完成。"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


class _GateRejectionWorkerClient(FakeClient):
    """Worker calls the write tool (gate rejects), then still claims success."""

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        body = _message_text(messages[-1].content)
        if "请为以下目标创建执行计划" in body:
            yield {"type": "text_delta", "text": _SINGLE_ANALYSIS_TASK_JSON}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        self.calls += 1
        if self.calls == 1:
            yield {
                "type": "tool_call_delta",
                "tool_call": {
                    "index": 0,
                    "id": "call-1",
                    "function": {"name": "write_file", "arguments": '{"path": "out.txt"}'},
                },
            }
            yield {"type": "message_end", "stop_reason": "tool_use"}
            return
        # The gate rejected the call, but the worker's text claims the task
        # finished anyway — exactly the fake-completion shape under test.
        yield {"type": "text_delta", "text": "文件已写入，任务完成。"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


class _PlanOnlyClient(FakeClient):
    """Planner only; the workers run through the faked query() seam."""

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        body = _message_text(messages[-1].content)
        if "请为以下目标创建执行计划" in body:
            yield {"type": "text_delta", "text": _SINGLE_ANALYSIS_TASK_JSON}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        yield {"type": "text_delta", "text": "任务已完成。"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


def _registry_with_write_tool(executions: list[str]) -> ToolRegistry:
    registry = ToolRegistry()

    async def handler(payload, _context):
        executions.append(str(payload))
        return ToolResult("written")

    registry.register(
        Tool(
            name="write_file",
            description="fake write tool",
            parameters=object_schema({"path": {"type": "string"}}),
            handler=handler,
            is_read_only=False,
            requires_approval=False,
        )
    )
    return registry


def _agent(client, tmp_path, config, **kwargs) -> PlanExecuteAgent:
    return PlanExecuteAgent(
        llm_client=client,
        tool_registry=kwargs.pop("tool_registry", ToolRegistry()),
        config=config,
        cwd=str(tmp_path),
        **kwargs,
    )


async def _drain(
    agent: PlanExecuteAgent, events
) -> tuple[str, list[dict[str, Any]], ExecutionPlan | None]:
    """Collect a plan run: text, all events, and the final checkpointed plan.

    ``plan_created`` is consumed inside PlanExecuteAgent.run and never
    re-yielded, so the plan comes from the ``plan_checkpointed`` id + the
    checkpoint store (whose terminal save carries the final task statuses).
    """
    text = ""
    collected: list[dict[str, Any]] = []
    plan_id = ""
    async for event in events:
        collected.append(event)
        if event.get("type") == "text_delta":
            text += str(event.get("text") or "")
        elif event.get("type") == "plan_checkpointed":
            plan_id = str(event.get("plan_id") or "")
        elif event.get("type") == "error":
            raise event["error"]
    plan = agent.checkpoint_store.load(plan_id) if plan_id else None
    return text, collected, plan


def _plan_failed_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event.get("type") == "plan_failed"]


def test_plan_mode_directive_whitelists_readonly_tools_and_bans_command_tasks():
    """The directive names the six whitelisted read-only tools and the banned
    task kinds: bash/execute_command (even read-only-looking commands) and
    pytest/npm/uv run/install/build/disk-writing tasks."""
    for tool_name in ("read_file", "glob", "grep", "search_code", "vfs_ls", "vfs_read"):
        assert tool_name in _PLAN_MODE_PLANNING_DIRECTIVE
    assert "bash" in _PLAN_MODE_PLANNING_DIRECTIVE
    assert "execute_command" in _PLAN_MODE_PLANNING_DIRECTIVE
    for keyword in ("pytest", "npm", "uv run", "安装", "构建", "写盘"):
        assert keyword in _PLAN_MODE_PLANNING_DIRECTIVE
    # 反诱惑措辞：点名 wc -l 反例——数行数诉求必须改排 read_file 自行计数。
    assert "wc -l" in _PLAN_MODE_PLANNING_DIRECTIVE


def test_plan_mode_planner_receives_readonly_directive(tmp_path, isolated_home):
    """Clause 1: in plan mode the planner's user turn carries the directive."""
    client = _RecordingPlanClient()
    config = load_config(project_root=tmp_path)
    config.policy.plan_mode = True
    config.policy.hitl_mode = "never"
    agent = _agent(client, tmp_path, config)

    asyncio.run(_drain(agent, agent.run(_GOAL)))

    assert client.planner_prompts, "planner never ran"
    assert all(_PLAN_MODE_PLANNING_DIRECTIVE in prompt for prompt in client.planner_prompts)
    # The directive rides the planner's user turn, not the session history:
    # it must not leak into task-worker prompts.
    assert not any(_PLAN_MODE_PLANNING_DIRECTIVE in prompt for prompt in client.worker_prompts)


def test_default_mode_planner_gets_no_directive(tmp_path, isolated_home):
    """Without plan mode the planning prompt is untouched."""
    client = _RecordingPlanClient()
    config = load_config(project_root=tmp_path)
    config.policy.plan_mode = False
    config.policy.hitl_mode = "never"
    agent = _agent(client, tmp_path, config)

    asyncio.run(_drain(agent, agent.run(_GOAL)))

    assert client.planner_prompts
    assert not any(_PLAN_MODE_PLANNING_DIRECTIVE in prompt for prompt in client.planner_prompts)


def test_plan_mode_worker_gate_rejection_blocks_task(tmp_path, isolated_home):
    """A worker rejected by the real read-only gate is blocked, not COMPLETED."""
    executions: list[str] = []
    config = load_config(project_root=tmp_path)
    config.policy.plan_mode = True
    config.policy.hitl_mode = "never"
    agent = _agent(
        _GateRejectionWorkerClient(),
        tmp_path,
        config,
        tool_registry=_registry_with_write_tool(executions),
    )

    text, events, plan = asyncio.run(_drain(agent, agent.run(_GOAL)))

    task = plan.get_task("task_1")
    assert task.status is TaskStatus.FAILED
    assert "not allowed in plan mode" in task.error
    # The gated call never executed, whatever the worker's text claimed.
    assert executions == []
    assert "已完成 [task_1]" not in text
    assert "已阻断 [task_1]" in text
    assert "计划执行完成" not in text
    assert "计划未完成：1 个任务被 plan 只读闸拒绝" in text
    assert "plan 模式是只读模式" in text
    assert plan.status is PlanStatus.FAILED

    failed_events = _plan_failed_events(events)
    assert len(failed_events) == 1
    assert set(failed_events[0]["blocked_tasks"]) == {"task_1"}


def test_plan_mode_yes_does_not_bypass_readonly_gate(tmp_path, isolated_home):
    """--yes approves approvals; the plan-mode read-only gate still blocks."""
    executions: list[str] = []
    prompts: list[dict[str, Any]] = []

    async def callback(request):
        prompts.append(request)
        return "approve"

    config = load_config(project_root=tmp_path)
    config.policy.plan_mode = True
    config.policy.hitl_mode = "never"
    agent = _agent(
        _GateRejectionWorkerClient(),
        tmp_path,
        config,
        tool_registry=_registry_with_write_tool(executions),
        approval_callback=callback,
    )

    text, _events, plan = asyncio.run(_drain(agent, agent.run(_GOAL)))

    task = plan.get_task("task_1")
    assert task.status is TaskStatus.FAILED
    assert "not allowed in plan mode" in task.error
    assert executions == []
    # The gate rejects before approval: --yes was never even consulted.
    assert prompts == []
    assert "计划未完成：1 个任务被 plan 只读闸拒绝" in text


def test_fake_query_gate_rejection_marks_task_blocked(tmp_path, isolated_home, monkeypatch):
    """The spec's fake-query shape: query() returns a rejected tool result."""

    async def fake_query(**kwargs):
        _ = kwargs
        yield {"type": "tool_call", "name": "write_file", "input": {"path": "out.txt"}}
        yield {
            "type": "tool_result",
            "name": "write_file",
            "result": (
                'Tool "write_file" is not allowed in plan mode. Only read-only tools are permitted.'
            ),
            "is_error": True,
        }
        yield {
            "type": "done",
            "total_turns": 2,
            "total_tokens": 5,
            "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            "messages": [],
        }

    monkeypatch.setattr(plan_execute_module, "query", fake_query)
    config = load_config(project_root=tmp_path)
    config.policy.plan_mode = True
    config.policy.hitl_mode = "never"
    agent = _agent(_PlanOnlyClient(), tmp_path, config)

    text, events, plan = asyncio.run(_drain(agent, agent.run(_GOAL)))

    task = plan.get_task("task_1")
    assert task.status is TaskStatus.FAILED
    assert "not allowed in plan mode" in task.error
    assert "已完成 [task_1]" not in text
    assert "已阻断 [task_1]" in text
    assert "计划未完成：1 个任务被 plan 只读闸拒绝" in text
    assert _plan_failed_events(events)


def test_default_mode_plan_still_completes_write_tasks(tmp_path, isolated_home):
    """Without plan mode the new behavior must not change plan outcomes."""
    client = _RecordingPlanClient()
    config = load_config(project_root=tmp_path)
    config.policy.plan_mode = False
    config.policy.hitl_mode = "never"
    agent = _agent(client, tmp_path, config)

    text, events, plan = asyncio.run(_drain(agent, agent.run(_GOAL)))

    assert plan.get_task("task_1").status is TaskStatus.COMPLETED
    assert plan.get_task("task_2").status is TaskStatus.COMPLETED
    assert plan.status is PlanStatus.COMPLETED
    assert "计划执行完成" in text
    assert _plan_failed_events(events) == []


def test_plan_mode_resume_blocks_gate_rejected_tasks(tmp_path, isolated_home):
    """The resume path blocks gate-rejected tasks exactly like a fresh run."""
    store = PlanCheckpointStore(root=tmp_path / "plan_runs")
    plan = ExecutionPlan(id="plan_res1", goal=_GOAL)
    plan.add_task(Task(id="task_1", description="分析项目结构", type=TaskType.ANALYSIS))
    plan.compute_execution_order()
    store.save(plan)

    executions: list[str] = []
    config = load_config(project_root=tmp_path)
    config.policy.plan_mode = True
    config.policy.hitl_mode = "never"
    agent = PlanExecuteAgent(
        llm_client=_GateRejectionWorkerClient(),
        tool_registry=_registry_with_write_tool(executions),
        config=config,
        cwd=str(tmp_path),
        checkpoint_store=store,
    )

    text, events, _ = asyncio.run(_drain(agent, agent.run_resume("plan_res1")))

    resumed = store.load("plan_res1")
    task = resumed.get_task("task_1")
    assert task.status is TaskStatus.FAILED
    assert "not allowed in plan mode" in task.error
    assert executions == []
    assert "计划未完成：1 个任务被 plan 只读闸拒绝" in text
    assert _plan_failed_events(events)


class _WriteCapture:
    """stdout replacement recording each write() call separately."""

    encoding = "utf-8"

    def __init__(self):
        self.writes: list[str] = []

    def write(self, s: str) -> int:
        if isinstance(s, bytes):
            raise TypeError("write() argument must be str, not bytes")
        self.writes.append(s)
        return len(s)

    def flush(self) -> None:
        pass


class _PlanEventsEngine:
    """QueryEngine double exposing only the plan() stream the CLI folds."""

    def __init__(self, events, **_kwargs):
        self._events = events

    async def plan(self, message):
        _ = message
        for event in self._events:
            yield event


def _install_plan_engine(monkeypatch: pytest.MonkeyPatch, events) -> _PlanEventsEngine:
    async def fake_build_registry(config, cwd):
        _ = config, cwd
        return None, None

    monkeypatch.setattr(cli, "build_tool_registry", fake_build_registry)
    monkeypatch.setattr(cli, "create_llm_client", lambda llm, telemetry_enabled: object())
    engine = _PlanEventsEngine(events)
    monkeypatch.setattr(cli, "QueryEngine", lambda **kwargs: engine)
    return engine


def _test_config() -> NexusCliConfig:
    config = NexusCliConfig()
    config.llm.api_key = "unit-test-value"
    return config


_BLOCKED_PLAN_EVENTS = [
    {"type": "text_delta", "text": "计划未完成：1 个任务被 plan 只读闸拒绝，未执行。\n\n"},
    {"type": "plan_failed", "plan_id": "plan_1", "blocked_tasks": {"task_1": "拒绝原因"}},
    {
        "type": "done",
        "total_turns": 2,
        "total_tokens": 10,
        "usage": {"input_tokens": 6, "output_tokens": 4, "total_tokens": 10},
    },
]

_CLEAN_PLAN_EVENTS = [
    {"type": "text_delta", "text": "计划执行完成。\n\n"},
    {
        "type": "done",
        "total_turns": 2,
        "total_tokens": 10,
        "usage": {"input_tokens": 6, "output_tokens": 4, "total_tokens": 10},
    },
]


def test_cli_plan_mode_blocked_plan_exits_non_zero(tmp_path, monkeypatch):
    capture = _WriteCapture()
    _install_plan_engine(monkeypatch, _BLOCKED_PLAN_EVENTS)
    monkeypatch.setattr(sys, "stdout", capture)

    with pytest.raises(typer.Exit) as excinfo:
        asyncio.run(cli._run_prompt(_GOAL, str(tmp_path), _test_config(), mode="plan"))

    assert excinfo.value.exit_code == 1
    # The non-success summary is still printed; only the exit code turns red.
    assert "计划未完成" in "".join(capture.writes)


def test_cli_plan_mode_blocked_plan_json_still_exits_non_zero(tmp_path, monkeypatch):
    capture = _WriteCapture()
    _install_plan_engine(monkeypatch, _BLOCKED_PLAN_EVENTS)
    monkeypatch.setattr(sys, "stdout", capture)

    with pytest.raises(typer.Exit) as excinfo:
        asyncio.run(
            cli._run_prompt(_GOAL, str(tmp_path), _test_config(), mode="plan", json_output=True)
        )

    assert excinfo.value.exit_code == 1


def test_cli_plan_mode_clean_plan_exits_zero(tmp_path, monkeypatch):
    capture = _WriteCapture()
    _install_plan_engine(monkeypatch, _CLEAN_PLAN_EVENTS)
    monkeypatch.setattr(sys, "stdout", capture)

    asyncio.run(cli._run_prompt(_GOAL, str(tmp_path), _test_config(), mode="plan"))

    assert "计划执行完成" in "".join(capture.writes)
