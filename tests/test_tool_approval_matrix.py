"""Which built-in tools must ask a human before acting.

The registry used to leave every write-shaped file tool at
requires_approval=False, so in the default hitl_mode="auto" the agent could
overwrite files without ever calling the approval callback — while bash, the
tool users actually watch, did prompt. That inversion is what these tests pin:
a tool that changes the workspace must ask, and a tool that only reads must not.
"""

from __future__ import annotations

import asyncio

import pytest

from nexuscli.config import NexusCliConfig
from nexuscli.tools.base import ToolContext
from nexuscli.tools.builtins import get_builtin_tools
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry

# Tools whose effect outlives the call and cannot be undone by ignoring the
# result: they write files, spawn writable sub-agents, or persist memory.
MUST_ASK = {
    "bash",
    "execute_command",
    "write_file",
    "edit_file",
    "save_memory",
    "save_skill",
    "task_stop",
    "revert_turn",
}


def test_every_workspace_mutator_requires_approval() -> None:
    registry = {tool.name: tool for tool in get_builtin_tools()}
    missing = sorted(name for name in MUST_ASK if not registry[name].requires_approval)
    assert missing == [], f"tools that change state without asking: {missing}"


def test_read_only_tools_never_require_approval() -> None:
    # A read-only fast path that still prompts makes the prompt meaningless.
    noisy = sorted(
        tool.name for tool in get_builtin_tools() if tool.is_read_only and tool.requires_approval
    )
    assert noisy == [], f"read-only tools that prompt anyway: {noisy}"


def test_write_file_actually_calls_the_approval_callback(tmp_path) -> None:
    registry = ToolRegistry()
    for tool in get_builtin_tools():
        registry.register(tool)
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    asked: list[str] = []

    def deny(request):
        asked.append(request["tool_name"])
        return "deny"

    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=deny)
    call = {
        "id": "c1",
        "name": "write_file",
        "arguments": {"path": "blocked.txt", "content": "nope"},
    }
    result = asyncio.run(ToolExecutor(registry).execute_all([call], context))[0]

    assert result.is_error
    assert asked == ["write_file"]
    assert not (tmp_path / "blocked.txt").exists()


def test_write_file_proceeds_after_approval(tmp_path) -> None:
    registry = ToolRegistry()
    for tool in get_builtin_tools():
        registry.register(tool)
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=lambda _r: "approve")
    call = {
        "id": "c1",
        "name": "write_file",
        "arguments": {"path": "allowed.txt", "content": "yes"},
    }
    result = asyncio.run(ToolExecutor(registry).execute_all([call], context))[0]

    assert not result.is_error
    assert (tmp_path / "allowed.txt").read_text() == "yes"


@pytest.mark.parametrize("tool_name", ["read_file", "grep", "glob", "list_dir"])
def test_reading_never_prompts(tool_name: str, tmp_path) -> None:
    registry = ToolRegistry()
    for tool in get_builtin_tools():
        registry.register(tool)
    tool = registry.get(tool_name)
    assert tool is not None
    assert tool.requires_approval is False
