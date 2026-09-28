"""background.py — In-process registry for background shell tasks.

Protocol: ``bash`` / ``execute_command`` accept ``run_in_background: true``
and return a short task id plus the on-disk log path immediately, without
blocking the turn. The model later polls ``task_output`` for the status and
the tail of the output, or terminates the task with ``task_stop``. Both
output streams are merged into one append-mode log file
(``~/.nexuscli/background/<task_id>.log``) by handing the same file object
to the subprocess as stdout and stderr, so the tail matches what a terminal
would have shown and no dual-file bookkeeping is needed.

Trade-offs:

- The registry is process-local state: task ids are lost when the REPL
  exits and child processes do not outlive a restart. The output files stay
  on disk and can be inspected manually — the minimal alignment with
  ZCode's persisted background-task semantics without a cross-session
  registry.
- ``stop`` escalates terminate -> kill on the direct child only. On Windows
  the direct child is the shell (``cmd.exe``); its descendants may outlive
  the stop and keep the log file open until they exit on their own. A tree
  kill would need another subprocess and platform-specific failure modes
  for little gain, so the log file is simply left to settle.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Defaults shared by the registry and the task_output/task_stop handlers
# (kept here so the byte budget has exactly one source of truth).
DEFAULT_TAIL_BYTES = 8192
_STOP_GRACE_SECONDS = 2.0

TaskStatus = Literal["running", "completed", "failed", "stopped"]


@dataclass(slots=True)
class BackgroundTask:
    task_id: str
    command: str
    process: asyncio.subprocess.Process
    output_path: Path
    started_at: float


class BackgroundTaskRegistry:
    """Registry of background shell tasks; see the module docstring for the protocol."""

    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        # Tasks stopped through this registry: their raw exit code reflects
        # the kill signal, but status() must still report "stopped" for them.
        self._stopped: set[str] = set()

    async def start(self, command: str, cwd: str | None = None) -> BackgroundTask:
        """Spawn *command* and register it. Returns at once, never waits for it."""
        task_id = uuid.uuid4().hex[:12]
        output_dir = self._background_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{task_id}.log"
        # One append-mode file merges stdout+stderr: both handles share the
        # same file description, so stream interleaving matches a terminal
        # and no dual-file bookkeeping is needed. The child receives a
        # duplicated handle, so closing our copy after the spawn is safe.
        with open(output_path, "ab") as sink:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=sink,
                stderr=sink,
            )
        task = BackgroundTask(
            task_id=task_id,
            command=command,
            process=process,
            output_path=output_path,
            started_at=time.time(),
        )
        self._tasks[task_id] = task
        return task

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def read_tail(self, task_id: str, max_bytes: int = DEFAULT_TAIL_BYTES) -> str:
        """Return the last *max_bytes* of the merged log, decoded leniently.

        Seeking back from the file end can land inside a multi-byte UTF-8
        sequence; ``errors="replace"`` turns that seam into U+FFFD instead of
        raising. Reading ``process.returncode`` is the lazy-reap equivalent
        of ``Popen.poll()``: the asyncio transport reports the exit code on
        the running loop, so it is already fresh when non-None.
        """
        task = self._get_or_raise(task_id)
        size = task.output_path.stat().st_size
        with open(task.output_path, "rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            return handle.read().decode("utf-8", errors="replace")

    async def stop(self, task_id: str) -> TaskStatus:
        """Terminate the task's process, escalating to kill after a grace period."""
        task = self._get_or_raise(task_id)
        process = task.process
        if process.returncode is None:
            self._stopped.add(task_id)
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=_STOP_GRACE_SECONDS)
            except TimeoutError:
                process.kill()
                await process.wait()
        # An already-exited process keeps its own status (completed/failed);
        # only tasks this registry stopped are reported as "stopped".
        return self.status(task_id)

    def status(self, task_id: str) -> TaskStatus | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if task.process.returncode is None:
            return "running"
        if task_id in self._stopped:
            return "stopped"
        return "completed" if task.process.returncode == 0 else "failed"

    def _get_or_raise(self, task_id: str) -> BackgroundTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def _background_dir(self) -> Path:
        # Resolved per call instead of at import time so tests can redirect
        # Path.home() to a temporary directory.
        return Path.home() / ".nexuscli" / "background"


# Process-wide singleton: the REPL runs one registry per process, shared by
# the bash/task_output/task_stop handlers.
background_registry = BackgroundTaskRegistry()
