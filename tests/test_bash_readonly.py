"""Tests for static read-only bash command detection and executor pass-through.

Covers two layers:

- the policy function ``is_readonly_bash_command`` (argv-level whitelist with
  conservative failure) via table-driven cases;
- the executor wiring: statically read-only bash/execute_command calls skip
  the plan gate and the HITL prompt, while deny/ask rules, PreToolUse hook
  hints, ``hitl_mode: "always"`` and the audit record keep their priority.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from nexuscli.config import HOOK_EVENT_FIELDS, HookCommandConfig, HookMatcherConfig, NexusCliConfig
from nexuscli.policy import is_readonly_bash_command
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Policy unit cases
# ---------------------------------------------------------------------------

READONLY_COMMANDS = [
    "git status",
    "git log --oneline -5",
    "git diff HEAD~1",
    "git show HEAD",
    "git branch --list",
    "git branch -a",
    "git branch -av",
    "git branch",
    "git remote -v",
    "git tag -l",
    "git tag",
    "git config --get user.name",
    "git config --list",
    "git --no-pager log",
    "git rev-parse HEAD",
    "git ls-files",
    "ls -la",
    "cat README.md",
    "rg pattern src",
    "find . -name *.py",
    "find . -maxdepth 2 -type f",
    "python --version",
    "python -V",
    "node --version",
    "npm --version",
    "uv tree",
    "pip freeze",
    "env",
    "env FOO=bar",
    "printenv PATH",
]

NON_READONLY_COMMANDS = [
    # required by the brief
    "git push",
    "git commit -m x",
    "git branch new-branch",
    "sed -i s/a/b/ f",
    "echo hi > f.txt",
    "pip install requests",
    "python script.py",
    "find . -name x -delete",
    "ls; rm x",
    "cat a | sh",
    "unknowncmd --x",
    "",
    'sh -c "ls"',
    # metacharacters are never judgeable
    "ls && rm x",
    "ls &",
    "ls $HOME",
    "cat `file`",
    "echo hi >> f.txt",
    "cat < /etc/passwd",
    "ls $(pwd)",
    # git writes and un-judged global options
    "git diff --output=x.patch",
    # --output writes a file to disk on every whitelisted git reader: log and
    # show support it directly (like diff), reflog show inherits it
    "git log --output=x",
    "git show --output=out.txt HEAD",
    "git log --output out.txt",
    "git show --output out.txt HEAD",
    "git reflog expire --all",
    "git reflog delete HEAD@{1}",
    "git remote add origin https://example.com/x.git",
    "git tag v1.0",
    "git branch -d main",
    "git config user.name x",
    "git config --global user.name x",
    "git -c core.autocrlf=false status",
    "git",
    # inspection commands with side-effecting spellings
    "env rm -rf x",
    "rg --pre cat pattern",
    "date -s 2020-01-01",
    # interpreters/package managers beyond self-describing forms
    "python",
    "python -c 'import os'",
    "npm run build",
    "uv sync",
    "uv pip install ruff",
    "uv list --bogus > out.txt",
]


@pytest.mark.parametrize("command", READONLY_COMMANDS)
def test_readonly_commands_are_detected(command: str) -> None:
    assert is_readonly_bash_command(command) is True


@pytest.mark.parametrize("command", NON_READONLY_COMMANDS)
def test_non_readonly_commands_are_rejected(command: str) -> None:
    assert is_readonly_bash_command(command) is False


def test_windows_style_executable_basename_is_dispatched() -> None:
    # ``where`` is whitelisted; the .exe suffix must land on the same entry.
    assert is_readonly_bash_command("where git") is True
    assert is_readonly_bash_command("where.exe git") is True
    assert is_readonly_bash_command("C:/Windows/system32/where.exe git") is True
    assert is_readonly_bash_command("reg.exe query HKLM") is False
    # posix shlex consumes backslashes, so a backslash path fails closed.
    assert is_readonly_bash_command("C:\\Windows\\system32\\where.exe git") is False


def test_unparseable_quoting_fails_conservatively() -> None:
    assert is_readonly_bash_command("cat 'unterminated") is False


# ---------------------------------------------------------------------------
# Executor integration
# ---------------------------------------------------------------------------


def _registry_with_shell_tool(executed: list[str], name: str = "bash") -> ToolRegistry:
    registry = ToolRegistry()

    async def handler(payload, _context):
        executed.append(str(payload["command"]))
        return ToolResult(f"ran {payload['command']}")

    registry.register(
        Tool(
            name=name,
            description=f"fake {name}",
            parameters=object_schema({"command": {"type": "string"}}, ["command"]),
            required_keys=["command"],
            handler=handler,
            is_read_only=False,
            is_concurrency_safe=False,
            danger_level="high",
            requires_approval=True,
        )
    )
    return registry


def _run_shell(
    name: str,
    command: str,
    config: NexusCliConfig,
    context: ToolContext,
    executed: list[str],
) -> ToolResult:
    executor = ToolExecutor(_registry_with_shell_tool(executed, name=name))
    call = {"id": "call-1", "name": name, "arguments": {"command": command}}
    return asyncio.run(executor.execute_all([call], context))[0]


def _hermetic_config(tmp_path: Path) -> NexusCliConfig:
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    return config


def _last_audit_record(tmp_path: Path) -> dict:
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert lines, "expected an audit record"
    return json.loads(lines[-1])


def test_plan_mode_allows_readonly_bash_but_rejects_writes(tmp_path) -> None:
    config = _hermetic_config(tmp_path)
    config.policy.plan_mode = True
    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config)

    readonly = _run_shell("bash", "git status", config, context, executed)
    assert not readonly.is_error
    assert executed == ["git status"]

    write = _run_shell("bash", "rm -rf x", config, context, executed)
    assert write.is_error
    assert "plan mode" in write.content
    assert "Shift+Tab" in write.content
    assert executed == ["git status"]


def test_default_mode_readonly_bash_skips_approval_callback(tmp_path) -> None:
    config = _hermetic_config(tmp_path)
    prompts: list[dict[str, object]] = []

    def callback(request):
        prompts.append(request)
        return "approve"

    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=callback)

    readonly = _run_shell("bash", "git status", config, context, executed)
    assert not readonly.is_error
    assert executed == ["git status"]
    assert prompts == []

    write = _run_shell("bash", "rm -rf x", config, context, executed)
    assert not write.is_error
    assert executed == ["git status", "rm -rf x"]
    assert len(prompts) == 1
    assert prompts[0]["tool_name"] == "bash"


def test_readonly_bash_runs_without_any_approval_callback(tmp_path) -> None:
    # No callback configured: a write tool would be denied, the readonly
    # command must still flow through (and stay audited, see next test).
    config = _hermetic_config(tmp_path)
    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config)

    readonly = _run_shell("bash", "ls -la", config, context, executed)
    assert not readonly.is_error
    assert executed == ["ls -la"]


def test_execute_command_alias_gets_the_same_readonly_bypass(tmp_path) -> None:
    config = _hermetic_config(tmp_path)
    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config)

    readonly = _run_shell("execute_command", "git status", config, context, executed)
    assert not readonly.is_error
    assert executed == ["git status"]

    write = _run_shell("execute_command", "python script.py", config, context, executed)
    assert write.is_error
    assert executed == ["git status"]


def test_ask_rule_still_prompts_for_readonly_bash(tmp_path) -> None:
    config = _hermetic_config(tmp_path)
    config.permissions.ask = ["bash(git status:*)"]
    prompts: list[dict[str, object]] = []

    def approve(_request):
        prompts.append("asked")
        return "approve"

    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=approve)

    result = _run_shell("bash", "git status", config, context, executed)
    assert not result.is_error
    assert prompts == ["asked"]
    assert executed == ["git status"]
    # The ask rule routed the call to a human: the audit stays "hitl".
    record = _last_audit_record(tmp_path)
    assert record["outcome"] == "allow"
    assert record["approver"] == "hitl"


def test_deny_rule_still_blocks_readonly_bash(tmp_path) -> None:
    config = _hermetic_config(tmp_path)
    config.permissions.deny = ["bash(git status:*)"]
    prompts: list[dict[str, object]] = []

    def approve(_request):
        prompts.append("asked")
        return "approve"

    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=approve)

    result = _run_shell("bash", "git status", config, context, executed)
    assert result.is_error
    assert "denied by permission rule" in result.content
    assert executed == []
    assert prompts == []


def test_always_mode_still_prompts_for_readonly_bash(tmp_path) -> None:
    config = _hermetic_config(tmp_path)
    config.policy.hitl_mode = "always"
    prompts: list[dict[str, object]] = []

    def approve(_request):
        prompts.append("asked")
        return "approve"

    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=approve)

    result = _run_shell("bash", "git status", config, context, executed)
    assert not result.is_error
    assert prompts == ["asked"]
    assert executed == ["git status"]
    # always mode prompted a human: the audit stays "hitl".
    record = _last_audit_record(tmp_path)
    assert record["outcome"] == "allow"
    assert record["approver"] == "hitl"


def test_pre_tool_use_ask_hook_still_prompts_for_readonly_bash(tmp_path: Path) -> None:
    hook_script = tmp_path / "ask_hook.py"
    hook_script.write_text(
        "import json\n"
        "print(json.dumps({'hookSpecificOutput': {'permissionDecision': 'ask',"
        " 'permissionDecisionReason': 'hook wants a prompt'}}))\n",
        encoding="utf-8",
    )
    config = _hermetic_config(tmp_path)
    setattr(
        config.hooks,
        HOOK_EVENT_FIELDS["PreToolUse"],
        [
            HookMatcherConfig(
                matcher="bash",
                hooks=[HookCommandConfig(command=f'"{sys.executable}" "{hook_script}"')],
            )
        ],
    )
    prompts: list[dict[str, object]] = []

    def approve(_request):
        prompts.append("asked")
        return "approve"

    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=approve)

    result = _run_shell("bash", "git status", config, context, executed)
    assert not result.is_error
    assert prompts == ["asked"]
    assert executed == ["git status"]
    # The ask hint routed the call to a human: the audit stays "hitl".
    record = _last_audit_record(tmp_path)
    assert record["outcome"] == "allow"
    assert record["approver"] == "hitl"


def test_readonly_bash_bypass_is_still_audited(tmp_path) -> None:
    # Pass-through is not trace-free: bash stays a non-read-only tool and
    # must keep producing an audit record — attributed to the rule that
    # bypassed the prompt ("readonly-rule"), since no human approved.
    config = _hermetic_config(tmp_path)
    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config)

    result = _run_shell("bash", "git status", config, context, executed)
    assert not result.is_error
    record = _last_audit_record(tmp_path)
    assert record["tool_name"] == "bash"
    assert record["outcome"] == "allow"
    assert record["approver"] == "readonly-rule"


def test_human_approved_write_is_still_audited_as_hitl(tmp_path) -> None:
    # When a person actually approves through the callback, the audit record
    # keeps attributing the call to "hitl" — only the rule-driven bypass is
    # renamed to "readonly-rule".
    config = _hermetic_config(tmp_path)

    def approve(_request):
        return "approve"

    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=approve)

    result = _run_shell("bash", "rm -rf x", config, context, executed)
    assert not result.is_error
    assert executed == ["rm -rf x"]
    record = _last_audit_record(tmp_path)
    assert record["outcome"] == "allow"
    assert record["approver"] == "hitl"
