from __future__ import annotations

import json

from nexuscli.session import SessionStore
from nexuscli.types import Message


def _make_store(tmp_path):
    return SessionStore(root=tmp_path / "sessions")


def _convo():
    return [
        Message(role="user", content="find the failing test"),
        Message(role="assistant", content="looking"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "grep", "arguments": "{}"},
                }
            ],
        ),
        Message(role="tool", content="no matches", tool_call_id="call_1"),
        Message(role="assistant", content="done"),
    ]


def test_writer_is_lazy_until_first_append(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")

    assert store.list() == []
    assert not writer.path.exists()


def test_append_round_trips_messages_and_title(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    convo = _convo()

    writer.append(convo)

    record = store.load(writer.meta.id)
    assert record is not None
    assert record.meta.title == "find the failing test"
    assert record.meta.message_count == len(convo)
    assert [(m.role, m.content, m.tool_call_id) for m in record.messages] == [
        (m.role, m.content, m.tool_call_id) for m in convo
    ]
    # Serialized tool calls survive the round trip.
    assert record.messages[2].tool_calls == convo[2].tool_calls


def test_append_tracks_cumulative_history_and_rewrites_on_shrink(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path))
    convo = _convo()
    writer.append(convo)
    writer.append(convo + [Message(role="user", content="next task")])

    record = store.load(writer.meta.id)
    assert record.meta.message_count == len(convo) + 1

    # Context compression shrinks in-memory history; the transcript follows.
    compressed = [Message(role="user", content="summary of earlier work")]
    writer.append(compressed)
    record = store.load(writer.meta.id)
    assert record.meta.message_count == 1
    assert [m.content for m in record.messages] == ["summary of earlier work"]


def test_list_filters_by_cwd_and_sorts_newest_first(tmp_path):
    store = _make_store(tmp_path)
    older = store.new_writer(cwd="/proj/a")
    older.append([Message(role="user", content="older session")])
    newer = store.new_writer(cwd="/proj/b")
    newer.append([Message(role="user", content="newer session")])

    # Force deterministic ordering independent of file timestamps.
    newer.meta.updated_at = older.meta.updated_at + 10
    newer._write_meta()

    assert [m.id for m in store.list()] == [newer.meta.id, older.meta.id]
    assert [m.id for m in store.list(cwd="/proj/a")] == [older.meta.id]
    assert store.list(cwd="/proj/missing") == []


def test_resolve_supports_index_exact_id_and_prefix(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path))
    writer.append([Message(role="user", content="hello")])
    meta = writer.meta

    assert store.resolve("1", cwd=str(tmp_path)).meta.id == meta.id
    assert store.resolve(meta.id).meta.id == meta.id
    assert store.resolve(meta.id[:8]).meta.id == meta.id
    assert store.resolve("999", cwd=str(tmp_path)) is None
    assert store.resolve("does-not-exist") is None


def test_load_returns_none_for_missing_or_corrupt_files(tmp_path):
    store = _make_store(tmp_path)
    store.root.mkdir(parents=True)
    (store.root / "broken.jsonl").write_text("not json\n", encoding="utf-8")
    (store.root / "partial.jsonl").write_text(
        json.dumps({"type": "meta", "id": "partial", "cwd": "/x"})
        + "\n"
        + "{corrupt line}\n"
        + json.dumps({"role": "user", "content": "kept"})
        + "\n",
        encoding="utf-8",
    )

    assert store.load("missing-id") is None
    assert store._read_meta(store.root / "broken.jsonl") is None

    record = store.load("partial")
    assert record is not None
    assert [m.content for m in record.messages] == ["kept"]


def test_delete_removes_transcript(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path))
    writer.append([Message(role="user", content="bye")])

    assert store.delete(writer.meta.id) is True
    assert store.load(writer.meta.id) is None
    assert store.delete(writer.meta.id) is False


def test_list_ignores_metaless_files(tmp_path):
    store = _make_store(tmp_path)
    store.root.mkdir(parents=True)
    (store.root / "garbage.jsonl").write_text("{}", encoding="utf-8")

    assert store.list() == []


def test_meta_line_survives_body_updates(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path))
    writer.append([Message(role="user", content="first")])
    writer.append([Message(role="user", content="first"), Message(role="assistant", content="ok")])

    record = store.load(writer.meta.id)
    assert record.meta.message_count == 2
    assert record.meta.title == "first"
