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
- Each task leads its own process group (``start_new_session``), so one
  signal reaches everything the shell forked. ``stop`` sends SIGTERM to the
  group, escalates to SIGKILL, and only reports ``stopped`` once the group is
  actually empty; members that outlive SIGKILL leave it at ``stopping``.
- On Windows there is no process group to signal: ``stop`` degrades to
  ``terminate``/``kill`` on the direct child, and that shell's descendants
  outlive the stop and keep the log file open until they exit on their own.
  A tree walk would need another subprocess and platform-specific failure
  modes for little gain.
- A task whose leader exited while its group still has members is a *leaked*
  task: ``task_output`` renders it as ``orphaned`` and ``task_stop`` still
  finds and reaps it instead of failing with an unknown id.
"""

from __future__ import annotations

import asyncio
import atexit
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from nexuscli.tools.commands import (
    kill_process_group,
    process_group_alive,
    process_group_of,
    process_group_spawn_kwargs,
    signal_process_group,
)

# Defaults shared by the registry and the task_output/task_stop handlers
# (kept here so the byte budget has exactly one source of truth).
DEFAULT_TAIL_BYTES = 8192
_STOP_GRACE_SECONDS = 2.0

# "stopping" is the honest answer when the group outlived the kill ladder:
# the log may still grow, so it must not be rendered as a finished stop.
# "orphaned" marks a task whose leader exited while its group still had
# members — leaked work that task_stop can still find and reap.
TaskStatus = Literal["running", "completed", "failed", "stopped", "stopping", "orphaned"]


@dataclass(slots=True)
class BackgroundTask:
    task_id: str
    command: str
    process: asyncio.subprocess.Process
    output_path: Path
    started_at: float
    # Captured at spawn: once the leader is reaped getpgid() answers for a
    # dead pid, and the group is exactly what a stop has to reach.
    pgid: int | None = None


class BackgroundTaskRegistry:
    """Registry of background shell tasks; see the module docstring for the protocol."""

    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        # Tasks stopped through this registry: their raw exit code reflects
        # the kill signal, but status() must still report "stopped" for them.
        self._stopped: set[str] = set()

    async def start(
        self,
        command: str,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> BackgroundTask:
        """Spawn *command* and register it. Returns at once, never waits for it.

        *env* is the child's environment passed straight to the subprocess;
        ``None`` keeps the historical behaviour of inheriting the parent's
        full ``os.environ``. Callers that want the guard pass the dict built
        by :func:`nexuscli.policy.sandbox.sanitize_environment` — the
        registry itself holds no config, so the sanitization switch stays
        with the caller.
        """
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
                env=env,
                **process_group_spawn_kwargs(),
            )
        task = BackgroundTask(
            task_id=task_id,
            command=command,
            process=process,
            output_path=output_path,
            started_at=time.time(),
            pgid=process_group_of(process.pid),
        )
        self._tasks[task_id] = task
        return task

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def live_task_ids(self) -> list[str]:
        """Ids whose process group still has members (running or leaked)."""
        return [
            task_id
            for task_id, task in self._tasks.items()
            if process_group_alive(task.pgid) or task.process.returncode is None
        ]

    def kill_live_tasks(self) -> list[str]:
        """SIGKILL every live group without waiting — the exit-time sweep.

        Called when the process is going away: nobody is left to poll the
        ladder, so the kill is unconditional. The registry cannot outlive the
        process that owns it, which is exactly why leaving the groups behind
        turns them into PPID=1 orphans.
        """
        killed: list[str] = []
        for task_id in self.live_task_ids():
            task = self._tasks[task_id]
            signal_process_group(task.pgid, hard=True)
            self._stopped.add(task_id)
            killed.append(task_id)
        return killed

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
        """Kill the task's whole process group; report what actually happened.

        The leader exiting is not the end of a background task: a shell that
        forked a worker leaves the worker holding the log file. So the group
        is what gets signalled and the group is what gets verified. An
        already-finished task is left untouched and keeps its own status.
        """
        task = self._get_or_raise(task_id)
        if task.process.returncode is not None and not process_group_alive(task.pgid):
            # Nothing left to stop: reporting "stopped" would imply this call
            # did something, so the task keeps its completed/failed status.
            return self.status(task_id)
        self._stopped.add(task_id)
        await kill_process_group(task.pgid, task.process, _STOP_GRACE_SECONDS)
        # status() re-probes the group, so "stopping" here means members
        # survived SIGKILL and the log may still grow.
        return self.status(task_id)

    def status(self, task_id: str) -> TaskStatus | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if task.process.returncode is None:
            return "running"
        if task_id in self._stopped:
            # The leader is gone; the group decides whether the stop finished.
            return "stopping" if process_group_alive(task.pgid) else "stopped"
        if process_group_alive(task.pgid):
            # Leader exited but work it forked still runs: that is the orphan
            # case, not a completion. task_stop still finds it by id.
            return "orphaned"
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


def _sweep_background_tasks() -> None:
    # Registered at import: every exit path (normal return, sys.exit from a
    # slash command, an unhandled exception) funnels through atexit, while a
    # shutdown hook would only cover a clean loop shutdown. Without the sweep
    # the groups reparent to init and keep writing to their logs with nobody
    # holding their ids.
    background_registry.kill_live_tasks()


atexit.register(_sweep_background_tasks)
