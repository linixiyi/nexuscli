"""Task 0001（spec 2026-11-10 fix-plan-worker-readonly）：plan 模式 worker 只读工具白名单。

plan 模式（``config.policy.plan_mode``）下，任务 worker 不再面对完整 registry：

1. ``filter_registry_for_plan_mode()`` 只保留六个只读工具
   （read_file/glob/grep/search_code/vfs_ls/vfs_read），bash/write_file/
   edit_file/list_dir 等一律不进 worker 的工具列表；registry 里没有的白名单
   工具静默跳过，不报错；输入 registry 不被原地修改；
2. 非 plan 模式 ``_worker_registry()`` 返回完整 registry（原对象），行为不变；
3. ``_task_system_prompt()`` 在 plan 模式追加只读约束：点名六个只读工具、
   明确禁止 bash/execute_command/write_file/edit_file、任务描述要求只读时
   不得自行改成写入或命令；非 plan 模式不追加；
4. ``_execute_task_stream()`` 调 ``query()`` 时传入过滤后的 registry
   （monkeypatch query 捕获参数）；非 plan 模式传入原 registry。

executor 的 plan 只读闸（tools/executor.py）不变，继续作为安全兜底——
本套测试全部走 fake planner/query，不依赖网络与真实模型。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import nexuscli.agent.plan_execute as plan_execute_module
from nexuscli.agent.plan_execute import (
    PLAN_MODE_WORKER_TOOLS,
    PlanExecuteAgent,
    filter_registry_for_plan_mode,
)
from nexuscli.config import NexusCliConfig, load_config
from nexuscli.plan import ExecutionPlan, Task, TaskStatus, TaskType
from nexuscli.tools.base import Tool, ToolResult, object_schema
from nexuscli.tools.registry import ToolRegistry

# 含「先/然后」多步线索，避开 planner 的 simple-goal 捷径。
_GOAL = "先读取 README.md 总结项目结构，然后分析 src 目录的模块划分"

_ALL_CANDIDATES = (
    "read_file",
    "glob",
    "grep",
    "search_code",
    "vfs_ls",
    "vfs_read",
    "bash",
    "write_file",
    "edit_file",
    "list_dir",
)

_BANNED = ("bash", "write_file", "edit_file")

_SINGLE_TASK_JSON = (
    '{"summary":"s","tasks":['
    '{"id":"a","description":"分析项目结构","type":"ANALYSIS","dependencies":[]}]}'
)


class FakeClient:
    """Planner 产出单任务计划；worker 侧在捕获型测试里由 fake query 顶替。"""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        body = str(messages[-1].content)
        if "请为以下目标创建执行计划" in body:
            yield {"type": "text_delta", "text": _SINGLE_TASK_JSON}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        yield {"type": "text_delta", "text": "任务已完成。"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


class HiddenToolCallClient(FakeClient):
    """Worker 先调用指定工具再自称完成；工具走真实 query()/executor 路径。"""

    def __init__(self, tool_name: str):
        self._tool_name = tool_name
        self.calls = 0

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        body = str(messages[-1].content)
        if "请为以下目标创建执行计划" in body:
            yield {"type": "text_delta", "text": _SINGLE_TASK_JSON}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        self.calls += 1
        if self.calls == 1:
            yield {
                "type": "tool_call_delta",
                "tool_call": {
                    "index": 0,
                    "id": "call-1",
                    "function": {"name": self._tool_name, "arguments": "{}"},
                },
            }
            yield {"type": "message_end", "stop_reason": "tool_use"}
            return
        yield {"type": "text_delta", "text": "任务已完成。"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


def _tool(name: str) -> Tool:
    async def handler(payload, _context):
        _ = payload
        return ToolResult("ok")

    return Tool(
        name=name,
        description=f"fake {name}",
        parameters=object_schema({}),
        handler=handler,
        is_read_only=name in PLAN_MODE_WORKER_TOOLS,
        requires_approval=False,
    )


def _registry_with_all_candidates() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_all([_tool(name) for name in _ALL_CANDIDATES])
    return registry


def _plan_config(tmp_path, *, plan_mode: bool) -> NexusCliConfig:
    config = load_config(project_root=tmp_path)
    config.policy.plan_mode = plan_mode
    config.policy.hitl_mode = "never"
    return config


def _agent(config: NexusCliConfig, tmp_path, **kwargs) -> PlanExecuteAgent:
    return PlanExecuteAgent(
        llm_client=kwargs.pop("llm_client", FakeClient()),
        tool_registry=kwargs.pop("tool_registry", _registry_with_all_candidates()),
        config=config,
        cwd=str(tmp_path),
        **kwargs,
    )


def _plan_with_task() -> tuple[ExecutionPlan, Task]:
    plan = ExecutionPlan(id="plan_worker1", goal="分析项目结构并总结")
    task = Task(id="task_1", description="读取 README 并总结", type=TaskType.ANALYSIS)
    plan.add_task(task)
    plan.compute_execution_order()
    return plan, task


async def _drain(
    agent: PlanExecuteAgent, events
) -> tuple[list[dict[str, Any]], ExecutionPlan | None]:
    collected: list[dict[str, Any]] = []
    plan_id = ""
    async for event in events:
        if event.get("type") == "error":
            raise event["error"]
        collected.append(event)
        if event.get("type") == "plan_checkpointed":
            plan_id = str(event.get("plan_id") or "")
    plan = agent.checkpoint_store.load(plan_id) if plan_id else None
    return collected, plan


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(
        str(event.get("text") or "") for event in events if event.get("type") == "text_delta"
    )


def _plan_failed_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event.get("type") == "plan_failed"]


# ---------------------------------------------------------------------------
# ① 过滤 helper：只留六个只读工具
# ---------------------------------------------------------------------------


def test_plan_mode_filter_keeps_only_six_readonly_tools():
    registry = _registry_with_all_candidates()
    filtered = filter_registry_for_plan_mode(registry)
    assert filtered.list_names() == sorted(PLAN_MODE_WORKER_TOOLS)
    for banned in (*_BANNED, "list_dir"):
        assert filtered.get(banned) is None
    # 输入 registry 不被原地修改
    assert "bash" in registry.list_names()
    assert "write_file" in registry.list_names()


def test_plan_mode_filter_skips_absent_whitelisted_tools():
    registry = ToolRegistry()
    registry.register(_tool("read_file"))
    filtered = filter_registry_for_plan_mode(registry)
    assert filtered.list_names() == ["read_file"]


# ---------------------------------------------------------------------------
# ② _worker_registry：非 plan 模式完整 registry，plan 模式过滤子集
# ---------------------------------------------------------------------------


def test_worker_registry_is_full_registry_outside_plan_mode(tmp_path):
    registry = _registry_with_all_candidates()
    agent = _agent(_plan_config(tmp_path, plan_mode=False), tmp_path, tool_registry=registry)
    assert agent._worker_registry() is registry


def test_worker_registry_is_filtered_subset_in_plan_mode(tmp_path):
    registry = _registry_with_all_candidates()
    agent = _agent(_plan_config(tmp_path, plan_mode=True), tmp_path, tool_registry=registry)
    filtered = agent._worker_registry()
    assert filtered is not registry
    assert filtered.list_names() == sorted(PLAN_MODE_WORKER_TOOLS)
    for banned in _BANNED:
        assert banned not in filtered.list_names()


# ---------------------------------------------------------------------------
# ③ _task_system_prompt：plan 只读约束与禁止写工具字样
# ---------------------------------------------------------------------------


def test_task_system_prompt_carries_readonly_constraint_in_plan_mode(tmp_path):
    agent = _agent(_plan_config(tmp_path, plan_mode=True), tmp_path)
    plan, task = _plan_with_task()
    prompt = agent._task_system_prompt(plan, task)
    assert "当前是 plan 只读模式" in prompt
    assert "只能使用上述只读工具" in prompt
    assert "不要调用 bash/execute_command/write_file/edit_file" in prompt
    assert "任务描述要求只读时不要自行改成写入或命令" in prompt
    for tool_name in sorted(PLAN_MODE_WORKER_TOOLS):
        assert tool_name in prompt


def test_task_system_prompt_has_no_constraint_outside_plan_mode(tmp_path):
    agent = _agent(_plan_config(tmp_path, plan_mode=False), tmp_path)
    plan, task = _plan_with_task()
    prompt = agent._task_system_prompt(plan, task)
    assert "当前是 plan 只读模式" not in prompt
    assert "不要调用 bash/execute_command/write_file/edit_file" not in prompt


# ---------------------------------------------------------------------------
# ④ _execute_task_stream：query() 收到过滤后的 registry
# ---------------------------------------------------------------------------


def _install_fake_query(monkeypatch, captured: dict[str, Any]) -> None:
    async def fake_query(**kwargs) -> AsyncIterator[dict[str, Any]]:
        captured.update(kwargs)
        yield {
            "type": "done",
            "total_turns": 1,
            "total_tokens": 3,
            "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
            "messages": [],
        }

    monkeypatch.setattr(plan_execute_module, "query", fake_query)


def test_plan_mode_worker_query_receives_filtered_registry(tmp_path, monkeypatch):
    captured: dict[str, Any] = {}
    _install_fake_query(monkeypatch, captured)
    registry = _registry_with_all_candidates()
    agent = _agent(_plan_config(tmp_path, plan_mode=True), tmp_path, tool_registry=registry)

    asyncio.run(_drain(agent, agent.run(_GOAL)))

    worker_registry = captured["tool_registry"]
    assert worker_registry is not registry
    assert worker_registry.list_names() == sorted(PLAN_MODE_WORKER_TOOLS)
    for banned in _BANNED:
        assert banned not in worker_registry.list_names()
    # worker 系统提示词同样带只读约束
    assert "当前是 plan 只读模式" in captured["system_prompt"]
    assert "不要调用 bash/execute_command/write_file/edit_file" in captured["system_prompt"]


def test_default_mode_worker_query_receives_full_registry(tmp_path, monkeypatch):
    captured: dict[str, Any] = {}
    _install_fake_query(monkeypatch, captured)
    registry = _registry_with_all_candidates()
    agent = _agent(_plan_config(tmp_path, plan_mode=False), tmp_path, tool_registry=registry)

    asyncio.run(_drain(agent, agent.run(_GOAL)))

    assert captured["tool_registry"] is registry
    assert "bash" in captured["tool_registry"].list_names()
    assert "当前是 plan 只读模式" not in captured["system_prompt"]


# ---------------------------------------------------------------------------
# 被白名单隐藏的工具调用：blocked 语义不因过滤而松动
# ---------------------------------------------------------------------------


def test_plan_mode_hidden_tool_call_blocks_task(tmp_path):
    """plan 模式 worker 调用被白名单隐藏的工具 = 闸拒绝：blocked，不得洗白。

    registry 里有 write_file，但过滤后 worker 看不到——调用在 executor 的
    unknown-tool 门就死了（比只读闸早一步），plan_execute 必须把它翻译回
    闸拒绝语义并 blocked，而不是让 worker 的自称完成蒙混成 COMPLETED。
    """
    registry = _registry_with_all_candidates()
    agent = _agent(
        _plan_config(tmp_path, plan_mode=True),
        tmp_path,
        tool_registry=registry,
        llm_client=HiddenToolCallClient("write_file"),
    )

    events, plan = asyncio.run(_drain(agent, agent.run(_GOAL)))

    task = plan.get_task("task_1")
    assert task.status is TaskStatus.FAILED
    assert "not allowed in plan mode" in task.error
    assert "已阻断 [task_1]" in _text(events)
    assert "已完成 [task_1]" not in _text(events)
    assert "计划未完成：1 个任务被 plan 只读闸拒绝" in _text(events)
    assert _plan_failed_events(events)


def test_plan_mode_hallucinated_tool_name_is_plain_error(tmp_path):
    """全 registry 都没有的名字是普通可恢复错误，不是闸拒绝。"""
    registry = _registry_with_all_candidates()
    agent = _agent(
        _plan_config(tmp_path, plan_mode=True),
        tmp_path,
        tool_registry=registry,
        llm_client=HiddenToolCallClient("definitely_not_a_tool"),
    )

    events, plan = asyncio.run(_drain(agent, agent.run(_GOAL)))

    assert plan.get_task("task_1").status is TaskStatus.COMPLETED
    assert _plan_failed_events(events) == []
    assert "已阻断" not in _text(events)


def test_default_mode_hidden_tool_call_executes_normally(tmp_path):
    """非 plan 模式完整 registry：同样的调用照常执行并完成任务。"""
    registry = _registry_with_all_candidates()
    agent = _agent(
        _plan_config(tmp_path, plan_mode=False),
        tmp_path,
        tool_registry=registry,
        llm_client=HiddenToolCallClient("write_file"),
    )

    events, plan = asyncio.run(_drain(agent, agent.run(_GOAL)))

    assert plan.get_task("task_1").status is TaskStatus.COMPLETED
    assert _plan_failed_events(events) == []
    assert "已完成 [task_1]" in _text(events)
