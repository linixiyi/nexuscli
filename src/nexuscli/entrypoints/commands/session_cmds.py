"""session_cmds.py — Transcript persistence plus the /resume, /fork and /history session helpers."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from nexuscli.agent import Agent
from nexuscli.session import SessionStore, SessionUsageTotals

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
    # Historical usage rides in the transcript (one record line per turn):
    # the resumed session continues counting instead of starting from zero.
    # The process accumulator resets with it — it only covers the previous
    # transcript's turns, and baseline + process must equal this
    # transcript's ledger.
    session_state.usage_baseline = session_state.store.load_usage(record.meta.id)
    session_state.process_usage = SessionUsageTotals()
    title = f" — {record.meta.title}" if record.meta.title else ""
    console.print(
        f"[green]Resumed session[/green] {record.meta.id} ({len(record.messages)} messages){title}"
    )


def _record_session_usage(session_state: ReplSessionState, agent: Agent) -> None:
    """Append one completed turn's usage to the live session transcript.

    Runs next to ``repl._record_turn_usage`` (which writes usage.db) after
    each regular/custom-command turn; the transcript copy is what makes the
    resume-time usage baseline work. Best-effort: any failure is swallowed —
    recording must never interrupt a turn.
    """
    try:
        totals = SessionUsageTotals.from_turn(
            usage=agent.last_usage,
            cost=agent.last_cost,
        )
        session_state.process_usage = session_state.process_usage + totals
        session_state.writer.append_usage(
            model=agent.llm_client.model_name,
            provider=agent.llm_client.provider_name,
            usage=agent.last_usage,
            cost=agent.last_cost,
        )
    except Exception:
        return


def _history_command(arg: str, console: Console, store: SessionStore, cwd: str) -> None:
    """Handle ``/history <query> [--all]``: search past session transcripts.

    ``--all`` lifts the current-project filter (same semantics as
    ``nexuscli sessions --all``). Empty or flag-only input prints a friendly
    usage hint instead of erroring.
    """
    tokens = arg.split()
    all_projects = "--all" in tokens
    query = " ".join(token for token in tokens if token != "--all").strip()
    if not query:
        console.print("[red]Usage:[/red] /history <query> [--all]")
        return
    hits = store.search(query, cwd=None if all_projects else cwd)
    if not hits:
        console.print("(no matches)")
        return
    table = Table(title="NexusCLI History")
    table.add_column("ID")
    table.add_column("Title")
    table.add_column("Match")
    table.add_column("Updated")
    for hit in hits:
        table.add_row(
            hit.session_id,
            hit.title or "(untitled)",
            hit.snippet,
            time.strftime("%Y-%m-%d %H:%M", time.localtime(hit.updated_at)),
        )
    console.print(table)
    scope = "all projects" if all_projects else "this project"
    console.print(f"[dim]Searched {scope}. Resume with /resume <id>[/dim]")


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
