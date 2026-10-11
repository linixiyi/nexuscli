"""usage.py — The /usage stats table (/usage per-turn display stays in repl.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.table import Table

from nexuscli.llm.usage_store import UsageStore
from nexuscli.session import SessionUsageTotals

if TYPE_CHECKING:
    # Typing-only edge, same convention as session_cmds.py: repl imports this
    # package, so importing ReplSessionState at runtime would be circular.
    from nexuscli.entrypoints.repl import ReplSessionState


def _session_usage_payload(session_state: ReplSessionState | None) -> dict[str, Any]:
    """Build the two-scope session usage view for the ``/usage`` JSON output.

    ``historical_total`` is the resume-time transcript baseline plus the turns
    recorded in this process (those turns append usage record lines to the
    transcript too, so the two add up to the transcript's ledger);
    ``current_process`` counts this process only. ``session_state=None``
    (single-shot / tests without a live REPL state) degrades both scopes to
    zeros while keeping the labeled sources.
    """
    empty = SessionUsageTotals()
    baseline = session_state.usage_baseline if session_state is not None else empty
    process = session_state.process_usage if session_state is not None else empty
    return {
        "historical_total": (baseline + process).to_dict(),
        "current_process": process.to_dict(),
        "sources": {
            "historical_total": (
                "session transcript usage records: resumed baseline + turns recorded this process"
            ),
            "current_process": "turns recorded in this process only",
        },
    }


def _usage_stats_command(rest: str, console: Console) -> None:
    """Render `/usage stats [N days]`: trailing-N-day totals plus per-model split.

    Any store failure (missing db, locked file, corrupt schema) is reported
    and swallowed — the stats view must never kill the REPL. An empty table
    renders as an all-zero summary, which is the desired first-run answer.
    """
    days = int(rest.strip()) if rest.strip().isdigit() and int(rest.strip()) >= 1 else 7
    try:
        stats = UsageStore().stats(days=days)
    except Exception as exc:  # noqa: BLE001 - report and keep the REPL alive
        console.print(f"[red]Usage stats unavailable:[/red] {exc}")
        return
    summary = Table(title=f"NexusCLI Usage — last {days} day(s)")
    summary.add_column("Field")
    summary.add_column("Value", justify="right")
    summary.add_row("turns", str(stats["turns"]))
    summary.add_row("total tokens", str(stats["total_tokens"]))
    summary.add_row("total cost (USD)", f"{stats['total_cost']['usd']:.6f}")
    summary.add_row("total cost (CNY)", f"{stats['total_cost']['cny']:.6f}")
    console.print(summary)
    per_model = Table(title="By model")
    per_model.add_column("Model")
    per_model.add_column("Turns", justify="right")
    per_model.add_column("Tokens", justify="right")
    per_model.add_column("USD", justify="right")
    per_model.add_column("CNY", justify="right")
    for row in stats["by_model"]:
        per_model.add_row(
            row["model"],
            str(row["turns"]),
            str(row["total_tokens"]),
            f"{row['total_cost']['usd']:.6f}",
            f"{row['total_cost']['cny']:.6f}",
        )
    console.print(per_model)
