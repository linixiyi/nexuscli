"""Tests for pattern-based permission rules (deny > ask > allow)."""

from __future__ import annotations

import asyncio
import json

from nexuscli.config import NexusCliConfig, PermissionsConfig, load_config
from nexuscli.policy import evaluate_permissions, parse_rule
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Rule parsing and matching
# ---------------------------------------------------------------------------


def test_parse_rule_forms() -> None:
    bare = parse_rule("bash")
    assert bare is not None and bare.tool == "bash" and bare.spec == ""
    spec = parse_rule("bash(git diff:*)")
    assert spec is not None and spec.tool == "bash" and spec.spec == "git diff:*"
    assert parse_rule("bad(rule(x)") is None
    assert parse_rule("nope(") is None
    assert parse_rule("nope)") is None
    assert parse_rule("") is None
    assert parse_rule("  ") is None


def test_command_rule_matching() -> None:
    perms = PermissionsConfig(allow=["bash(git diff:*)"])
    hit = evaluate_permissions(perms, "bash", {"command": "git diff --stat"})
    assert hit is not None and hit.action == "allow" and hit.rule == "bash(git diff:*)"
    assert evaluate_permissions(perms, "bash", {"command": "git push"}) is None


def test_command_glob_rule_matching() -> None:
    perms = PermissionsConfig(ask=["bash(npm run *)"])
    hit = evaluate_permissions(perms, "bash", {"command": "npm run build"})
    assert hit is not None and hit.action == "ask" and hit.rule == "bash(npm run *)"
    assert evaluate_permissions(perms, "bash", {"command": "npm install"}) is None


def test_path_rule_matching_and_backslash_normalization() -> None:
    perms = PermissionsConfig(ask=["write_file(src/**)"])
    hit = evaluate_permissions(perms, "write_file", {"path": "src/nexuscli/config.py"})
    assert hit is not None and hit.action == "ask"
    windows = evaluate_permissions(perms, "write_file", {"path": "src\\nexuscli\\config.py"})
    assert windows is not None
    assert evaluate_permissions(perms, "write_file", {"path": "tests/test_x.py"}) is None


def test_domain_rule_matching() -> None:
    perms = PermissionsConfig(deny=["web_fetch(domain:internal.corp)"])
    assert (
        evaluate_permissions(perms, "web_fetch", {"url": "https://internal.corp/wiki"}) is not None
    )
    sub = evaluate_permissions(perms, "web_fetch", {"url": "https://wiki.internal.corp/x"})
    assert sub is not None and sub.action == "deny"
    assert evaluate_permissions(perms, "web_fetch", {"url": "https://github.com"}) is None


def test_precedence_deny_beats_ask_beats_allow() -> None:
    perms = PermissionsConfig(allow=["bash"], deny=["bash(rm *)"], ask=["bash(npm *)"])
    assert evaluate_permissions(perms, "bash", {"command": "rm -rf /"}).action == "deny"
    assert evaluate_permissions(perms, "bash", {"command": "npm test"}).action == "ask"
    assert evaluate_permissions(perms, "bash", {"command": "git status"}).action == "allow"


def test_tool_name_glob_for_mcp_tools() -> None:
    perms = PermissionsConfig(allow=["mcp__github__*"])
    assert evaluate_permissions(perms, "mcp__github__create_issue", {}) is not None
    assert evaluate_permissions(perms, "mcp__gitlab__create_issue", {}) is None


def test_spec_rule_never_matches_subjectless_tool() -> None:
    perms = PermissionsConfig(allow=["bash(rm *)"])
    assert evaluate_permissions(perms, "save_memory", {"content": "rm -rf"}) is None


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


def _run(registry: ToolRegistry, config: NexusCliConfig, context: ToolContext) -> ToolResult:
    executor = ToolExecutor(registry)
    call = {"id": "call-1", "name": "mutate", "arguments": {"value": "ok"}}
    return asyncio.run(executor.execute_all([call], context))[0]


def test_allow_rule_skips_approval_prompt(tmp_path) -> None:
    config = NexusCliConfig()
    config.permissions.allow = ["mutate"]
    # No approval callback configured; without the rule the call would be denied.
    executed: list[str] = []
    result = _run(
        _registry_with_mutate_tool(executed), config, ToolContext(cwd=str(tmp_path), config=config)
    )
    assert not result.is_error
    assert executed == ["ok"]


def test_deny_rule_blocks_execution_with_rule_text(tmp_path) -> None:
    config = NexusCliConfig()
    config.permissions.deny = ["mutate"]
    executed: list[str] = []
    result = _run(
        _registry_with_mutate_tool(executed), config, ToolContext(cwd=str(tmp_path), config=config)
    )
    assert result.is_error
    assert "denied by permission rule: mutate" in result.content
    assert executed == []


def test_ask_rule_forces_prompt_in_auto_mode(tmp_path) -> None:
    config = NexusCliConfig()
    config.permissions.ask = ["mutate"]
    prompts: list[str] = []

    def approve(_request):
        prompts.append("asked")
        return "approve"

    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=approve)
    result = _run(_registry_with_mutate_tool(executed), config, context)
    assert not result.is_error
    assert prompts == ["asked"]
    assert executed == ["ok"]


def test_ask_rule_fails_closed_in_never_mode(tmp_path) -> None:
    config = NexusCliConfig()
    config.policy.hitl_mode = "never"
    config.permissions.ask = ["mutate"]
    executed: list[str] = []
    result = _run(
        _registry_with_mutate_tool(executed),
        config,
        ToolContext(cwd=str(tmp_path), config=config, approval_callback=lambda _r: "approve"),
    )
    assert result.is_error
    assert executed == []


def test_allow_rule_still_prompts_in_always_mode(tmp_path) -> None:
    config = NexusCliConfig()
    config.policy.hitl_mode = "always"
    config.permissions.allow = ["mutate"]
    prompts: list[str] = []

    def deny(_request):
        prompts.append("asked")
        return "deny"

    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=deny)
    result = _run(_registry_with_mutate_tool(executed), config, context)
    assert result.is_error
    assert prompts == ["asked"]
    assert executed == []


def test_no_rules_keeps_legacy_behavior(tmp_path) -> None:
    config = NexusCliConfig()
    executed: list[str] = []
    # auto + requires_approval + no callback → denied, exactly as before rules existed.
    result = _run(
        _registry_with_mutate_tool(executed), config, ToolContext(cwd=str(tmp_path), config=config)
    )
    assert result.is_error
    assert executed == []


def test_no_rules_auto_mode_still_prompts(tmp_path) -> None:
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    prompts: list[str] = []

    def approve(_request):
        prompts.append("asked")
        return "approve"

    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=approve)
    result = _run(_registry_with_mutate_tool(executed), config, context)
    assert not result.is_error
    assert prompts == ["asked"]
    assert executed == ["ok"]


def test_no_rules_never_mode_passes_without_prompt(tmp_path) -> None:
    config = NexusCliConfig()
    config.policy.hitl_mode = "never"
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    prompts: list[str] = []

    def record(_request):
        prompts.append("asked")
        return "deny"

    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=record)
    result = _run(_registry_with_mutate_tool(executed), config, context)
    assert not result.is_error
    assert prompts == []
    assert executed == ["ok"]


def test_permission_deny_is_audited(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.permissions.deny = ["mutate"]
    executed: list[str] = []
    result = _run(
        _registry_with_mutate_tool(executed), config, ToolContext(cwd=str(tmp_path), config=config)
    )
    assert result.is_error
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert lines, "expected an audit record"
    record = json.loads(lines[-1])
    assert record["tool_name"] == "mutate"
    assert record["outcome"] == "deny"
    assert record["approver"] == "permission-rule"


# ---------------------------------------------------------------------------
# Config layering
# ---------------------------------------------------------------------------


def test_permission_lists_concat_across_user_and_project_configs(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    (home / ".nexuscli").mkdir(parents=True)
    (home / ".nexuscli" / "config.json").write_text(
        json.dumps({"permissions": {"deny": ["bash(rm *)"]}}), encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    # win32 Path.home() prefers USERPROFILE over HOME; set both so the user
    # layer is actually isolated from the real profile on every platform.
    monkeypatch.setenv("USERPROFILE", str(home))

    project = tmp_path / "proj"
    (project / ".nexuscli").mkdir(parents=True)
    (project / ".nexuscli" / "config.json").write_text(
        json.dumps({"permissions": {"allow": ["bash(git *)"]}}), encoding="utf-8"
    )

    config = load_config(project_root=project)
    assert config.permissions.deny == ["bash(rm *)"]
    assert config.permissions.allow == ["bash(git *)"]

    decision = evaluate_permissions(config.permissions, "bash", {"command": "rm -rf /"})
    assert decision is not None and decision.action == "deny"


def test_legacy_config_without_permissions_key_loads(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    # win32 Path.home() prefers USERPROFILE over HOME; set both to stay
    # hermetic against a real ~/.nexuscli/config.json on Windows.
    monkeypatch.setenv("USERPROFILE", str(home))
    config = load_config(project_root=tmp_path)
    assert config.permissions.allow == []
    assert config.permissions.deny == []
    assert config.permissions.ask == []
