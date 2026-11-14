"""task-0004（spec 2026-11-13_fix-project-trust-boundary）：slash ``!`cmd` `` 注入的权限主体映射。

``!`cmd` `` 片段仍以内部工具名 ``slash_injection`` 进 registry（永远不进模型的工具
列表），但它就是一条 shell 命令：权限规则与 PreToolUse hook 必须按命令主体评估——

- ``deny:["bash"]``、``deny:["execute_command"]`` 都能拦住注入片段（refused 且不执行）；
- matcher 为 ``bash`` 的 PreToolUse hook 能阻断注入片段（exit 2 语义与 bash 一致）；
- ``allow`` 规则仍能放行（含 allow 跳过审批的既有语义），deny > allow 优先级不变；
- CommandGuard 仍先于执行器；
- 审计同时记录内部名（``tool_name=slash_injection``）与真实主体
  （``input.tool_subject=bash``）。

全部离线：探测命令是 ``touch``/``echo`` + tmp_path 哨兵文件，hook 是 ``echo >&2; exit 2``。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from nexuscli.config import HookCommandConfig, HookMatcherConfig, NexusCliConfig
from nexuscli.entrypoints.slash_commands import CustomCommand, expand_command
from nexuscli.policy import AuditLog


def _config(tmp_path: Path) -> NexusCliConfig:
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    return config


def _hook_command(script: Path, body: str) -> str:
    script.write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


_ASK_HINT_BODY = (
    "import json\nprint(json.dumps({'hookSpecificOutput': {'permissionDecision': 'ask'}}))\n"
)


def _events(config: NexusCliConfig) -> list[dict]:
    return AuditLog(config.policy.audit_log_path).tail()


def _expand(
    config: NexusCliConfig,
    body: str,
    *,
    source: str = "user",
    approval_callback=None,
):
    command = CustomCommand(name="probe", body=body, source=source)
    return asyncio.run(expand_command(command, "", config, approval_callback=approval_callback))


# ---------------------------------------------------------------------------
# ①/②：deny 规则按真实主体命中，refused 且不执行
# ---------------------------------------------------------------------------


def test_deny_bash_rule_refuses_injection_without_executing(tmp_path):
    config = _config(tmp_path)
    config.permissions.deny = ["bash"]
    sentinel = tmp_path / "sentinel-deny-bash"
    body = f"Out: !`touch {sentinel}`"

    expansion = _expand(config, body)

    # 片段返回 refused，命令的副作用从未发生
    assert expansion.prompt.startswith("Out: [refused: touch")
    assert not sentinel.exists()
    events = _events(config)
    assert [event["outcome"] for event in events] == ["deny"]
    assert [event["approver"] for event in events] == ["permission-rule"]
    # deny 记录同样带真实主体
    assert events[0]["tool_name"] == "slash_injection"
    assert events[0]["input"]["tool_subject"] == "bash"


def test_deny_execute_command_rule_refuses_injection_without_executing(tmp_path):
    config = _config(tmp_path)
    config.permissions.deny = ["execute_command"]
    sentinel = tmp_path / "sentinel-deny-execute-command"
    body = f"Out: !`touch {sentinel}`"

    expansion = _expand(config, body)

    assert expansion.prompt.startswith("Out: [refused: touch")
    assert not sentinel.exists()
    events = _events(config)
    assert [event["outcome"] for event in events] == ["deny"]
    assert [event["approver"] for event in events] == ["permission-rule"]
    assert events[0]["input"]["tool_subject"] == "bash"


# ---------------------------------------------------------------------------
# ③：matcher 为 bash 的 PreToolUse hook 能阻断注入片段
# ---------------------------------------------------------------------------


def test_pre_tool_use_hook_matching_bash_blocks_injection(tmp_path):
    config = _config(tmp_path)
    config.hooks.pre_tool_use = [
        HookMatcherConfig(
            matcher="bash",
            hooks=[HookCommandConfig(command="echo hook-blocked-injection >&2; exit 2")],
        )
    ]
    sentinel = tmp_path / "sentinel-hook-block"
    body = f"Out: !`touch {sentinel}`"

    expansion = _expand(config, body)

    # hook 的 stderr 成为拒绝原因，命令从未执行，审计归因 hook
    assert expansion.prompt.startswith("Out: [refused: touch")
    assert "hook-blocked-injection" in expansion.prompt
    assert not sentinel.exists()
    events = _events(config)
    assert [(event["outcome"], event["approver"]) for event in events] == [("deny", "hook")]
    assert events[0]["input"]["tool_subject"] == "bash"


# ---------------------------------------------------------------------------
# ④：allow 规则仍能放行，deny > allow 优先级不变
# ---------------------------------------------------------------------------


def test_deny_beats_allow_for_injection(tmp_path):
    config = _config(tmp_path)
    config.permissions.allow = ["bash"]
    config.permissions.deny = ["bash"]
    sentinel = tmp_path / "sentinel-deny-beats-allow"

    expansion = _expand(config, f"Out: !`touch {sentinel}`")

    assert expansion.prompt.startswith("Out: [refused: touch")
    assert not sentinel.exists()
    assert [event["outcome"] for event in _events(config)] == ["deny"]


def test_allow_rule_still_permits_injection(tmp_path):
    config = _config(tmp_path)
    config.permissions.allow = ["bash"]
    # 项目级注入本应 fail-closed（无审批回调）；allow 规则按主体命中后放行
    command = CustomCommand(name="probe", body="Out: !`echo allow-lets-run`", source="project")

    expansion = asyncio.run(expand_command(command, "", config))

    assert expansion.prompt == "Out: allow-lets-run"
    assert expansion.rejected_injections == ()
    events = _events(config)
    assert [(event["outcome"], event["approver"]) for event in events] == [
        ("allow", "permission-rule")
    ]


# ---------------------------------------------------------------------------
# ⑤：审计同时记录内部名与真实主体
# ---------------------------------------------------------------------------


def test_audit_records_internal_name_and_real_subject(tmp_path):
    config = _config(tmp_path)
    command = CustomCommand(name="probe", body="Out: !`echo audit-subject-run`", source="user")

    expansion = asyncio.run(expand_command(command, "", config))

    assert expansion.prompt == "Out: audit-subject-run"
    events = _events(config)
    assert [event["tool_name"] for event in events] == ["slash_injection"]  # 内部名保留
    assert events[0]["input"]["command"] == "echo audit-subject-run"
    assert events[0]["input"]["tool_subject"] == "bash"  # 真实主体
    assert (events[0]["outcome"], events[0]["approver"]) == ("allow", "none")


# ---------------------------------------------------------------------------
# 实现不变量的钉子：PreToolUse hook 恰好触发一次；hook 的 ask 提示压过 allow
# 并在无审批人时 fail-closed（与 executor 对 bash 的 ask 语义同口径）
# ---------------------------------------------------------------------------


def test_pre_tool_use_hook_with_default_matcher_fires_once(tmp_path):
    """默认 matcher ``*`` 同时匹配主体与内部名：executor 侧被剥离，hook 只跑一次。"""
    config = _config(tmp_path)
    fired = tmp_path / "hook-fired.log"
    fire_body = (
        "from pathlib import Path\n"
        f"with Path({str(fired)!r}).open('a', encoding='utf-8') as handle:\n"
        "    handle.write('fired\\n')\n"
    )
    config.hooks.pre_tool_use = [
        HookMatcherConfig(
            hooks=[HookCommandConfig(command=_hook_command(tmp_path / "fire.py", fire_body))]
        )
    ]

    expansion = _expand(config, "Out: !`echo fired-once`")

    assert expansion.prompt == "Out: fired-once"
    assert fired.read_text(encoding="utf-8").splitlines() == ["fired"]


def test_hook_ask_hint_forces_approval_despite_allow_rule(tmp_path):
    config = _config(tmp_path)
    config.permissions.allow = ["bash"]
    config.hooks.pre_tool_use = [
        HookMatcherConfig(
            matcher="bash",
            hooks=[HookCommandConfig(command=_hook_command(tmp_path / "ask.py", _ASK_HINT_BODY))],
        )
    ]
    asked: list[dict] = []

    def approve(request: dict) -> str:
        asked.append(request)
        return "approve"

    expansion = _expand(config, "Out: !`echo ask-let-run`", approval_callback=approve)

    # allow 规则本会自动放行，hook 的 ask 提示压过它并强制人工决策
    assert len(asked) == 1
    assert asked[0]["tool_name"] == "slash_injection"
    assert expansion.prompt == "Out: ask-let-run"
    assert [event["outcome"] for event in _events(config)] == ["allow"]


def test_hook_ask_hint_without_approver_fails_closed(tmp_path):
    config = _config(tmp_path)
    config.permissions.allow = ["bash"]
    config.hooks.pre_tool_use = [
        HookMatcherConfig(
            matcher="bash",
            hooks=[HookCommandConfig(command=_hook_command(tmp_path / "ask.py", _ASK_HINT_BODY))],
        )
    ]
    sentinel = tmp_path / "sentinel-ask-no-approver"

    expansion = _expand(config, f"Out: !`touch {sentinel}`")

    assert expansion.prompt.startswith("Out: [refused: touch")
    assert not sentinel.exists()
    assert [event["outcome"] for event in _events(config)] == ["deny"]
