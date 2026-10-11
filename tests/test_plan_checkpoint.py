"""tests/test_plan_checkpoint.py — graph-level checkpoint, interrupt and resume.

Covers: Task/ExecutionPlan serialization round trips, per-task checkpoint
writes during ``run()``, resuming a partially completed plan (only PENDING
tasks re-execute), the cooperative ``request_interrupt()`` between batches,
and the checkpoint root resolution (HOME redirect + injectable root) — all
against a temp home so the real ``~/.nexuscli`` is never touched.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from nexuscli.agent import PlanExecuteAgent
from nexuscli.config import load_config
from nexuscli.plan import ExecutionPlan, PlanStatus, Task, TaskStatus, TaskType
from nexuscli.plan.checkpoint import PlanCheckpointStore, plan_runs_root
from nexuscli.tools import ToolRegistry, get_builtin_tools


@pytest.fixture
def checkpoint_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the plan-run directory into a temp home.

    plan/checkpoint.py resolves ~/.nexuscli/plan_runs through Path.home(),
    which on Windows reads USERPROFILE (not HOME), so both environment
    variables are redirected as well — same convention as
    tests/test_background.py::background_home.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


# ---------------------------------------------------------------------------
# ① Serialization round trips.
# ---------------------------------------------------------------------------


def test_task_and_plan_round_trip():
    plan = ExecutionPlan(id="plan_rt", goal="round trip 目标")
    plan.summary = "序列化往返"
    task_1 = Task(id="task_1", description="任务 A", type=TaskType.FILE_READ)
    task_1.mark_completed("A 的结果")
    task_2 = Task(
        id="task_2",
        description="任务 B",
        type=TaskType.VERIFICATION,
        dependencies=["task_1"],
    )
    task_2.mark_failed("boom")
    task_3 = Task(id="task_3", description="任务 C", type=TaskType.COMMAND)
    plan.add_task(task_1)
    plan.add_task(task_2)
    plan.add_task(task_3)
    plan.mark_started()

    data = plan.to_dict()

    # StrEnum fields serialize by .value; derived state stays out.
    assert data["status"] == "RUNNING"
    assert data["tasks"]["task_1"]["type"] == "FILE_READ"
    assert data["tasks"]["task_1"]["status"] == "COMPLETED"
    assert data["tasks"]["task_1"]["result"] == "A 的结果"
    assert data["tasks"]["task_2"]["dependencies"] == ["task_1"]
    assert "_execution_order" not in data

    restored = ExecutionPlan.from_dict(data)

    assert restored.to_dict() == data
    assert restored.execution_order() == plan.execution_order()
    assert restored.get_task("task_2").dependents == []
    assert restored.get_task("task_1").dependents == ["task_2"]
    assert restored.status == PlanStatus.RUNNING
    assert restored.summarize() == plan.summarize()


def test_from_dict_degrades_unknown_enum_and_garbage_fields():
    task = Task.from_dict(
        {"id": "x", "type": "NOPE", "status": "WEIRD", "start_time": "not-a-float"}
    )
    assert task.type == TaskType.ANALYSIS
    assert task.status == TaskStatus.PENDING
    assert task.start_time == 0.0

    plan = ExecutionPlan.from_dict({"id": "p", "status": "NOPE", "start_time": "bad"})
    assert plan.status == PlanStatus.CREATED
    assert plan.start_time == 0.0


def test_from_dict_normalizes_running_to_pending():
    """A crash inside a parallel apply window persists RUNNING tasks.

    On restore nothing is actually running, and ``is_executable`` only accepts
    PENDING — a literal RUNNING restore would stall the plan into the FAILED
    path forever, so it must normalize back to PENDING and re-execute.
    """
    data = {
        "id": "plan_crash",
        "goal": "crash recovery",
        "status": "RUNNING",
        "tasks": {
            "task_1": {"id": "task_1", "description": "A", "status": "COMPLETED"},
            "task_2": {"id": "task_2", "description": "B", "status": "RUNNING"},
        },
    }
    plan = ExecutionPlan.from_dict(data)

    restored_task = plan.get_task("task_2")
    assert restored_task.status == TaskStatus.PENDING
    assert restored_task.is_executable(plan.tasks)  # PENDING + dependency done
    # Finished work and the resumable plan status are untouched.
    assert plan.get_task("task_1").status == TaskStatus.COMPLETED
    assert plan.status == PlanStatus.RUNNING


# ---------------------------------------------------------------------------
# Fake clients (FakeClient style from tests/test_plan.py).
# ---------------------------------------------------------------------------


class ChainPlanClient:
    """Planner returns a task_1 -> task_2 chain; tasks answer with text."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self, probe=None):
        self.task_calls: list[str] = []
        self.task_bodies: list[str] = []
        self.planner_calls = 0
        self.probe = probe  # called once, at task_2 chat time

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        body = _message_text(messages[-1].content)
        if "请为以下目标创建执行计划" in body:
            self.planner_calls += 1
            yield {"type": "text_delta", "text": _CHAIN_PLAN_JSON}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        if "当前任务 [task_1]" in body:
            self.task_calls.append("task_1")
            self.task_bodies.append(body)
            yield {"type": "text_delta", "text": "A 的新结果"}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        if "当前任务 [task_2]" in body:
            self.task_calls.append("task_2")
            self.task_bodies.append(body)
            if self.probe is not None:
                self.probe()
            yield {"type": "text_delta", "text": "B 的新结果"}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        yield {"type": "text_delta", "text": "fallback"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


class InterruptingChainClient(ChainPlanClient):
    """task_1's handler raises the interrupt flag before answering."""

    def __init__(self, interrupt):
        super().__init__()
        self._interrupt = interrupt

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        body = _message_text(messages[-1].content)
        if "当前任务 [task_1]" in body:
            self.task_calls.append("task_1")
            self._interrupt()
            yield {"type": "text_delta", "text": "A 的新结果"}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        async for event in super().chat(messages, tools, system_prompt=system_prompt):
            yield event


_CHAIN_PLAN_JSON = (
    '{"summary":"chain","tasks":['
    '{"id":"a","description":"任务 A","type":"ANALYSIS","dependencies":[]},'
    '{"id":"b","description":"任务 B","type":"ANALYSIS","dependencies":["a"]}]}'
)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return str(content)


def _make_agent(client, tmp_path, **kwargs) -> PlanExecuteAgent:
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "never"
    return PlanExecuteAgent(
        llm_client=client,
        tool_registry=registry,
        config=config,
        cwd=str(tmp_path),
        **kwargs,
    )


async def _drain(events):
    collected = []
    text = ""
    async for event in events:
        if event.get("type") == "error":
            raise event["error"]
        collected.append(event)
        if event.get("type") == "text_delta":
            text += str(event.get("text") or "")
    return collected, text


def _chain_plan(plan_id: str, completed_first: bool) -> ExecutionPlan:
    """task_1 -> task_2; task_1 optionally already completed with a result."""
    plan = ExecutionPlan(id=plan_id, goal="先完成任务 A，然后执行任务 B")
    plan.summary = "链式两步"
    task_1 = Task(id="task_1", description="任务 A", type=TaskType.ANALYSIS)
    if completed_first:
        task_1.mark_completed("A 已有结果")
    task_2 = Task(
        id="task_2",
        description="任务 B",
        type=TaskType.ANALYSIS,
        dependencies=["task_1"],
    )
    plan.add_task(task_1)
    plan.add_task(task_2)
    plan.compute_execution_order()
    return plan


# ---------------------------------------------------------------------------
# ② run() writes a checkpoint after the planner and after every task.
# ---------------------------------------------------------------------------


def test_run_writes_checkpoint_after_each_task(tmp_path, checkpoint_home):
    store = PlanCheckpointStore()
    probe_result: dict[str, Any] = {}

    def probe():
        # Called at task_2 chat time: task_1's checkpoint must already exist.
        files = list(store.root.glob("*.json"))
        probe_result["file_count"] = len(files)
        loaded = store.load(files[0].stem) if files else None
        probe_result["task_1"] = loaded.get_task("task_1").status.value if loaded else None
        probe_result["task_2"] = loaded.get_task("task_2").status.value if loaded else None

    client = ChainPlanClient(probe=probe)
    agent = _make_agent(client, tmp_path)

    async def run():
        return await _drain(agent.run("先完成任务 A，然后执行任务 B"))

    events, text = asyncio.run(run())

    plan_ids = [event["plan_id"] for event in events if event.get("type") == "plan_checkpointed"]
    assert len(plan_ids) == 1
    plan_id = plan_ids[0]
    assert f"/plan --resume {plan_id}" in text

    # At task_2 chat time the checkpoint existed with task_1 completed.
    assert probe_result == {"file_count": 1, "task_1": "COMPLETED", "task_2": "PENDING"}

    loaded = store.load(plan_id)
    assert loaded is not None
    assert loaded.status == PlanStatus.COMPLETED
    assert loaded.get_task("task_1").result == "A 的新结果"
    assert loaded.get_task("task_2").result == "B 的新结果"
    assert loaded.is_all_completed()
    assert [event["type"] for event in events][-1] == "done"


# ---------------------------------------------------------------------------
# ③ Resume executes only the PENDING tasks of a partial checkpoint.
# ---------------------------------------------------------------------------


def test_resume_executes_only_pending_tasks(tmp_path, checkpoint_home):
    store = PlanCheckpointStore()
    plan = _chain_plan("plan_resume_1", completed_first=True)
    store.save(plan)

    client = ChainPlanClient()
    agent = _make_agent(client, tmp_path)

    async def run():
        return await _drain(agent.run_resume("plan_resume_1"))

    events, text = asyncio.run(run())

    # Planning was skipped entirely: no planner call, no plan_created.
    assert client.planner_calls == 0
    assert client.task_calls == ["task_2"]
    assert not any(event.get("type") == "plan_created" for event in events)
    assert "恢复计划 plan_resume_1" in text
    # The stored dependency result feeds the resumed task's context.
    assert "A 已有结果" in client.task_bodies[0]

    loaded = store.load("plan_resume_1")
    assert loaded.get_task("task_1").status == TaskStatus.COMPLETED
    assert loaded.get_task("task_1").result == "A 已有结果"  # preserved verbatim
    assert loaded.get_task("task_2").status == TaskStatus.COMPLETED
    assert loaded.get_task("task_2").result == "B 的新结果"
    assert loaded.status == PlanStatus.COMPLETED
    assert events[-1]["type"] == "done"


def test_resume_reports_error_for_unknown_plan_id(tmp_path, checkpoint_home):
    client = ChainPlanClient()
    agent = _make_agent(client, tmp_path)

    async def run():
        collected = []
        async for event in agent.run_resume("plan_missing"):
            collected.append(event)
        return collected

    events = asyncio.run(run())

    assert [event["type"] for event in events] == ["error"]
    assert "plan_missing" in str(events[0]["error"])
    assert client.task_calls == []


# ---------------------------------------------------------------------------
# ④ request_interrupt stops cooperatively between batches.
# ---------------------------------------------------------------------------


def test_request_interrupt_stops_between_batches(tmp_path, checkpoint_home):
    store = PlanCheckpointStore()
    plan = _chain_plan("plan_interrupt_1", completed_first=False)
    store.save(plan)

    client = InterruptingChainClient(interrupt=lambda: None)  # replaced below
    agent = _make_agent(client, tmp_path)
    client._interrupt = agent.request_interrupt

    async def run():
        return await _drain(agent.run_resume("plan_interrupt_1"))

    events, text = asyncio.run(run())

    interrupted = [event for event in events if event.get("type") == "plan_interrupted"]
    assert len(interrupted) == 1
    assert interrupted[0]["plan_id"] == "plan_interrupt_1"
    assert "/plan --resume plan_interrupt_1" in text  # resume hint is user-visible
    # task_1 answered, then the loop stopped before batch 2.
    assert client.task_calls == ["task_1"]

    loaded = store.load("plan_interrupt_1")
    assert loaded.get_task("task_1").status == TaskStatus.COMPLETED
    assert loaded.get_task("task_2").status == TaskStatus.PENDING
    assert loaded.status == PlanStatus.RUNNING  # still resumable
    assert events[-1]["type"] == "done"


# ---------------------------------------------------------------------------
# ⑤ Root resolution: HOME redirect + injectable root.
# ---------------------------------------------------------------------------


def test_checkpoint_root_follows_home_and_accepts_explicit_root(tmp_path, checkpoint_home):
    assert plan_runs_root() == checkpoint_home / ".nexuscli" / "plan_runs"
    assert plan_runs_root(tmp_path / "alt") == tmp_path / "alt" / ".nexuscli" / "plan_runs"
    default_store = PlanCheckpointStore()
    assert default_store.root == checkpoint_home / ".nexuscli" / "plan_runs"

    explicit_store = PlanCheckpointStore(root=tmp_path / "custom")
    plan = _chain_plan("plan_explicit_1", completed_first=False)
    path = explicit_store.save(plan)

    assert path == tmp_path / "custom" / "plan_explicit_1.json"
    assert explicit_store.load("plan_explicit_1") is not None
    # The default-root store must not see the explicitly rooted checkpoint.
    assert default_store.load("plan_explicit_1") is None


def test_store_load_degrades_corrupt_checkpoint(tmp_path):
    store = PlanCheckpointStore(root=tmp_path / "runs")
    store.root.mkdir(parents=True)
    (store.root / "plan_bad.json").write_text("{not json", encoding="utf-8")
    (store.root / "plan_wrong_shape.json").write_text('{"plan": 3}', encoding="utf-8")

    assert store.load("plan_bad") is None
    assert store.load("plan_wrong_shape") is None
    assert store.load("plan_absent") is None


def test_store_rejects_unsafe_plan_ids(tmp_path):
    """plan ids reach ``root / f"{id}.json"`` from raw /plan --resume input."""
    store = PlanCheckpointStore(root=tmp_path / "runs")

    for unsafe in ("../secret", "a/b", "a\\b", "..", "", ".hidden"):
        with pytest.raises(ValueError):
            store.path_for(unsafe)
        # The read path degrades an unsafe id to "not found": it must never
        # probe an arbitrary .json outside plan_runs.
        assert store.load(unsafe) is None

    # Generated ids (timestamp + uuid suffix) and test ids stay filename-safe.
    for safe in ("plan_1760000000000_0a1b2c3d", "plan_resume_1", "plan_rt"):
        assert store.path_for(safe) == tmp_path / "runs" / f"{safe}.json"
