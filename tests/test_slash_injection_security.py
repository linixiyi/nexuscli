"""slash ``!`` 注入接入 ToolExecutor 门链的安全测试（spec 2026-11-07 task 0002）。

锁定四条不变量：
1. 注入不再直连 CommandExecutor：片段作为内部 ``slash_injection`` 工具走
   ToolExecutor 同一条门链（权限规则 → hooks → HITL 审批 → 审计）。
2. 项目级命令（``source == "project"``，仓库可控文件）的注入必须人工批准，
   无审批回调时 fail-closed：命令不执行、片段替换为 ``[refused: ...]`` 标记。
3. 用户级命令自动执行，但审计落记录、子进程环境经
   ``sanitize_environment`` 清洗（OPENAI_API_KEY 不外泄）。
4. CommandGuard 仍在门链之前拒绝危险命令（审批不被打扰）；超时与失败
   marker 语义不变。

子进程环境探测沿用 test_sandbox_guard.py 的约定：探测命令是临时脚本文件 +
``sys.executable``（免引号、跨平台），子进程只回 PRESENT/ABSENT，不回显变量值。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints import slash_commands
from nexuscli.entrypoints.slash_commands import CustomCommand, expand_command
from nexuscli.policy import AuditLog


def _audit_config(tmp_path: Path) -> NexusCliConfig:
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    return config


def _slash_injection_events(config: NexusCliConfig) -> list[dict]:
    return [
        event
        for event in AuditLog(config.policy.audit_log_path).tail()
        if event["tool_name"] == "slash_injection"
    ]


# ---------------------------------------------------------------------------
# 项目级注入：无回调 fail-closed / 批准执行 / 拒绝 marker
# ---------------------------------------------------------------------------


def test_project_injection_without_callback_fails_closed(tmp_path):
    config = _audit_config(tmp_path)
    command = CustomCommand(name="probe", body="Out: !`echo project-run`", source="project")

    expansion = asyncio.run(expand_command(command, "", config))

    # 拒绝 marker 保留，命令的 stdout 从未出现（fail-closed，不执行）
    assert expansion.prompt.startswith("Out: [refused: echo project-run")
    assert expansion.rejected_injections[0].startswith("[refused: echo project-run")
    # 审计只允许 deny 记录——命令一次都没有跑过
    events = _slash_injection_events(config)
    assert [event["outcome"] for event in events] == ["deny"]


def test_project_injection_with_approval_executes_and_audits(tmp_path):
    config = _audit_config(tmp_path)
    requests: list[dict] = []

    def approve(request: dict) -> str:
        requests.append(request)
        return "approve"

    command = CustomCommand(name="probe", body="Out: !`echo approved-run`", source="project")

    expansion = asyncio.run(expand_command(command, "", config, approval_callback=approve))

    assert expansion.prompt == "Out: approved-run"
    assert expansion.rejected_injections == ()
    # 审批请求走的是 slash_injection 工具合同，带着命令本体
    assert [request["tool_name"] for request in requests] == ["slash_injection"]
    assert requests[0]["input"]["command"] == "echo approved-run"
    assert requests[0]["danger_level"] == "high"
    # 人工批准 → 审计记 allow + hitl
    events = _slash_injection_events(config)
    assert [(event["outcome"], event["approver"]) for event in events] == [("allow", "hitl")]


def test_project_injection_denied_by_callback_keeps_refused_marker(tmp_path):
    config = _audit_config(tmp_path)
    command = CustomCommand(name="probe", body="Out: !`echo denied-run`", source="project")

    expansion = asyncio.run(
        expand_command(command, "", config, approval_callback=lambda _request: "deny")
    )

    assert expansion.prompt.startswith("Out: [refused: echo denied-run")
    events = _slash_injection_events(config)
    assert [(event["outcome"], event["approver"]) for event in events] == [("deny", "hitl")]


# ---------------------------------------------------------------------------
# 用户级注入：自动执行 + 审计
# ---------------------------------------------------------------------------


def test_user_injection_auto_executes_and_audits(tmp_path):
    config = _audit_config(tmp_path)
    command = CustomCommand(name="probe", body="Out: !`echo user-run`", source="user")

    expansion = asyncio.run(expand_command(command, "", config))

    assert expansion.prompt == "Out: user-run"
    assert expansion.rejected_injections == ()
    # 自动执行也必须落审计：无人工参与，approver 记 none
    events = _slash_injection_events(config)
    assert [(event["outcome"], event["approver"]) for event in events] == [("allow", "none")]


# ---------------------------------------------------------------------------
# CommandGuard 仍在门链之前
# ---------------------------------------------------------------------------


def test_dangerous_injection_refused_by_command_guard_before_approval(tmp_path):
    config = _audit_config(tmp_path)
    asked: list[dict] = []

    def approve(request: dict) -> str:
        asked.append(request)
        return "approve"

    command = CustomCommand(name="nuke", body="Run: !`rm -rf /`", source="project")

    expansion = asyncio.run(expand_command(command, "", config, approval_callback=approve))

    assert expansion.prompt == "Run: [refused: rm -rf / — blocked by command guard]"
    assert expansion.rejected_injections == ("[refused: rm -rf / — blocked by command guard]",)
    # 守卫在门链之前拒绝：审批没被问，命令也没执行（无任何审计记录）
    assert asked == []
    assert _slash_injection_events(config) == []


# ---------------------------------------------------------------------------
# 子进程环境清洗
# ---------------------------------------------------------------------------

_PRINT_KEYS_BODY = (
    "import os, sys\n"
    "for name in sys.argv[1:]:\n"
    "    present = os.environ.get(name) is not None\n"
    "    print(name, 'PRESENT' if present else 'ABSENT')\n"
)


def _probe_command(tmp_path: Path, keys: tuple[str, ...]) -> str:
    script = tmp_path / "slash_probe_env.py"
    script.write_text(_PRINT_KEYS_BODY, encoding="utf-8")
    return '"' + sys.executable + '" "' + str(script) + '" ' + " ".join(keys)


def test_injection_child_environment_hides_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-value-not-a-credential")
    config = NexusCliConfig()
    command = CustomCommand(
        name="envprobe",
        body="Env: !`" + _probe_command(tmp_path, ("OPENAI_API_KEY", "PATH")) + "`",
        source="user",
    )

    expansion = asyncio.run(expand_command(command, "", config))

    # 秘钥形变量被 sanitize_environment 剔除；PATH 属于必留键，子进程仍能运行
    assert expansion.prompt == "Env: OPENAI_API_KEY ABSENT\nPATH PRESENT"


def test_env_sanitization_disabled_hands_full_environ(tmp_path, monkeypatch):
    """``policy.env_sanitization`` 是全局开关：关掉时与 bash 同口径，回全量环境。"""
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-value-not-a-credential")
    config = NexusCliConfig()
    config.policy.env_sanitization = False
    command = CustomCommand(
        name="envprobe",
        body="Env: !`" + _probe_command(tmp_path, ("OPENAI_API_KEY", "PATH")) + "`",
        source="user",
    )

    expansion = asyncio.run(expand_command(command, "", config))

    assert expansion.prompt == "Env: OPENAI_API_KEY PRESENT\nPATH PRESENT"


# ---------------------------------------------------------------------------
# 超时与失败 marker 语义保留
# ---------------------------------------------------------------------------


def test_injection_timeout_keeps_failed_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(slash_commands, "INJECTION_TIMEOUT_SECONDS", 0.5)
    config = NexusCliConfig()
    command = CustomCommand(name="slow", body="Out: !`sleep 5`", source="user")

    expansion = asyncio.run(expand_command(command, "", config))

    assert expansion.prompt == "Out: [failed: sleep 5 — timed out]"
    assert expansion.rejected_injections == ("[failed: sleep 5 — timed out]",)
