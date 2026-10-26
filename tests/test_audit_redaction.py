"""审计日志不能变成 payload 的副本。

修复前：write_file 的 200KB 正文原样落盘（实测日志 200430 字节），bash 命令里的
sk- 前缀密钥也原样保留，文件权限 0644——任何本机用户都能读到 agent 写过的全部文件
内容和凭据。这里锁住四条行为：正文截断、密钥形态掩码、0600、短字段不动。
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from nexuscli.policy.audit_log import MAX_FIELD_CHARS, AuditLog

# Windows 的 ACL 不映射到 POSIX 权限位，chmod 断言在那边没有意义。
_posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")


def _record(tmp_path: Path, input_data: dict[str, Any]) -> dict[str, Any]:
    path = tmp_path / "audit.jsonl"
    AuditLog(path).record(
        tool_name="write_file",
        input_data=input_data,
        outcome="allow",
        approver="hitl",
        cwd=str(tmp_path),
    )
    return json.loads(path.read_text(encoding="utf-8").splitlines()[-1])


def test_long_body_is_truncated_with_the_dropped_count(tmp_path: Path) -> None:
    body = "x" * 200_000
    logged = _record(tmp_path, {"path": "big.txt", "content": body})["input"]["content"]
    assert logged.startswith("x" * MAX_FIELD_CHARS)
    assert logged.endswith("…[truncated 199500 chars]")


def test_a_truncated_record_stays_small_on_disk(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    AuditLog(path).record(
        tool_name="write_file",
        input_data={"path": "big.txt", "content": "x" * 200_000},
        outcome="allow",
        approver="hitl",
        cwd=str(tmp_path),
    )
    assert path.stat().st_size < 2000


def test_short_fields_are_recorded_verbatim(tmp_path: Path) -> None:
    record = _record(tmp_path, {"path": "notes.md", "command": "git status --short"})
    assert record["input"]["path"] == "notes.md"
    assert record["input"]["command"] == "git status --short"


def test_a_field_at_the_limit_is_not_truncated(tmp_path: Path) -> None:
    command = "a" * MAX_FIELD_CHARS
    assert _record(tmp_path, {"command": command})["input"]["command"] == command


def test_sensitive_keys_are_still_masked(tmp_path: Path) -> None:
    record = _record(tmp_path, {"api_key": "whatever", "password": "hunter2"})
    assert record["input"]["api_key"] == "***"
    assert record["input"]["password"] == "***"


@pytest.mark.parametrize(
    "secret",
    [
        "sk-a1b2c3d4e5f6g7h8i9j0",
        "ghp_0123456789abcdefghijABCDEFGHIJ012345",
        "AKIAIOSFODNN7EXAMPLE",
    ],
)
def test_token_shaped_values_are_masked(tmp_path: Path, secret: str) -> None:
    command = f"export OPENAI_API_KEY={secret} && ./run.sh"
    logged = _record(tmp_path, {"command": command})["input"]["command"]
    assert secret not in logged
    assert "***" in logged
    assert "./run.sh" in logged


def test_tokens_are_masked_before_truncation(tmp_path: Path) -> None:
    # 先掩码再截断：否则跨在 500 字符边界上的 token 会留下未掩码的前缀。
    body = "x" * 490 + "sk-a1b2c3d4e5f6g7h8i9j0" + "y" * 100
    logged = _record(tmp_path, {"content": body})["input"]["content"]
    assert "sk-" not in logged


@_posix_only
def test_created_log_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    AuditLog(path).record(
        tool_name="bash",
        input_data={"command": "ls"},
        outcome="allow",
        approver="hitl",
        cwd=str(tmp_path),
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@_posix_only
def test_existing_world_readable_log_is_tightened_on_append(tmp_path: Path) -> None:
    # 升级前写下的日志是 0644，第一次追加就该收紧，而不是只保护新文件。
    path = tmp_path / "audit.jsonl"
    path.write_text("", encoding="utf-8")
    os.chmod(path, 0o644)
    AuditLog(path).record(
        tool_name="bash",
        input_data={"command": "ls"},
        outcome="allow",
        approver="hitl",
        cwd=str(tmp_path),
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_bearer_and_jwt_in_command_strings_are_masked(tmp_path):
    """夜间③ P2-8：bash command 串里的 Bearer/JWT 不在任何敏感键下，必须值级掩码。"""
    from nexuscli.policy.audit_log import AuditLog

    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record(
        tool_name="bash",
        input_data={
            "command": f"curl -sS -H 'Authorization: Bearer {jwt}' https://api.example.com/v1/me"
        },
        outcome="success",
        approver="hitl",
        cwd=str(tmp_path),
    )
    line = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert jwt not in line
    assert "eyJ" not in line
    assert "Bearer ***" in line


def test_plain_words_are_not_masked(tmp_path):
    """掩码正则不得误伤普通命令文本（长度/三段式约束的反例）。"""
    from nexuscli.policy.audit_log import AuditLog

    log = AuditLog(tmp_path / "audit.jsonl")
    command = "echo bearer authentication docs && cat readme.md"
    log.record(
        tool_name="bash",
        input_data={"command": command},
        outcome="success",
        approver="readonly-rule",
        cwd=str(tmp_path),
    )
    line = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert command in line
