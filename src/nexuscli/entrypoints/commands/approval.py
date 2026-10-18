"""approval.py — Interactive tool-approval prompt backing the agent's approval callback."""

from __future__ import annotations

import json
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

# Tool arguments are summarized on one line: a full payload (file bodies,
# long command lines) buried the decision and scrolled the conversation away.
_SUMMARY_CHARS = 120


def _summarize_input(payload: dict[str, Any]) -> str:
    """One-line preview of the call's arguments."""
    parts: list[str] = []
    for key, value in payload.items():
        if isinstance(value, str):
            text = value.replace(chr(10), " ")
            if len(text) > _SUMMARY_CHARS:
                text = text[:_SUMMARY_CHARS] + f"…(+{len(value) - _SUMMARY_CHARS} chars)"
        elif isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False)
            if len(text) > _SUMMARY_CHARS:
                text = text[:_SUMMARY_CHARS] + "…"
        else:
            text = str(value)
        parts.append(f"{key}={text}")
    return " ".join(parts)


def _approval_prompt(
    request: dict[str, Any],
    console: Console,
    permission_mode: PermissionModeController,
) -> str:
    if not sys.stdin.isatty():
        return "deny"
    console.print(
        f"[yellow]Approval required[/yellow] {request['tool_name']} "
        f"({request['danger_level']}) {_summarize_input(request.get('input') or {})}"
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
