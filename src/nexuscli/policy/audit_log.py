from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = ("token", "key", "password", "secret", "authorization", "bearer")

# 正文类字段动辄是整个文件内容（实测一次 write_file 让审计日志涨到 200KB）。
# 审计要回答的是"谁在什么时候做了什么"，不需要 payload 的完整副本。
MAX_FIELD_CHARS = 500

# 键名之外的第二道闸：凭据常常藏在普通字段的值里（`export OPENAI_API_KEY=sk-...`），
# 按键名匹配一个都抓不到。
_TOKEN_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
)

# 值级掩码的补充：bash command 串里的凭证不在任何 SENSITIVE_KEYS 键下，上面的
# 键名闸抓不到。Bearer 要求词边界与最小长度、JWT 要求 eyJ 开头的三段式
# base64url，避免把普通命令文本误伤成掩码。
_BEARER_PATTERN = re.compile(r"\b(bearer\s+)[A-Za-z0-9\-._~+/]{20,}", re.IGNORECASE)
_JWT_PATTERN = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}")


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()

    def record(
        self,
        *,
        tool_name: str,
        input_data: dict[str, Any],
        outcome: str,
        approver: str,
        cwd: str,
        trace_id: str = "",
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "tool_name": tool_name,
            "input": self._redact(input_data),
            "outcome": outcome,
            "approver": approver,
            "cwd": cwd,
            # Optional correlation id (see nexuscli.observability): empty for
            # callers that don't pass one, so existing records stay valid.
            "trace_id": trace_id,
        }
        line = json.dumps(event, ensure_ascii=False) + "\n"
        # os.open 而不是 Path.open：新文件从创建那一刻就是 0600，不留 0644 的窗口
        # （umask 只能收窄权限，不能放宽）。
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(line)
        self._restrict_permissions()

    def tail(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        events = []
        for line in lines:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def _redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                if any(marker in key.lower() for marker in SENSITIVE_KEYS):
                    redacted[key] = "***"
                else:
                    redacted[key] = self._redact(item)
            return redacted
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, str):
            # 先掩码再截断：顺序反过来会留下跨在边界上的 token 前缀。
            return self._truncate(self._mask_tokens(value))
        return value

    @staticmethod
    def _mask_tokens(text: str) -> str:
        text = _JWT_PATTERN.sub("***", text)
        text = _BEARER_PATTERN.sub(r"\1***", text)
        for pattern in _TOKEN_PATTERNS:
            text = pattern.sub("***", text)
        return text

    @staticmethod
    def _truncate(text: str) -> str:
        if len(text) <= MAX_FIELD_CHARS:
            return text
        return f"{text[:MAX_FIELD_CHARS]}…[truncated {len(text) - MAX_FIELD_CHARS} chars]"

    def _restrict_permissions(self) -> None:
        """把日志收紧到属主读写。

        best-effort：Windows 的 chmod 只映射到只读位，某些文件系统（FAT/exFAT、
        部分网络盘）也不支持 POSIX 权限位；这些失败不该让一条已经写成功的审计
        记录把工具调用拖成异常。
        """
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            return
