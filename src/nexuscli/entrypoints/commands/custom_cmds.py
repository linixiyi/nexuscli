"""custom_cmds.py — User-defined slash commands: matching, whitelist narrowing, gated run."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from nexuscli.agent import Agent
from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.commands.run import _run_agent
from nexuscli.entrypoints.slash_commands import (
    CommandExpansion,
    CustomCommand,
    expand_command,
    split_command_message,
)
from nexuscli.render import RichRenderer
from nexuscli.tools import ToolRegistry

if TYPE_CHECKING:
    # Runtime import would be circular: repl imports this package for its
    # handlers while owning the permission controller. Annotations are
    # strings (PEP 563), so this is a typing-only edge.
    from nexuscli.entrypoints.repl import PermissionModeController


async def _run_custom_command(
    agent: Agent,
    renderer: RichRenderer,
    console: Console,
    permission_mode: PermissionModeController,
    expansion: CommandExpansion,
) -> None:
    """Run an expanded custom command with its frontmatter hints applied.

    ``mode: plan`` switches the permission controller into plan mode so the
    executor's read-only gate covers the whole run; ``react`` / ``team``
    switch the agent runner mode instead. ``allowed-tools`` temporarily
    narrows the live registry to the whitelist. Every override is restored
    in ``finally`` so a failed run cannot leak session state.
    """
    original_agent_mode = agent.mode
    original_permission_mode = permission_mode.mode
    original_registry = agent.tool_registry
    for marker in expansion.rejected_injections:
        console.print(f"[yellow]Refused injection:[/yellow] {marker}")
    switched_permission_mode = False
    try:
        if expansion.mode == "plan":
            permission_mode.set("plan")
            switched_permission_mode = True
        elif expansion.mode:
            agent.mode = expansion.mode  # type: ignore[assignment]
        if expansion.allowed_tools:
            agent.tool_registry = _narrow_registry(original_registry, expansion.allowed_tools)
        await _run_agent(agent, renderer, expansion.prompt)
    finally:
        if switched_permission_mode:
            permission_mode.set(original_permission_mode)
        agent.mode = original_agent_mode
        agent.tool_registry = original_registry


def _narrow_registry(
    registry: ToolRegistry,
    allowed_tools: tuple[str, ...],
) -> ToolRegistry:
    """Build a registry holding only the whitelisted tools of *registry*.

    Mirrors the subagent whitelist filtering: unknown names are dropped
    silently, and an empty result simply means every call misses the registry.
    """
    narrowed = ToolRegistry()
    for name in allowed_tools:
        tool = registry.get(name)
        if tool is not None:
            narrowed.register(tool)
    return narrowed


async def _match_custom_command(
    message: str,
    custom_commands: dict[str, CustomCommand],
    config: NexusCliConfig,
) -> tuple[CustomCommand, CommandExpansion] | None:
    """Return ``(command, expansion)`` when *message* names a custom command."""
    parsed = split_command_message(message)
    if parsed is None:
        return None
    name, args = parsed
    command = custom_commands.get(name.lstrip("/"))
    if command is None:
        return None
    return command, await expand_command(command, args, config)
