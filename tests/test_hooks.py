"""Tests for the lifecycle hook system (config, matcher, runner, executor)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from nexuscli.config import (
    HOOK_EVENT_FIELDS,
    HookCommandConfig,
    HookMatcherConfig,
    HooksConfig,
    NexusCliConfig,
    load_config,
)
from nexuscli.hooks import HookOutcome, fire_event, has_hooks, hooks_for, run_hooks
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Hook scripts (real subprocesses; written to tmp_path for Windows-safe
# quoting: the command only ever carries quoted native paths, never inline
# code with quote characters). Payload-capturing scripts write their stdin
# JSON next to themselves (script.py -> script.json).
# ---------------------------------------------------------------------------

_CAPTURE_STDIN = (
    "import json, sys\n"
    "from pathlib import Path\n"
    'sys.stdin.reconfigure(encoding="utf-8")\n'
    "payload = json.load(sys.stdin)\n"
    "capture = Path(__file__).with_suffix('.json')\n"
    "capture.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')\n"
)

_EXIT_TWO_BODY = (
    "import sys\nsys.stderr.write('no pushing allowed')\nsys.stderr.flush()\nsys.exit(2)\n"
)

_DECISION_BLOCK = 'print(json.dumps({"decision": "block", "reason": "not today"}))\n'
_PERMISSION_DENY = (
    "print(json.dumps({'hookSpecificOutput': {'permissionDecision': 'deny',"
    " 'permissionDecisionReason': 'denied by hook policy'}}))\n"
)
_PERMISSION_ASK = (
    "print(json.dumps({'hookSpecificOutput': {'permissionDecision': 'ask',"
    " 'permissionDecisionReason': 'better ask first'}}))\n"
)
_PERMISSION_ALLOW = "print(json.dumps({'hookSpecificOutput': {'permissionDecision': 'allow'}}))\n"
_ADDITIONAL_CONTEXT = (
    "print(json.dumps({'hookSpecificOutput': {'additionalContext': 'remember the rules'}}))\n"
)


def _hook_command(script: Path, body: str) -> str:
    script.write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


def _capture_hook(script: Path, stdout_lines: str = "") -> str:
    return _hook_command(script, _CAPTURE_STDIN + stdout_lines)


def _runner_payload(tmp_path: Path) -> dict[str, object]:
    return {"cwd": str(tmp_path)}


# ---------------------------------------------------------------------------
# Matcher registry
# ---------------------------------------------------------------------------


def _config_with_matcher(event: str, matcher: str) -> HooksConfig:
    config = HooksConfig()
    setattr(
        config,
        HOOK_EVENT_FIELDS[event],
        [HookMatcherConfig(matcher=matcher, hooks=[HookCommandConfig(command="echo hi")])],
    )
    return config


def test_matcher_exact_match_and_miss() -> None:
    config = _config_with_matcher("PreToolUse", "bash")
    assert len(hooks_for(config, "PreToolUse", "bash")) == 1
    assert hooks_for(config, "PreToolUse", "write_file") == []
    assert hooks_for(config, "PreToolUse", None) == []


def test_matcher_regex_alternation_and_anchor() -> None:
    config = _config_with_matcher("PreToolUse", "bash|write_file")
    assert len(hooks_for(config, "PreToolUse", "bash")) == 1
    assert len(hooks_for(config, "PreToolUse", "write_file")) == 1
    assert hooks_for(config, "PreToolUse", "web_fetch") == []
    anchored = _config_with_matcher("PreToolUse", "^web_")
    assert len(hooks_for(anchored, "PreToolUse", "web_fetch")) == 1
    assert hooks_for(anchored, "PreToolUse", "fetch_web") == []


def test_matcher_star_and_empty_match_everything() -> None:
    for matcher in ("*", ""):
        config = _config_with_matcher("PreToolUse", matcher)
        assert len(hooks_for(config, "PreToolUse", "anything")) == 1
        assert len(hooks_for(config, "PreToolUse", None)) == 1


def test_matcher_ignored_for_non_tool_events() -> None:
    config = _config_with_matcher("SessionStart", "bash")
    assert len(hooks_for(config, "SessionStart", None)) == 1


def test_invalid_matcher_regex_never_matches() -> None:
    config = _config_with_matcher("PreToolUse", "([unclosed")
    assert hooks_for(config, "PreToolUse", "bash") == []


def test_unknown_event_matches_nothing() -> None:
    config = _config_with_matcher("PreToolUse", "*")
    assert hooks_for(config, "SubagentStop", "bash") == []


def test_only_matching_matcher_groups_contribute_hooks() -> None:
    config = HooksConfig()
    config.pre_tool_use = [
        HookMatcherConfig(matcher="bash", hooks=[HookCommandConfig(command="echo one")]),
        HookMatcherConfig(matcher="write_file", hooks=[HookCommandConfig(command="echo two")]),
    ]
    assert [hook.command for hook in hooks_for(config, "PreToolUse", "bash")] == ["echo one"]


# ---------------------------------------------------------------------------
# Runner protocol
# ---------------------------------------------------------------------------


def test_exit_two_blocks_with_stderr_reason(tmp_path: Path) -> None:
    command = _hook_command(tmp_path / "block.py", _EXIT_TWO_BODY)
    outcome = asyncio.run(
        run_hooks([HookCommandConfig(command=command)], "PreToolUse", _runner_payload(tmp_path))
    )
    assert outcome.blocked
    assert "no pushing allowed" in outcome.reason
    assert outcome.errors == []


def test_exit_two_without_stderr_uses_fallback_reason(tmp_path: Path) -> None:
    command = _hook_command(tmp_path / "silent_block.py", "import sys\nsys.exit(2)\n")
    outcome = asyncio.run(
        run_hooks([HookCommandConfig(command=command)], "PreToolUse", _runner_payload(tmp_path))
    )
    assert outcome.blocked
    assert outcome.reason == "blocked by hook"


def test_stdout_decision_block_with_reason(tmp_path: Path) -> None:
    command = _hook_command(tmp_path / "decide.py", "import json\n" + _DECISION_BLOCK)
    outcome = asyncio.run(
        run_hooks([HookCommandConfig(command=command)], "PreToolUse", _runner_payload(tmp_path))
    )
    assert outcome.blocked
    assert outcome.reason == "not today"


def test_permission_decision_deny_blocks(tmp_path: Path) -> None:
    command = _hook_command(tmp_path / "deny.py", "import json\n" + _PERMISSION_DENY)
    outcome = asyncio.run(
        run_hooks([HookCommandConfig(command=command)], "PreToolUse", _runner_payload(tmp_path))
    )
    assert outcome.blocked
    assert outcome.reason == "denied by hook policy"


def test_permission_decision_ask_and_allow_are_hints_only(tmp_path: Path) -> None:
    ask = _hook_command(tmp_path / "ask.py", "import json\n" + _PERMISSION_ASK)
    ask_outcome = asyncio.run(
        run_hooks([HookCommandConfig(command=ask)], "PreToolUse", _runner_payload(tmp_path))
    )
    assert ask_outcome.permission_hint == "ask"
    assert not ask_outcome.blocked

    allow = _hook_command(tmp_path / "allow.py", "import json\n" + _PERMISSION_ALLOW)
    allow_outcome = asyncio.run(
        run_hooks([HookCommandConfig(command=allow)], "PreToolUse", _runner_payload(tmp_path))
    )
    assert allow_outcome.permission_hint == "allow"
    assert not allow_outcome.blocked


def test_timeout_is_a_non_blocking_error(tmp_path: Path) -> None:
    command = _hook_command(tmp_path / "slow.py", "import time\ntime.sleep(5)\n")
    outcome = asyncio.run(
        run_hooks(
            [HookCommandConfig(command=command, timeout=1)],
            "PreToolUse",
            _runner_payload(tmp_path),
        )
    )
    assert not outcome.blocked
    assert len(outcome.errors) == 1
    assert "timed out" in outcome.errors[0]


def test_other_exit_codes_are_non_blocking_errors(tmp_path: Path) -> None:
    body = "import sys\nsys.stderr.write('boom details')\nsys.exit(3)\n"
    command = _hook_command(tmp_path / "crash.py", body)
    outcome = asyncio.run(
        run_hooks([HookCommandConfig(command=command)], "PreToolUse", _runner_payload(tmp_path))
    )
    assert not outcome.blocked
    assert len(outcome.errors) == 1
    assert "boom details" in outcome.errors[0]
    assert "3" in outcome.errors[0]


def test_plain_stdout_is_ignored(tmp_path: Path) -> None:
    command = _hook_command(tmp_path / "chatty.py", "print('just a log line')\n")
    outcome = asyncio.run(
        run_hooks([HookCommandConfig(command=command)], "PreToolUse", _runner_payload(tmp_path))
    )
    assert not outcome.blocked
    assert outcome.reason == ""
    assert outcome.permission_hint == ""
    assert outcome.errors == []


def test_additional_context_is_collected(tmp_path: Path) -> None:
    command = _hook_command(tmp_path / "ctx.py", "import json\n" + _ADDITIONAL_CONTEXT)
    outcome = asyncio.run(
        run_hooks([HookCommandConfig(command=command)], "PreToolUse", _runner_payload(tmp_path))
    )
    assert outcome.additional_context == "remember the rules"


def test_hook_outcome_defaults() -> None:
    outcome = HookOutcome()
    assert not outcome.blocked
    assert outcome.reason == ""
    assert outcome.additional_context == ""
    assert outcome.errors == []
    assert outcome.permission_hint == ""


# ---------------------------------------------------------------------------
# Executor integration
# ---------------------------------------------------------------------------


def _registry_with_mutate_tool(executed: list[str]) -> ToolRegistry:
    registry = ToolRegistry()

    async def mutate(payload, _context):
        executed.append(str(payload["value"]))
        return ToolResult(f"mutated {payload['value']}")

    registry.register(
        Tool(
            name="mutate",
            description="Mutate test state",
            parameters=object_schema({"value": {"type": "string"}}, ["value"]),
            required_keys=["value"],
            handler=mutate,
            is_read_only=False,
            requires_approval=True,
        )
    )
    return registry


def _run_mutate(config: NexusCliConfig, context: ToolContext) -> ToolResult:
    executed: list[str] = []
    registry = _registry_with_mutate_tool(executed)
    executor = ToolExecutor(registry)
    call = {"id": "call-1", "name": "mutate", "arguments": {"value": "ok"}}
    result = asyncio.run(executor.execute_all([call], context))[0]
    result.content += f"|executed={executed}"  # surface execution for assertions
    return result


def _hooked_config(tmp_path: Path, event: str, command: str, matcher: str = "*") -> NexusCliConfig:
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    setattr(
        config.hooks,
        HOOK_EVENT_FIELDS[event],
        [
            HookMatcherConfig(
                matcher=matcher,
                hooks=[HookCommandConfig(command=command)],
            )
        ],
    )
    return config


def _last_audit_record(tmp_path: Path) -> dict[str, object]:
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert lines, "expected an audit record"
    return json.loads(lines[-1])


def test_pre_hook_exit_two_blocks_executor_and_audits(tmp_path: Path) -> None:
    command = _hook_command(tmp_path / "deny.py", _EXIT_TWO_BODY)
    config = _hooked_config(tmp_path, "PreToolUse", command)
    result = _run_mutate(config, ToolContext(cwd=str(tmp_path), config=config))
    assert result.is_error
    assert "no pushing allowed" in result.content
    assert "executed=[]" in result.content
    record = _last_audit_record(tmp_path)
    assert record["outcome"] == "deny"
    assert record["approver"] == "hook"


def test_pre_hook_decision_block_reaches_executor(tmp_path: Path) -> None:
    command = _hook_command(tmp_path / "decide.py", "import json\n" + _DECISION_BLOCK)
    config = _hooked_config(tmp_path, "PreToolUse", command)
    result = _run_mutate(config, ToolContext(cwd=str(tmp_path), config=config))
    assert result.is_error
    assert "not today" in result.content
    assert "executed=[]" in result.content


def test_pre_hook_permission_deny_reaches_executor(tmp_path: Path) -> None:
    command = _hook_command(tmp_path / "deny.py", "import json\n" + _PERMISSION_DENY)
    config = _hooked_config(tmp_path, "PreToolUse", command)
    result = _run_mutate(config, ToolContext(cwd=str(tmp_path), config=config))
    assert result.is_error
    assert "denied by hook policy" in result.content
    assert "executed=[]" in result.content


def test_pre_hook_ask_forces_hitl_callback(tmp_path: Path) -> None:
    command = _hook_command(tmp_path / "ask.py", "import json\n" + _PERMISSION_ASK)
    config = _hooked_config(tmp_path, "PreToolUse", command)
    prompts: list[str] = []

    def approve(_request):
        prompts.append("asked")
        return "approve"

    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=approve)
    result = _run_mutate(config, context)
    # auto mode + no requires_approval would never prompt without the hook;
    # the "ask" hint must force the callback anyway.
    assert not result.is_error
    assert prompts == ["asked"]
    assert "executed=['ok']" in result.content


def test_pre_hook_allow_does_not_bypass_requires_approval(tmp_path: Path) -> None:
    command = _hook_command(tmp_path / "allow.py", "import json\n" + _PERMISSION_ALLOW)
    config = _hooked_config(tmp_path, "PreToolUse", command)
    prompts: list[str] = []

    def deny(_request):
        prompts.append("asked")
        return "deny"

    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=deny)
    result = _run_mutate(config, context)
    # The hook said "allow", but the tool requires approval: the callback must
    # still run and its denial must stand.
    assert result.is_error
    assert prompts == ["asked"]
    assert "executed=[]" in result.content


def test_pre_hook_timeout_does_not_block_execution(tmp_path: Path) -> None:
    command = _hook_command(tmp_path / "slow.py", "import time\ntime.sleep(5)\n")
    config = _hooked_config(tmp_path, "PreToolUse", command)
    config.hooks.pre_tool_use[0].hooks[0].timeout = 1
    prompts: list[str] = []

    def approve(_request):
        prompts.append("asked")
        return "approve"

    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=approve)
    result = _run_mutate(config, context)
    # The timed-out hook is only an error: the tool still runs and the normal
    # requires-approval prompt is unaffected.
    assert not result.is_error
    assert "executed=['ok']" in result.content
    assert prompts == ["asked"]


def test_post_hook_runs_after_tool_and_blocking_is_audited(tmp_path: Path) -> None:
    command = _hook_command(
        tmp_path / "post.py", "import json\n" + _CAPTURE_STDIN + _PERMISSION_DENY
    )
    config = _hooked_config(tmp_path, "PostToolUse", command)
    context = ToolContext(
        cwd=str(tmp_path), config=config, approval_callback=lambda _request: "approve"
    )
    result = _run_mutate(config, context)
    # The produced result is untouched by post hooks.
    assert not result.is_error
    assert "executed=['ok']" in result.content
    record = _last_audit_record(tmp_path)
    assert record["outcome"] == "deny"
    assert record["approver"] == "hook"


# ---------------------------------------------------------------------------
# Stdin payload
# ---------------------------------------------------------------------------


def test_pre_and_post_hook_payload_fields(tmp_path: Path) -> None:
    pre_capture = tmp_path / "pre.json"
    post_capture = tmp_path / "post.json"
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.policy.session_id = "sess-1234"
    config.hooks.pre_tool_use = [
        HookMatcherConfig(hooks=[HookCommandConfig(command=_capture_hook(tmp_path / "pre.py"))])
    ]
    config.hooks.post_tool_use = [
        HookMatcherConfig(hooks=[HookCommandConfig(command=_capture_hook(tmp_path / "post.py"))])
    ]
    result = _run_mutate(
        config,
        ToolContext(
            cwd=str(tmp_path),
            config=config,
            approval_callback=lambda _request: "approve",
        ),
    )
    assert not result.is_error

    pre = json.loads(pre_capture.read_text(encoding="utf-8"))
    assert pre["hook_event_name"] == "PreToolUse"
    assert pre["session_id"] == "sess-1234"
    assert pre["cwd"] == str(tmp_path)
    assert pre["tool_name"] == "mutate"
    assert pre["tool_input"] == {"value": "ok"}

    post = json.loads(post_capture.read_text(encoding="utf-8"))
    assert post["hook_event_name"] == "PostToolUse"
    assert post["session_id"] == "sess-1234"
    assert post["tool_name"] == "mutate"
    assert post["tool_input"] == {"value": "ok"}
    assert post["tool_response"] == "mutated ok"


def test_user_prompt_submit_payload_via_fire_event(tmp_path: Path) -> None:
    capture = tmp_path / "ups.json"
    stdout = "print(json.dumps({'hookSpecificOutput': {'additionalContext': 'mind the budget'}}))\n"
    config = NexusCliConfig()
    config.policy.session_id = "sess-xyz"
    config.hooks.user_prompt_submit = [
        HookMatcherConfig(
            hooks=[HookCommandConfig(command=_capture_hook(tmp_path / "ups.py", stdout))]
        )
    ]
    outcome = asyncio.run(
        fire_event(config, "UserPromptSubmit", {"prompt": "hello there"}, str(tmp_path))
    )
    payload = json.loads(capture.read_text(encoding="utf-8"))
    assert payload["hook_event_name"] == "UserPromptSubmit"
    assert payload["session_id"] == "sess-xyz"
    assert payload["cwd"] == str(tmp_path)
    assert payload["prompt"] == "hello there"
    assert outcome.additional_context == "mind the budget"


# ---------------------------------------------------------------------------
# Unconfigured regression + zero hook calls
# ---------------------------------------------------------------------------


def test_no_hooks_keeps_legacy_executor_behavior(tmp_path: Path, monkeypatch) -> None:
    import nexuscli.tools.executor as executor_module

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("hook machinery ran although no hooks are configured")

    monkeypatch.setattr(executor_module, "fire_event", _must_not_run)
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    prompts: list[str] = []

    def approve(_request):
        prompts.append("asked")
        return "approve"

    result = _run_mutate(
        config, ToolContext(cwd=str(tmp_path), config=config, approval_callback=approve)
    )
    assert not result.is_error
    assert prompts == ["asked"]
    assert "executed=['ok']" in result.content

    # Without a callback the legacy fail-closed denial still applies.
    denied = _run_mutate(config, ToolContext(cwd=str(tmp_path), config=config))
    assert denied.is_error
    assert "executed=[]" in denied.content

    for event in HOOK_EVENT_FIELDS:
        assert not has_hooks(config, event)
        assert hooks_for(config.hooks, event, "mutate") == []


# ---------------------------------------------------------------------------
# Config parsing and layering
# ---------------------------------------------------------------------------


def test_hook_config_parses_camelcase_sections(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / ".nexuscli").mkdir(parents=True)
    (home / ".nexuscli" / "config.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "bash|write_file",
                            "unknown_key": 1,
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo pre",
                                    "timeout": 10,
                                    "nope": True,
                                }
                            ],
                        }
                    ],
                    "UnknownEvent": [{"matcher": "*", "hooks": []}],
                    "Stop": "not-a-list",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    # win32 Path.home() prefers USERPROFILE over HOME; set both so the user
    # layer is isolated from the real profile on every platform.
    monkeypatch.setenv("USERPROFILE", str(home))

    config = load_config(project_root=tmp_path)
    (matcher,) = config.hooks.pre_tool_use
    assert matcher.matcher == "bash|write_file"
    (hook,) = matcher.hooks
    assert (hook.type, hook.command, hook.timeout) == ("command", "echo pre", 10)
    assert config.hooks.stop == []
    assert config.hooks.session_start == []
    assert config.policy.session_id == ""


def test_hook_lists_concat_across_user_and_project_configs(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / ".nexuscli").mkdir(parents=True)
    (home / ".nexuscli" / "config.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [{"matcher": "bash", "hooks": [{"command": "echo user"}]}],
                    "SessionStart": [{"matcher": "*", "hooks": [{"command": "echo start"}]}],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    project = tmp_path / "proj"
    (project / ".nexuscli").mkdir(parents=True)
    (project / ".nexuscli" / "config.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "write_file", "hooks": [{"command": "echo project"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(project_root=project)
    # User first, project appended after; the project layer must not erase the
    # user's SessionStart hooks either.
    assert [m.matcher for m in config.hooks.pre_tool_use] == ["bash", "write_file"]
    assert [m.matcher for m in config.hooks.session_start] == ["*"]

    commands = hooks_for(config.hooks, "PreToolUse", "write_file")
    assert [hook.command for hook in commands] == ["echo project"]
