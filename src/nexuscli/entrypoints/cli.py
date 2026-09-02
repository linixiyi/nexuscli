"""cli.py — NexusCLI terminal AI agent CLI entry point.

Inspired by Claude Code: a terminal-native AI agent that understands your
workspace, executes commands, edits files, and answers questions — all from
the command line.

Usage modes::

    # Interactive REPL (default)
    nexuscli
    nexuscli --api-key sk-xxx
    nexuscli --model deepseek-chat --provider deepseek

    # Single-shot prompt
    nexuscli -p "list all Python files"
    nexuscli -p "refactor this module" --mode plan

    # Sessions
    nexuscli sessions              # list saved conversations
    nexuscli -c                    # continue the most recent session (REPL)
    nexuscli --resume <id>         # resume a specific session (REPL)

    # Tooling
    nexuscli doctor              # system health check
    nexuscli serve               # Runtime HTTP API
    nexuscli mcp serve           # Expose tools via MCP
    nexuscli mcp init-chrome     # Configure Chrome DevTools MCP
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from nexuscli import __version__
from nexuscli.agent import QueryEngine
from nexuscli.bootstrap import build_tool_registry
from nexuscli.config import get_config_paths, load_config
from nexuscli.entrypoints.repl import start_repl
from nexuscli.entrypoints.slash_commands import (
    expand_custom_command,
    load_slash_commands,
    split_command_message,
)
from nexuscli.llm import create_llm_client
from nexuscli.mcp import (
    load_mcp_server_specs,
    serve_http,
    serve_stdio,
    write_chrome_devtools_config,
)
from nexuscli.runtime import RuntimeApiServer
from nexuscli.runtime.api import runtime_api_key
from nexuscli.session import SessionStore

app = typer.Typer(
    name="nexuscli",
    help="NexusCLI — Terminal AI Agent in Python (Claude Code alternative)",
    invoke_without_command=True,
    no_args_is_help=False,
)
mcp_app = typer.Typer(help="MCP server management")
app.add_typer(mcp_app, name="mcp")
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"nexuscli {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    # --- Interaction mode ---
    prompt: Annotated[
        str | None,
        typer.Option("-p", "--prompt", help="Single prompt (non-interactive mode)"),
    ] = None,
    # --- LLM configuration ---
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="LLM API key (overrides env/config)"),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("-m", "--model", help="Override LLM model name"),
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Override LLM provider (e.g. deepseek, glm)"),
    ] = None,
    base_url: Annotated[
        str | None,
        typer.Option("--base-url", help="Override LLM API base URL"),
    ] = None,
    # --- Rendering ---
    plain: Annotated[
        bool,
        typer.Option("--plain", help="Use plain text rendering (no rich formatting)"),
    ] = False,
    # --- Agent mode ---
    mode: Annotated[
        str | None,
        typer.Option("--mode", help="Agent mode: react, plan, or team"),
    ] = None,
    worker_mode: Annotated[
        str,
        typer.Option("--worker-mode", help="Sub-Agent worker mode in team runs: react or plan"),
    ] = "react",
    # --- Output ---
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit result, usage, and cost as JSON (single-prompt only)"),
    ] = False,
    # --- Sessions ---
    resume: Annotated[
        str | None,
        typer.Option("--resume", help="Resume a saved session by id (see `nexuscli sessions`)"),
    ] = None,
    continue_session: Annotated[
        bool,
        typer.Option(
            "-c",
            "--continue",
            help="Resume the most recent session in this project",
        ),
    ] = False,
    # --- Workspace ---
    cwd: Annotated[
        Path | None,
        typer.Option("--cwd", help="Working directory (default: current dir)"),
    ] = None,
    # --- Version ---
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version"),
    ] = False,
) -> None:
    """NexusCLI — a terminal AI agent that works in your workspace.

    Start an interactive session (default), run a single prompt with -p,
    or use the subcommands for diagnostics, serving, and MCP management.
    """
    _ = version
    if ctx.invoked_subcommand is not None:
        return

    root = (cwd or Path.cwd()).resolve()

    # Build overrides dict from all explicit CLI flags.
    overrides: dict = {}
    llm_overrides: dict = {}
    if api_key is not None:
        llm_overrides["api_key"] = api_key
    if provider is not None:
        llm_overrides["provider"] = provider
    if model is not None:
        llm_overrides["model"] = model
    if base_url is not None:
        llm_overrides["base_url"] = base_url
    if llm_overrides:
        overrides["llm"] = llm_overrides
    if plain:
        overrides["render_mode"] = "plain"

    config = load_config(project_root=root, overrides=overrides)
    if plain:
        config.render_mode = "plain"

    if prompt is not None:
        selected_mode = (mode or config.prompt.agent_mode or "react").lower()
        if selected_mode not in {"react", "plan", "team"}:
            raise typer.BadParameter("mode must be react, plan, or team", param_hint="--mode")
        if worker_mode not in {"react", "plan"}:
            raise typer.BadParameter(
                "worker-mode must be react or plan", param_hint="--worker-mode"
            )
        prompt = _expand_custom_prompt(prompt, str(root))
        history = _load_resume_history(str(root), resume=resume, continue_last=continue_session)
        if history and selected_mode != "react":
            typer.echo("Note: --resume/--continue only restores react mode history.", err=True)
            history = []
        asyncio.run(
            _run_prompt(
                prompt,
                str(root),
                config,
                mode=selected_mode,
                worker_mode=worker_mode,
                json_output=json_output,
                history=history,
            )
        )
    elif resume or continue_session:
        asyncio.run(start_repl(str(root), config, resume=resume, continue_last=continue_session))
    else:
        asyncio.run(start_repl(str(root), config))


def _expand_custom_prompt(prompt: str, root: str) -> str:
    """Expand a custom slash command in single-prompt mode, when one matches."""
    parsed = split_command_message(prompt)
    if parsed is None:
        return prompt
    name, args = parsed
    command = load_slash_commands(root).get(name.lstrip("/"))
    if command is None:
        return prompt
    return expand_custom_command(command, args)


def _load_resume_history(
    root: str,
    *,
    resume: str | None,
    continue_last: bool,
) -> list:
    """Resolve a session transcript for resume; empty list when nothing to load."""
    if not resume and not continue_last:
        return []
    store = SessionStore()
    record = None
    if resume:
        record = store.resolve(resume, cwd=root)
        if record is None:
            typer.echo(f"Session not found: {resume}", err=True)
            raise typer.Exit(1)
    else:
        recent = store.list(limit=1, cwd=root)
        if not recent:
            typer.echo("No previous session to continue in this project.", err=True)
            raise typer.Exit(1)
        record = store.load(recent[0].id)
    if record is None:
        typer.echo("Session transcript is unreadable.", err=True)
        raise typer.Exit(1)
    typer.echo(
        f"Resuming session {record.meta.id} ({len(record.messages)} messages): "
        f"{record.meta.title or '(untitled)'}",
        err=True,
    )
    return record.messages


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


@app.command("doctor")
def doctor(
    cwd: Annotated[Path | None, typer.Option("--cwd", help="Working directory")] = None,
) -> None:
    """Inspect the system: Python, uv, Node, API key, and config."""
    root = (cwd or Path.cwd()).resolve()
    config = load_config(project_root=root)
    checks = {
        "python": sys.version.split()[0],
        "uv": shutil.which("uv") or "missing",
        "node": _version_of("node"),
        "npx": shutil.which("npx") or "missing",
        "rg": shutil.which("rg") or "missing",
        "api_key": "configured" if config.llm.api_key else "missing",
        "provider": config.llm.provider,
        "model": config.llm.model,
        "cwd": str(root),
        "config_paths": [str(path) for path in get_config_paths(root)],
    }
    console.print_json(json.dumps(checks, ensure_ascii=False))


@app.command("sessions")
def sessions_list(
    cwd: Annotated[Path | None, typer.Option("--cwd", help="Working directory")] = None,
    all_projects: Annotated[
        bool, typer.Option("--all", help="List sessions from every project")
    ] = False,
    limit: Annotated[int, typer.Option("--limit", help="Maximum sessions to show")] = 20,
) -> None:
    """List saved conversation sessions (newest first)."""
    root = str((cwd or Path.cwd()).resolve())
    store = SessionStore()
    metas = store.list(limit=limit, cwd=None if all_projects else root)
    if not metas:
        typer.echo("No saved sessions." if all_projects else f"No saved sessions in {root}")
        return
    for index, meta in enumerate(metas, 1):
        updated = time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.updated_at))
        title = meta.title or "(untitled)"
        typer.echo(f"{index}\t{meta.id}\t{meta.message_count} msgs\t{updated}\t{title}")


@app.command("serve")
def runtime_serve(
    http: Annotated[bool, typer.Option("--http", help="Serve Runtime API over HTTP")] = True,
    port: Annotated[int, typer.Option("--port", help="HTTP port")] = 8080,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="Runtime API key. Defaults to NEXUSCLI_RUNTIME_API_KEY."),
    ] = None,
    cwd: Annotated[Path | None, typer.Option("--cwd", help="Working directory")] = None,
) -> None:
    """Start the durable task runtime server (HTTP API)."""
    _ = http
    root = (cwd or Path.cwd()).resolve()
    config = load_config(project_root=root)
    try:
        key = runtime_api_key(api_key)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    RuntimeApiServer(cwd=str(root), config=config, api_key=key, port=port).serve_forever()


@app.command("worker")
def task_worker(
    workers: Annotated[int, typer.Option("--workers", help="Concurrent task workers")] = 2,
    cwd: Annotated[Path | None, typer.Option("--cwd", help="Working directory")] = None,
) -> None:
    """Consume durable background tasks without the HTTP API server."""
    if workers < 1:
        raise typer.BadParameter("workers must be at least 1")
    root = (cwd or Path.cwd()).resolve()
    config = load_config(project_root=root)
    RuntimeApiServer(
        cwd=str(root),
        config=config,
        api_key="worker-only-not-exposed",
        workers=workers,
    ).work_forever()


# ---------------------------------------------------------------------------
# MCP subcommands
# ---------------------------------------------------------------------------


@mcp_app.command("serve")
def mcp_serve(
    transport: Annotated[
        str,
        typer.Option("--transport", help="Transport type: stdio or http"),
    ] = "stdio",
    port: Annotated[int, typer.Option("--port", help="HTTP port")] = 3000,
    cwd: Annotated[Path | None, typer.Option("--cwd", help="Working directory")] = None,
) -> None:
    """Expose NexusCLI tools via the Model Context Protocol."""
    root = str((cwd or Path.cwd()).resolve())
    if transport == "http":
        serve_http(port=port, cwd=root)
    elif transport == "stdio":
        asyncio.run(serve_stdio(cwd=root))
    else:
        raise typer.BadParameter("transport must be stdio or http")


@mcp_app.command("init-chrome")
def mcp_init_chrome(
    scope: Annotated[
        str,
        typer.Option("--scope", help="Config scope: user or project"),
    ] = "project",
    cwd: Annotated[Path | None, typer.Option("--cwd", help="Working directory")] = None,
    browser_url: Annotated[
        str | None,
        typer.Option("--browser-url", help="Connect to an existing Chrome remote debugging URL"),
    ] = None,
    headless: Annotated[bool, typer.Option("--headless", help="Start Chrome headless")] = False,
    slim: Annotated[bool, typer.Option("--slim", help="Use Chrome DevTools slim mode")] = False,
) -> None:
    """Write Chrome DevTools MCP config to the project or user scope."""
    if scope not in {"user", "project"}:
        raise typer.BadParameter("scope must be user or project")
    root = None if scope == "user" else (cwd or Path.cwd()).resolve()
    path = write_chrome_devtools_config(
        scope_root=root,
        browser_url=browser_url,
        headless=headless,
        slim=slim,
    )
    typer.echo(f"Wrote Chrome DevTools MCP config to {path}")


@mcp_app.command("list")
def mcp_list(
    cwd: Annotated[Path | None, typer.Option("--cwd", help="Working directory")] = None,
) -> None:
    """List configured MCP servers."""
    root = (cwd or Path.cwd()).resolve()
    specs = load_mcp_server_specs(root)
    if not specs:
        typer.echo("No MCP servers configured.")
        return
    for spec in specs.values():
        target = spec.url or f"{spec.command} {' '.join(spec.args)}".strip()
        typer.echo(f"{spec.name}\t{spec.type}\t{target}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _run_prompt(
    prompt: str,
    cwd: str,
    config,
    *,
    mode: str = "react",
    worker_mode: str = "react",
    json_output: bool = False,
    history: list | None = None,
) -> None:
    """Execute a single prompt and print the result."""
    config.render_mode = "plain"
    if not config.llm.api_key:
        typer.echo(
            "Fatal error: LLM API key is not configured. Set it with --api-key, "
            "via the NEXUSCLI_API_KEY environment variable, or in "
            "~/.nexuscli/config.json or .nexuscli/config.json.",
            err=True,
        )
        raise typer.Exit(1)
    registry, manager = await build_tool_registry(config=config, cwd=cwd)
    if manager and manager.last_errors:
        for name, error in manager.last_errors.items():
            typer.echo(f"MCP server {name} failed to load: {error}", err=True)
    engine = QueryEngine(
        llm_client=create_llm_client(config.llm),
        tool_registry=registry,
        config=config,
        cwd=cwd,
    )
    try:
        if mode == "plan":
            result = await engine.plan_complete_async(prompt)
        elif mode == "team":
            result = await engine.team_complete_async(prompt, worker_mode=worker_mode)
        else:
            result = await engine.ask_complete_async(prompt, history=history)
    except Exception as exc:  # noqa: BLE001 - CLI should report model/config errors cleanly
        typer.echo(f"Fatal error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "text": result.text,
                    "mode": mode,
                    "worker_mode": worker_mode if mode == "team" else None,
                    "turns": result.turns,
                    "total_tokens": result.total_tokens,
                    "usage": result.usage.to_dict(),
                    "cost": result.cost,
                },
                ensure_ascii=False,
            )
        )
    else:
        typer.echo(result.text)


def _version_of(command: str) -> str:
    if not shutil.which(command):
        return "missing"
    try:
        result = subprocess.run(
            [command, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        return "unknown"
    return (result.stdout or result.stderr).strip() or "unknown"
