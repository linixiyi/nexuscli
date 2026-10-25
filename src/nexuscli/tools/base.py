from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from nexuscli.config import NexusCliConfig

DangerLevel = Literal["safe", "medium", "high"]
ToolDecision = Literal["approve", "deny", "skip", "approve_run"]


@dataclass(slots=True)
class ToolResult:
    content: str
    is_error: bool = False
    display_summary: str | None = None
    tool_use_id: str | None = None
    # 稳定错误码（如 outcome_unknown / tool_journal_conflict）：供事件流与恢复层分类，
    # 不做消息字符串匹配（05-execution-durability.md F11 的分类机制沿用此口径）。
    error_type: str | None = None


@dataclass(slots=True)
class ToolContext:
    cwd: str
    config: NexusCliConfig
    approval_callback: Callable[[dict[str, Any]], Awaitable[ToolDecision] | ToolDecision] | None = (
        None
    )
    skill_context_buffer: Any | None = None
    subagent_depth: int = 0
    # Per-run tool approvals granted through the approval prompt's "r" option
    # ("approve this tool for the rest of the run"). The executor adds a tool
    # name when the approval callback returns "approve_run"; later calls to
    # the same tool skip the HITL prompt for the remainder of the run. A fresh
    # ToolContext per Agent.run() (nexuscli.agent.agent._run_react) starts from
    # the default empty set, which is how the grant expires at run end.
    run_scope_approved: set[str] = field(default_factory=set)
    # F9 工具副作用日志：journal 非空时，三类有副作用工具在分发点做 claim/complete；
    # run_id 是每次 Agent.run() 的 uuid4 hex，恢复路径必须沿用原 run_id（两级去重的前提）。
    journal: Any | None = None
    run_id: str = ""

    def clear_run_scope(self) -> None:
        """Drop every per-run tool approval (new agent run / new user turn)."""
        self.run_scope_approved.clear()


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    danger_level: DangerLevel = "safe"
    requires_approval: bool = False
    timeout: float = 60.0
    required_keys: list[str] = field(default_factory=list)

    def definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError(f'tool "{self.name}" input must be an object')
        for key in self.required_keys:
            if key not in payload:
                raise ValueError(f'tool "{self.name}" missing required input: {key}')
        return payload

    async def execute(self, payload: dict[str, Any], context: ToolContext) -> ToolResult:
        data = self.validate(payload)
        return await asyncio.wait_for(self.handler(data, context), timeout=self.timeout)


def object_schema(
    properties: dict[str, dict[str, Any]],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
    }
