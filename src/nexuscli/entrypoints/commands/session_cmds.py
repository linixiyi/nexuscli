"""session_cmds.py — Transcript persistence plus the /resume and /fork session helpers."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from nexuscli.agent import Agent
from nexuscli.session import SessionStore

if TYPE_CHECKING:
    # Runtime import would be circular: repl imports this package for its
    # handlers while owning the state dataclasses. Annotations are strings
    # (PEP 563), so this is a typing-only edge.
    from nexuscli.entrypoints.repl import ReplSessionState


def _persist_history(
    session_state: ReplSessionState,
    agent: Agent,
    console: Console,
) -> None:
    try:
        session_state.writer.append(agent.history)
    except OSError as exc:
        console.print(f"[yellow]Failed to persist session transcript:[/yellow] {exc}")


def _apply_resume(
    console: Console,
    session_state: ReplSessionState,
    agent: Agent,
    *,
    resume: str | None = None,
    continue_last: bool = False,
) -> None:
    """Restore a previous transcript into the live agent, if requested."""
    if not resume and not continue_last:
        return
    record = None
    if resume:
        record = session_state.store.resolve(resume, cwd=agent.cwd)
        if record is None:
            console.print(f"[red]Session not found:[/red] {resume}")
            return
    else:
        recent = session_state.store.list(limit=1, cwd=agent.cwd)
        if not recent:
            console.print("[yellow]No previous session to continue.[/yellow]")
            return
        record = session_state.store.load(recent[0].id)
    if record is None:
        console.print("[yellow]Session transcript is unreadable; starting fresh.[/yellow]")
        return
    agent.history = list(record.messages)
    session_state.writer = session_state.store.writer_for(record.meta, record.messages)
    title = f" — {record.meta.title}" if record.meta.title else ""
    console.print(
        f"[green]Resumed session[/green] {record.meta.id} ({len(record.messages)} messages){title}"
    )


def _print_session_list(console: Console, store: SessionStore, cwd: str) -> None:
    metas = store.list(limit=20, cwd=cwd)
    if not metas:
        console.print("(no saved sessions)")
        return
    table = Table(title="NexusCLI Sessions")
    table.add_column("#", justify="right")
    table.add_column("ID")
    table.add_column("Title")
    table.add_column("Messages", justify="right")
    table.add_column("Updated")
    for index, meta in enumerate(metas, 1):
        table.add_row(
            str(index),
            meta.id,
            meta.title or "(untitled)",
            str(meta.message_count),
            time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.updated_at)),
        )
    console.print(table)
    console.print("[dim]Resume with /resume <index-or-id>[/dim]")
