"""session_cmds.py — Transcript persistence plus the /resume, /fork and /history session helpers."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from nexuscli.agent import Agent
from nexuscli.session import SessionMeta, SessionStore, SessionUsageTotals
from nexuscli.session.store import tree_parent_id

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
    # pi-style frameless listing: a bold heading line, then a headerless grid
    # with one dim header row — no box characters, same language as the
    # /config summary and the /usage line grid.
    console.print("[bold]NexusCLI History[/bold]")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column()
    grid.add_column()
    grid.add_column(no_wrap=True)
    grid.add_row(
        "[dim]ID[/dim]",
        "[dim]Title[/dim]",
        "[dim]Match[/dim]",
        "[dim]Updated[/dim]",
    )
    for hit in hits:
        grid.add_row(
            hit.session_id,
            hit.title or "(untitled)",
            hit.snippet,
            time.strftime("%Y-%m-%d %H:%M", time.localtime(hit.updated_at)),
        )
    console.print(grid)
    scope = "all projects" if all_projects else "this project"
    console.print(f"[dim]Searched {scope}. Resume with /resume <id>[/dim]")


def _print_session_list(console: Console, store: SessionStore, cwd: str) -> None:
    metas = store.list(limit=20, cwd=cwd)
    if not metas:
        console.print("(no saved sessions)")
        return
    # Frameless listing, same language as /history above.
    console.print("[bold]NexusCLI Sessions[/bold]")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right")
    grid.add_column(no_wrap=True)
    grid.add_column()
    grid.add_column(justify="right")
    grid.add_column(no_wrap=True)
    grid.add_row(
        "[dim]#[/dim]",
        "[dim]ID[/dim]",
        "[dim]Title[/dim]",
        "[dim]Messages[/dim]",
        "[dim]Updated[/dim]",
    )
    for index, meta in enumerate(metas, 1):
        grid.add_row(
            str(index),
            meta.id,
            meta.title or "(untitled)",
            str(meta.message_count),
            time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.updated_at)),
        )
    console.print(grid)
    console.print("[dim]Resume with /resume <index-or-id>[/dim]")


def _session_tree_line(meta: SessionMeta, prefix: str, current_id: str) -> str:
    """One frameless tree row: connector prefix, id, title, branch, created."""
    marker = "*" if meta.id == current_id else " "
    line = f"{prefix}{marker} {meta.id}  {meta.title or '(untitled)'}"
    if meta.branch:
        line += f"  [dim](branch: {meta.branch})[/dim]"
    if meta.id == current_id:
        line += "  [dim](current)[/dim]"
    if meta.created_at > 0:
        created = time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.created_at))
        line += f"  [dim]{created}[/dim]"
    return line


def _tree_command(arg: str, console: Console, store: SessionStore, current_session_id: str) -> None:
    """Handle ``/tree [id]``: render the parent/child/branch tree of a session.

    Without an argument the tree anchors at the current session: ancestors up
    to the root first, then every branch subtree below it. An optional id
    (exact/prefix, same resolution as ``/resume``) anchors the tree at another
    session instead. Sessions unrelated to the anchor are not part of the
    tree. A session that was never persisted (no transcript on disk — the
    fork/new-session laziness) renders as a lone root with a hint.

    Frameless listing, same language as /history and the session list above.
    """
    anchor_id = current_session_id
    if arg.strip():
        record = store.resolve(arg.strip())
        if record is None:
            console.print(f"[red]Session not found:[/red] {arg.strip()}")
            return
        anchor_id = record.meta.id
    metas = store.list(limit=10**6)
    by_id = {meta.id: meta for meta in metas}
    anchor = by_id.get(anchor_id)
    console.print("[bold]NexusCLI Session Tree[/bold]")
    if anchor is None:
        # Never persisted: no transcript file, so no stored relatives to show.
        console.print(f"* {anchor_id}  [dim](current)[/dim]")
        console.print("[dim]Current session has no transcript yet — send a message first.[/dim]")
        return
    children: dict[str, list[SessionMeta]] = {}
    for meta in metas:
        parent_id = tree_parent_id(meta)
        if parent_id in by_id:
            children.setdefault(parent_id, []).append(meta)
    for siblings in children.values():
        siblings.sort(key=lambda meta: (meta.created_at, meta.id))
    # Ancestor chain of the anchor, root first; the seen-set guards against
    # a hand-edited transcript pointing two sessions at each other.
    chain = [anchor]
    seen = {anchor.id}
    node = anchor
    while True:
        parent_id = tree_parent_id(node)
        parent = by_id.get(parent_id) if parent_id else None
        if parent is None or parent.id in seen:
            break
        seen.add(parent.id)
        chain.append(parent)
        node = parent
    chain.reverse()
    lines = [
        _session_tree_line(meta, "  " * depth + ("└─ " if depth else ""), anchor_id)
        for depth, meta in enumerate(chain)
    ]

    def walk(parent_id: str, prefix: str) -> None:
        siblings = children.get(parent_id, [])
        for index, child in enumerate(siblings):
            last = index == len(siblings) - 1
            lines.append(_session_tree_line(child, prefix + ("└─ " if last else "├─ "), anchor_id))
            walk(child.id, prefix + ("   " if last else "│  "))

    walk(anchor.id, "  " * (len(chain) - 1) + ("   " if len(chain) > 1 else ""))
    for line in lines:
        console.print(line)
    console.print(f"[dim]{len(lines)} sessions · Resume with /resume <id>[/dim]")
