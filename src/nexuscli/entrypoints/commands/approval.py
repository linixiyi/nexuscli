"""approval.py — Interactive tool-approval prompt backing the agent's approval callback."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.prompt import Prompt

if TYPE_CHECKING:
    # Runtime import would be circular: repl imports this package for its
    # handlers while owning the permission controller. Annotations are
    # strings (PEP 563), so this is a typing-only edge.
    from nexuscli.entrypoints.repl import PermissionModeController


def _approval_prompt(
    request: dict[str, Any],
    console: Console,
    permission_mode: PermissionModeController,
) -> str:
    if not sys.stdin.isatty():
        return "deny"
    console.print(
        f"[yellow]Approval required[/yellow] {request['tool_name']} "
        f"({request['danger_level']})\n{request['input']}"
    )
    console.print(
        "[dim]y approve once · r approve this tool for the rest of this run "
        "(subagents prompt separately) · a switch to auto mode · s skip · "
        "n deny[/dim]"
    )
    answer = Prompt.ask("Approve?", choices=["y", "n", "r", "a", "s"], default="n")
    if answer == "a":
        permission_mode.set("auto")
        return "approve"
    if answer == "r":
        # Run-scoped grant: the executor records the tool name so later calls
        # to the same tool skip the prompt until this agent run ends.
        return "approve_run"
    if answer == "y":
        return "approve"
    if answer == "s":
        return "skip"
    return "deny"
