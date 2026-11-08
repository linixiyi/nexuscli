"""deny/skip 分支的审计受 ``features.audit_log`` 开关控制（spec 2026-11-07 task 0016）。

``ToolExecutor._execute_single`` 的其余 6 处 ``audit.record`` 都包在
``if context.config.features.audit_log`` 里，唯独审批 deny/skip 分支曾无条件
落盘：feature 关掉的运行（如 hermetic eval，``eval/runner.py`` 会把该开关
置 False）仍会在磁盘上留下拒绝痕迹。这里锁定两种配置 × deny/skip 两种
决定：关闭 → 审计文件不存在；开启 → deny/skip 记录仍在（approver=hitl）。
工具与门链的用法沿用 test_tool_approval_matrix.py 的约定。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nexuscli.config import NexusCliConfig
from nexuscli.policy import AuditLog
from nexuscli.tools.base import ToolContext
from nexuscli.tools.builtins import get_builtin_tools
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry


def _executor() -> ToolExecutor:
    registry = ToolRegistry()
    for tool in get_builtin_tools():
        registry.register(tool)
    return ToolExecutor(registry)


def _run_denied_write(tmp_path: Path, decision: str, audit_enabled: bool, approval_callback=None):
    """走一次会被审批门拒绝的 write_file，返回执行结果。

    ``approval_callback`` 传 None 时无审批回调，依赖门链 fail-closed 的 deny。
    """
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.features.audit_log = audit_enabled
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=approval_callback)
    call = {
        "id": "c1",
        "name": "write_file",
        "arguments": {"path": "blocked.txt", "content": "nope"},
    }
    result = asyncio.run(_executor().execute_all([call], context))[0]

    # 命令被拒：文件未写，结果带错误标记
    assert result.is_error
    assert not (tmp_path / "blocked.txt").exists()
    return result


@pytest.mark.parametrize("decision", ["deny", "skip"])
def test_deny_skip_writes_no_audit_file_when_feature_disabled(tmp_path, decision: str) -> None:
    _run_denied_write(
        tmp_path, decision, audit_enabled=False, approval_callback=lambda _r: decision
    )
    # feature 关闭：拒绝也不落审计，文件根本不该被创建
    assert not (tmp_path / "audit.jsonl").exists()


@pytest.mark.parametrize("decision", ["deny", "skip"])
def test_deny_skip_still_audited_when_feature_enabled(tmp_path, decision: str) -> None:
    _run_denied_write(tmp_path, decision, audit_enabled=True, approval_callback=lambda _r: decision)
    events = AuditLog(str(tmp_path / "audit.jsonl")).tail()
    assert [(event["outcome"], event["approver"]) for event in events] == [(decision, "hitl")]


def test_fail_closed_deny_respects_feature_switch(tmp_path) -> None:
    """无审批回调的 fail-closed deny 同样受开关控制。"""
    _run_denied_write(tmp_path, "deny", audit_enabled=False, approval_callback=None)
    assert not (tmp_path / "audit.jsonl").exists()

    _run_denied_write(tmp_path, "deny", audit_enabled=True, approval_callback=None)
    events = AuditLog(str(tmp_path / "audit.jsonl")).tail()
    assert [(event["outcome"], event["approver"]) for event in events] == [("deny", "hitl")]
