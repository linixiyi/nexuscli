"""tests/test_lane_a_snapshot_restore.py — Lane A snapshot surfaces (/snapshot, /restore).

Command-level, user-visible coverage for the testing-matrix rows that were ⬜/🟡
on 2026-10-18: the messages a REPL user reads and the workspace state they can
observe. Service-level snapshot/restore semantics already live in
tests/test_snapshot.py (create/incremental/restore round trip) — not rewritten
here; this file covers what the *commands* show and do, including the
``/snapshot save`` creator added this round (before it, no command could create
a restore point with the default ``features.auto_snapshot=false``).
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.commands.snapshot_cmds import _snapshot_command
from nexuscli.entrypoints.repl import (
    PermissionModeController,
    ReplSessionState,
    _handle_slash,
)
from nexuscli.session import SessionStore
from nexuscli.snapshot import SnapshotService


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


def _make_state(tmp_path: Path) -> ReplSessionState:
    store = SessionStore(root=tmp_path / "sessions")
    return ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "note.txt").write_text("before", encoding="utf-8")
    return project


# ---------------------------------------------------------------------------
# [matrix 4. /restore] — roll a broken file back to the snapshot instant
# ---------------------------------------------------------------------------


def test_restore_without_arg_prints_readable_usage(tmp_path):
    state = _make_state(tmp_path)
    agent = SimpleNamespace(history=[], cwd=str(tmp_path))
    console = _console()

    should_exit = _run_slash("/restore", console, str(tmp_path), agent, state)

    assert should_exit is False
    assert "Usage: /restore <snapshot-id-or-index>" in _console_text(console)


def test_restore_by_index_rolls_file_back_and_confirms(tmp_path):
    """The user story: snapshot before a risky change, break the file, restore,
    and read the pre-change content again with a visible confirmation."""
    project = _make_project(tmp_path)
    console = _console()
    state = _make_state(tmp_path)
    agent = SimpleNamespace(history=[], cwd=str(project))

    # Save a manual snapshot (the /snapshot save command), then break the file.
    _snapshot_command("save before-risky-edit", console, str(project))
    snapshot_id = SnapshotService(project).list()[0].id
    (project / "note.txt").write_text("corrupted!!", encoding="utf-8")

    console = _console()
    should_exit = _run_slash("/restore 1", console, str(project), agent, state)

    assert should_exit is False
    assert f"Restored {snapshot_id}" in _console_text(console)
    assert (project / "note.txt").read_text(encoding="utf-8") == "before"


def test_restore_by_exact_id_works_too(tmp_path):
    project = _make_project(tmp_path)
    service = SnapshotService(project)
    record = service.create("pre-turn")
    (project / "note.txt").write_text("broken", encoding="utf-8")

    state = _make_state(tmp_path)
    agent = SimpleNamespace(history=[], cwd=str(project))
    console = _console()

    _run_slash(f"/restore {record.id}", console, str(project), agent, state)

    assert f"Restored {record.id}" in _console_text(console)
    assert (project / "note.txt").read_text(encoding="utf-8") == "before"


def test_restore_unknown_ref_fails_without_touching_workspace(tmp_path):
    project = _make_project(tmp_path)
    state = _make_state(tmp_path)
    agent = SimpleNamespace(history=[], cwd=str(project))
    console = _console()

    with pytest.raises(ValueError, match="snapshot not found: 999"):
        _run_slash("/restore 999", console, str(project), agent, state)

    # The failed restore must not have damaged the workspace.
    assert (project / "note.txt").read_text(encoding="utf-8") == "before"


# ---------------------------------------------------------------------------
# [matrix 8.4] end-to-end chain piece the service tests don't cover: a manual
# snapshot survives into a *later* process's /restore (fresh SnapshotService
# instances, like exiting and reopening the REPL between save and restore).
# ---------------------------------------------------------------------------


def test_manual_snapshot_restorable_from_fresh_service(tmp_path):
    project = _make_project(tmp_path)
    first_console = _console()
    _snapshot_command("save checkpoint-a", first_console, str(project))
    (project / "note.txt").write_text("broken", encoding="utf-8")
    (project / "extra.txt").write_text("junk from the broken edit", encoding="utf-8")

    # A brand-new service instance (new REPL process) restores by index.
    fresh = SnapshotService(project)
    restored = fresh.restore("1")

    assert restored.phase == "checkpoint-a"
    assert (project / "note.txt").read_text(encoding="utf-8") == "before"
    # Files created after the snapshot are gone: the workspace matches the
    # snapshot instant, not a merge of both.
    assert not (project / "extra.txt").exists()
