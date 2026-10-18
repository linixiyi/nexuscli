"""approval.py — Interactive tool-approval prompt backing the agent's approval callback."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.prompt import Prompt

from nexuscli.entrypoints.commands.approval_keys import APPROVAL_HELP, interpret_approval_answer

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
    console.print(f"[dim]{APPROVAL_HELP}[/dim]")
    # rich.prompt.Prompt re-asks forever on unknown input and ignores Esc, so a
    # stray keystroke (/usage, /exit, a paste) trapped the user in a loop with
    # no exit but two Ctrl+C presses, the second of which killed the session.
    # Ask with a plain key read instead: Esc / Ctrl+C / Ctrl+D deny immediately,
    # anything unrecognised is reported once and asked again with the options
    # restated, and the answer is interpreted in one tested place.
    while True:
        try:
            raw = Prompt.ask("Approve? (y / r / a / s / n, Esc denies)", default="n")
        except (EOFError, KeyboardInterrupt):
            console.print("[yellow]Denied (no answer).[/yellow]")
            return "deny"
        decision = interpret_approval_answer(raw)
        if decision is not None:
            if decision == "approve_auto":
                permission_mode.set("auto")
                return "approve"
            return decision
        console.print(
            f"[yellow]Not an option:[/yellow] {raw.strip()!r}. {APPROVAL_HELP}",
        )
