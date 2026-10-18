"""Lane C machine assertions: /team · /task user-visible behavior.

Backs testing-matrix row 4./task (REPL `/task` 派任务): the command's queue
lifecycle as a user sees it — queue with a mode, list, log, cancel, and the
error copy for a bad mode. The durable queue itself (claims/leases/requeue)
is covered by tests/test_runtime.py; team-mode orchestration internals are
covered by tests/test_multi_agent.py.
"""

from __future__ import annotations

import re

from rich.console import Console

from nexuscli.entrypoints.commands.task_cmds import _task_command


def _render() -> tuple[Console, list[str]]:
    chunks: list[str] = []
    console = Console(
        file=type(
            "_Sink",
            (),
            {
                "write": staticmethod(chunks.append),
                "flush": staticmethod(lambda: None),
            },
        )(),
        width=200,
        legacy_windows=False,
    )
    return console, chunks


def _text(chunks: list[str]) -> str:
    return "".join(chunks)


def test_task_command_add_list_log_cancel_lifecycle(tmp_path) -> None:
    console, chunks = _render()
    cwd = str(tmp_path)

    _task_command("add --mode plan 帮我规划并审计 docs 目录", console, cwd)
    queued = _text(chunks)
    match = re.search(r"Queued (task_\S+) \(plan\)\.", queued)
    assert match, f"unexpected queue output: {queued!r}"
    assert "Run `nexuscli worker` or `nexuscli serve` to consume it." in queued
    task_id = match.group(1)

    chunks.clear()
    _task_command("", console, cwd)
    listing = _text(chunks)
    assert task_id in listing
    assert "queued" in listing and "plan" in listing
    assert "帮我规划并审计 docs 目录" in listing

    chunks.clear()
    _task_command(f"log {task_id}", console, cwd)
    assert f"Task {task_id} is queued" in _text(chunks)

    chunks.clear()
    _task_command(f"cancel {task_id}", console, cwd)
    assert "Canceled: True" in _text(chunks)

    chunks.clear()
    _task_command(f"log {task_id}", console, cwd)
    assert f"Task {task_id} is canceled" in _text(chunks)


def test_task_command_error_copy_for_bad_mode_and_missing_text(tmp_path) -> None:
    console, chunks = _render()
    cwd = str(tmp_path)

    _task_command("add --mode wizard do it", console, cwd)
    assert "mode must be one of: plan, react, team" in _text(chunks)

    chunks.clear()
    _task_command("add --mode plan", console, cwd)
    assert "task text is required after --mode" in _text(chunks)

    chunks.clear()
    _task_command("log task_does_not_exist", console, cwd)
    assert "(task not found)" in _text(chunks)

    chunks.clear()
    _task_command("", console, cwd)
    assert "(no tasks)" in _text(chunks)
