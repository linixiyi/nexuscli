from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nexuscli.agent import PlanExecuteAgent
from nexuscli.config import load_config
from nexuscli.plan import ExecutionPlan, Planner, Task, TaskType
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.types import Message


def test_execution_plan_exposes_dag_batches():
    plan = ExecutionPlan(id="plan_1", goal="demo")
    task_1 = Task("task_1", "read a", TaskType.FILE_READ)
    task_2 = Task("task_2", "read b", TaskType.FILE_READ)
    task_3 = Task("task_3", "summarize", TaskType.ANALYSIS, ["task_1", "task_2"])

    plan.add_task(task_1)
    plan.add_task(task_2)
    plan.add_task(task_3)

    assert plan.execution_order() == ["task_1", "task_2", "task_3"]
    assert plan.execution_batches() == [[task_1, task_2], [task_3]]
    assert plan.executable_tasks() == [task_1, task_2]
    task_1.mark_completed("done")
    assert plan.executable_tasks() == [task_2]


def test_execution_plan_summary_uses_chinese_labels():
    plan = ExecutionPlan(id="plan_1", goal="检查项目")
    plan.summary = "先检查再汇总"
    plan.add_task(Task("task_1", "检查文件", TaskType.FILE_READ))

    summary = plan.summarize()

    assert "计划 plan_1：先检查再汇总" in summary
    assert "任务数：1" in summary
    assert "当前可执行：1" in summary


def test_planner_parses_tasks_and_dependencies():
    planner = Planner(FakeClient())

    plan = planner.parse_plan(
        "demo",
        """
        ```json
        {
          "summary": "demo plan",
          "tasks": [
            {"id": "a", "description": "A", "type": "COMMAND", "dependencies": []},
            {"id": "b", "description": "B", "type": "VERIFICATION", "dependencies": ["a"]}
          ]
        }
        ```
        """,
    )

    assert plan.summary == "demo plan"
    assert plan.get_task("task_2").dependencies == ["task_1"]
    assert plan.get_task("task_2").type == TaskType.VERIFICATION


def test_planner_extracts_json_from_prose_with_tool_markers():
    planner = Planner(FakeClient())

    plan = planner.parse_plan(
        "demo",
        "我先查看当前目录，然后读取 README.md 的第一行。\n\n"
        '<｜DSML｜｜ invoke name="bash">ls -la\n\n'
        '{"summary":"s","tasks":[{"id":"a","description":"A","type":"COMMAND","dependencies":[]}]}',
    )

    assert plan.summary == "s"
    assert plan.get_task("task_1").description == "A"


def test_planner_repairs_prose_output_with_a_retry_round():
    client = ProseThenJsonClient()
    planner = Planner(client)

    plan = asyncio.run(planner.create_plan(_MULTI_STEP_GOAL))

    assert plan.summary == "repaired"
    assert len(client.calls) == 2
    repair_messages = client.calls[1]
    assert repair_messages[1].role == "assistant"
    assert "我先查看当前目录" in str(repair_messages[1].content)
    assert repair_messages[2].role == "user"
    assert "不是合法的执行计划 JSON" in str(repair_messages[2].content)


def test_planner_retries_transient_api_errors():
    client = TransientErrorThenJsonClient()
    planner = Planner(client)

    plan = asyncio.run(planner.create_plan("demo goal"))

    assert plan.summary == "retried"
    assert client.calls == 2


def test_planner_reports_raw_output_after_persistent_prose():
    client = AlwaysProseClient()
    planner = Planner(client)

    with pytest.raises(ValueError) as excinfo:
        asyncio.run(planner.create_plan(_MULTI_STEP_GOAL))

    message = str(excinfo.value)
    assert "could not be parsed" in message
    assert "我先查看当前目录" in message
    assert client.calls == 2


def test_plan_execute_runs_independent_tasks_in_parallel(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    client = ParallelPlanClient()
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "never"
    agent = PlanExecuteAgent(
        llm_client=client,
        tool_registry=registry,
        config=config,
        cwd=str(tmp_path),
    )

    async def run():
        text = ""
        events = []
        async for event in agent.run("先做 A 和 B，然后汇总"):
            events.append(event)
            if event.get("type") == "text_delta":
                text += str(event.get("text") or "")
            elif event.get("type") == "error":
                raise event["error"]
        return text, events

    result, events = asyncio.run(run())

    assert "正在规划任务：" in result
    assert "开始执行计划" in result
    assert "已完成 [task_1]" in result
    assert "已完成 [task_2]" in result
    assert "计划执行完成" in result
    assert "Planning task" not in result
    assert "Completed [" not in result
    assert client.task_system_prompts
    assert all(
        "所有进度说明、分析和最终结果都必须使用中文" in prompt
        for prompt in client.task_system_prompts
    )
    assert client.peak_concurrency == 2
    assert any(
        event.get("type") == "thinking_delta" and event.get("phase") == "planning"
        for event in events
    )
    assert {
        event.get("task_id")
        for event in events
        if event.get("type") == "thinking_delta" and event.get("phase") == "execution"
    } == {"task_1", "task_2"}
    assert {
        event.get("task_id") for event in events if event.get("type") == "plan_task_started"
    } == {"task_1", "task_2"}


class FakeClient:
    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        yield {"type": "text_delta", "text": "{}"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


# Long enough and multi-step ("先…然后…") so _is_simple_goal never shortcuts
# the planner LLM round-trip in the retry tests below.
_MULTI_STEP_GOAL = "先读取 README.md 的第一行，然后用一句话总结这个项目是做什么的"

_PROSE_REPLY = '我先查看当前目录，然后读取 README.md。\n\n<｜DSML｜｜ invoke name="bash">ls -la'


class ProseThenJsonClient(FakeClient):
    """First planner reply is prose; the repair round returns valid JSON."""

    def __init__(self):
        self.calls: list[list[Message]] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            yield {"type": "text_delta", "text": _PROSE_REPLY}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        yield {
            "type": "text_delta",
            "text": (
                '{"summary":"repaired","tasks":'
                '[{"id":"a","description":"读取 README","type":"FILE_READ","dependencies":[]}]}'
            ),
        }
        yield {"type": "message_end", "stop_reason": "end_turn"}


class TransientErrorThenJsonClient(FakeClient):
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.calls += 1
        if self.calls == 1:
            yield {
                "type": "error",
                "error": RuntimeError(
                    "openai-compatible API returned HTTP 503. "
                    "Check the API key, model access, account balance, and provider status."
                ),
            }
            return
        yield {
            "type": "text_delta",
            "text": (
                '{"summary":"retried","tasks":'
                '[{"id":"a","description":"A","type":"ANALYSIS","dependencies":[]}]}'
            ),
        }
        yield {"type": "message_end", "stop_reason": "end_turn"}


class AlwaysProseClient(FakeClient):
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.calls += 1
        yield {"type": "text_delta", "text": "我先查看当前目录，然后读取 README.md 的第一行。"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


class ParallelPlanClient(FakeClient):
    def __init__(self):
        self.current_concurrency = 0
        self.peak_concurrency = 0
        self.ready = asyncio.Event()
        self.task_system_prompts: list[str] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        body = _message_text(messages[-1].content)
        if "请为以下目标创建执行计划" in body:
            yield {"type": "thinking_delta", "thinking": "先拆分可并行任务"}
            yield {
                "type": "text_delta",
                "text": (
                    '{"summary":"parallel","tasks":['
                    '{"id":"a","description":"任务 A","type":"ANALYSIS","dependencies":[]},'
                    '{"id":"b","description":"任务 B","type":"ANALYSIS","dependencies":[]}'
                    "]}"
                ),
            }
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return

        if "任务 A" in body or "任务 B" in body:
            self.task_system_prompts.append(system_prompt)
            self.current_concurrency += 1
            self.peak_concurrency = max(self.peak_concurrency, self.current_concurrency)
            if self.current_concurrency == 2:
                self.ready.set()
            await asyncio.wait_for(self.ready.wait(), timeout=2)
            self.current_concurrency -= 1
            text = "A 的结果" if "任务 A" in body else "B 的结果"
            yield {"type": "thinking_delta", "thinking": f"分析{text}"}
            yield {"type": "text_delta", "text": text}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return

        yield {"type": "text_delta", "text": "fallback"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return str(content)
