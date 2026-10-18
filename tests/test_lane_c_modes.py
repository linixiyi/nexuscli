"""Lane C machine assertions: agent-mode surfaces (team worker plan mode).

Backs testing-matrix row 2.7 (team 的 worker-mode=plan): a worker configured
to plan mode must get write tools hard-denied by the executor's read-only
plan gate — the team counterpart of the `-p --mode plan` gate turned on in
cli.py. The test drives the production path (SubAgent → PlanExecuteAgent →
query → ToolExecutor) with only the LLM scripted.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nexuscli.agent.orchestrator import AgentMessage, AgentMessageType, AgentRole, SubAgent
from nexuscli.config import load_config
from nexuscli.tools import ToolRegistry, get_builtin_tools


class _PlanModeWorkerClient:
    """Scripted LLM for one nested plan run.

    Branches on the ``tools`` argument: the nested planner chats without
    tools, the task executor chats with the registry definitions and tries
    one write_file, then reports whatever the tool told it. Tool-role
    messages are recorded so the test can assert exactly what the model was
    told when its write was refused.
    """

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self) -> None:
        self.tool_messages: list[str] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        if not tools:
            plan = {
                "summary": "write probe",
                "tasks": [
                    {
                        "id": "probe",
                        "description": "create plan_gate_probe.txt containing PROBE",
                        "type": "FILE_WRITE",
                        "dependencies": [],
                    }
                ],
            }
            yield {"type": "text_delta", "text": json.dumps(plan)}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        if not any(getattr(message, "role", "") == "tool" for message in messages):
            yield {
                "type": "tool_call_delta",
                "tool_call": {
                    "index": 0,
                    "id": "call_probe",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {"path": "plan_gate_probe.txt", "content": "PROBE"}
                        ),
                    },
                },
            }
            yield {"type": "message_end", "stop_reason": "tool_use"}
            return
        self.tool_messages.extend(
            str(message.content) for message in messages if getattr(message, "role", "") == "tool"
        )
        yield {"type": "text_delta", "text": "report: the write attempt was refused"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


def test_team_plan_worker_write_is_hard_denied(tmp_path: Path) -> None:
    config = load_config(project_root=tmp_path)
    assert config.policy.plan_mode is False  # session itself is not in plan mode
    client = _PlanModeWorkerClient()
    registry = ToolRegistry()
    for tool in get_builtin_tools():
        registry.register(tool)
    worker = SubAgent(
        name="worker-plan",
        role=AgentRole.WORKER,
        llm_client=client,
        tool_registry=registry,
        config=config,
        cwd=str(tmp_path),
        default_mode="plan",
    )

    result = asyncio.run(
        worker.execute(
            AgentMessage.task("orchestrator", "create plan_gate_probe.txt with content PROBE"),
            mode="plan",
        )
    )

    # The write was refused by the plan-mode gate, not executed and not
    # merely denied by the (absent) approver.
    assert any("is not allowed in plan mode" in text for text in client.tool_messages), (
        f"plan-gate refusal never reached the model; tool messages: {client.tool_messages}"
    )
    assert not (tmp_path / "plan_gate_probe.txt").exists()
    # The worker still finishes with a readable report (the gate refusal is a
    # tool result the model can react to, not a crashed run).
    assert result.type is AgentMessageType.RESULT
    assert "refused" in result.content
    # The gate rides on a per-run config copy: the shared session config must
    # come back unchanged or concurrent react workers would be gated too.
    assert config.policy.plan_mode is False
