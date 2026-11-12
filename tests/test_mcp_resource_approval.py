"""MCP 虚拟资源/提示词工具的审批与审计语义（task-0009）。

``read_resource``/``get_prompt`` 原先在 ``mcp/client.py`` 本地声明
``is_read_only=True``：既穿过 plan 模式的硬只读闸（``tools/executor.py`` 的
plan gate 看 ``is_read_only``），又在 auto 模式跳过审批回调，且因只读豁免
不产生审计记录。MCP server 是外部进程，资源内容与提示词渲染结果对本地代码
而言不可信——与远端工具同一口径（task-0003：一律 ``is_read_only=False`` +
``requires_approval=True`` + ``danger_level="medium"``）。

锁定五条（tasks.md ①-⑤）：

- ① auto 模式 read_resource 触发一次审批，批准后经原链路执行；
- ② 审计出现该调用（放行 → allow/hitl）；
- ③ plan 模式被只读闸拒绝（list_resources 纯列举照常放行）；
- ④ list_resources/list_prompts 纯列举保持只读，不弹审批；
- ⑤ 未知 URI/失败不改变拒绝语义：失败照记 error 审计，拒绝回调仍在
  执行前生效（未触达 server）。

fake session 离线可跑：不 spawn 子进程、不连网。mcp.json 写在用户层
（``~/.nexuscli/mcp.json``），不经过 task-0002 的项目信任门。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nexuscli.config import NexusCliConfig
from nexuscli.mcp import McpClientManager
from nexuscli.policy import AuditLog
from nexuscli.tools.base import ToolContext
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry

_KNOWN_URI = "mem://notes/todo"
_UNKNOWN_URI = "mem://notes/missing"
_RESOURCE_TEXT = "todo: write the plan"
_PROMPT_NAME = "greet"
_PROMPT_TEXT = "hello from prompt"

_READ_TOOL = "mcp__fake__read_resource"
_GET_PROMPT_TOOL = "mcp__fake__get_prompt"
_LIST_RESOURCES_TOOL = "mcp__fake__list_resources"
_LIST_PROMPTS_TOOL = "mcp__fake__list_prompts"


class _FakeSession:
    """Stands in for the SDK session: one known resource, one known prompt."""

    def __init__(self, read_attempts: list[str]):
        self._read_attempts = read_attempts

    async def list_tools(self) -> SimpleNamespace:
        return SimpleNamespace(tools=[])

    async def list_resources(self) -> SimpleNamespace:
        return SimpleNamespace(
            resources=[SimpleNamespace(uri=_KNOWN_URI, name="todo", description="notes")]
        )

    async def read_resource(self, uri: Any) -> SimpleNamespace:
        self._read_attempts.append(str(uri))
        if str(uri) != _KNOWN_URI:
            raise RuntimeError(f"unknown resource: {uri}")
        return SimpleNamespace(contents=[SimpleNamespace(text=_RESOURCE_TEXT)])

    async def list_prompts(self) -> SimpleNamespace:
        return SimpleNamespace(
            prompts=[SimpleNamespace(name=_PROMPT_NAME, description="a greeting")]
        )

    async def get_prompt(self, name: str, arguments: dict | None = None) -> SimpleNamespace:
        _ = arguments
        if str(name) != _PROMPT_NAME:
            raise RuntimeError(f"unknown prompt: {name}")
        # The message carries .text directly (the same branch of
        # _content_to_text that real SDK TextContent takes), so the assertion
        # below pins on the prompt text rather than conversion cosmetics.
        return SimpleNamespace(messages=[SimpleNamespace(role="assistant", text=_PROMPT_TEXT)])


def _manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_attempts: list[str]
) -> McpClientManager:
    """Manager with the fake server registered at the user layer (no trust gate)."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    user_mcp = home / ".nexuscli" / "mcp.json"
    user_mcp.parent.mkdir(exist_ok=True)
    user_mcp.write_text(
        json.dumps({"mcpServers": {"fake": {"command": "python"}}}), encoding="utf-8"
    )
    manager = McpClientManager(tmp_path)

    @asynccontextmanager
    async def fake_session(spec: object):
        _ = spec
        yield _FakeSession(read_attempts)

    monkeypatch.setattr(manager, "_session", fake_session)
    return manager


def _registered_executor(manager: McpClientManager) -> ToolExecutor:
    """Load tools through the real registration path and wrap them in a registry."""
    tools = asyncio.run(manager.load_tools())
    assert _READ_TOOL in [tool.name for tool in tools]
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return ToolExecutor(registry)


def _config(tmp_path: Path, *, plan_mode: bool = False) -> NexusCliConfig:
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.policy.plan_mode = plan_mode
    return config


def _approver(decisions: list[str], prompts: list[dict]):
    def callback(request: dict) -> str:
        prompts.append(request)
        return decisions.pop(0) if decisions else "approve"

    return callback


def _events(tmp_path: Path) -> list[dict]:
    return AuditLog(str(tmp_path / "audit.jsonl")).tail()


# ---------------------------------------------------------------------------
# ① auto 模式：read_resource 触发一次审批，批准后经原链路执行
# ---------------------------------------------------------------------------


def test_auto_mode_read_resource_prompts_once(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    read_attempts: list[str] = []
    prompts: list[dict] = []
    manager = _manager(tmp_path, monkeypatch, read_attempts)
    executor = _registered_executor(manager)
    context = ToolContext(
        cwd=str(tmp_path),
        config=_config(tmp_path),
        approval_callback=_approver([], prompts),
    )

    call = {"id": "c1", "name": _READ_TOOL, "arguments": {"uri": _KNOWN_URI}}
    results = asyncio.run(executor.execute_all([call], context))

    # 恰好一次审批，且指名是这个工具
    assert [prompt["tool_name"] for prompt in prompts] == [_READ_TOOL]
    # 批准后真实执行：内容来自（fake）远端 server
    assert not results[0].is_error
    assert results[0].content == _RESOURCE_TEXT
    assert read_attempts == [_KNOWN_URI]


def test_auto_mode_get_prompt_prompts_once(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    read_attempts: list[str] = []
    prompts: list[dict] = []
    manager = _manager(tmp_path, monkeypatch, read_attempts)
    executor = _registered_executor(manager)
    context = ToolContext(
        cwd=str(tmp_path),
        config=_config(tmp_path),
        approval_callback=_approver([], prompts),
    )

    call = {"id": "c2", "name": _GET_PROMPT_TOOL, "arguments": {"name": _PROMPT_NAME}}
    results = asyncio.run(executor.execute_all([call], context))

    assert [prompt["tool_name"] for prompt in prompts] == [_GET_PROMPT_TOOL]
    assert not results[0].is_error
    assert results[0].content == _PROMPT_TEXT


# ---------------------------------------------------------------------------
# ② 审计出现该调用
# ---------------------------------------------------------------------------


def test_approved_read_resource_is_audited(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    read_attempts: list[str] = []
    manager = _manager(tmp_path, monkeypatch, read_attempts)
    executor = _registered_executor(manager)
    context = ToolContext(
        cwd=str(tmp_path),
        config=_config(tmp_path),
        approval_callback=_approver([], []),
    )

    call = {"id": "c1", "name": _READ_TOOL, "arguments": {"uri": _KNOWN_URI}}
    results = asyncio.run(executor.execute_all([call], context))
    assert not results[0].is_error

    events = _events(tmp_path)
    assert [(event["tool_name"], event["outcome"], event["approver"]) for event in events] == [
        (_READ_TOOL, "allow", "hitl")
    ]


# ---------------------------------------------------------------------------
# ③ plan 模式被只读闸拒绝；纯列举照常放行
# ---------------------------------------------------------------------------


def test_plan_mode_rejects_read_resource_and_get_prompt(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read_attempts: list[str] = []
    manager = _manager(tmp_path, monkeypatch, read_attempts)
    executor = _registered_executor(manager)
    context = ToolContext(cwd=str(tmp_path), config=_config(tmp_path, plan_mode=True))

    calls = [
        {"id": "r", "name": _READ_TOOL, "arguments": {"uri": _KNOWN_URI}},
        {"id": "g", "name": _GET_PROMPT_TOOL, "arguments": {"name": _PROMPT_NAME}},
    ]
    results = asyncio.run(executor.execute_all(calls, context))

    assert all(result.is_error for result in results)
    assert all("not allowed in plan mode" in result.content for result in results)
    # 拒绝发生在执行之前：请求从未发往（fake）远端 server
    assert read_attempts == []

    # 纯列举工具保持只读：plan 模式下照常放行
    listing = {"id": "l", "name": _LIST_RESOURCES_TOOL, "arguments": {}}
    listing_result = asyncio.run(executor.execute_all([listing], context))[0]
    assert not listing_result.is_error
    assert _KNOWN_URI in listing_result.content


# ---------------------------------------------------------------------------
# ④ list_resources / list_prompts 保持只读，不弹审批
# ---------------------------------------------------------------------------


def test_list_tools_stay_read_only_without_prompt(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read_attempts: list[str] = []
    prompts: list[dict] = []
    manager = _manager(tmp_path, monkeypatch, read_attempts)
    executor = _registered_executor(manager)
    context = ToolContext(
        cwd=str(tmp_path),
        config=_config(tmp_path),
        approval_callback=_approver([], prompts),
    )

    calls = [
        {"id": "lr", "name": _LIST_RESOURCES_TOOL, "arguments": {}},
        {"id": "lp", "name": _LIST_PROMPTS_TOOL, "arguments": {}},
    ]
    results = asyncio.run(executor.execute_all(calls, context))

    # 一次审批都不弹：纯列举不进入审批链
    assert prompts == []
    assert all(not result.is_error for result in results)
    # 只读声明本身保持不变（plan 闸与并发调度的依据）
    assert executor.registry.get(_LIST_RESOURCES_TOOL).is_read_only is True
    assert executor.registry.get(_LIST_PROMPTS_TOOL).is_read_only is True


# ---------------------------------------------------------------------------
# ⑤ 未知 URI/失败不改变拒绝语义
# ---------------------------------------------------------------------------


def test_unknown_uri_failure_keeps_rejection_semantics(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a) 放行后远端失败：结果带错，但审计照记（error/hitl），不做只读豁免
    read_attempts: list[str] = []
    manager = _manager(tmp_path, monkeypatch, read_attempts)
    executor = _registered_executor(manager)
    context = ToolContext(
        cwd=str(tmp_path),
        config=_config(tmp_path),
        approval_callback=_approver([], []),
    )
    call = {"id": "c1", "name": _READ_TOOL, "arguments": {"uri": _UNKNOWN_URI}}
    results = asyncio.run(executor.execute_all([call], context))
    assert results[0].is_error
    assert read_attempts == [_UNKNOWN_URI]
    events = _events(tmp_path)
    assert [(event["tool_name"], event["outcome"], event["approver"]) for event in events] == [
        (_READ_TOOL, "error", "hitl")
    ]

    # b) 拒绝回调 + 未知 URI：审批仍在执行前，请求未触达 server
    read_attempts_denied: list[str] = []
    prompts_denied: list[dict] = []
    manager_denied = _manager(tmp_path, monkeypatch, read_attempts_denied)
    executor_denied = _registered_executor(manager_denied)
    context_denied = ToolContext(
        cwd=str(tmp_path),
        config=_config(tmp_path),
        approval_callback=_approver(["deny"], prompts_denied),
    )
    denied_results = asyncio.run(executor_denied.execute_all([call], context_denied))
    assert [prompt["tool_name"] for prompt in prompts_denied] == [_READ_TOOL]
    assert denied_results[0].is_error
    assert "denied by approval policy" in denied_results[0].content
    assert read_attempts_denied == []
