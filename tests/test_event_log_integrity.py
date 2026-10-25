"""Event-log integrity：transcript 损坏尾处理 + SessionWriter flush 纪律。

移植自 kitagent（MIT, zed123214/kitagent）：
- core/graph/event_log.py ``read_event_log`` / ``_truncate_tail``：收尾换行是提交
  标记，无换行收尾的最后一行是中断写入的半行，读取时截回最后完整行；尾部之前的
  损坏行走既有跳过语义。
- core/events/writer.py：每次写盘后 flush，fsync 可选（默认关）。

conftest autouse fixture 重定向 HOME/USERPROFILE，``sessions_dir()`` 落在临时目录。
"""

from __future__ import annotations

import json
import os

from nexuscli.runtime.reads import read_transcript, sessions_dir
from nexuscli.session import SessionStore
from nexuscli.types import Message


def _meta_line(session_id: str) -> str:
    return json.dumps(
        {
            "type": "meta",
            "id": session_id,
            "cwd": "/proj",
            "created_at": 1792303810.0,
            "updated_at": 1792303865.0,
            "model": "m1",
            "provider": "p1",
            "title": "integrity",
            "message_count": 2,
            "forked_from": "",
        },
        ensure_ascii=False,
    )


def _msg(role: str, content: str) -> str:
    return json.dumps({"role": role, "content": content}, ensure_ascii=False)


def _write_raw(session_id: str, payload: bytes) -> None:
    sessions_dir().mkdir(parents=True, exist_ok=True)
    (sessions_dir() / f"{session_id}.jsonl").write_bytes(payload)


# ---------------------------------------------------------------------------
# 场景一：半行 JSON 尾（无换行结尾）→ 截回最后完整行，total 按截断后计
# ---------------------------------------------------------------------------


def test_transcript_torn_tail_half_line_json_is_cut() -> None:
    """结尾半行 JSON（解析必失败）被截去，只剩最后完整行之前的记录。"""
    sid = "20261025-000000-tail01"
    payload = (
        _meta_line(sid) + "\n" + _msg("user", "hello") + "\n" + '{"role":"assistant","cont'
    ).encode("utf-8")
    _write_raw(sid, payload)

    result = read_transcript(sid)

    assert result is not None
    assert result["total"] == 1
    assert result["messages"] == [{"role": "user", "content": "hello"}]


def test_transcript_unterminated_but_parseable_tail_is_uncommitted() -> None:
    """kitagent 语义：换行是提交标记——没有收尾换行的行即使能解析也算未提交。"""
    sid = "20261025-000000-tail02"
    payload = (
        _meta_line(sid) + "\n" + _msg("user", "hello") + "\n" + _msg("assistant", "hi")
    ).encode("utf-8")  # 无收尾换行
    _write_raw(sid, payload)

    result = read_transcript(sid)

    assert result is not None
    assert result["total"] == 1
    assert [m["role"] for m in result["messages"]] == ["user"]


def test_transcript_torn_tail_cut_before_utf8_decode() -> None:
    """半行截断发生在解码之前：尾部被切断的多字节字符不再破坏整个读取。"""
    sid = "20261025-000000-tail03"
    torn = '{"role":"assistant","content":"你好'.encode()[:-1]  # 切在多字节中间
    payload = (_meta_line(sid) + "\n" + _msg("user", "hello") + "\n").encode() + torn
    _write_raw(sid, payload)

    result = read_transcript(sid)

    assert result is not None
    assert result["total"] == 1
    assert result["messages"][0]["content"] == "hello"


def test_transcript_offset_window_applies_after_tail_cut() -> None:
    sid = "20261025-000000-tail04"
    payload = (
        _meta_line(sid)
        + "\n"
        + _msg("user", "q1")
        + "\n"
        + _msg("assistant", "a1")
        + "\n"
        + _msg("user", "q2")
    ).encode("utf-8")  # 最后一行无换行 → 截去，剩 q1/a1
    _write_raw(sid, payload)

    result = read_transcript(sid, offset=1, limit=10)

    assert result is not None
    assert result["total"] == 2
    assert [m["content"] for m in result["messages"]] == ["a1"]


# ---------------------------------------------------------------------------
# 场景二：正常（完整收尾换行）→ 行为与既有完全一致
# ---------------------------------------------------------------------------


def test_transcript_intact_file_reads_all_records() -> None:
    sid = "20261025-000000-okline"
    usage = json.dumps({"type": "usage", "model": "m1", "total_tokens": 7}, ensure_ascii=False)
    text = (
        "\n".join([_meta_line(sid), _msg("user", "hello"), _msg("assistant", "hi"), usage]) + "\n"
    )
    _write_raw(sid, text.encode("utf-8"))

    result = read_transcript(sid)

    assert result is not None
    assert result["total"] == 3  # meta 行剔除，usage 行保留
    assert [m.get("role") or m.get("type") for m in result["messages"]] == [
        "user",
        "assistant",
        "usage",
    ]

    page = read_transcript(sid, offset=1, limit=1)
    assert page is not None
    assert page["messages"][0]["role"] == "assistant"


def test_transcript_missing_and_bad_ids_unchanged() -> None:
    assert read_transcript("../escape") is None
    assert read_transcript("bad_id!@") is None
    assert read_transcript("20261025-000000-nonexistent") is None


# ---------------------------------------------------------------------------
# 场景三：中部坏行 → 维持既有跳过语义（坏行不可见，前后记录保留）
# ---------------------------------------------------------------------------


def test_transcript_middle_corrupt_line_is_skipped() -> None:
    sid = "20261025-000000-midbad"
    text = (
        _meta_line(sid)
        + "\n"
        + _msg("user", "hello")
        + "\n"
        + "{not json at all"
        + "\n"
        + _msg("assistant", "hi")
        + "\n"
    )
    _write_raw(sid, text.encode("utf-8"))

    result = read_transcript(sid)

    assert result is not None
    assert result["total"] == 2
    assert [m["role"] for m in result["messages"]] == ["user", "assistant"]


def test_append_after_torn_tail_keeps_transcript_readable(tmp_path) -> None:
    """损坏尾文件照常恢复会话：半行随下一次追加成为中部坏行，继续被跳过。"""
    sid = "20261025-000000-tail05"
    torn = (
        _meta_line(sid) + "\n" + _msg("user", "hello") + "\n" + '{"role":"assistant","con'
    ).encode("utf-8")
    _write_raw(sid, torn)
    before = read_transcript(sid)
    assert before is not None and before["total"] == 1

    store = SessionStore(root=sessions_dir())
    record = store.load(sid)
    assert record is not None
    assert [m.content for m in record.messages] == ["hello"]  # load 跳过坏行
    writer = store.writer_for(record.meta, record.messages)
    writer.append(record.messages + [Message(role="assistant", content="hi")])

    after = read_transcript(sid)
    assert after is not None
    assert after["total"] == 2  # 半行成为中部坏行，仍不可见
    assert [m["content"] for m in after["messages"]] == ["hello", "hi"]
    reloaded = store.load(sid)
    assert reloaded is not None
    assert [m.content for m in reloaded.messages] == ["hello", "hi"]


# ---------------------------------------------------------------------------
# SessionWriter flush 纪律：默认无 fsync（性能特征不变），fsync=True 生效
# ---------------------------------------------------------------------------


def test_writer_default_write_flushes_without_fsync(tmp_path, monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd))

    store = SessionStore(root=tmp_path / "sessions")
    writer = store.new_writer(cwd=str(tmp_path))
    writer.append([Message(role="user", content="hello")])

    assert calls == []  # fsync 默认关闭
    # 原子替换语义不变：落盘内容完整、tmp 消失、收尾换行在
    assert not writer.path.with_name(writer.path.name + ".tmp").exists()
    raw = writer.path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert json.loads(raw.splitlines()[0])["type"] == "meta"
    record = store.load(writer.meta.id)
    assert record is not None
    assert [m.content for m in record.messages] == ["hello"]


def test_writer_fsync_opt_in_fires_on_append_and_rewrite(tmp_path, monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(fd))

    store = SessionStore(root=tmp_path / "sessions")
    writer = store.new_writer(cwd=str(tmp_path), fsync=True)
    writer.append([Message(role="user", content="hello")])
    assert len(calls) >= 1

    calls.clear()
    writer.append([])  # 历史收缩 → _rewrite，同样走 flush 纪律
    assert len(calls) >= 1

    record = store.load(writer.meta.id)
    assert record is not None
    resumed = store.writer_for(record.meta, record.messages, fsync=True)
    assert resumed.fsync is True
