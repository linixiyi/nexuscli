"""Read-only data endpoints backing the workbench (F4/F5).

会话数据源是 `~/.nexuscli/sessions/<id>.jsonl`：首行 `type:"meta"` 元信息，之后每行
一条消息记录（`type:"usage"` 行混排其中）。`SessionWriter` 每次追加都是临时文件 +
`os.replace` 原子替换且路径为整文件重写，读者只会看到旧或新的完整内容——因此这里
直接整文件读，无需 tail。文件在写入方之外受损（历史版本追加中断、手工编辑）时，
结尾半行按下方 `read_transcript` 的损坏尾处理截去。usage 聚合直接复用
`UsageStore.stats`。
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
    """Return the message records of one transcript, or None for bad id / missing file.

    损坏尾处理（Ported from kitagent (MIT, zed123214/kitagent)
    core/graph/event_log.py ``read_event_log``）：收尾换行是提交标记——结尾没有
    换行的最后一行视为中断写入的半行，从返回视图截回最后完整行（``total`` 按
    截断后计数）。末行解析失败与中部损坏行一样维持既有跳过语义。本模块只读：
    这里只截返回视图，不物理改写文件。
    """
    if not _SESSION_ID_RE.fullmatch(session_id):
        return None
    path = sessions_dir() / f"{session_id}.jsonl"
    if not path.is_file():
        return None
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    # Ported from kitagent (MIT, zed123214/kitagent) _truncate_tail 的判定语义：
    # 无收尾换行 → 截回最后一个换行（视图级；文件本身不动）。
    if payload and not payload.endswith(b"\n"):
        payload = payload[: payload.rfind(b"\n") + 1]
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    messages: list[dict[str, Any]] = []
    for line in text.split("\n"):
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
    window = messages[offset : offset + max(0, limit)]
    return {"messages": window, "total": len(messages)}


def usage_summary(days: int = 7) -> dict[str, Any]:
    from nexuscli.llm.usage_store import UsageStore

    return UsageStore().stats(days=max(1, days))
