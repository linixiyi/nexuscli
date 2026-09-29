"""hooks_cmds.py — REPL-side hook lifecycle: workspace trust gate and SessionStart firing."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt

from nexuscli.config import NexusCliConfig, user_level_hooks
from nexuscli.hooks import fire_event, has_hooks, trust


async def _gate_workspace_hooks(
    config: NexusCliConfig,
    cwd: str,
    console: Console,
    confirm: Callable[[str], bool] | None = None,
) -> None:
    """Require one-time sha256 trust before project-layer hooks take effect.

    Project (workspace) hooks are merged into ``config.hooks`` at load time
    with no confirmation, so cloning a repository could otherwise run
    arbitrary shell commands on every tool event. This gate fingerprints the
    project layer's ``hooks`` section, asks once per workspace+fingerprint
    (an already-trusted fingerprint skips the prompt), and on refusal replaces
    ``config.hooks`` with the user-level hooks only — user-level hooks are
    never affected. The ``confirm`` parameter is the injection seam for tests
    (``confirm=lambda description: True/False``); the default implementation
    refuses in non-interactive environments and prompts on a TTY.

    Known boundary: only the interactive REPL path is gated here; the one-shot
    execution entrypoint (cli.py) and serve are out of scope for this task.
    """
    section = trust.workspace_hooks_section(cwd)
    if not section:
        return
    fingerprint = trust.hooks_fingerprint(section)
    store = trust.HookTrustStore()
    workspace = str(Path(cwd).resolve())
    if store.is_trusted(workspace, fingerprint):
        return
    description = f"{workspace} (fingerprint {fingerprint[:16]})"
    if confirm is not None:
        trusted = confirm(description)
    elif not sys.stdin.isatty():
        # Non-interactive: refuse conservatively, same posture as _approval_prompt.
        trusted = False
    else:
        console.print(f"[yellow]Workspace hooks detected[/yellow] {description}")
        trusted = Prompt.ask("Trust workspace hooks?", choices=["y", "n"], default="n") == "y"
    if trusted:
        store.trust(workspace, fingerprint)
        console.print("[green]Workspace hooks trusted (remembered for this workspace).[/green]")
    else:
        # The merged config is user matchers first, project matchers appended;
        # swapping in the user-level parse strips exactly the project layer.
        config.hooks = user_level_hooks()
        console.print(
            "[yellow]Workspace hooks disabled for this session "
            "(user-level hooks unaffected).[/yellow]"
        )


async def _fire_session_start_hooks(
    config: NexusCliConfig,
    console: Console,
    cwd: str,
) -> None:
    """Fire SessionStart hooks once before the prompt loop (repl-side trigger).

    SessionStart hooks cannot block the session, so only their errors and
    additional context are surfaced. Unconfigured events are a no-op.
    """
    if not has_hooks(config, "SessionStart"):
        return
    outcome = await fire_event(config, "SessionStart", {}, cwd)
    for error in outcome.errors:
        console.print(f"[yellow]Hook error:[/yellow] {error}")
    if outcome.additional_context:
        console.print(outcome.additional_context)
