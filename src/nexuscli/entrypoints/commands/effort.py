"""effort.py — Session-level reasoning-effort switch (/effort).

Pi-style thinking levels: ``off minimal low medium high xhigh max``. The
command sets the level live on the current client (no /model re-creation);
``/effort`` with no argument cycles to the next level, and the status bar
shows the active level next to the model name — the same place pi puts it.
"""

from __future__ import annotations

from rich.console import Console

# Ordered low→high; the cycle and clamping both read this order.
EFFORT_LEVELS: tuple[str, ...] = (
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

# Legacy four-level spelling kept as aliases so old fingers and old docs land.
_ALIASES = {"default": None, "clear": "off", "none": "off"}


def next_effort_level(current: str | None) -> str:
    """The level after ``current`` in the cycle; None/unset starts at ``low``."""
    if current is None or current == "":
        return "low"
    try:
        index = EFFORT_LEVELS.index(current)
    except ValueError:
        return "low"
    return EFFORT_LEVELS[(index + 1) % len(EFFORT_LEVELS)]


def supports_effort(client) -> bool:
    """Capability probe: AnthropicClient is a slots dataclass without the field."""
    return hasattr(client, "reasoning_effort")


def _usage_line() -> str:
    return "Usage: /effort off|minimal|low|medium|high|xhigh|max (no arg cycles)"


def _effort_command(arg: str, console: Console, agent: Agent) -> None:  # noqa: F821
    """Session-level reasoning effort switch (live on the current client)."""
    client = agent.llm_client
    if not supports_effort(client):
        console.print(
            f"[yellow]Provider {client.provider_name} does not support reasoning effort.[/yellow]"
        )
        return
    if not arg:
        level = next_effort_level(client.reasoning_effort)
        client.reasoning_effort = None if level == "off" else level
        _print_set(console, client.reasoning_effort, cycled=True)
        return
    level = arg.strip().lower()
    if level in _ALIASES:
        level = _ALIASES[level] or "off"
    if level not in EFFORT_LEVELS:
        console.print(f"[red]{_usage_line()}[/red]")
        return
    client.reasoning_effort = None if level == "off" else level
    _print_set(console, client.reasoning_effort)


def _print_set(console: Console, level: str | None, *, cycled: bool = False) -> None:
    verb = "cycled to" if cycled else "set to"
    if level is None:
        console.print(
            f"[green]Reasoning effort {verb} off[/green] (no reasoning_effort field sent)"
        )
    else:
        console.print(f"[green]Reasoning effort {verb} {level}[/green]")
    console.print("[dim]Live for this client; a /model switch resets it.[/dim]")
