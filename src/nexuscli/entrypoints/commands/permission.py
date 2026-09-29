"""permission.py — /hitl permission-mode switch and the shared human-readable mode label."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    # Runtime import would be circular: repl imports this package for its
    # handlers while owning the permission-mode types. Annotations are
    # strings (PEP 563), so this is a typing-only edge.
    from nexuscli.entrypoints.repl import PermissionMode, PermissionModeController


def _hitl_command(
    arg: str,
    console: Console,
    permission_mode: PermissionModeController,
) -> None:
    aliases: dict[str, PermissionMode] = {
        "default": "default",
        "on": "default",
        "auto": "auto",
        "off": "auto",
    }
    if arg in aliases:
        permission_mode.set(aliases[arg])
    elif arg:
        console.print("[red]Usage:[/red] /hitl default|auto")
        return
    console.print(f"Permission mode: {_permission_mode_label(permission_mode.mode)}")


def _permission_mode_label(mode: PermissionMode) -> str:
    if mode == "auto":
        return "Auto (full access)"
    if mode == "plan":
        return "plan (read-only)"
    return "Default"
