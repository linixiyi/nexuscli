from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace

from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.repl import (
    PermissionModeController,
    ReplSessionState,
    _handle_slash,
)
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


def test_fork_copies_full_history_and_links_meta(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    convo = _convo()
    writer.append(convo)

    forked = store.fork(writer.meta.id)

    assert forked is not None
    assert forked.meta.id != writer.meta.id
    assert forked.meta.forked_from == writer.meta.id
    assert forked.meta.message_count == 5
    # The fork holds the full history, including the tool_calls round trip.
    assert [(m.role, m.content, m.tool_call_id, m.tool_calls) for m in forked.messages] == [
        (m.role, m.content, m.tool_call_id, m.tool_calls) for m in convo
    ]

    # The source transcript is untouched: same messages, no forked_from link.
    reread = store.load(writer.meta.id)
    assert reread is not None
    assert [(m.role, m.content, m.tool_call_id, m.tool_calls) for m in reread.messages] == [
        (m.role, m.content, m.tool_call_id, m.tool_calls) for m in convo
    ]
    assert reread.meta.message_count == 5
    assert reread.meta.forked_from == ""


def test_fork_title_arg_overrides_and_default_inherits(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    writer.append([Message(role="user", content="fix the login bug")])

    inherited = store.fork(writer.meta.id)
    assert inherited is not None
    assert inherited.meta.title == "fix the login bug"

    overridden = store.fork(writer.meta.id, title="my fork")
    assert overridden is not None
    assert overridden.meta.title == "my fork"

    # A source without a title (no user message yet) forks titleless; the fork
    # then derives one from its first user message on the next append.
    seed = store.new_writer(cwd=str(tmp_path))
    seed.append([Message(role="assistant", content="booting")])
    forked = store.fork(seed.meta.id)
    assert forked is not None
    assert forked.meta.title == ""
    fork_writer = store.writer_for(forked.meta, forked.messages)
    fork_writer.append(
        [Message(role="assistant", content="booting"), Message(role="user", content="hello there")]
    )
    reloaded = store.load(forked.meta.id)
    assert reloaded is not None
    assert reloaded.meta.title == "hello there"


def test_fork_unknown_id_returns_none(tmp_path):
    store = _make_store(tmp_path)

    assert store.fork("missing-id") is None


def test_forked_session_appends_independently(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path))
    writer.append(_convo())
    source_before = store.load(writer.meta.id)

    forked = store.fork(writer.meta.id)
    assert forked is not None
    fork_writer = store.writer_for(forked.meta, forked.messages)
    fork_writer.append(_convo() + [Message(role="user", content="one more turn")])

    fork_record = store.load(forked.meta.id)
    assert fork_record is not None
    assert len(fork_record.messages) == 6
    assert fork_record.meta.message_count == 6
    # The two writes never cross: source file stays at 5 messages.
    source_after = store.load(writer.meta.id)
    assert source_after is not None
    assert len(source_after.messages) == 5
    assert source_after.meta.message_count == 5
    assert source_before is not None
    assert source_after.meta.updated_at == source_before.meta.updated_at


# --- /fork REPL-level wiring (direct _handle_slash pattern, as test_repl.py) ---


class _RecordingBuffer:
    """Local stand-in for SkillContextBuffer that records clear() calls."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []
        self.clear_calls = 0

    def push(self, name: str, body: str) -> None:
        self.items.append((name, body))

    def clear(self) -> None:
        self.clear_calls += 1

    def is_empty(self) -> bool:
        return not self.items


def test_fork_slash_switches_writer_and_keeps_history(tmp_path):
    store = SessionStore(root=tmp_path / "sessions")
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    convo = [Message(role="user", content="fix the login bug")]
    writer.append(convo)
    state = ReplSessionState(store=store, writer=writer)
    buffer = _RecordingBuffer()
    agent = SimpleNamespace(
        history=list(convo),
        cwd=str(tmp_path),
        llm_client=SimpleNamespace(model_name="m1", provider_name="p"),
        skill_context_buffer=buffer,
    )
    console = Console(file=io.StringIO(), width=200)

    should_exit = asyncio.run(
        _handle_slash(
            "/fork 我的分叉",
            console,
            str(tmp_path),
            NexusCliConfig(),
            agent,
            None,
            PermissionModeController(NexusCliConfig()),
            None,
            state,
        )
    )

    assert should_exit is False
    first_fork_id = state.writer.meta.id
    assert first_fork_id != writer.meta.id
    assert state.writer.meta.forked_from == writer.meta.id
    # The new writer took over with the copied history persisted already.
    assert state.writer.persisted == 1
    # Conversation continues: agent.history is kept, not reset.
    assert len(agent.history) == 1
    assert buffer.clear_calls == 1
    assert "Forked session" in console.file.getvalue()

    # Chained fork off the already-persisted fork works too.
    should_exit = asyncio.run(
        _handle_slash(
            "/fork",
            console,
            str(tmp_path),
            NexusCliConfig(),
            agent,
            None,
            PermissionModeController(NexusCliConfig()),
            None,
            state,
        )
    )
    assert should_exit is False
    assert state.writer.meta.id != first_fork_id
    assert state.writer.meta.forked_from == first_fork_id
