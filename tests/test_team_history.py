"""Team worker history isolation (spec task_0014).

Concurrent team workers must each start from an independent copy of the
orchestrator's session-history snapshot, and the orchestrator folds each
worker's conversation increment back in task-index order after the batch
completes — never via a shared writeback from the event loop, which made the
last-finishing worker decide the whole history.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nexuscli.agent.orchestrator import (
    AgentMessage,
    AgentMessageType,
    AgentOrchestrator,
    AgentRole,
    ExecutionStep,
    StepStatus,
    SubAgent,
)
from nexuscli.config import load_config
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.types import Message


def _orchestrator(tmp_path, client) -> AgentOrchestrator:
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "never"
    return AgentOrchestrator(
        llm_client=client,
        tool_registry=registry,
        config=config,
        cwd=str(tmp_path),
    )


def test_parallel_worker_increments_merge_in_task_order(tmp_path) -> None:
    """Worker B finishes first (shorter delay) yet worker A's increment
    still lands first: the merge is by task index, not completion order."""
    client = DelayedParallelClient()
    orchestrator = _orchestrator(tmp_path, client)

    async def run():
        text = ""
        done = None
        async for event in orchestrator.run("并行完成 A 和 B"):
            if event.get("type") == "error":
                raise event["error"]
            if event.get("type") == "text_delta":
                text += str(event.get("text") or "")
            elif event.get("type") == "done":
                done = event
        return text, done

    text, done = asyncio.run(run())

    assert done is not None
    assert "Multi-Agent task completed" in text
    contents = [str(message.content) for message in done["messages"]]
    # Both worker increments survive in the merged history…
    assert "Task A result" in contents
    assert "Task B result" in contents
    idx_a_task = next(
        index for index, item in enumerate(contents) if "Current task:" in item and "Task A" in item
    )
    idx_b_task = next(
        index for index, item in enumerate(contents) if "Current task:" in item and "Task B" in item
    )
    idx_a_result = contents.index("Task A result")
    idx_b_result = contents.index("Task B result")
    # …in task-index order although worker B completed first.
    assert idx_a_task < idx_a_result < idx_b_task < idx_b_result
    # No cross-talk: each worker chat only ever saw its own task text.
    for inputs in client.worker_inputs:
        joined = "\n".join(inputs)
        if "Task A" in joined:
            assert "Task B" not in joined
        if "Task B" in joined:
            assert "Task A" not in joined


def test_batch_merges_worker_increments_by_task_index(tmp_path) -> None:
    """Two fake workers with different delays: both start from the same
    isolated snapshot, both increments are merged exactly once, in plan
    order, with the base history kept in front."""
    orchestrator = _orchestrator(tmp_path, ApprovingReviewerClient())
    base = [
        Message(role="user", content="prior turn"),
        Message(role="assistant", content="prior answer"),
    ]
    orchestrator.history = list(base)
    slow_first = DelayedWorker("worker-1", 0.05, "A")
    fast_second = DelayedWorker("worker-2", 0.0, "B")
    orchestrator.workers = [slow_first, fast_second]
    steps = [
        ExecutionStep("step_1", "Task A", "ANALYSIS", []),
        ExecutionStep("step_2", "Task B", "ANALYSIS", []),
    ]

    async def run_batch():
        async for _event in orchestrator._execute_steps(
            steps, lambda text: {"type": "text_delta", "text": text}
        ):
            pass

    asyncio.run(run_batch())

    assert [step.status for step in steps] == [StepStatus.COMPLETED, StepStatus.COMPLETED]
    # Every worker started from the same isolated snapshot: the base is
    # visible, the sibling's increment is not.
    assert slow_first.seen_snapshot == ["prior turn", "prior answer"]
    assert fast_second.seen_snapshot == ["prior turn", "prior answer"]
    # Both increments present exactly once, in task-index order — the slower
    # first worker's increment still lands first although it finished last.
    assert [str(message.content) for message in orchestrator.history] == [
        "prior turn",
        "prior answer",
        "worker task: A",
        "worker done: A",
        "worker task: B",
        "worker done: B",
    ]


def test_plan_worker_increment_keeps_seeded_base(tmp_path, monkeypatch) -> None:
    """A plan-mode worker must keep the seeded snapshot in front of its own
    envelope, so the orchestrator's increment slice keeps its messages."""
    worker = SubAgent(
        name="worker-plan",
        role=AgentRole.WORKER,
        llm_client=ApprovingReviewerClient(),
        tool_registry=ToolRegistry(),
        config=load_config(project_root=tmp_path),
        cwd=str(tmp_path),
    )
    worker.history = [Message(role="user", content="prior turn")]

    async def fake_plan_run(self, message):  # noqa: ARG001
        yield {"type": "text_delta", "text": "nested plan completed"}
        yield {
            "type": "done",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_tokens": 15,
            "total_turns": 3,
        }

    monkeypatch.setattr("nexuscli.agent.plan_execute.PlanExecuteAgent.run", fake_plan_run)

    result = asyncio.run(worker.execute(AgentMessage.task("test", "complex task"), mode="plan"))

    assert result.type == AgentMessageType.RESULT
    assert [str(message.content) for message in worker.history] == [
        "prior turn",
        "complex task",
        "nested plan completed",
    ]


class DelayedParallelClient:
    """Plans two independent steps; the Task A worker finishes after Task B."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self) -> None:
        self.worker_inputs: list[list[str]] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        body = _message_text(messages[-1].content)
        if "Create an execution plan" in body:
            yield {
                "type": "text_delta",
                "text": (
                    '{"summary":"parallel","steps":['
                    '{"id":"a","description":"Task A","type":"ANALYSIS","dependencies":[]},'
                    '{"id":"b","description":"Task B","type":"ANALYSIS","dependencies":[]}'
                    "]}"
                ),
            }
        elif "Original task" in body:
            yield {"type": "text_delta", "text": '{"approved": true, "issues": []}'}
        elif "Current task:" in body and "Task A" in body:
            self.worker_inputs.append([_message_text(item.content) for item in messages])
            await asyncio.sleep(0.15)
            yield {"type": "text_delta", "text": "Task A result"}
        elif "Current task:" in body and "Task B" in body:
            self.worker_inputs.append([_message_text(item.content) for item in messages])
            await asyncio.sleep(0.01)
            yield {"type": "text_delta", "text": "Task B result"}
        else:
            yield {"type": "text_delta", "text": "fallback"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


class ApprovingReviewerClient:
    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        yield {"type": "text_delta", "text": '{"approved": true, "issues": []}'}
        yield {"type": "message_end", "stop_reason": "end_turn"}


class DelayedWorker:
    """Duck-typed SubAgent: sleeps a per-worker delay, then appends its own
    increment to its own history — mirroring the real worker contract."""

    def __init__(self, name: str, delay: float, marker: str) -> None:
        self.name = name
        self.delay = delay
        self.marker = marker
        self.history: list[Message] = []
        self.seen_snapshot: list[str] = []

    async def execute(self, task, context="", *, mode=None):  # noqa: ARG002
        self.seen_snapshot = [str(item.content) for item in self.history]
        await asyncio.sleep(self.delay)
        self.history = [
            *self.history,
            Message(role="user", content=f"worker task: {self.marker}"),
            Message(role="assistant", content=f"worker done: {self.marker}"),
        ]
        return AgentMessage.result(self.name, AgentRole.WORKER, f"result {self.marker}")

    def clear_history(self) -> None:
        self.history = []


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return str(content)
