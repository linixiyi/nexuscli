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


# Providers whose request builder actually forwards reasoning_effort. The
# client keeps the attribute everywhere it exists, so hasattr alone would
# report a level as "set" on a provider that silently drops it.
_EFFORT_WIRE_PROVIDERS = frozenset({"openai", "openai-compatible", "compatible"})


def effort_reaches_wire(client) -> bool:
    """True when the current provider will actually send reasoning_effort."""
    return str(getattr(client, "provider_name", "")).lower() in _EFFORT_WIRE_PROVIDERS


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
        _print_set(console, client, cycled=True)
        return
    level = arg.strip().lower()
    if level in _ALIASES:
        level = _ALIASES[level] or "off"
    if level not in EFFORT_LEVELS:
        console.print(f"[red]{_usage_line()}[/red]")
        return
    client.reasoning_effort = None if level == "off" else level
    _print_set(console, client)


def _print_set(console: Console, client, *, cycled: bool = False) -> None:
    verb = "cycled to" if cycled else "set to"
    level = client.reasoning_effort
    if level is None:
        console.print(
            f"[green]Reasoning effort {verb} off[/green] (no reasoning_effort field sent)"
        )
    else:
        console.print(f"[green]Reasoning effort {verb} {level}[/green]")
    # The client stores the level on every provider that has the field, but the
    # request builder only forwards it for whitelisted backends. Reporting a
    # successful switch while the field is silently dropped is a lie the user
    # pays for in tokens and latency, so say it plainly.
    if level is not None and not effort_reaches_wire(client):
        console.print(
            f"[yellow]Not sent:[/yellow] provider {client.provider_name} does not forward "
            "reasoning_effort (only openai / openai-compatible do). The level is stored but "
            "has no effect on requests."
        )
    console.print("[dim]Live for this client; a /model switch resets it.[/dim]")
