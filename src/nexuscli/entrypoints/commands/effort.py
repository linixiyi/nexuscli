"""effort.py — Session-level reasoning-effort switch (/effort)."""

from __future__ import annotations

from rich.console import Console

from nexuscli.agent import Agent

_EFFORT_LEVELS = ("minimal", "low", "medium", "high")


def _effort_command(arg: str, console: Console, agent: Agent) -> None:
    """Session-level reasoning effort switch (live on the current client)."""
    client = agent.llm_client
    if not arg:
        current = getattr(client, "reasoning_effort", None)
        console.print(f"Reasoning effort: {current or 'not set (provider default)'}")
        return
    level = arg.strip().lower()
    if level not in _EFFORT_LEVELS:
        console.print("[red]Usage:[/red] /effort minimal|low|medium|high")
        return
    if not hasattr(client, "reasoning_effort"):
        # AnthropicClient is a slots dataclass without the field
        # (llm/anthropic.py:47-59): hasattr is the capability probe.
        console.print(
            f"[yellow]Provider {client.provider_name} does not support "
            "reasoning effort; field not set.[/yellow]"
        )
        return
    client.reasoning_effort = level
    console.print(
        f"[green]Reasoning effort set to {level}[/green] "
        "(live for this client; a /model switch creates a fresh client and resets it)"
    )
