"""Read-only data endpoints backing the workbench (F4/F5).

会话数据源是 `~/.nexuscli/sessions/<id>.jsonl`：首行 `type:"meta"` 元信息，之后每行
一条消息记录（`type:"usage"` 行混排其中）。`SessionWriter` 每次追加都是临时文件 +
`os.replace` 原子替换且路径为整文件重写，读者只会看到旧或新的完整内容——因此这里
直接整文件读，无需 tail。usage 聚合直接复用 `UsageStore.stats`。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_SESSION_ID_RE = re.compile(r"^[0-9a-zA-Z-]+$")


def sessions_dir() -> Path:
    return Path.home() / ".nexuscli" / "sessions"


def list_sessions(*, cwd: str, limit: int = 20, all_projects: bool = False) -> dict[str, Any]:
    base = sessions_dir()
    root = os.path.realpath(cwd)
    items: list[dict[str, Any]] = []
    if base.is_dir():
        for path in sorted(base.glob("*.jsonl")):
            try:
                with path.open(encoding="utf-8") as fh:
                    first = fh.readline()
            except OSError:
                continue
            try:
                meta = json.loads(first)
            except json.JSONDecodeError:
                continue
            if not isinstance(meta, dict) or meta.get("type") != "meta":
                continue
            if not all_projects and os.path.realpath(str(meta.get("cwd") or "")) != root:
                continue
            items.append(meta)
    items.sort(key=lambda meta: float(meta.get("updated_at") or 0), reverse=True)
    return {"sessions": items[: max(0, limit)]}


def read_transcript(session_id: str, *, offset: int = 0, limit: int = 200) -> dict[str, Any] | None:
    """Return the message records of one transcript, or None for bad id / missing file."""

    if not _SESSION_ID_RE.fullmatch(session_id):
        return None
    path = sessions_dir() / f"{session_id}.jsonl"
    if not path.is_file():
        return None
    messages: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("type") == "meta":
                    continue
                messages.append(record)
    except OSError:
        return None
    window = messages[offset : offset + max(0, limit)]
    return {"messages": window, "total": len(messages)}


def usage_summary(days: int = 7) -> dict[str, Any]:
    from nexuscli.llm.usage_store import UsageStore

    return UsageStore().stats(days=max(1, days))
