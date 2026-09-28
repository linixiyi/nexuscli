"""Cross-feature integration tests for the agent-platform waves.

These cover the seams between features rather than any single one: the task
tool versus plan mode, permission rules stacked with hooks and plan mode, the
repl-side SessionStart trigger, and manual /compact leaving hooks and
permissions working.
"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from nexuscli.config import (
    HookCommandConfig,
    HookMatcherConfig,
    NexusCliConfig,
)
from nexuscli.entrypoints.repl import (
    SLASH_COMMANDS,
    PermissionModeController,
    ReplSessionState,
    _fire_session_start_hooks,
    _handle_slash,
)
from nexuscli.session import SessionStore
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.builtins import get_builtin_tools
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry
from nexuscli.types import Message

# Real-subprocess hook bodies (same pattern as tests/test_hooks.py: scripts
# live in tmp_path and the command only carries quoted native paths).
_EXIT_TWO_BODY = "import sys\nsys.stderr.write('hook says no')\nsys.stderr.flush()\nsys.exit(2)\n"
_CAPTURE_AND_GREET = (
    "import json, sys\n"
    "from pathlib import Path\n"
    'sys.stdin.reconfigure(encoding="utf-8")\n'
    "payload = json.load(sys.stdin)\n"
    "with open(Path(__file__).with_suffix('.count'), 'a', encoding='utf-8') as fh:\n"
    "    fh.write(payload['hook_event_name'] + '\\n')\n"
    "print(json.dumps({'hookSpecificOutput': {'additionalContext': 'session begun'}}))\n"
)


def _hook_command(script: Path, body: str) -> str:
    script.write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


def _console() -> Console:
    return Console(file=io.StringIO(), width=200)


def _console_text(console: Console) -> str:
    return console.file.getvalue()


def _mutate_registry(executed: list[str]) -> ToolRegistry:
    registry = ToolRegistry()

    async def handler(payload, _context):
        executed.append(str(payload["value"]))
        return ToolResult(f"mutated {payload['value']}")

    registry.register(
        Tool(
            name="mutate",
            description="Mutate test state",
            parameters=object_schema({"value": {"type": "string"}}, ["value"]),
            required_keys=["value"],
            handler=handler,
            is_read_only=False,
        )
    )

    async def grep(payload, _context):
        return ToolResult("grep matches: none")

    registry.register(
        Tool(
            name="grep",
            description="Search file contents",
            parameters=object_schema({"pattern": {"type": "string"}}, ["pattern"]),
            required_keys=["pattern"],
            handler=grep,
            is_read_only=True,
        )
    )
    return registry


def _call(name: str, arguments: dict) -> dict:
    return {"id": f"call-{name}", "name": name, "arguments": arguments}


# ---------------------------------------------------------------------------
# task tool x plan mode (F3 x F6)
# ---------------------------------------------------------------------------


def test_plan_mode_rejects_task_tool_before_any_subagent_runs(tmp_path):
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.policy.plan_mode = True
    registry = ToolRegistry()
    registry.register(next(tool for tool in get_builtin_tools() if tool.name == "task"))
    executor = ToolExecutor(registry)
    context = ToolContext(cwd=str(tmp_path), config=config)

    result = asyncio.run(
        executor.execute_all(
            [_call("task", {"description": "explore", "prompt": "list the modules"})],
            context,
        )
    )[0]

    # The plan gate short-circuits before the handler: no subagent is spawned
    # (a spawned subagent would fail later with a "task failed" message).
    assert result.is_error
    assert "plan mode" in result.content
    assert "task failed" not in result.content


# ---------------------------------------------------------------------------
# permission deny x PreToolUse hook x plan mode stacking (F1 x F2 x F6)
# ---------------------------------------------------------------------------


def _stacked_config(tmp_path: Path, *, plan: bool, deny: bool, hook: bool) -> NexusCliConfig:
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.policy.plan_mode = plan
    if deny:
        config.permissions.deny = ["mutate"]
    if hook:
        config.hooks.pre_tool_use = [
            HookMatcherConfig(
                hooks=[
                    HookCommandConfig(command=_hook_command(tmp_path / "deny.py", _EXIT_TWO_BODY))
                ]
            )
        ]
    return config


def test_any_of_plan_deny_or_hook_rejects_with_distinct_reasons(tmp_path):
    executed: list[str] = []
    executor = ToolExecutor(_mutate_registry(executed))
    call = _call("mutate", {"value": "ok"})

    def run(config: NexusCliConfig) -> ToolResult:
        executed.clear()
        context = ToolContext(cwd=str(tmp_path), config=config)
        return asyncio.run(executor.execute_all([call], context))[0]

    # Without any of the three gates the tool executes.
    baseline = run(_stacked_config(tmp_path, plan=False, deny=False, hook=False))
    assert not baseline.is_error
    assert executed == ["ok"]

    # Plan mode rejects first: its reason is the plan message even though a
    # deny rule and a blocking hook are also configured.
    plan = run(_stacked_config(tmp_path, plan=True, deny=True, hook=True))
    assert plan.is_error
    assert "plan mode" in plan.content
    assert executed == []

    # Without plan mode the deny rule wins and names the rule.
    denied = run(_stacked_config(tmp_path, plan=False, deny=True, hook=True))
    assert denied.is_error
    assert "denied by permission rule" in denied.content
    assert executed == []

    # Without plan mode and without a deny rule the PreToolUse hook rejects
    # with the hook's own reason.
    hooked = run(_stacked_config(tmp_path, plan=False, deny=False, hook=True))
    assert hooked.is_error
    assert "denied by pre-tool-use hook" in hooked.content
    assert "hook says no" in hooked.content
    assert executed == []


# ---------------------------------------------------------------------------
# SessionStart hooks at repl startup (repl-side trigger)
# ---------------------------------------------------------------------------


def test_session_start_hooks_fire_once_and_surface_context(tmp_path):
    config = NexusCliConfig()
    config.hooks.session_start = [
        HookMatcherConfig(
            hooks=[
                HookCommandConfig(command=_hook_command(tmp_path / "start.py", _CAPTURE_AND_GREET))
            ]
        )
    ]
    console = _console()

    asyncio.run(_fire_session_start_hooks(config, console, str(tmp_path)))

    counts = (tmp_path / "start.count").read_text(encoding="utf-8").splitlines()
    assert counts == ["SessionStart"]
    assert "session begun" in _console_text(console)


def test_session_start_hooks_are_skipped_when_unconfigured(tmp_path):
    console = _console()

    asyncio.run(_fire_session_start_hooks(NexusCliConfig(), console, str(tmp_path)))

    assert _console_text(console) == ""
    assert not (tmp_path / "start.count").exists()


# ---------------------------------------------------------------------------
# /compact wiring x hooks/permissions smoke (F5 x F2/F1)
# ---------------------------------------------------------------------------


def _long_history(turns: int = 16) -> list[Message]:
    messages: list[Message] = []
    for index in range(turns):
        messages.append(Message(role="user", content=f"question {index}: " + "detail " * 80))
        messages.append(Message(role="assistant", content=f"answer {index}: " + "finding " * 80))
    return messages


def _slash_agent(history: list[Message], tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        history=history,
        cwd=str(tmp_path),
        llm_client=SimpleNamespace(max_context_window=128_000),
    )


def _slash(console: Console, config: NexusCliConfig, agent, tmp_path: Path, raw: str) -> None:
    store = SessionStore(root=tmp_path / "sessions")
    state = ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))
    asyncio.run(
        _handle_slash(
            raw,
            console,
            str(tmp_path),
            config,
            agent,
            None,
            PermissionModeController(config),
            None,
            state,
        )
    )


def test_compact_slash_command_reports_and_shrinks_history(tmp_path):
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    history = _long_history()
    agent = _slash_agent(history, tmp_path)
    console = _console()

    _slash(console, config, agent, tmp_path, "/compact keep the login bug")

    text = _console_text(console)
    assert "NexusCLI Compaction" in text
    assert "tokens before" in text
    assert "tokens after" in text
    assert "summarized messages" in text
    assert "keep the login bug" in text
    assert len(agent.history) < len(history)


def test_compact_with_empty_history_prints_friendly_notice(tmp_path):
    config = NexusCliConfig()
    agent = _slash_agent([], tmp_path)
    console = _console()

    _slash(console, config, agent, tmp_path, "/compact")

    assert "no conversation history" in _console_text(console)
    assert agent.history == []


def test_compact_slash_command_is_listed_in_help():
    assert "/compact" in SLASH_COMMANDS


def test_after_compact_hooks_and_permissions_still_apply(tmp_path):
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.permissions.deny = ["grep"]
    config.hooks.pre_tool_use = [
        HookMatcherConfig(
            hooks=[
                HookCommandConfig(command=_hook_command(tmp_path / "pre.py", _CAPTURE_AND_GREET))
            ]
        )
    ]
    agent = _slash_agent(_long_history(), tmp_path)
    console = _console()
    _slash(console, config, agent, tmp_path, "/compact")
    assert len(agent.history) < 32  # compaction actually happened

    executed: list[str] = []
    executor = ToolExecutor(_mutate_registry(executed))
    context = ToolContext(cwd=str(tmp_path), config=config)
    results = asyncio.run(
        executor.execute_all(
            [
                _call("mutate", {"value": "after-compact"}),
                _call("grep", {"pattern": "x"}),
            ],
            context,
        )
    )
    by_name = {result.tool_use_id: result for result in results}

    # The mutate call still executes and its PreToolUse hook still fires; the
    # deny rule still rejects the read-only grep call.
    assert not by_name["call-mutate"].is_error
    assert executed == ["after-compact"]
    events = (tmp_path / "pre.count").read_text(encoding="utf-8").splitlines()
    assert events == ["PreToolUse"]
    assert by_name["call-grep"].is_error
    assert "denied by permission rule" in by_name["call-grep"].content
