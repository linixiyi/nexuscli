"""bg_tasks.py — In-process registry for background subagent tasks.

Protocol: the ``task`` tool accepts ``run_in_background: true`` and hands back
a short task id plus the on-disk report path immediately, without blocking the
turn. The subagent runs on an ``asyncio.create_task`` wrapper around
:func:`nexuscli.agent.subagent.run_subagent`; once it reaches a terminal state,
the final report (or the failure note) is written to
``~/.nexuscli/bg-subagents/<task_id>.md`` and cached in the registry. The REPL
drains finished tasks once per turn end and prints a one-shot summary to the
user — notifications are for the user only and never enter model context.

Trade-offs / not ported from ZCode (core/src/runtime/methods/
background-notifications.ts):

- The registry is process-local state: unfinished tasks die with the event
  loop when the REPL exits, while the report files of finished tasks stay on
  disk and remain readable (best-effort persistence — the write happens at
  completion time, not on shutdown). No notification persistence, no session
  ledger, no cross-session recovery.
- No cancellation: ``task_stop`` is not extended to background subagents in
  this slice; the depth limit already bounds runaway delegation.
- No mid-turn steering: completion notices are printed to the user only and
  are not injected into the model context (ZCode feeds them back to the
  model).
- Approval callbacks still fire from inside the background task, so an
  ask-level approval prompt may interleave with the REPL input line; this
  slice accepts that — with ``hitl_mode: "always"``, delegate in the
  foreground instead.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from nexuscli.agent.subagent import AgentDefinition, run_subagent
from nexuscli.tools.base import ToolContext

BgSubagentStatus = Literal["running", "completed", "failed"]


@dataclass(slots=True)
class BgSubagentTask:
    task_id: str
    agent_type: str
    description: str
    prompt: str
    report_path: Path
    task: asyncio.Task
    started_at: float


@dataclass(slots=True)
class BgSubagentResult:
    status: Literal["completed", "failed"]
    report: str
    error: str = ""


class BgSubagentRegistry:
    """Registry of background subagent tasks; see the module docstring for the protocol."""

    def __init__(self) -> None:
        self._tasks: dict[str, BgSubagentTask] = {}
        self._results: dict[str, BgSubagentResult] = {}
        # Task ids already handed out by drain_notifications(): this keeps the
        # end-of-turn announcement idempotent (a second drain returns []).
        self._notified: set[str] = set()

    def start(
        self,
        agent_def: AgentDefinition,
        prompt: str,
        context: ToolContext,
        *,
        agent_type: str,
        description: str,
    ) -> BgSubagentTask:
        """Register and spawn the subagent coroutine. Returns at once, never awaits."""
        task_id = uuid.uuid4().hex[:12]
        report_path = self._bg_dir() / f"{task_id}.md"
        task = asyncio.create_task(
            self._run(task_id, agent_def, prompt, context),
            name=f"bg-subagent-{task_id}",
        )
        entry = BgSubagentTask(
            task_id=task_id,
            agent_type=agent_type,
            description=description,
            prompt=prompt,
            report_path=report_path,
            task=task,
            started_at=time.time(),
        )
        self._tasks[task_id] = entry
        return entry

    async def _run(
        self,
        task_id: str,
        agent_def: AgentDefinition,
        prompt: str,
        context: ToolContext,
    ) -> None:
        """Run the subagent to a terminal state; no exception ever escapes.

        The depth-limit ValueError from run_subagent lands in the failed
        branch like any other error — a background task must never interrupt
        the turn that spawned it.
        """
        entry = self._tasks.get(task_id)
        try:
            report = await run_subagent(agent_def, prompt, context)
        except Exception as exc:  # a background task must not interrupt the turn
            message = str(exc).strip() or exc.__class__.__name__
            error = message.splitlines()[0]
            self._results[task_id] = BgSubagentResult(status="failed", report="", error=error)
            note = f"Background subagent task {task_id} failed.\nerror: {error}\n"
        else:
            self._results[task_id] = BgSubagentResult(status="completed", report=report)
            note = report
        # Best-effort report file: an unwritable home must not break the task.
        if entry is not None:
            with suppress(OSError):
                self._bg_dir().mkdir(parents=True, exist_ok=True)
                entry.report_path.write_text(note, encoding="utf-8")

    def status(self, task_id: str) -> BgSubagentStatus | None:
        result = self._results.get(task_id)
        if result is not None:
            return result.status
        if task_id not in self._tasks:
            return None
        # Either the wrapper is still running, or it finished a beat ago and
        # _run has not published the cached result yet (race seam): report
        # "running" for that beat — the next task_output poll or the
        # end-of-turn announcement observes the terminal state.
        return "running"

    def result(self, task_id: str) -> BgSubagentResult | None:
        return self._results.get(task_id)

    def get(self, task_id: str) -> BgSubagentTask | None:
        return self._tasks.get(task_id)

    def drain_notifications(self) -> list[dict[str, str]]:
        """Collect tasks newly finished since the last drain; each exactly once.

        This is the idempotence basis of the "one summary per turn end"
        announcement — a repeated drain returns [].
        """
        notes: list[dict[str, str]] = []
        for task_id, entry in self._tasks.items():
            if task_id in self._notified:
                continue
            result = self._results.get(task_id)
            if result is None:
                continue  # still running — picked up by a later drain
            self._notified.add(task_id)
            notes.append(
                {
                    "task_id": task_id,
                    "agent_type": entry.agent_type,
                    "description": entry.description,
                    "status": result.status,
                    "report_path": str(entry.report_path),
                    "error": result.error,
                }
            )
        return notes

    def _bg_dir(self) -> Path:
        # Resolved per call instead of at import time so tests can redirect
        # Path.home() to a temporary directory (same seam as background.py).
        # Deliberately separate from the bash log directory
        # ~/.nexuscli/background/: these reports are markdown documents read
        # by users and task_output, not process logs.
        return Path.home() / ".nexuscli" / "bg-subagents"


# Process-wide singleton: the REPL runs one registry per process, shared by
# the task/task_output handlers and the end-of-turn announcer (same shape as
# background_registry in tools.background).
bg_subagent_registry = BgSubagentRegistry()
