"""Transcript 完整性：``_chain`` 哈希提交链 + 孤儿 tool_use 标注。

移植自 kitagent（MIT, zed123214/kitagent）core/session/store.py：
- ``transcript_commit`` / ``canonical_transcript_commit``：提交哈希按 canonical
  JSON（sort_keys + 紧凑分隔符）计算——NexusCLI 改造成逐行链
  ``sha256(prev_hash + 行内容)``，存入行内 ``_chain`` 字段；读端
  ``read_transcript(verify_chain=True)`` 逐行验链，断链如实标注不抛异常。
- ``_trim_orphan_tool_use``：孤儿 tool_use 检测语义——kitagent 读路径物理修剪，
  NexusCLI reads 只读，只如实标注 ``orphans`` 计数；物理修剪不做（语义落点见
  ``session/store.py`` ``SessionWriter`` 写路径文档）。

conftest autouse fixture 重定向 HOME/USERPROFILE，``sessions_dir()`` 落在临时目录。
"""

from __future__ import annotations

import hashlib
import json

from nexuscli.runtime.reads import read_transcript, sessions_dir
from nexuscli.session import SessionStore
from nexuscli.session.store import TRANSCRIPT_CHAIN_ZERO
from nexuscli.types import Message, Usage


def _store() -> SessionStore:
    return SessionStore(root=sessions_dir())


def _tool_call(call_id: str) -> dict[str, object]:
    return {"id": call_id, "type": "function", "function": {"name": "grep", "arguments": "{}"}}


def _convo() -> list[Message]:
    return [
        Message(role="user", content="find the bug"),
        Message(role="assistant", content="looking", tool_calls=[_tool_call("call_1")]),
        Message(role="tool", content="no matches", tool_call_id="call_1"),
        Message(role="assistant", content="done"),
    ]


def _expected_chain(prev: str, record: dict[str, object]) -> str:
    """独立于被测实现的链公式（canonical JSON + sha256），用于钉死语义。"""
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev + payload).encode("utf-8")).hexdigest()


def _body_lines(sid: str) -> list[str]:
    lines = (sessions_dir() / f"{sid}.jsonl").read_text(encoding="utf-8").splitlines()
    return lines[1:]  # 首行是 meta


def _assert_chain_intact(sid: str) -> list[str]:
    """逐行独立重算哈希链（要求每行都带 ``_chain``），返回各行链值。"""
    prev = TRANSCRIPT_CHAIN_ZERO
    chains: list[str] = []
    for index, line in enumerate(_body_lines(sid)):
        record: dict[str, object] = json.loads(line)
        stored = record.pop("_chain", None)
        assert isinstance(stored, str) and stored, f"line {index} missing _chain"
        assert stored == _expected_chain(prev, record), f"line {index} chain mismatch"
        chains.append(stored)
        prev = stored
    assert chains, "transcript body is empty"
    return chains


def _write_raw_transcript(sid: str, records: list[dict[str, object]]) -> None:
    sessions_dir().mkdir(parents=True, exist_ok=True)
    meta = {
        "type": "meta",
        "id": sid,
        "cwd": "/proj",
        "created_at": 1792303810.0,
        "updated_at": 1792303865.0,
        "model": "m1",
        "provider": "p1",
        "title": "integrity",
        "message_count": 0,
        "forked_from": "",
    }
    lines = [json.dumps(meta, ensure_ascii=False)]
    lines.extend(json.dumps(record, ensure_ascii=False) for record in records)
    (sessions_dir() / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 哈希提交链：追加即挂链、连续、公式钉死
# ---------------------------------------------------------------------------


def test_append_builds_continuous_hash_chain() -> None:
    store = _store()
    writer = store.new_writer(cwd="/proj")
    writer.append(_convo()[:2])
    writer.append_usage(
        model="m1",
        provider="p1",
        usage=Usage(input_tokens=3, output_tokens=5),
        ts="2026-10-25 10:00:00",
    )
    writer.append(_convo())  # 追加增量：tool + assistant

    chains = _assert_chain_intact(writer.meta.id)  # 首行 prev 即零串
    assert len(chains) == 5  # 2 message + 1 usage + 2 message

    result = read_transcript(writer.meta.id, verify_chain=True)
    assert result is not None
    assert result["chain"] == {"verified": True, "breaks": []}


def test_chain_hash_pins_canonical_formula() -> None:
    store = _store()
    writer = store.new_writer(cwd="/proj")
    writer.append([Message(role="user", content="钉住公式")])

    first: dict[str, object] = json.loads(_body_lines(writer.meta.id)[0])
    stored = first.pop("_chain")
    assert stored == _expected_chain(TRANSCRIPT_CHAIN_ZERO, first)
    # canonical 形态与存储形态解耦：键序不同的等价记录必须复算出同一哈希
    reordered = dict(reversed(list(first.items())))
    assert _expected_chain(TRANSCRIPT_CHAIN_ZERO, reordered) == stored


def test_append_does_not_rechain_existing_lines() -> None:
    """O(1) 写入开销的行为面：追加只哈希新行，旧行字节级不动。"""
    store = _store()
    writer = store.new_writer(cwd="/proj")
    writer.append(_convo()[:2])
    before = _body_lines(writer.meta.id)

    writer.append(_convo())

    after = _body_lines(writer.meta.id)
    assert after[: len(before)] == before
    assert len(after) == len(before) + 2
    _assert_chain_intact(writer.meta.id)


def test_legacy_transcript_starts_chain_at_zero_on_next_append() -> None:
    """既有无链 transcript：旧行原样保留，新行从零串锚定，验链通过。"""
    sid = "20261025-000000-legacy1"
    _write_raw_transcript(
        sid,
        [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ],
    )
    store = _store()
    record = store.load(sid)
    assert record is not None
    assert all("_chain" not in json.loads(line) for line in _body_lines(sid))

    writer = store.writer_for(record.meta, record.messages)
    writer.append(record.messages + [Message(role="user", content="new turn")])

    lines = _body_lines(sid)
    assert "_chain" not in json.loads(lines[0])  # 旧行不被注入链字段
    new_record: dict[str, object] = json.loads(lines[2])
    stored = new_record.pop("_chain")
    assert stored == _expected_chain(TRANSCRIPT_CHAIN_ZERO, new_record)

    result = read_transcript(sid, verify_chain=True)
    assert result is not None
    assert result["chain"] == {"verified": True, "breaks": []}


def test_append_after_torn_tail_verifies() -> None:
    """损坏尾恢复（回归）：半行成为中部坏行被跳过，追加的新行照常挂链可验。"""
    sid = "20261025-000000-tornc1"
    sessions_dir().mkdir(parents=True, exist_ok=True)
    meta = {
        "type": "meta",
        "id": sid,
        "cwd": "/proj",
        "created_at": 1.0,
        "updated_at": 2.0,
        "model": "m1",
        "provider": "p1",
        "title": "",
        "message_count": 1,
        "forked_from": "",
    }
    torn = (
        json.dumps(meta, ensure_ascii=False)
        + "\n"
        + json.dumps({"role": "user", "content": "hello"}, ensure_ascii=False)
        + "\n"
        + '{"role":"assistant","con'
    )
    (sessions_dir() / f"{sid}.jsonl").write_text(torn, encoding="utf-8")

    store = _store()
    record = store.load(sid)
    assert record is not None
    writer = store.writer_for(record.meta, record.messages)
    writer.append(record.messages + [Message(role="assistant", content="hi")])

    result = read_transcript(sid, verify_chain=True)
    assert result is not None
    assert result["total"] == 2
    assert result["chain"] == {"verified": True, "breaks": []}


# ---------------------------------------------------------------------------
# 断链检测：篡改中间行如实标注，不抛异常、不雪崩
# ---------------------------------------------------------------------------


def test_verify_chain_detects_tampered_middle_line() -> None:
    store = _store()
    writer = store.new_writer(cwd="/proj")
    writer.append(_convo())
    writer.append_usage(
        model="m1",
        provider="p1",
        usage=Usage(input_tokens=3, output_tokens=5),
        ts="2026-10-25 10:00:00",
    )
    sid = writer.meta.id

    path = sessions_dir() / f"{sid}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered: dict[str, object] = json.loads(lines[2])  # body 0 基 1：assistant tool_calls 行
    tampered["content"] = "篡改后的内容"
    lines[2] = json.dumps(tampered, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = read_transcript(sid, verify_chain=True)
    assert result is not None
    # 只标断裂行本身；断链后按存储 prev 续走，后续行（含 usage 行）不雪崩
    assert result["chain"] == {"verified": False, "breaks": [1]}
    assert result["total"] == 5  # 篡改行仍如实出现在记录里


def test_chain_field_strip_detected_by_next_chain_line() -> None:
    """剥除中间行的 _chain：其行本身不可检测，由下一链行如实断链。"""
    store = _store()
    writer = store.new_writer(cwd="/proj")
    writer.append(_convo()[:3])
    sid = writer.meta.id

    path = sessions_dir() / f"{sid}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    stripped: dict[str, object] = json.loads(lines[2])  # body 0 基 1
    stripped.pop("_chain")
    lines[2] = json.dumps(stripped, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = read_transcript(sid, verify_chain=True)
    assert result is not None
    assert result["chain"] == {"verified": False, "breaks": [2]}


def test_append_reanchors_after_chainless_tail() -> None:
    """末行 _chain 被剥除（外部破坏）：下一次追加从零串重新锚定，验链恢复通过。"""
    store = _store()
    writer = store.new_writer(cwd="/proj")
    writer.append(_convo())
    sid = writer.meta.id

    path = sessions_dir() / f"{sid}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    stripped: dict[str, object] = json.loads(lines[-1])  # 末行（body 0 基 3）
    stored_before = stripped.pop("_chain")
    lines[-1] = json.dumps(stripped, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    record = store.load(sid)
    assert record is not None
    resumed = store.writer_for(record.meta, record.messages)
    resumed.append(record.messages + [Message(role="assistant", content="recovered")])

    after = read_transcript(sid, verify_chain=True)
    assert after is not None
    assert after["chain"] == {"verified": True, "breaks": []}
    # 新末行确以零串为 prev 重新锚定（而非沿用被剥行的旧链值）
    tail: dict[str, object] = json.loads(_body_lines(sid)[-1])
    assert tail.pop("_chain") == _expected_chain(TRANSCRIPT_CHAIN_ZERO, tail)
    assert stored_before


def test_verify_chain_default_off_keeps_existing_shape() -> None:
    store = _store()
    writer = store.new_writer(cwd="/proj")
    writer.append([Message(role="user", content="hello")])

    result = read_transcript(writer.meta.id)
    assert result is not None
    assert set(result) == {"messages", "total", "orphans"}  # 默认无 chain 键
    assert result["total"] == 1
    assert result["orphans"] == 0
    assert result["messages"][0]["content"] == "hello"
    assert result["messages"][0]["_chain"]  # 链字段随行返回，对消费方透明


# ---------------------------------------------------------------------------
# 孤儿 tool_use 标注：只计数不修剪
# ---------------------------------------------------------------------------


def test_orphans_zero_when_tool_results_paired() -> None:
    sid = "20261025-000000-orph00"
    _write_raw_transcript(
        sid,
        [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_tool_call("call_1"), _tool_call("call_2")],
            },
            {"role": "tool", "content": "r1", "tool_call_id": "call_1"},
            {"role": "tool", "content": "r2", "tool_call_id": "call_2"},
            {"role": "assistant", "content": "done"},
            {"type": "usage", "total_tokens": 7},
        ],
    )

    result = read_transcript(sid)

    assert result is not None
    assert result["orphans"] == 0


def test_orphans_count_crash_truncated_tail() -> None:
    """崩溃截断形态：assistant 带 tool_calls 后再无任何 tool 结果。"""
    sid = "20261025-000000-orph01"
    _write_raw_transcript(
        sid,
        [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("call_1")]},
        ],
    )

    result = read_transcript(sid)

    assert result is not None
    assert result["orphans"] == 1


def test_orphans_partial_pairing_counts_message() -> None:
    sid = "20261025-000000-orph02"
    _write_raw_transcript(
        sid,
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_tool_call("call_1"), _tool_call("call_2")],
            },
            {"role": "tool", "content": "r1", "tool_call_id": "call_1"},
        ],
    )

    result = read_transcript(sid)

    assert result is not None
    assert result["orphans"] == 1  # 任一 tool_call 落空即计入该消息


def test_orphans_count_each_affected_assistant_message() -> None:
    sid = "20261025-000000-orph03"
    _write_raw_transcript(
        sid,
        [
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("call_a")]},
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("call_b")]},
            {"role": "tool", "content": "unrelated", "tool_call_id": "call_x"},
        ],
    )

    result = read_transcript(sid)

    assert result is not None
    assert result["orphans"] == 2


def test_orphans_pairing_requires_later_result() -> None:
    """「后续无配对」按位置判定：结果出现在 assistant 之前不算配对。"""
    sid = "20261025-000000-orph04"
    _write_raw_transcript(
        sid,
        [
            {"role": "tool", "content": "stale", "tool_call_id": "call_1"},
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("call_1")]},
        ],
    )

    result = read_transcript(sid)

    assert result is not None
    assert result["orphans"] == 1


def test_chained_transcript_with_orphan_annotates_both() -> None:
    """链校验与孤儿标注互不干扰：截断产物链仍完整，orphans 如实计数。"""
    store = _store()
    writer = store.new_writer(cwd="/proj")
    writer.append(_convo()[:2])  # assistant 带 tool_calls，无结果 → 崩溃截断形态

    result = read_transcript(writer.meta.id, verify_chain=True)

    assert result is not None
    assert result["chain"] == {"verified": True, "breaks": []}
    assert result["orphans"] == 1


# ---------------------------------------------------------------------------
# 重写 / meta / resume / fork 路径的链语义 + 既有消费方回归
# ---------------------------------------------------------------------------


def test_rewrite_rechains_all_lines_and_keeps_usage_ledger() -> None:
    store = _store()
    writer = store.new_writer(cwd="/proj")
    writer.append(_convo())
    writer.append_usage(
        model="m1",
        provider="p1",
        usage=Usage(input_tokens=3, output_tokens=5),
        ts="2026-10-25 10:00:00",
    )
    writer.append([Message(role="user", content="summary of earlier work")])  # 收缩 → _rewrite

    record = store.load(writer.meta.id)
    assert [m.content for m in record.messages] == ["summary of earlier work"]
    assert store.load_usage(writer.meta.id).total_tokens == 8  # usage 台账不被收缩抹掉

    _assert_chain_intact(writer.meta.id)  # 重写后全行重新挂链
    result = read_transcript(writer.meta.id, verify_chain=True)
    assert result is not None
    assert result["chain"] == {"verified": True, "breaks": []}
    assert any(m.get("type") == "usage" for m in result["messages"])


def test_write_meta_and_resume_keep_chain_valid() -> None:
    store = _store()
    writer = store.new_writer(cwd="/proj")
    writer.append(_convo()[:2])
    writer.meta.updated_at += 10
    writer._write_meta()

    result = read_transcript(writer.meta.id, verify_chain=True)
    assert result is not None
    assert result["chain"] == {"verified": True, "breaks": []}

    record = store.load(writer.meta.id)
    assert record is not None
    resumed = store.writer_for(record.meta, record.messages)
    resumed.append(record.messages + [Message(role="user", content="next turn")])

    after = read_transcript(writer.meta.id, verify_chain=True)
    assert after is not None
    assert after["chain"] == {"verified": True, "breaks": []}
    _assert_chain_intact(writer.meta.id)


def test_fork_builds_valid_chain_and_source_untouched() -> None:
    store = _store()
    writer = store.new_writer(cwd="/proj", model="m1", provider="p1")
    writer.append(_convo())
    source_chains = _assert_chain_intact(writer.meta.id)

    forked = store.fork(writer.meta.id)
    assert forked is not None

    _assert_chain_intact(forked.meta.id)  # 分叉副本全链可验
    assert _assert_chain_intact(writer.meta.id) == source_chains  # 源文件链值原样


def test_existing_consumers_unaffected_by_chain_field() -> None:
    """/sessions 列表、load/load_usage/search（/history 检索）对 _chain 透明。"""
    store = _store()
    writer = store.new_writer(cwd="/proj", model="m1", provider="p1")
    writer.append(_convo())
    writer.append_usage(model="m1", provider="p1", usage=Usage(input_tokens=3, output_tokens=5))

    record = store.load(writer.meta.id)
    assert record.meta.message_count == 4
    assert [m.role for m in record.messages] == ["user", "assistant", "tool", "assistant"]
    assert record.messages[1].tool_calls == [_tool_call("call_1")]  # _chain 不入 Message
    assert record.messages[1].content == "looking"

    totals = store.load_usage(writer.meta.id)
    assert (totals.turns, totals.total_tokens) == (1, 8)

    hits = store.search("no matches")
    assert hits and hits[0].session_id == writer.meta.id

    metas = store.list(cwd="/proj")
    assert [meta.id for meta in metas] == [writer.meta.id]
