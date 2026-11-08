"""Concurrent plan/team execution and journal recovery (spec task_0024).

The plan side (``PlanExecuteAgent``, task_0015: task workers claim side
effects in the tool journal under the plan's persisted ``run_id``) and the
team side (``AgentOrchestrator``, task_0014: workers start from isolated
history snapshots, increments fold back in task-index order) were each
covered on their own; this file runs them **concurrently in one event loop
and one shared tool journal** and pins the composition contracts:

* **no duplicate side effects** — every scripted ``write_file`` runs exactly
  once, and a crashed plan run resumed afterwards replays completed calls
  from the journal instead of re-executing them (both dedup branches: the
  exact ``tool_use_id`` claim and the hash-level ``lookup_by_hash`` replay);
* **no history crosstalk** — the plan agent's history never carries team
  worker texts, the orchestrator's history never carries plan task texts,
  and each worker's first-round input contains only its own task (the
  plan-agent runs and the team runs keep disjoint histories although they
  share one process, one registry and one journal singleton);
* **journal keying** — rows live under the plan's ``run_id`` only: the same
  lookup under another run identity misses, so a concurrent run can never
  consume another run's stored results, and no ``started`` /
  ``outcome_unknown`` rows dangle after a resume.

Crash injection follows ``test_plan_checkpoint.py``: the event consumer
raises after a ``tool_result`` event is seen — by that point the executor has
already completed the journal row, so the "process death" leaves a
consistent, resumable state behind.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from nexuscli.agent.orchestrator import AgentOrchestrator
from nexuscli.agent.plan_execute import PlanExecuteAgent
from nexuscli.config import load_config
from nexuscli.plan.checkpoint import PlanCheckpointStore
from nexuscli.tools import ToolRegistry
from nexuscli.tools import journal as journal_module
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.journal import ToolJournal, journal_db_path
from nexuscli.types import Message


@pytest.fixture
def journal_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the journal DB into a temp home, per test.

    Same convention as ``tests/test_plan_checkpoint.py``: HOME **and**
    USERPROFILE (win32 ``Path.home()`` prefers USERPROFILE), ``Path.home``
    patched for code that reads it directly, and the process-level journal
    singleton reset so no earlier test's DB leaks in (and none of ours leaks
    out).
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(journal_module, "_journal", None)
    yield home
    journal_module._journal = None


# ---------------------------------------------------------------------------
# Shared fakes: a counted write_file + scripted plan/team clients
# ---------------------------------------------------------------------------


class _CrashAfterToolResult(Exception):
    """Simulated process death at the event consumer."""


def _counted_write_tool(counter: dict[str, int]) -> Tool:
    """A fake ``write_file``: covered by the executor's claim protocol
    (``_JOURNALED_TOOLS``) and counting every real execution by path."""

    async def handler(data: dict[str, Any], context: ToolContext) -> ToolResult:  # noqa: ARG001
        counter[str(data.get("path"))] = counter.get(str(data.get("path")), 0) + 1
        return ToolResult(content=f"wrote {data.get('path')}", is_error=False)

    return Tool(
        name="write_file",
        description="Fake side-effectful write (journalled).",
        parameters=object_schema(
            {"path": {"type": "string"}, "content": {"type": "string"}},
            required=["path", "content"],
        ),
        handler=handler,
        is_read_only=False,
        is_concurrency_safe=False,
    )


def _tool_call(call_id: str, path: str, content: str) -> dict[str, Any]:
    """Event shape consumed by the react loop's ``_merge_tool_delta``."""
    return {
        "index": 0,
        "id": call_id,
        "function": {
            "name": "write_file",
            "arguments": json.dumps({"path": path, "content": content}),
        },
    }


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return str(content)


def _text_then_end(text: str) -> list[dict[str, Any]]:
    return [
        {"type": "text_delta", "text": text},
        {"type": "message_end", "stop_reason": "end_turn"},
    ]


def _tool_call_then_end(call: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"type": "tool_call_delta", "tool_call": call},
        {"type": "message_end", "stop_reason": "tool_use"},
    ]


# Two independent tasks: the executor runs them as one parallel batch.
_PARALLEL_PLAN_JSON = (
    '{"summary":"parallel plan","tasks":['
    '{"id":"a","description":"计划任务 A","type":"ANALYSIS","dependencies":[]},'
    '{"id":"b","description":"计划任务 B","type":"ANALYSIS","dependencies":[]}]}'
)

# task a -> task b chain: deterministic single-side-effect crash point.
_CHAIN_PLAN_JSON = (
    '{"summary":"chain plan","tasks":['
    '{"id":"a","description":"计划任务 A","type":"ANALYSIS","dependencies":[]},'
    '{"id":"b","description":"计划任务 B","type":"ANALYSIS","dependencies":["a"]}]}'
)

# Two independent team steps (parse_plan remaps ids to step_1/step_2).
_TEAM_STEPS_JSON = (
    '{"summary":"parallel team","steps":['
    '{"id":"ta","description":"Team Task A","type":"ANALYSIS","dependencies":[]},'
    '{"id":"tb","description":"Team Task B","type":"ANALYSIS","dependencies":[]}]}'
)


class ParallelPlanClient:
    """Planner returns two independent tasks; each task writes one file and
    then answers with its own (distinctly marked) text.

    Round 2 of a task is recognized by the tool result sitting last in the
    conversation; the file path inside it tells the two tasks apart. A fresh
    instance re-issues the same calls under the same ``tool_use_id`` — the
    exact-claim replay branch of the journal dedup.
    """

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self) -> None:
        self.task_inputs: list[str] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        last = messages[-1]
        body = _message_text(last.content)
        events: list[dict[str, Any]] | None = None
        if "请为以下目标创建执行计划" in body:
            events = _text_then_end(_PARALLEL_PLAN_JSON)
        elif last.role == "tool":
            task = "A" if "plan-file-a" in body else "B"
            events = _text_then_end(f"plan 任务 {task} 完成")
        elif "当前任务 [task_1]" in body:
            self.task_inputs.append(body)
            events = _tool_call_then_end(_tool_call("call_orig_a", "plan-file-a.txt", "plan-a"))
        elif "当前任务 [task_2]" in body:
            self.task_inputs.append(body)
            events = _tool_call_then_end(_tool_call("call_orig_b", "plan-file-b.txt", "plan-b"))
        if events is None:
            events = _text_then_end("fallback")
        for event in events:
            yield event


class ChainPlanClient:
    """Planner returns an a -> b chain; each task writes one file.

    ``call_id_a``/``call_id_b`` are injectable so a resumed run can re-issue
    the same calls under *fresh* tool_use_id — the hash-level replay branch
    (``lookup_by_hash``) of the journal dedup.
    """

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self, call_id_a: str = "call_orig_a", call_id_b: str = "call_orig_b") -> None:
        self.task_inputs: list[str] = []
        self._call_id_a = call_id_a
        self._call_id_b = call_id_b

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        last = messages[-1]
        body = _message_text(last.content)
        events: list[dict[str, Any]] | None = None
        if "请为以下目标创建执行计划" in body:
            events = _text_then_end(_CHAIN_PLAN_JSON)
        elif last.role == "tool":
            task = "A" if "plan-file-a" in body else "B"
            events = _text_then_end(f"plan 任务 {task} 完成")
        elif "当前任务 [task_1]" in body:
            self.task_inputs.append(body)
            events = _tool_call_then_end(_tool_call(self._call_id_a, "plan-file-a.txt", "plan-a"))
        elif "当前任务 [task_2]" in body:
            self.task_inputs.append(body)
            events = _tool_call_then_end(_tool_call(self._call_id_b, "plan-file-b.txt", "plan-b"))
        if events is None:
            events = _text_then_end("fallback")
        for event in events:
            yield event


class ParallelTeamClient:
    """Team planner returns two independent steps; each worker writes one
    file (team workers run ``query()`` without a journal — side effects just
    execute), then answers with its own distinctly marked text."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self) -> None:
        self.worker_inputs: list[str] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        last = messages[-1]
        body = _message_text(last.content)
        events: list[dict[str, Any]] | None = None
        if "Create an execution plan" in body:
            events = _text_then_end(_TEAM_STEPS_JSON)
        elif "Original task" in body:
            events = _text_then_end('{"approved": true, "issues": []}')
        elif last.role == "tool":
            task = "A" if "team-file-a" in body else "B"
            events = _text_then_end(f"team 任务 {task} 完成")
        elif "Current task:" in body and "Team Task A" in body:
            self.worker_inputs.append(body)
            events = _tool_call_then_end(_tool_call("call_team_a", "team-file-a.txt", "team-a"))
        elif "Current task:" in body and "Team Task B" in body:
            self.worker_inputs.append(body)
            events = _tool_call_then_end(_tool_call("call_team_b", "team-file-b.txt", "team-b"))
        if events is None:
            events = _text_then_end("fallback")
        for event in events:
            yield event


# ---------------------------------------------------------------------------
# Wiring + drain helpers
# ---------------------------------------------------------------------------


def _plan_agent(client, tmp_path, registry: ToolRegistry) -> PlanExecuteAgent:
    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "never"
    return PlanExecuteAgent(
        llm_client=client,
        tool_registry=registry,
        config=config,
        cwd=str(tmp_path),
        use_journal=True,
    )


def _team_orchestrator(client, tmp_path, registry: ToolRegistry) -> AgentOrchestrator:
    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "never"
    return AgentOrchestrator(
        llm_client=client,
        tool_registry=registry,
        config=config,
        cwd=str(tmp_path),
    )


def _registry(counter: dict[str, int]) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_counted_write_tool(counter))
    return registry


async def _collect(stream) -> list[dict[str, Any]]:
    """Drain an event stream without raising on ``error`` events, so tests
    can assert on the full shape (including a run that must stay clean)."""
    return [event async for event in stream]


async def _collect_plan(stream, *, crash_after: int | None, plan_ids: list[str]):
    """Drain a plan run, optionally simulating process death at the Nth
    ``tool_result`` (the row is already ``completed`` by then) and recording
    the checkpointed plan id."""
    events: list[dict[str, Any]] = []
    tool_results = 0
    async for event in stream:
        events.append(event)
        if event.get("type") == "plan_checkpointed":
            plan_ids.append(event["plan_id"])
        if event.get("type") == "tool_result":
            tool_results += 1
            if crash_after is not None and tool_results >= crash_after:
                raise _CrashAfterToolResult()
    return events


def _texts(events) -> str:
    return "".join(str(event.get("text") or "") for event in events)


def _history_text(messages: list[Message]) -> str:
    return " ".join(str(message.content) for message in messages)


def _journal_rows(home: Path) -> list[tuple[str, str]]:
    """(run_id, status) of every row in the process journal DB."""
    with sqlite3.connect(journal_db_path()) as conn:
        return list(conn.execute("select run_id, status from tool_invocations"))


# ---------------------------------------------------------------------------
# ① plan ∥ team: one event loop, one journal — isolated and exactly-once
# ---------------------------------------------------------------------------


def test_plan_and_team_parallel_no_duplicate_side_effects_or_history_crosstalk(
    tmp_path, journal_home
):
    counter: dict[str, int] = {}
    registry = _registry(counter)
    plan_client = ParallelPlanClient()
    team_client = ParallelTeamClient()
    plan_agent = _plan_agent(plan_client, tmp_path, registry)
    orchestrator = _team_orchestrator(team_client, tmp_path, registry)

    async def run_both():
        return await asyncio.gather(
            _collect(plan_agent.run("并行完成两个计划任务")),
            _collect(orchestrator.run("并行完成两个团队任务")),
        )

    plan_events, team_events = asyncio.run(run_both())

    # Both runs completed cleanly under concurrency.
    assert plan_events[-1]["type"] == "done"
    assert team_events[-1]["type"] == "done"
    assert not [event for event in plan_events if event.get("type") == "error"]
    assert not [event for event in team_events if event.get("type") == "error"]

    # No duplicate side effects: every scripted write ran exactly once.
    assert counter == {
        "plan-file-a.txt": 1,
        "plan-file-b.txt": 1,
        "team-file-a.txt": 1,
        "team-file-b.txt": 1,
    }

    # No history crosstalk: each run's history carries only its own markers.
    plan_history = _history_text(plan_agent.history)
    team_history = _history_text(orchestrator.history)
    assert "plan 任务 A 完成" in plan_history and "plan 任务 B 完成" in plan_history
    assert "team 任务" not in plan_history and "Team Task" not in plan_history
    assert "team 任务 A 完成" in team_history and "team 任务 B 完成" in team_history
    assert "plan 任务" not in team_history and "当前任务" not in team_history

    # The journal only holds the plan run's rows: the team side (no journal)
    # wrote nothing under any run identity, and another run id finds nothing.
    rows = _journal_rows(journal_home)
    assert {run_id for run_id, _ in rows} == {plan_agent._run_id}
    assert {status for _, status in rows} == {"completed"}

    journal = ToolJournal()
    hit = journal.lookup_by_hash(
        scope=str(tmp_path),
        run_id=plan_agent._run_id,
        tool_name="write_file",
        tool_input={"path": "plan-file-a.txt", "content": "plan-a"},
    )
    assert hit is not None and hit.action == "reuse"
    # Same input under a different run identity: nothing — a concurrent run
    # can never consume (or be consumed by) another run's stored result.
    assert (
        journal.lookup_by_hash(
            scope=str(tmp_path),
            run_id="some-other-run",
            tool_name="write_file",
            tool_input={"path": "plan-file-a.txt", "content": "plan-a"},
        )
        is None
    )
    assert (
        journal.lookup_by_hash(
            scope=str(tmp_path),
            run_id=plan_agent._run_id,
            tool_name="write_file",
            tool_input={"path": "team-file-a.txt", "content": "team-a"},
        )
        is None
    )

    # Task/worker inputs stayed inside their own run: plan task bodies never
    # mention team tasks and vice versa.
    for body in plan_client.task_inputs:
        assert "Team Task" not in body and "team 任务" not in body
    for body in team_client.worker_inputs:
        assert "当前任务" not in body and "plan 任务" not in body


# ---------------------------------------------------------------------------
# ② parallel plan batch crashes after both side effects: resume replays both
# ---------------------------------------------------------------------------


def test_parallel_plan_batch_crash_resume_replays_side_effects_exactly_once(tmp_path, journal_home):
    counter: dict[str, int] = {}
    registry = _registry(counter)
    store = PlanCheckpointStore()
    plan_ids: list[str] = []

    async def crash_run():
        agent = _plan_agent(ParallelPlanClient(), tmp_path, registry)
        await _collect_plan(
            agent.run("并行完成两个计划任务"),
            crash_after=2,  # both parallel writes completed and journaled
            plan_ids=plan_ids,
        )

    with pytest.raises(_CrashAfterToolResult):
        asyncio.run(crash_run())

    assert counter == {"plan-file-a.txt": 1, "plan-file-b.txt": 1}
    plan_id = plan_ids[0]
    checkpointed = store.load(plan_id)
    assert checkpointed is not None
    original_run_id = checkpointed.run_id

    # "Fresh process": new agent, new client — same tool_use_ids and inputs.
    resumed_client = ParallelPlanClient()
    resumed_agent = _plan_agent(resumed_client, tmp_path, registry)

    async def resume_run():
        return await _collect(resumed_agent.run_resume(plan_id))

    events = asyncio.run(resume_run())

    # The resume replays both completed calls from the journal (exact
    # tool_use_id claim → reuse): still exactly one execution per path, and
    # the tool results carry the stored payloads.
    assert counter == {"plan-file-a.txt": 1, "plan-file-b.txt": 1}
    assert sorted(
        str(event.get("result") or "") for event in events if event.get("type") == "tool_result"
    ) == ["wrote plan-file-a.txt", "wrote plan-file-b.txt"]
    text = _texts(events)
    assert "已完成 [task_1]" in text and "已完成 [task_2]" in text
    assert events[-1]["type"] == "done"

    plan = store.load(plan_id)
    assert plan is not None
    assert plan.is_all_completed()
    assert plan.run_id == original_run_id  # resume ran under the original run
    # Both dedup branches left consistent rows: everything completed.
    assert {status for _, status in _journal_rows(journal_home)} == {"completed"}
    # The resumed tasks answered with their own texts (history intact).
    assert "plan 任务 A 完成" in _history_text(resumed_agent.history)
    assert "plan 任务 B 完成" in _history_text(resumed_agent.history)


# ---------------------------------------------------------------------------
# ③ plan crash + resume runs concurrently with team runs: no cross-run replay
# ---------------------------------------------------------------------------


def test_plan_journal_recovery_concurrent_with_team_no_crosstalk(tmp_path, journal_home):
    counter: dict[str, int] = {}
    registry = _registry(counter)
    store = PlanCheckpointStore()
    plan_ids: list[str] = []

    # Phase 1: a chain plan crashes right after task a's write, while a team
    # run executes in the same event loop.
    async def phase_one():
        plan_agent = _plan_agent(ChainPlanClient(), tmp_path, registry)
        orchestrator = _team_orchestrator(ParallelTeamClient(), tmp_path, registry)
        return await asyncio.gather(
            _collect_plan(
                plan_agent.run("先写文件再执行任务 B"),
                crash_after=1,
                plan_ids=plan_ids,
            ),
            _collect(orchestrator.run("并行完成两个团队任务")),
            return_exceptions=True,
        )

    plan_outcome, team_events = asyncio.run(phase_one())

    assert isinstance(plan_outcome, _CrashAfterToolResult)
    assert team_events[-1]["type"] == "done"
    # The crash left: task a's write once (row completed), team writes once.
    assert counter == {
        "plan-file-a.txt": 1,
        "team-file-a.txt": 1,
        "team-file-b.txt": 1,
    }
    plan_id = plan_ids[0]
    original_run_id = store.load(plan_id).run_id

    # Phase 2: the plan resumes while a second team run executes concurrently.
    async def phase_two():
        resumed_agent = _plan_agent(
            ChainPlanClient(call_id_a="call_replay_a", call_id_b="call_replay_b"),
            tmp_path,
            registry,
        )
        orchestrator = _team_orchestrator(ParallelTeamClient(), tmp_path, registry)
        return await asyncio.gather(
            _collect(resumed_agent.run_resume(plan_id)),
            _collect(orchestrator.run("并行完成两个团队任务")),
        )

    resume_events, team_two_events = asyncio.run(phase_two())

    # Task a replayed from the journal under fresh tool ids (hash-level
    # reuse); task b executed its own write once. The two concurrent team
    # runs advanced their own counters — no cross-run replay anywhere.
    assert counter == {
        "plan-file-a.txt": 1,  # replayed, NOT re-executed
        "plan-file-b.txt": 1,
        "team-file-a.txt": 2,  # once per team run
        "team-file-b.txt": 2,
    }
    assert sorted(
        str(event.get("result") or "")
        for event in resume_events
        if event.get("type") == "tool_result"
    ) == ["wrote plan-file-a.txt", "wrote plan-file-b.txt"]
    assert resume_events[-1]["type"] == "done"
    assert team_two_events[-1]["type"] == "done"

    resumed_plan = store.load(plan_id)
    assert resumed_plan is not None
    assert resumed_plan.is_all_completed()
    assert resumed_plan.run_id == original_run_id
    assert {status for _, status in _journal_rows(journal_home)} == {"completed"}

    # History isolation across the recovery boundary: the resumed plan's
    # history carries plan markers only; the second team run's history and
    # worker inputs carry team markers only.
    resumed_history = _history_text(_plan_agent_history(resume_events))
    assert "plan 任务 A 完成" in resumed_history and "plan 任务 B 完成" in resumed_history
    assert "team 任务" not in resumed_history and "Team Task" not in resumed_history
    team_two_history = _team_history_text(team_two_events)
    assert "team 任务 A 完成" in team_two_history
    assert "plan 任务" not in team_two_history and "当前任务" not in team_two_history


def _plan_agent_history(events) -> list[Message]:
    """The done event's messages are the plan-level history verbatim."""
    done = next(event for event in events if event.get("type") == "done")
    return list(done["messages"])


def _team_history_text(events) -> str:
    done = next(event for event in events if event.get("type") == "done")
    return _history_text(list(done["messages"]))
