"""task_cmds.py — /task queue handler and the shared --mode task-argument parser."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from nexuscli.runtime import DurableTaskManager


def _task_command(arg: str, console: Console, cwd: str) -> None:
    manager = DurableTaskManager(Path.home() / ".nexuscli" / "tasks" / "tasks.db", scope=cwd)
    sub, _, rest = arg.partition(" ")
    if sub == "add" and rest:
        try:
            mode, prompt = _parse_mode_argument(rest, allowed={"react", "plan", "team"})
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            return
        task_id = manager.add(prompt, mode=mode)
        console.print(
            f"Queued {task_id} ({mode}). Run `nexuscli worker` or `nexuscli serve` to consume it."
        )
    elif sub == "cancel" and rest:
        console.print(f"Canceled: {manager.cancel(rest.strip())}")
    elif sub == "log" and rest:
        task = manager.get(rest.strip())
        if not task:
            console.print("(task not found)")
        else:
            console.print(task.result or task.error or f"Task {task.id} is {task.status}")
    else:
        rows = manager.list(limit=20)
        console.print(
            "\n".join(
                f"{task.id} {task.status} {task.mode} attempts={task.attempts} {task.prompt[:80]}"
                for task in rows
            )
            or "(no tasks)"
        )


def _parse_mode_argument(
    value: str,
    *,
    allowed: set[str] | None = None,
) -> tuple[str, str]:
    modes = allowed or {"react", "plan"}
    parts = value.strip().split(maxsplit=2)
    if len(parts) >= 2 and parts[0] in {"--mode", "-m"}:
        mode = parts[1].lower()
        if mode not in modes:
            raise ValueError(f"mode must be one of: {', '.join(sorted(modes))}")
        prompt = parts[2].strip() if len(parts) == 3 else ""
        if not prompt:
            raise ValueError("task text is required after --mode")
        return mode, prompt
    if parts and parts[0] == "--plan":
        prompt = value.strip()[len("--plan") :].strip()
        if not prompt:
            raise ValueError("task text is required after --plan")
        return "plan", prompt
    return "react", value.strip()
