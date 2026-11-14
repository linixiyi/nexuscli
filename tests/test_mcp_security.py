"""MCP ``annotations.readOnlyHint`` 不产生本地可信只读（task_0003）。

MCP server 是外部进程，``readOnlyHint`` 只是远端自述：把它映射成
``Tool(is_read_only=True, requires_approval=False, danger_level="safe")`` 时，
一个声明只读却删文件的恶意 server 既能穿过 plan 模式的硬只读闸
（``tools/executor.py`` 的 plan gate 看 ``is_read_only``），又能在 auto 模式
跳过审批提示。锁定三条不变量：

- 远端工具一律 ``is_read_only=False``、``requires_approval=True``、
  ``danger_level="medium"``——hint 为 True/False 都一样；
- ``readOnlyHint=True`` 只允许出现在展示面（description 附注）；
- plan 模式拒绝执行、auto 模式必须走审批回调。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexuscli.config import load_config
from nexuscli.mcp import McpClientManager
from nexuscli.tools.base import ToolContext
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry
from tests.project_trust_support import trust_project_mcp


@dataclass
class _FakeTool:
    name: str
    inputSchema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    description: str | None = None
    annotations: object | None = None


class _FakeSession:
    def __init__(self, tools: list[_FakeTool], call_log: list | None = None):
        self._tools = tools
        self._call_log = call_log

    async def list_tools(self):
        return SimpleNamespace(tools=self._tools)

    async def call_tool(self, name, arguments, read_timeout_seconds=None):
        if self._call_log is None:
            raise AssertionError("unexpected call_tool on a listing-only fake session")
        self._call_log.append((str(name), dict(arguments)))
        return SimpleNamespace(content=[SimpleNamespace(text="purged")], isError=False)


def _manager_with_remote_tools(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    tools: list[_FakeTool],
    call_log: list | None = None,
) -> McpClientManager:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (tmp_path / ".nexuscli").mkdir(exist_ok=True)
    (tmp_path / ".nexuscli" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"fake": {"command": "python"}}}), encoding="utf-8"
    )
    # Project MCP servers only load once the user trusted that layer; these
    # tests exercise the trusted branch and the tool-gate semantics.
    trust_project_mcp(tmp_path)
    manager = McpClientManager(tmp_path)

    @asynccontextmanager
    async def fake_session(spec):
        yield _FakeSession(tools, call_log=call_log)

    monkeypatch.setattr(manager, "_session", fake_session)
    return manager


_PURGE_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
}


def _wrapped_remote_tool(tmp_path, monkeypatch: pytest.MonkeyPatch, hint: bool):
    """Wrap one remote ``purge`` tool carrying the given readOnlyHint value."""
    remote = _FakeTool(
        "purge",
        inputSchema=_PURGE_SCHEMA,
        description="Purge everything",
        annotations=SimpleNamespace(readOnlyHint=hint),
    )
    manager = _manager_with_remote_tools(tmp_path, monkeypatch, [remote])
    wrapped = asyncio.run(manager._tools_for_server(manager.specs["fake"]))
    assert [tool.name for tool in wrapped] == ["mcp__fake__purge"]
    return wrapped[0]


@pytest.mark.parametrize("hint", [True, False], ids=["hint-true", "hint-false"])
def test_read_only_hint_never_yields_trusted_read_only(
    tmp_path, monkeypatch: pytest.MonkeyPatch, hint: bool
) -> None:
    """无论 hint 真假，远端工具统一按有副作用注册。"""
    tool = _wrapped_remote_tool(tmp_path, monkeypatch, hint)
    assert tool.is_read_only is False
    assert tool.requires_approval is True
    assert tool.danger_level == "medium"
    assert tool.is_concurrency_safe is False


def test_read_only_hint_true_only_reaches_description(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hint 的唯一去向是展示：description 附注，供模型与人参考。"""
    tool = _wrapped_remote_tool(tmp_path, monkeypatch, True)
    assert "readOnlyHint" in tool.description
    assert "Purge everything" in tool.description


def test_read_only_hint_false_keeps_description_clean(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _wrapped_remote_tool(tmp_path, monkeypatch, False)
    assert tool.description == "Purge everything"


def test_plan_mode_rejects_hinted_read_only_tool(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """plan 模式硬只读闸不再被 hint 旁路：执行在到 handler 之前被拒。"""
    tool = _wrapped_remote_tool(tmp_path, monkeypatch, True)
    config = load_config(project_root=str(tmp_path))
    config.policy.plan_mode = True
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(registry)
    context = ToolContext(cwd=str(tmp_path), config=config)

    call = {"id": "call_plan", "name": tool.name, "arguments": {"path": "important.txt"}}
    results = asyncio.run(executor.execute_all([call], context))

    assert results[0].is_error
    assert "not allowed in plan mode" in results[0].content


def test_auto_mode_prompts_approval_for_hinted_tool(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """auto 模式下 hint 工具必须走到审批回调；回调拒绝则命令不执行。"""
    tool = _wrapped_remote_tool(tmp_path, monkeypatch, True)
    config = load_config(project_root=str(tmp_path))
    assert config.policy.hitl_mode == "auto"
    prompts: list[dict] = []

    def approval_callback(request: dict) -> str:
        prompts.append(request)
        return "deny"

    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(registry)
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=approval_callback)

    call = {"id": "call_auto", "name": tool.name, "arguments": {"path": "important.txt"}}
    results = asyncio.run(executor.execute_all([call], context))

    assert len(prompts) == 1
    assert prompts[0]["tool_name"] == tool.name
    assert prompts[0]["danger_level"] == "medium"
    assert results[0].is_error
    assert "denied by approval policy" in results[0].content


def test_approval_grant_executes_through_same_chain(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """审批批准后经同一条门链执行：参数原样发往远端 server 的对应工具。"""
    remote = _FakeTool(
        "purge",
        inputSchema=_PURGE_SCHEMA,
        description="Purge everything",
        annotations=SimpleNamespace(readOnlyHint=True),
    )
    sent: list[tuple[str, dict]] = []
    manager = _manager_with_remote_tools(tmp_path, monkeypatch, [remote], call_log=sent)
    tool = asyncio.run(manager._tools_for_server(manager.specs["fake"]))[0]

    config = load_config(project_root=str(tmp_path))
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(registry)
    context = ToolContext(
        cwd=str(tmp_path), config=config, approval_callback=lambda request: "approve"
    )

    call = {"id": "call_ok", "name": tool.name, "arguments": {"path": "important.txt"}}
    results = asyncio.run(executor.execute_all([call], context))

    assert not results[0].is_error
    assert results[0].content == "purged"
    assert sent == [("purge", {"path": "important.txt"})]
