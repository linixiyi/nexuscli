"""tests/test_lane_a_session_surface.py — Lane A session surfaces (/save, /fork).

User-visible coverage for the testing-matrix rows that were still ⬜/🟡 on
2026-10-18: what the REPL prints and what state changes a user can observe.
Drives ``_handle_slash`` directly (same pattern as tests/test_repl.py:94-218).

Not rewritten here on purpose: /resume already has command-level assertions
(tests/test_repl.py:94 test_handle_slash_resume_loads_session_into_agent and
neighbours) and store-level fork semantics live in
tests/test_session_store.py:167 — these files only add what was missing.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.commands.snapshot_cmds import _snapshot_command
from nexuscli.entrypoints.repl import (
    PermissionModeController,
    ReplSessionState,
    _handle_slash,
)
from nexuscli.memory.manager import MemoryManager
from nexuscli.session import SessionStore
from nexuscli.snapshot import SnapshotService
from nexuscli.types import Message


def _console() -> Console:
    return Console(file=io.StringIO(), width=200)


def _console_text(console: Console) -> str:
    return console.file.getvalue()


def _run_slash(raw: str, console: Console, cwd: str, agent, state: ReplSessionState) -> bool:
    return asyncio.run(
        _handle_slash(
            raw,
            console,
            cwd,
            NexusCliConfig(),
            agent,
            None,
            PermissionModeController(NexusCliConfig()),
            None,
            state,
        )
    )


def _make_state(
    tmp_path: Path, *, messages: list[Message] | None = None
) -> tuple[ReplSessionState, SessionStore]:
    store = SessionStore(root=tmp_path / "sessions")
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    if messages:
        writer.append(messages)
    return ReplSessionState(store=store, writer=writer), store


# ---------------------------------------------------------------------------
# [matrix 4. /save] — what a user sees when saving a fact
# ---------------------------------------------------------------------------


def test_save_without_arg_prints_readable_usage(tmp_path):
    state, _store = _make_state(tmp_path)
    agent = SimpleNamespace(history=[], cwd=str(tmp_path))
    console = _console()

    should_exit = _run_slash("/save", console, str(tmp_path), agent, state)

    assert should_exit is False
    assert "Usage: /save <fact>" in _console_text(console)


def test_save_confirms_with_memory_id_and_fact_is_listable(tmp_path):
    """The visible confirmation names the stored memory, and the fact lands in
    the long-term store as a manual entry (what /memory later shows)."""
    state, _store = _make_state(tmp_path)
    agent = SimpleNamespace(history=[], cwd=str(tmp_path))
    console = _console()

    _run_slash(
        "/save project uses uv for dependency management", console, str(tmp_path), agent, state
    )

    text = _console_text(console)
    assert "Saved memory #" in text

    manager = MemoryManager("~/.nexuscli/memory.db", scope=str(tmp_path))
    entries = manager.list()
    assert len(entries) == 1
    assert entries[0].content == "project uses uv for dependency management"
    assert entries[0].source == "manual"


# ---------------------------------------------------------------------------
# [matrix 8.3 / 4. /fork] — fork a session, main line stays untouched
# ---------------------------------------------------------------------------


def test_fork_copies_live_history_switches_writer_and_confirms(tmp_path):
    convo = [
        Message(role="user", content="remember the codeword: lemon-seven"),
        Message(role="assistant", content="Got it — codeword is lemon-seven."),
    ]
    state, store = _make_state(tmp_path, messages=convo)
    agent = SimpleNamespace(history=list(convo), cwd=str(tmp_path), skill_context_buffer=["stale"])
    console = _console()
    original_id = state.writer.meta.id

    should_exit = _run_slash("/fork experiment-B", console, str(tmp_path), agent, state)

    assert should_exit is False
    text = _console_text(console)
    assert "Forked session" in text
    assert original_id in text
    assert "(2 messages)" in text
    assert "experiment-B" in text

    # The live session is now the fork, linked back to its source.
    fork_id = state.writer.meta.id
    assert fork_id != original_id
    assert state.writer.meta.forked_from == original_id
    # One-shot context queued for the old session must not leak into the fork.
    assert agent.skill_context_buffer == []
    # Fork-time usage baselines start from zero.
    assert state.usage_baseline.turns == 0
    assert state.process_usage.turns == 0

    # On disk: the fork holds the full history; the source is byte-identical
    # in message content and carries no forked_from link.
    forked = store.load(fork_id)
    assert forked is not None
    assert [(m.role, m.content) for m in forked.messages] == [
        ("user", "remember the codeword: lemon-seven"),
        ("assistant", "Got it — codeword is lemon-seven."),
    ]
    source = store.load(original_id)
    assert source is not None
    assert [(m.role, m.content) for m in source.messages] == [
        ("user", "remember the codeword: lemon-seven"),
        ("assistant", "Got it — codeword is lemon-seven."),
    ]
    assert source.meta.forked_from == ""


def test_fork_after_fork_point_keeps_source_history_frozen(tmp_path):
    """Turns taken after the fork append to the fork transcript only — the
    source transcript on disk never gains them (the matrix's 不污染主线)."""
    convo = [Message(role="user", content="main line turn")]
    state, store = _make_state(tmp_path, messages=convo)
    agent = SimpleNamespace(history=list(convo), cwd=str(tmp_path), skill_context_buffer=[])
    console = _console()
    original_id = state.writer.meta.id

    _run_slash("/fork side", console, str(tmp_path), agent, state)

    # A turn in the forked session: the live history grows and the fork
    # transcript syncs with it (the same _persist_history flow the REPL runs).
    agent.history.append(Message(role="user", content="experiment direction turn"))
    state.writer.append(agent.history)

    source = store.load(original_id)
    assert source is not None
    assert [(m.role, m.content) for m in source.messages] == [("user", "main line turn")]
    forked = store.load(state.writer.meta.id)
    assert forked is not None
    assert [(m.role, m.content) for m in forked.messages] == [
        ("user", "main line turn"),
        ("user", "experiment direction turn"),
    ]


def test_fork_with_no_history_prints_friendly_hint(tmp_path):
    state, _store = _make_state(tmp_path)
    agent = SimpleNamespace(history=[], cwd=str(tmp_path))
    console = _console()

    _run_slash("/fork", console, str(tmp_path), agent, state)

    assert "Nothing to fork yet — send a message first." in _console_text(console)


# ---------------------------------------------------------------------------
# [matrix 4. /snapshot] — manual snapshot save + readable listing
# ---------------------------------------------------------------------------


def test_snapshot_save_creates_snapshot_and_confirms(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "note.txt").write_text("before", encoding="utf-8")
    console = _console()

    _snapshot_command("save before-risky-edit", console, str(project))

    text = _console_text(console)
    assert "Snapshot saved:" in text
    service = SnapshotService(project)
    rows = service.list()
    assert len(rows) == 1
    assert rows[0].phase == "before-risky-edit"
    assert rows[0].id in text
    # The saved snapshot really holds the workspace file.
    assert (rows[0].path / "note.txt").read_text(encoding="utf-8") == "before"


def test_snapshot_save_default_label_when_bare_save(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    console = _console()

    _snapshot_command("save", console, str(project))

    rows = SnapshotService(project).list()
    assert len(rows) == 1
    assert rows[0].phase == "manual"
    assert "Snapshot saved:" in _console_text(console)


def test_snapshot_save_label_is_slugified_to_a_safe_directory_name(tmp_path):
    """The label becomes a directory name under ~/.nexuscli/snapshots — path
    separators must never survive into it."""
    project = tmp_path / "project"
    project.mkdir()
    console = _console()

    _snapshot_command("save ../escape attempt/here", console, str(project))

    service = SnapshotService(project)
    rows = service.list()
    assert len(rows) == 1
    record = rows[0]
    assert "/" not in record.id
    assert record.path.parent == service.root
    assert record.path.is_dir()


def test_snapshot_listing_is_readable_and_empty_state_hints_save(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    console = _console()

    _snapshot_command("", console, str(project))
    assert "(no snapshots — create one with: /snapshot save [label])" in _console_text(console)

    service = SnapshotService(project)
    first = service.create("pre-turn")
    second = service.create("post-turn")
    console = _console()
    _snapshot_command("", console, str(project))

    text = _console_text(console)
    # Newest first, one numbered line per snapshot with id + phase + timestamp.
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines[0].startswith("1. ") and second.id in lines[0]
    assert lines[1].startswith("2. ") and first.id in lines[1]
    assert "pre-turn" in lines[1] and "post-turn" in lines[0]


def test_snapshot_clean_reports_count(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    service = SnapshotService(project)
    service.create("pre-turn")
    console = _console()

    _snapshot_command("clean", console, str(project))

    assert "Cleaned 1 snapshots." in _console_text(console)
    assert service.list() == []
