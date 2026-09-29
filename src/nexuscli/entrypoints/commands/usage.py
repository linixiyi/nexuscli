"""usage.py — The /usage stats table (/usage per-turn display stays in repl.py)."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from nexuscli.llm.usage_store import UsageStore


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
