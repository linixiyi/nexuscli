"""Tests for pattern-based permission rules (deny > ask > allow)."""

from __future__ import annotations

import asyncio
import json

from nexuscli.config import NexusCliConfig, PermissionsConfig, load_config
from nexuscli.policy import evaluate_permissions, parse_rule
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry
from tests.project_trust_support import trust_project_config

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


def test_command_prefix_rule_matches_token_boundary() -> None:
    # ``prefix:*`` used to be a bare startswith, letting the longer command
    # word ``git difftool`` ride the ``bash(git diff:*)`` allow rule. The
    # prefix now has to end at a token boundary.
    perms = PermissionsConfig(allow=["bash(git diff:*)"])
    hit = evaluate_permissions(perms, "bash", {"command": "git diff"})
    assert hit is not None and hit.action == "allow" and hit.rule == "bash(git diff:*)"
    assert evaluate_permissions(perms, "bash", {"command": "git difftool"}) is None


def test_command_prefix_rule_with_arguments() -> None:
    perms = PermissionsConfig(allow=["bash(git diff:*)"])
    hit = evaluate_permissions(perms, "bash", {"command": "git diff --stat"})
    assert hit is not None and hit.action == "allow" and hit.rule == "bash(git diff:*)"
    assert evaluate_permissions(perms, "bash", {"command": "git difftool --stat"}) is None


def test_command_prefix_rule_with_quotes() -> None:
    perms = PermissionsConfig(allow=["bash(git commit:*)"])
    # Quoted arguments after the token boundary are ordinary payload.
    hit = evaluate_permissions(perms, "bash", {"command": 'git commit -m "fix: typo"'})
    assert hit is not None and hit.action == "allow" and hit.rule == "bash(git commit:*)"
    # A quote glued to the prefix joins the word (shell quote removal), so
    # ``git diff"tool"`` runs ``git difftool`` — no allow, the human is asked.
    diff_perms = PermissionsConfig(allow=["bash(git diff:*)"])
    assert evaluate_permissions(diff_perms, "bash", {"command": 'git diff"tool"'}) is None
    hit = evaluate_permissions(diff_perms, "bash", {"command": 'git diff "--stat"'})
    assert hit is not None and hit.action == "allow"


def test_command_prefix_rule_rejects_compound_commands() -> None:
    # A space after the prefix ends the rule's word, but the tail may still
    # chain another command (`git diff --stat; touch /tmp/x`). A ``prefix:*``
    # rule only speaks for its own segment, so any shell operator or command
    # substitution keeps it silent and the call falls back to the approval
    # chain instead of auto-approving the chained tail.
    perms = PermissionsConfig(allow=["bash(git diff:*)"])
    for command in (
        "git diff --stat; touch /tmp/x; echo chained-ok",
        "git diff --stat;touch /tmp/x",
        "git diff --stat && git push --force",
        "git diff --stat || git push --force",
        "git diff --stat | tee /tmp/x",
        "git diff --stat & rm -rf /tmp/x",
        "git diff --stat\nrm -rf /tmp/x",
        "git diff --stat\n",
        "git diff --stat $(touch /tmp/x)",
        "git diff --stat `touch /tmp/x`",
        "git diff --stat > /tmp/x; cat /tmp/x",
    ):
        assert evaluate_permissions(perms, "bash", {"command": command}) is None, command


def test_compound_command_cannot_dodge_deny_or_ride_allow() -> None:
    # deny > allow used to be defeated by chaining: the allow rule matched the
    # whole string, so the deny rule's target rode in as the second segment of
    # `git diff --stat && git push --force`. A compound matches no prefix rule
    # at all, so it falls to the approval chain (fail-closed with no approver)
    # instead of being auto-allowed past the deny rule.
    perms = PermissionsConfig(allow=["bash(git diff:*)"], deny=["bash(git push:*)"])
    assert (
        evaluate_permissions(perms, "bash", {"command": "git diff --stat && git push --force"})
        is None
    )
    # Straight calls keep their exact semantics.
    assert evaluate_permissions(perms, "bash", {"command": "git push --force"}).action == "deny"
    assert evaluate_permissions(perms, "bash", {"command": "git diff --stat"}).action == "allow"


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


def _registry_with_shell_tool(executed: list[str]) -> ToolRegistry:
    registry = ToolRegistry()

    async def run(payload, _context):
        executed.append(str(payload["command"]))
        return ToolResult(f"ran {payload['command']}")

    registry.register(
        Tool(
            name="bash",
            description="fake bash",
            parameters=object_schema({"command": {"type": "string"}}, ["command"]),
            required_keys=["command"],
            handler=run,
            is_read_only=False,
            is_concurrency_safe=False,
            danger_level="high",
            requires_approval=True,
        )
    )
    return registry


def test_allow_prefix_rule_fails_closed_on_chained_bash_without_approver(tmp_path) -> None:
    # The regression: allow=[bash(git diff:*)] used to auto-run
    # `git diff --stat; touch /tmp/x; echo chained-ok` with no approver — the
    # rule matched the whole compound and the chained segments never passed a
    # gate. The rule now stays silent on the operators, and the executor
    # denies the call exactly like any other unapproved write tool.
    config = NexusCliConfig()
    config.permissions.allow = ["bash(git diff:*)"]
    executed: list[str] = []
    executor = ToolExecutor(_registry_with_shell_tool(executed))
    call = {
        "id": "call-1",
        "name": "bash",
        "arguments": {"command": "git diff --stat; touch /tmp/x; echo chained-ok"},
    }
    result = asyncio.run(
        executor.execute_all([call], ToolContext(cwd=str(tmp_path), config=config))
    )[0]
    assert result.is_error
    assert executed == []

    # The plain, un-chained command the rule actually named still auto-runs.
    executed.clear()
    executor = ToolExecutor(_registry_with_shell_tool(executed))
    call = {"id": "call-2", "name": "bash", "arguments": {"command": "git diff --stat"}}
    result = asyncio.run(
        executor.execute_all([call], ToolContext(cwd=str(tmp_path), config=config))
    )[0]
    assert not result.is_error
    assert executed == ["git diff --stat"]


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


def test_non_interactive_denial_reads_grammatically_and_names_the_way_out(tmp_path) -> None:
    # No approval callback is exactly the single-prompt (-p) situation: every
    # non-read-only call is refused. The message used to be built by appending
    # "ed" to the decision, producing "was denyed by approval policy." — wrong
    # English, and silent about how the caller could ever get the tool allowed.
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    executed: list[str] = []
    result = _run(
        _registry_with_mutate_tool(executed), config, ToolContext(cwd=str(tmp_path), config=config)
    )

    assert result.is_error
    assert "was denied by approval policy." in result.content
    assert "denyed" not in result.content
    assert "skiped" not in result.content
    assert "permissions.allow" in result.content


def test_interactive_denial_does_not_lecture_about_config(tmp_path) -> None:
    # With a human on the other end the prompt already answered the question;
    # the message stays short instead of pointing at config.json.
    config = NexusCliConfig()
    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=lambda _r: "deny")
    result = _run(_registry_with_mutate_tool(executed), config, context)

    assert "was denied by approval policy." in result.content
    assert "permissions.allow" not in result.content


def test_skipped_call_is_reported_as_skipped(tmp_path) -> None:
    config = NexusCliConfig()
    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=lambda _r: "skip")
    result = _run(_registry_with_mutate_tool(executed), config, context)

    assert "was skipped by approval policy." in result.content
    assert "skiped" not in result.content


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
    # permissions.allow is a project security-sensitive key: this test owns the
    # user+project list merge, so record the trust decision explicitly.
    trust_project_config(project)

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
