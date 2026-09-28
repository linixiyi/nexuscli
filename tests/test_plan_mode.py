"""Tests for the REPL plan mode (three-state toggle, executor read-only gate)."""

from __future__ import annotations

import asyncio

from nexuscli.config import NexusCliConfig, load_config
from nexuscli.entrypoints.repl import PermissionModeController, _permission_mode_label
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry


def _controller(config: NexusCliConfig) -> PermissionModeController:
    return PermissionModeController(config)


def _registry_with_tools(executions: list[str]) -> ToolRegistry:
    registry = ToolRegistry()

    def make_tool(name: str, read_only: bool, requires_approval: bool) -> Tool:
        async def handler(payload, _context):
            executions.append(name)
            return ToolResult(f"{name} ok")

        return Tool(
            name=name,
            description=f"fake {name}",
            parameters=object_schema({"path": {"type": "string"}}),
            handler=handler,
            is_read_only=read_only,
            requires_approval=requires_approval,
        )

    registry.register(make_tool("read_file", True, False))
    registry.register(make_tool("grep", True, False))
    registry.register(make_tool("write_file", False, False))
    registry.register(make_tool("bash", False, True))
    return registry


def test_plan_mode_rejects_write_tools_and_allows_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = load_config(project_root=tmp_path)
    _controller(config).set("plan")
    executions: list[str] = []
    registry = _registry_with_tools(executions)
    executor = ToolExecutor(registry)
    context = ToolContext(cwd=str(tmp_path), config=config)

    def call(name: str) -> dict[str, object]:
        return {"id": f"call-{name}", "name": name, "arguments": {"path": "x.txt"}}

    results = asyncio.run(
        executor.execute_all(
            [call("write_file"), call("bash"), call("read_file"), call("grep")],
            context,
        )
    )

    # Read-only calls run first (concurrently), the rest follow serially, so
    # results are keyed by tool_use_id instead of order.
    by_name = {result.tool_use_id: result for result in results}
    assert by_name["call-write_file"].is_error
    assert "plan mode" in by_name["call-write_file"].content
    assert "Shift+Tab" in by_name["call-write_file"].content
    assert by_name["call-bash"].is_error
    assert "plan mode" in by_name["call-bash"].content
    assert not by_name["call-read_file"].is_error
    assert not by_name["call-grep"].is_error
    assert executions == ["read_file", "grep"]


def test_plan_mode_still_honors_permission_deny_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = load_config(project_root=tmp_path)
    config.permissions.deny = ["read_file"]
    _controller(config).set("plan")
    executions: list[str] = []
    registry = _registry_with_tools(executions)
    executor = ToolExecutor(registry)
    context = ToolContext(cwd=str(tmp_path), config=config)

    calls = [
        {"id": "call-ro", "name": "read_file", "arguments": {"path": "x.txt"}},
        {"id": "call-wo", "name": "write_file", "arguments": {"path": "y.txt"}},
    ]
    results = asyncio.run(executor.execute_all(calls, context))

    # The read-only tool flows through the normal chain and is still denied
    # by its rule; the write tool is rejected by the plan gate (which runs
    # before rule evaluation), so its reason is the plan-mode message.
    by_id = {result.tool_use_id: result for result in results}
    assert by_id["call-ro"].is_error
    assert "denied by permission rule" in by_id["call-ro"].content
    assert by_id["call-wo"].is_error
    assert "plan mode" in by_id["call-wo"].content
    assert executions == []


def test_plan_mode_never_triggers_approval_callback(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = load_config(project_root=tmp_path)
    controller = _controller(config)
    executions: list[str] = []
    prompts: list[dict[str, object]] = []

    async def callback(request):
        prompts.append(request)
        return "approve"

    registry = _registry_with_tools(executions)
    executor = ToolExecutor(registry)
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=callback)
    call = {"id": "call-1", "name": "bash", "arguments": {"path": "x.txt"}}

    # In default mode the approval-required tool reaches the callback.
    approved = asyncio.run(executor.execute_all([call], context))[0]
    assert not approved.is_error
    assert len(prompts) == 1

    # In plan mode the same call is short-circuited before approval.
    controller.set("plan")
    rejected = asyncio.run(executor.execute_all([call], context))[0]
    assert rejected.is_error
    assert "plan mode" in rejected.content
    assert len(prompts) == 1


def test_permission_mode_toggles_through_three_states():
    config = NexusCliConfig()
    config.policy.hitl_mode = "always"
    controller = PermissionModeController(config)

    assert controller.mode == "default"
    assert config.policy.hitl_mode == "always"
    assert not config.policy.plan_mode

    assert controller.toggle() == "auto"
    assert config.policy.hitl_mode == "never"
    assert not config.policy.path_guard_enabled
    assert not config.policy.command_guard_enabled
    assert not config.policy.plan_mode

    assert controller.toggle() == "plan"
    assert config.policy.plan_mode
    # Plan is a read-only constraint, not an approval bypass: HITL and the
    # guards are restored exactly like in default mode.
    assert config.policy.hitl_mode == "always"
    assert config.policy.path_guard_enabled
    assert config.policy.command_guard_enabled

    assert controller.toggle() == "default"
    assert not config.policy.plan_mode
    assert config.policy.hitl_mode == "always"


def test_plan_mode_label_and_config_defaults():
    config = NexusCliConfig()
    assert config.policy.plan_mode is False
    assert _permission_mode_label("default") == "Default"
    assert _permission_mode_label("auto") == "Auto (full access)"
    assert _permission_mode_label("plan") == "plan (read-only)"
