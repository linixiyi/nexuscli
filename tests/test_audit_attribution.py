"""审计归因：无人工参与的自动放行不得记成 hitl（spec 2026-11-13 task-0007）。

``ToolExecutor._execute_single`` 的审批链放行后按固定顺序归因：
permission-rule → run-scope → readonly-rule → hitl。缺口在无人工参与的自动
放行：``hitl_mode=never`` 下无人可问，无条件放行却落进 requires_approval
分支被记成 "hitl"（把策略决定伪装成人工审批）。本文件锁定五个场景：

① never + write_file → "policy-never"；② auto 无审批回调的直通 → 保持既有
明确值 "none"（行为条款允许「auto（或既有明确值）」，而
test_slash_injection_security.py 已在同一执行器路径上——用户级注入
requires_approval=False + 无回调——钉住 "none"，本任务 boundary 不含该文件，
故维持既有值）；③ --yes 式回调放行仍 "hitl"；④ run-scope 仍 "run-scope"；
⑤ readonly 仍 "readonly-rule"。③④⑤ 是「既有归因顺序保持不变」的回归钉。

工具与门链用法沿用 test_audit_gating.py / test_run_scope_approval.py 的约定
（sync test + asyncio.run；审计路径落 tmp_path，不碰真实 HOME）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nexuscli.config import NexusCliConfig
from nexuscli.policy import AuditLog
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.builtins import get_builtin_tools
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry


def _config(tmp_path: Path, hitl_mode: str = "auto") -> NexusCliConfig:
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.policy.hitl_mode = hitl_mode
    return config


def _builtin_executor() -> ToolExecutor:
    registry = ToolRegistry()
    for tool in get_builtin_tools():
        registry.register(tool)
    return ToolExecutor(registry)


def _registry_with_auto_tool(executed: list[str]) -> ToolRegistry:
    """有副作用但不要求审批的工具：auto 模式无审批回调时的直通主体。"""
    registry = ToolRegistry()

    def make_handler(tool_name: str):
        async def handler(payload, _context):
            executed.append(f"{tool_name}:{payload['value']}")
            return ToolResult(f"ran {tool_name}")

        return handler

    registry.register(
        Tool(
            name="mutate_auto",
            description="Mutate test state without requiring approval",
            parameters=object_schema({"value": {"type": "string"}}, ["value"]),
            required_keys=["value"],
            handler=make_handler("mutate_auto"),
            is_read_only=False,
        )
    )
    return registry


def _registry_with_mutate(executed: list[str]) -> ToolRegistry:
    """requires_approval=True 的有副作用工具：审批链各归因值的主体。"""
    registry = ToolRegistry()

    def make_handler(tool_name: str):
        async def handler(payload, _context):
            executed.append(f"{tool_name}:{payload['value']}")
            return ToolResult(f"ran {tool_name}")

        return handler

    registry.register(
        Tool(
            name="mutate",
            description="Mutate test state (requires approval)",
            parameters=object_schema({"value": {"type": "string"}}, ["value"]),
            required_keys=["value"],
            handler=make_handler("mutate"),
            is_read_only=False,
            requires_approval=True,
        )
    )
    return registry


def _events(tmp_path: Path) -> list[dict]:
    return AuditLog(str(tmp_path / "audit.jsonl")).tail()


# ---------------------------------------------------------------------------
# ① never + write_file：无人可问的策略放行，不是人工审批
# ---------------------------------------------------------------------------


def test_never_mode_write_file_is_attributed_policy_never(tmp_path):
    config = _config(tmp_path, hitl_mode="never")
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=None)
    call = {"id": "c1", "name": "write_file", "arguments": {"path": "out.txt", "content": "x"}}

    result = asyncio.run(_builtin_executor().execute_all([call], context))[0]

    # 放行真实发生了：文件落盘、结果不带错误标记
    assert not result.is_error
    assert (tmp_path / "out.txt").exists()
    events = _events(tmp_path)
    assert [(e["tool_name"], e["outcome"], e["approver"]) for e in events] == [
        ("write_file", "allow", "policy-never")
    ]


# ---------------------------------------------------------------------------
# ② auto 无审批回调的直通：既有明确值 none，不得记成 hitl
# ---------------------------------------------------------------------------


def test_auto_passthrough_without_callback_is_not_hitl(tmp_path):
    executed: list[str] = []
    config = _config(tmp_path, hitl_mode="auto")
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=None)
    call = {"id": "c1", "name": "mutate_auto", "arguments": {"value": "ok"}}

    result = asyncio.run(
        ToolExecutor(_registry_with_auto_tool(executed)).execute_all([call], context)
    )[0]

    assert not result.is_error
    assert executed == ["mutate_auto:ok"]
    events = _events(tmp_path)
    assert [(e["tool_name"], e["outcome"], e["approver"]) for e in events] == [
        ("mutate_auto", "allow", "none")
    ]


# ---------------------------------------------------------------------------
# ③ --yes 式回调放行仍记 hitl（cli._auto_approver 的同形替身）
# ---------------------------------------------------------------------------


def test_yes_style_callback_approval_stays_hitl(tmp_path):
    config = _config(tmp_path, hitl_mode="auto")
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=lambda _r: "approve")
    call = {"id": "c1", "name": "write_file", "arguments": {"path": "out.txt", "content": "x"}}

    result = asyncio.run(_builtin_executor().execute_all([call], context))[0]

    assert not result.is_error
    events = _events(tmp_path)
    assert [(e["outcome"], e["approver"]) for e in events] == [("allow", "hitl")]


# ---------------------------------------------------------------------------
# ④ run-scope 直通仍记 run-scope
# ---------------------------------------------------------------------------


def test_run_scope_grant_keeps_run_scope_attribution(tmp_path):
    executed: list[str] = []
    config = _config(tmp_path)
    prompts: list[str] = []

    def callback(_request):
        prompts.append("asked")
        return "approve_run"

    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=callback)
    calls = [
        {"id": "c1", "name": "mutate", "arguments": {"value": "ok"}},
        {"id": "c2", "name": "mutate", "arguments": {"value": "ok"}},
    ]

    results = asyncio.run(ToolExecutor(_registry_with_mutate(executed)).execute_all(calls, context))

    assert not any(r.is_error for r in results)
    assert prompts == ["asked"]  # 第二次由 run grant 直通，不再问人
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    approvers = [json.loads(line)["approver"] for line in lines]
    assert approvers == ["hitl", "run-scope"]


# ---------------------------------------------------------------------------
# ⑤ 只读命令直通仍记 readonly-rule
# ---------------------------------------------------------------------------


def test_readonly_command_keeps_readonly_rule_attribution(tmp_path):
    config = _config(tmp_path)
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=None)
    call = {"id": "c1", "name": "bash", "arguments": {"command": "ls"}}

    result = asyncio.run(_builtin_executor().execute_all([call], context))[0]

    assert not result.is_error
    events = _events(tmp_path)
    assert [(e["tool_name"], e["outcome"], e["approver"]) for e in events] == [
        ("bash", "allow", "readonly-rule")
    ]
