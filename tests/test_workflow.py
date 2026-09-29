from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.repl import (
    SLASH_COMMANDS,
    PermissionModeController,
    ReplSessionState,
    _handle_slash,
)
from nexuscli.entrypoints.slash_commands import (
    build_expert_graph,
    build_step_dispatch_prompt,
)
from nexuscli.plan import ExecutionPlan, Task, TaskType
from nexuscli.session import SessionStore
from nexuscli.workflow import (
    WorkflowError,
    WorkflowGraph,
    WorkflowStep,
    load_workflow,
    load_workflow_file,
)

# ---------------------------------------------------------------------------
# graph: load / validate / order
# ---------------------------------------------------------------------------


def test_load_workflow_parses_steps_and_topological_order():
    graph = load_workflow(
        {
            "steps": [
                {"id": "a", "prompt": "do a"},
                {"id": "b", "prompt": "do b"},
                {"id": "c", "prompt": "do c", "depends_on": ["a", "b"]},
            ]
        }
    )

    assert list(graph.steps) == ["a", "b", "c"]
    assert graph.steps["a"].prompt == "do a"
    # depends_on omitted → defaults to [].
    assert graph.steps["a"].depends_on == []
    assert graph.steps["c"].depends_on == ["a", "b"]
    assert graph.topological_order() == ["a", "b", "c"]
    assert graph.ready_steps(set()) == ["a", "b"]
    assert graph.ready_steps({"a"}) == ["b"]
    assert graph.ready_steps({"a", "b"}) == ["c"]


@pytest.mark.parametrize(
    "payload",
    [
        "just a string",  # payload not a dict
        {},  # steps key missing
        {"steps": "nope"},  # steps not a list
        {"steps": []},  # empty steps
        {"steps": ["nope"]},  # step not an object
        {"steps": [{"prompt": "p"}]},  # id missing
        {"steps": [{"id": 1, "prompt": "p"}]},  # id not a string
        {"steps": [{"id": "   ", "prompt": "p"}]},  # id blank after strip
        {"steps": [{"id": "a", "prompt": "p"}, {"id": " a ", "prompt": "q"}]},  # duplicate id
        {"steps": [{"id": "a"}]},  # prompt missing
        {"steps": [{"id": "a", "prompt": " \n"}]},  # prompt blank after strip
        {"steps": [{"id": "a", "prompt": "p", "depends_on": "b"}]},  # depends_on not a list
        {"steps": [{"id": "a", "prompt": "p", "depends_on": [1]}]},  # element not a string
        {"steps": [{"id": "a", "prompt": "p", "depends_on": ["ghost"]}]},  # dangling reference
    ],
)
def test_load_workflow_rejects_invalid_payloads(payload):
    with pytest.raises(WorkflowError):
        load_workflow(payload)


def test_load_workflow_rejects_cycles(tmp_path):
    cases = [
        # Self-dependency: one-node cycle.
        {"steps": [{"id": "a", "prompt": "p", "depends_on": ["a"]}]},
        # Two-node cycle.
        {
            "steps": [
                {"id": "a", "prompt": "p", "depends_on": ["b"]},
                {"id": "b", "prompt": "q", "depends_on": ["a"]},
            ]
        },
        # Three-node cycle a → c → b → a.
        {
            "steps": [
                {"id": "a", "prompt": "p", "depends_on": ["c"]},
                {"id": "b", "prompt": "q", "depends_on": ["a"]},
                {"id": "c", "prompt": "r", "depends_on": ["b"]},
            ]
        },
    ]
    for payload in cases:
        with pytest.raises(WorkflowError) as exc_info:
            load_workflow(payload)
        assert "cycle" in str(exc_info.value)
        assert payload["steps"][0]["id"] in str(exc_info.value)

    # One cycle case through the file path as well.
    path = tmp_path / "cycle.json"
    path.write_text(json.dumps(cases[1]), encoding="utf-8")
    with pytest.raises(WorkflowError) as exc_info:
        load_workflow_file(path)
    assert "cycle" in str(exc_info.value)


def test_load_workflow_file_wraps_read_errors(tmp_path):
    with pytest.raises(WorkflowError) as exc_info:
        load_workflow_file(tmp_path / "missing.json")
    assert "cannot read" in str(exc_info.value)

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(WorkflowError) as exc_info:
        load_workflow_file(bad)
    assert "not valid JSON" in str(exc_info.value)


def test_ready_steps_match_plan_is_executable():
    # Same shape as tests/test_plan.py::test_execution_plan_exposes_dag_batches:
    # task_1 / task_2 independent, task_3 depends on both.
    graph = WorkflowGraph(
        steps={
            "task_1": WorkflowStep(id="task_1", prompt="read a"),
            "task_2": WorkflowStep(id="task_2", prompt="read b"),
            "task_3": WorkflowStep(
                id="task_3",
                prompt="summarize",
                depends_on=["task_1", "task_2"],
            ),
        }
    )
    plan = ExecutionPlan(id="plan_1", goal="demo")
    plan.add_task(Task("task_1", "read a", TaskType.FILE_READ))
    plan.add_task(Task("task_2", "read b", TaskType.FILE_READ))
    plan.add_task(Task("task_3", "summarize", TaskType.ANALYSIS, ["task_1", "task_2"]))

    completed: set[str] = set()
    for task in (plan.get_task("task_1"), plan.get_task("task_2"), plan.get_task("task_3")):
        assert graph.ready_steps(completed) == [ready.id for ready in plan.executable_tasks()]
        task.mark_completed("done")
        completed.add(task.id)
    assert graph.ready_steps(completed) == []
    assert plan.executable_tasks() == []


# ---------------------------------------------------------------------------
# /expert graph and controlled dispatch prompt
# ---------------------------------------------------------------------------


def test_expert_graph_builds_three_step_loop():
    graph = build_expert_graph("重构登录模块")

    assert list(graph.steps) == ["research", "execute", "report"]
    assert graph.steps["research"].depends_on == []
    assert graph.steps["execute"].depends_on == ["research"]
    assert graph.steps["report"].depends_on == ["execute"]
    for step in graph.steps.values():
        assert "重构登录模块" in step.prompt


def test_expert_dispatch_prompt_contains_steps():
    graph = build_expert_graph("重构登录模块")

    prompt = build_step_dispatch_prompt(graph, "execute", {"research": "调研报告全文XYZ"})

    assert "你是受控工作流编排中的子代理" in prompt
    # The step list covers all three steps in topological order with deps.
    assert prompt.index("- research（无依赖）") < prompt.index("- execute（依赖：research）")
    assert prompt.index("- execute（依赖：research）") < prompt.index("- report（依赖：execute）")
    # The dispatched step marker with position and the step prompt in full.
    assert "本次执行的步骤：execute（第 2/3 步）" in prompt
    assert "基于上一步的调研要点" in prompt
    # Upstream report injected under its dependency heading.
    assert "上游步骤报告" in prompt
    assert "### research" in prompt
    assert "调研报告全文XYZ" in prompt

    # A step without dependencies has no upstream-report section at all.
    head = build_step_dispatch_prompt(graph, "research", {})
    assert "上游步骤报告" not in head


# ---------------------------------------------------------------------------
# REPL branches (run_subagent faked; no LLM)
# ---------------------------------------------------------------------------


def _string_io():
    return io.StringIO()


def _console_text(console: Console) -> str:
    return console.file.getvalue()


def _session_state(tmp_path: Path) -> ReplSessionState:
    store = SessionStore(root=tmp_path / "sessions")
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    return ReplSessionState(store=store, writer=writer)


def _run_slash(message: str, tmp_path: Path) -> Console:
    console = Console(file=_string_io(), width=200)
    asyncio.run(
        _handle_slash(
            message,
            console,
            str(tmp_path),
            NexusCliConfig(),
            SimpleNamespace(history=[], cwd=str(tmp_path), approval_callback=None),
            None,
            PermissionModeController(NexusCliConfig()),
            None,
            _session_state(tmp_path),
        )
    )
    return console


_THREE_STEP_PAYLOAD = {
    "steps": [
        {"id": "research", "prompt": "调研步骤"},
        {"id": "implement", "prompt": "实现步骤", "depends_on": ["research"]},
        {"id": "summarize", "prompt": "汇总步骤", "depends_on": ["implement"]},
    ]
}


def test_handle_slash_workflow_run_dispatches_in_topological_order(tmp_path, monkeypatch):
    (tmp_path / "plan.json").write_text(
        json.dumps(_THREE_STEP_PAYLOAD, ensure_ascii=False), encoding="utf-8"
    )
    calls: list[str] = []
    reports = ["报告一", "报告二", "报告三"]

    async def fake_run_subagent(agent_def, prompt, context):
        calls.append(prompt)
        return reports[len(calls) - 1]

    monkeypatch.setattr("nexuscli.agent.subagent.run_subagent", fake_run_subagent)

    console = _run_slash("/workflow run plan.json", tmp_path)

    assert len(calls) == 3
    assert "本次执行的步骤：research" in calls[0]
    assert "本次执行的步骤：implement" in calls[1]
    assert "本次执行的步骤：summarize" in calls[2]
    # Each step's prompt collects its upstream reports verbatim.
    assert "### research" in calls[1]
    assert "报告一" in calls[1]
    assert "### implement" in calls[2]
    assert "报告二" in calls[2]
    text = _console_text(console)
    assert "dispatching step research" in text
    assert "dispatching step implement" in text
    assert "dispatching step summarize" in text
    assert "=== research ===" in text
    assert "=== implement ===" in text
    assert "=== summarize ===" in text
    for report in reports:
        assert report in text


def test_handle_slash_workflow_usage_and_error_paths(tmp_path, monkeypatch):
    calls: list[str] = []

    async def fake_run_subagent(agent_def, prompt, context):
        calls.append(prompt)
        return "不应被调用"

    monkeypatch.setattr("nexuscli.agent.subagent.run_subagent", fake_run_subagent)

    for message in ("/workflow", "/workflow run"):
        console = _run_slash(message, tmp_path)
        assert "Usage: /workflow run <file>.json" in _console_text(console)
    assert calls == []

    console = _run_slash("/workflow run missing.json", tmp_path)
    text = _console_text(console)
    assert "Workflow error:" in text
    assert "cannot read" in text

    (tmp_path / "plan.json").write_text("{not json", encoding="utf-8")
    console = _run_slash("/workflow run plan.json", tmp_path)
    text = _console_text(console)
    assert "Workflow error:" in text
    assert "not valid JSON" in text
    assert calls == []


def test_handle_slash_workflow_aborts_remaining_on_failure(tmp_path, monkeypatch):
    (tmp_path / "plan.json").write_text(
        json.dumps(_THREE_STEP_PAYLOAD, ensure_ascii=False), encoding="utf-8"
    )
    calls: list[str] = []

    async def fake_run_subagent(agent_def, prompt, context):
        calls.append(prompt)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return f"报告{len(calls)}"

    monkeypatch.setattr("nexuscli.agent.subagent.run_subagent", fake_run_subagent)

    console = _run_slash("/workflow run plan.json", tmp_path)

    assert len(calls) == 2  # the third step is never dispatched
    text = _console_text(console)
    assert "Step implement failed: boom" in text
    assert "中止剩余步骤：summarize" in text
    assert "Workflow complete" not in text


def test_handle_slash_expert_dispatches_and_collects(tmp_path, monkeypatch):
    calls: list[str] = []
    reports = ["调研要点清单", "核心工作完成", "最终综合报告"]

    async def fake_run_subagent(agent_def, prompt, context):
        calls.append(prompt)
        return reports[len(calls) - 1]

    monkeypatch.setattr("nexuscli.agent.subagent.run_subagent", fake_run_subagent)

    console = _run_slash("/expert 重构登录模块", tmp_path)

    assert len(calls) == 3
    assert all("重构登录模块" in prompt for prompt in calls)
    text = _console_text(console)
    for report in reports:
        assert report in text

    # No topic → usage line, zero dispatches.
    calls.clear()
    console = _run_slash("/expert", tmp_path)
    assert "Usage: /expert <topic>" in _console_text(console)
    assert calls == []


def test_slash_commands_list_includes_new_entries():
    assert "/workflow" in SLASH_COMMANDS
    assert "/expert" in SLASH_COMMANDS
