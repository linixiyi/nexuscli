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

from nexuscli.session.store import TRANSCRIPT_CHAIN_ZERO, transcript_chain_hash

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


def _count_orphan_tool_calls(records: list[Any]) -> int:
    """Assistant messages whose ``tool_calls`` never got a *later* tool result.

    Ported from kitagent (MIT, zed123214/kitagent) core/session/store.py
    ``_trim_orphan_tool_use`` 的检测语义（assistant ``tool_use`` / tool result
    配对）。kitagent 在读路径物理修剪尾部孤儿块；本模块只读，这里只如实计数：
    崩溃截断等产物里 assistant 消息带 ``tool_calls`` 但其后没有配对
    ``role:"tool"`` 结果的，计入 ``orphans``。一条 assistant 消息只要有任一
    tool_call 落空即计入；配对要求结果出现在该消息之后（位置校验）。
    ``type:`` 记录行（usage）不是对话消息，不参与配对。
    """
    open_calls: list[tuple[int, set[str]]] = []  # (record index, unresolved call ids)
    result_pos: dict[str, int] = {}  # tool_call_id -> last result record index
    for index, record in enumerate(records):
        if not isinstance(record, dict) or "type" in record:
            continue
        role = record.get("role")
        if role == "assistant":
            ids = {
                str(call["id"])
                for call in record.get("tool_calls") or []
                if isinstance(call, dict) and call.get("id") is not None
            }
            if ids:
                open_calls.append((index, ids))
        elif role == "tool":
            tool_call_id = record.get("tool_call_id")
            if tool_call_id is not None:
                result_pos[str(tool_call_id)] = index
    return sum(
        1
        for assistant_index, ids in open_calls
        if any(result_pos.get(call_id, -1) <= assistant_index for call_id in ids)
    )


def _verify_transcript_chain(records: list[Any]) -> dict[str, Any]:
    """逐行验证 ``_chain`` 提交哈希；断链如实标注，不抛异常。

    Ported from kitagent (MIT, zed123214/kitagent) core/session/store.py
    ``transcript_commit`` 的提交哈希语义，改造成逐行哈希链。规则与写端
    （``session/store.py`` ``SessionWriter``）逐字一致：带 ``_chain`` 的行覆盖
    「上一条可见记录的 ``_chain``（无则零串）」；不带 ``_chain`` 的行（legacy
    前缀）重置为零串。断链后 prev 取该行存储值继续走，后续行的断链各自独立
    可检测。已知边界：整行剥除 ``_chain`` 字段的篡改其行本身不可检测——行在
    中部时，其后第一条链行会因存储 prev 与重置后的零串不符而如实断链；行在
    末尾时，下一次追加从零串重新锚定，验链恢复通过。``breaks`` 下标与
    ``total`` 同坐标系（全量记录、offset/limit 窗口截取之前）。
    """
    breaks: list[int] = []
    prev = TRANSCRIPT_CHAIN_ZERO
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            prev = TRANSCRIPT_CHAIN_ZERO
            continue
        stored = record.get("_chain")
        if stored is None:
            prev = TRANSCRIPT_CHAIN_ZERO
            continue
        expected = transcript_chain_hash(
            prev, {key: value for key, value in record.items() if key != "_chain"}
        )
        if isinstance(stored, str) and stored == expected:
            prev = stored
        else:
            breaks.append(index)
            prev = stored if isinstance(stored, str) and stored else TRANSCRIPT_CHAIN_ZERO
    return {"verified": not breaks, "breaks": breaks}


def read_transcript(
    session_id: str,
    *,
    offset: int = 0,
    limit: int = 200,
    verify_chain: bool = False,
) -> dict[str, Any] | None:
    """Return the message records of one transcript, or None for bad id / missing file.

    损坏尾处理（Ported from kitagent (MIT, zed123214/kitagent)
    core/graph/event_log.py ``read_event_log``）：收尾换行是提交标记——结尾没有
    换行的最后一行视为中断写入的半行，从返回视图截回最后完整行（``total`` 按
    截断后计数）。末行解析失败与中部损坏行一样维持既有跳过语义。本模块只读：
    这里只截返回视图，不物理改写文件。

    孤儿 tool_use 标注（Ported from kitagent (MIT, zed123214/kitagent)
    core/session/store.py ``_trim_orphan_tool_use``）：返回值新增 ``orphans``
    计数——assistant 消息带 ``tool_calls`` 但其后没有配对 ``role:"tool"`` 结果
    （崩溃截断产物）的条数；物理修剪不做（本模块只读，修剪语义落点见
    ``session/store.py`` 写路径文档）。

    哈希提交链校验（Ported from kitagent (MIT, zed123214/kitagent)
    core/session/store.py ``transcript_commit``）：``verify_chain=True`` 时逐行
    验证 body 行的 ``_chain``（sha256(prev_hash + 行内容 canonical JSON)），断链
    如实返回 ``chain``（``verified`` 布尔 + ``breaks`` 0 基记录下标，坐标与
    ``total`` 一致）而非抛异常；默认 False 保持既有行为。
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
    result: dict[str, Any] = {
        "messages": messages[offset : offset + max(0, limit)],
        "total": len(messages),
        "orphans": _count_orphan_tool_calls(messages),
    }
    if verify_chain:
        result["chain"] = _verify_transcript_chain(messages)
    return result


def usage_summary(days: int = 7) -> dict[str, Any]:
    from nexuscli.llm.usage_store import UsageStore

    return UsageStore().stats(days=max(1, days))
