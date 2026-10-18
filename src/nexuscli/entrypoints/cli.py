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
import errno
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
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
    build_init_prompt,
    expand_custom_command,
    load_slash_commands,
    split_command_message,
)
from nexuscli.eval import (
    EvalSchemaError,
    PreviousReportError,
    build_report,
    builtin_eval_dir,
    format_report,
    load_eval_scenarios,
    load_previous_report,
    run_eval_scenarios,
    write_report,
)
from nexuscli.llm import create_llm_client
from nexuscli.mcp import (
    McpConfigError,
    read_mcp_config_entries,
    serve_http,
    serve_stdio,
    write_chrome_devtools_config,
)
from nexuscli.runtime import RuntimeApiServer
from nexuscli.runtime.api import runtime_api_key
from nexuscli.runtime.cron import CronError, cron_tasks_path, list_tasks, next_trigger, run_task
from nexuscli.session import SessionStore, SessionUsageTotals

app = typer.Typer(
    name="nexuscli",
    help="NexusCLI — Terminal AI Agent in Python (Claude Code alternative)",
    invoke_without_command=True,
    no_args_is_help=False,
)
mcp_app = typer.Typer(help="MCP server management")
app.add_typer(mcp_app, name="mcp")
cron_app = typer.Typer(help="Scheduled task management")
app.add_typer(cron_app, name="cron")
eval_app = typer.Typer(help="Offline scripted eval regression")
app.add_typer(eval_app, name="eval")
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
    # --- Non-interactive approval ---
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Single-prompt only: approve every tool call that would ask (non-interactive)",
        ),
    ] = False,
    no_approve: Annotated[
        bool,
        typer.Option(
            "--no-approve",
            help="Single-prompt only: refuse every tool call that would ask (the default)",
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

    if yes and no_approve:
        raise typer.BadParameter("--yes and --no-approve are mutually exclusive")

    if prompt is not None:
        selected_mode = (mode or config.prompt.agent_mode or "react").lower()
        if selected_mode not in {"react", "plan", "team"}:
            raise typer.BadParameter("mode must be react, plan, or team", param_hint="--mode")
        if worker_mode not in {"react", "plan"}:
            raise typer.BadParameter(
                "worker-mode must be react or plan", param_hint="--worker-mode"
            )
        # --mode plan selects the Plan-and-Execute AGENT, which is not the same
        # thing as the REPL's read-only plan gate. Leaving policy.plan_mode
        # False meant '-p --mode plan' happily wrote files while README offered
        # it as the "read a bit, then verify" example — two ideas sharing one
        # name. Turn the gate on so the advertised guarantee holds; the flag is
        # reset for react/team so it never leaks into those runs.
        config.policy.plan_mode = selected_mode == "plan"
        prompt = _expand_custom_prompt(prompt, str(root))
        history, usage_baseline = _load_resume_history(
            str(root), resume=resume, continue_last=continue_session
        )
        if history and selected_mode != "react":
            typer.echo("Note: --resume/--continue only restores react mode history.", err=True)
            history = []
            # The run did not continue that transcript, so its usage records
            # are not part of this run's session ledger either.
            usage_baseline = SessionUsageTotals()
        asyncio.run(
            _run_prompt(
                prompt,
                str(root),
                config,
                mode=selected_mode,
                worker_mode=worker_mode,
                json_output=json_output,
                history=history,
                usage_baseline=usage_baseline,
                approve_all=yes,
            )
        )
    elif resume or continue_session:
        asyncio.run(start_repl(str(root), config, resume=resume, continue_last=continue_session))
    else:
        asyncio.run(start_repl(str(root), config))


def _expand_custom_prompt(prompt: str, root: str) -> str:
    """Expand a custom slash command (or built-in ``/init``) in one-shot mode.

    Custom commands win on name conflicts: a user/project ``init`` command is
    looked up and expanded first, and the built-in initialization prompt is
    only used when no custom command matched. Any other unmatched ``/name``
    is returned unchanged as a plain prompt.
    """
    parsed = split_command_message(prompt)
    if parsed is None:
        return prompt
    name, args = parsed
    command = load_slash_commands(root).get(name.lstrip("/"))
    if command is None:
        if name.lstrip("/") == "init":
            return build_init_prompt(args, root)
        return prompt
    return expand_custom_command(command, args)


def _load_resume_history(
    root: str,
    *,
    resume: str | None,
    continue_last: bool,
) -> tuple[list, SessionUsageTotals]:
    """Resolve a session transcript for resume; empty list when nothing to load.

    Also returns the transcript's historical usage baseline (its ``usage``
    record lines) so single-prompt ``--json`` can report the same dual scope
    as the REPL's ``/usage``: resumed baseline plus this run.
    """
    if not resume and not continue_last:
        return [], SessionUsageTotals()
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
    return record.messages, store.load_usage(record.meta.id)


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
    server = RuntimeApiServer(cwd=str(root), config=config, api_key=key, port=port)
    _serve_or_exit(server.serve_forever, port)


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
        _serve_or_exit(lambda: serve_http(port=port, cwd=root), port)
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
    """List configured MCP servers, including the disabled ones."""
    root = (cwd or Path.cwd()).resolve()
    try:
        specs, skipped = read_mcp_config_entries(root)
    except McpConfigError as exc:
        # A corrupt mcp.json used to print "No MCP servers configured.", which
        # sends the user looking for a missing file instead of the broken one.
        typer.echo(f"Invalid MCP config: {exc}", err=True)
        raise typer.Exit(1) from exc
    for entry in skipped:
        typer.echo(f"[skip] {entry}", err=True)
    if not specs:
        typer.echo("No MCP servers configured.")
        return
    for spec in specs.values():
        target = spec.url or f"{spec.command} {' '.join(spec.args)}".strip()
        state = "enabled" if spec.enabled else "disabled"
        typer.echo(f"{spec.name}\t{spec.type}\t{state}\t{target}")


# ---------------------------------------------------------------------------
# Cron subcommands
# ---------------------------------------------------------------------------


@cron_app.command("list")
def cron_list() -> None:
    """List scheduled cron tasks."""
    tasks = list_tasks(cron_tasks_path())
    if not tasks:
        typer.echo("No cron tasks.")
        return
    for task in tasks:
        state = "enabled" if task.enabled else "disabled"
        typer.echo(f"{task.id}\t{task.cron}\t{state}\t{task.prompt}")


@cron_app.command("next")
def cron_next() -> None:
    """Show the next trigger time of every cron task (local time)."""
    for task, upcoming in next_trigger(cron_tasks_path()):
        if not task.enabled:
            # Disabled tasks never fire; showing a time made external
            # schedulers queue work that ``cron run`` then refuses.
            typer.echo(f"[disabled] {task.id}")
            continue
        if upcoming is None:
            typer.echo(f"[warn] invalid cron expression: {task.cron}")
            continue
        typer.echo(f"{task.id}\t{upcoming.strftime('%Y-%m-%d %H:%M')}")


@cron_app.command("run")
def cron_run(
    task_id: Annotated[str, typer.Argument(help="Task id")],
) -> None:
    """Print a task's prompt on one line, ready for ``nexuscli -p "$(...)"``.

    Offline form — nothing is executed here. Windows Task Scheduler / system
    cron invokes this command at the scheduled time and feeds the printed
    prompt to ``nexuscli -p`` for the actual run. Newlines inside the prompt
    are folded to spaces: ``-p`` takes its prompt as an argument and does not
    read stdin, so a multi-line prompt cannot be piped into it.
    """
    try:
        prompt = run_task(cron_tasks_path(), task_id)
    except CronError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(_single_line(prompt))


# ---------------------------------------------------------------------------
# Eval subcommands (offline scripted regression)
# ---------------------------------------------------------------------------


@eval_app.command("list")
def eval_list() -> None:
    """List the built-in eval scenarios (examples/evals)."""
    eval_dir = builtin_eval_dir()
    if eval_dir is None:
        typer.echo("No eval scenarios found (examples/evals is missing).", err=True)
        raise typer.Exit(1)
    try:
        scenarios = load_eval_scenarios(eval_dir)
    except EvalSchemaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if not scenarios:
        typer.echo(f"No eval scenarios in {eval_dir}")
        return
    typer.echo(f"Scenarios in {eval_dir}:")
    for scenario in scenarios:
        typer.echo(f"{scenario.name}\t{len(scenario.turns)} turns\t{scenario.description}")


@eval_app.command("run")
def eval_run(
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            help="Run one scenario file or a directory of scenarios instead of the built-ins",
        ),
    ] = None,
    all_scenarios: Annotated[
        bool, typer.Option("--all", help="Run every built-in scenario (the default)")
    ] = False,
    cwd: Annotated[Path | None, typer.Option("--cwd", help="Working directory")] = None,
) -> None:
    """Run scripted eval scenarios offline and diff against the previous report.

    The report is written to ``<cwd>/.nexuscli/evals/last-report.json``; the
    next run reads it back and marks pass->fail as regressions and fail->pass
    as fixes. Exits 1 when any scenario fails.
    """
    _ = all_scenarios  # --all is the default behaviour; kept as an explicit flag
    root = (cwd or Path.cwd()).resolve()
    target: Path
    if file is not None:
        target = file
    else:
        builtin = builtin_eval_dir()
        if builtin is None:
            typer.echo("No eval scenarios found (examples/evals is missing).", err=True)
            raise typer.Exit(1)
        target = builtin
    try:
        scenarios = load_eval_scenarios(target)
    except EvalSchemaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if not scenarios:
        typer.echo(f"No eval scenarios in {target}", err=True)
        raise typer.Exit(1)
    results = run_eval_scenarios(scenarios, cwd=str(root))
    try:
        previous = load_previous_report(root)
    except PreviousReportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    report = build_report(results, previous=previous)
    report_path = write_report(root, report)
    for line in format_report(report):
        typer.echo(line)
    typer.echo(f"Report written to {report_path}")
    if report["summary"]["failed"]:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _ask_streaming(
    engine: QueryEngine,
    prompt: str,
    *,
    history: list | None,
    stream_deltas: bool,
):
    """Run react mode over the engine's live event stream.

    Text deltas are written straight to stdout (write + flush) as they arrive,
    so the answer shows up while the model is still generating instead of a
    long silent wait. With ``stream_deltas`` off (``--json`` reserves stdout
    for the single payload) events are consumed silently. Aggregation is not
    reimplemented: the ``QueryResult`` comes from the engine's existing event
    aggregation helper, keeping text/turns/usage/cost plus warning/error
    handling identical to the buffered ``ask_complete_async`` path.
    """

    async def _tee(events):
        async for event in events:
            if stream_deltas and event.get("type") == "text_delta":
                delta = str(event.get("text") or "")
                sys.stdout.write(delta)
                sys.stdout.flush()
            yield event

    return await engine._complete_from_events(_tee(engine.ask(prompt, history)))


def _auto_approver(request: dict) -> str:
    """The --yes approval callback: approve without asking a human.

    Only reached for calls the permission chain already let through to the
    approval step, so deny rules, ask rules and hook blocks keep their
    priority. The executor audits these as hitl; the CLI warns at startup so
    the choice is visible in the transcript.
    """
    _ = request
    return "approve"


async def _run_prompt(
    prompt: str,
    cwd: str,
    config,
    *,
    mode: str = "react",
    worker_mode: str = "react",
    json_output: bool = False,
    history: list | None = None,
    usage_baseline: SessionUsageTotals | None = None,
    approve_all: bool = False,
) -> None:
    """Execute a single prompt and print the result.

    approve_all is the --yes switch: without an approval callback every tool
    that requires one is refused (the executor fails closed), which made -p
    useless for scripted work that writes files or runs commands. The default
    stays "deny"; --yes says out loud that nobody will be asked.
    """
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
        llm_client=create_llm_client(config.llm, telemetry_enabled=config.telemetry.enabled),
        tool_registry=registry,
        config=config,
        cwd=cwd,
        approval_callback=_auto_approver if approve_all else None,
    )
    try:
        if mode == "plan":
            result = await engine.plan_complete_async(prompt)
        elif mode == "team":
            result = await engine.team_complete_async(prompt, worker_mode=worker_mode)
        else:
            result = await _ask_streaming(
                engine,
                prompt,
                history=history,
                stream_deltas=not json_output,
            )
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
                    "session_usage": _single_shot_session_usage(result, usage_baseline),
                },
                ensure_ascii=False,
            )
        )
    elif mode == "react":
        # The answer text was already echoed incrementally; only the trailing
        # newline that typer.echo(result.text) used to add is still missing.
        sys.stdout.write("\n")
        sys.stdout.flush()
    else:
        typer.echo(result.text)


def _single_shot_session_usage(result, usage_baseline: SessionUsageTotals | None) -> dict:
    """Dual-scope session usage for single-prompt ``--json``.

    Mirrors the REPL ``/usage`` JSON: ``historical_total`` is the resumed
    transcript's baseline plus this run's usage; ``current_process`` is this
    run only. One caveat stated in ``sources``: a single-prompt run does not
    append its usage to the transcript, so the baseline here is informational
    and is not persisted back.
    """
    this_run = SessionUsageTotals.from_turn(usage=result.usage, cost=result.cost)
    baseline = usage_baseline if usage_baseline is not None else SessionUsageTotals()
    return {
        "historical_total": (baseline + this_run).to_dict(),
        "current_process": this_run.to_dict(),
        "sources": {
            "historical_total": "resumed session usage baseline + this run (not persisted)",
            "current_process": "this run only",
        },
    }


def _serve_or_exit(serve: Callable[[], None], port: int) -> None:
    """Run a blocking server, turning a busy port into a readable error.

    Binding used to escape as a raw ``OSError: [Errno 48] Address already in
    use`` traceback, which says nothing about which port or what to do next.
    """
    try:
        serve()
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            typer.echo(f"Port {port} is already in use. Try --port <other>.", err=True)
            raise typer.Exit(1) from exc
        if exc.errno == errno.EACCES:
            typer.echo(
                f"Port {port} cannot be bound (permission denied). "
                "Ports below 1024 need elevated privileges; try --port <other>.",
                err=True,
            )
            raise typer.Exit(1) from exc
        typer.echo(f"Cannot start the server on port {port}: {exc}", err=True)
        raise typer.Exit(1) from exc


def _single_line(text: str) -> str:
    """Fold every run of whitespace (including newlines) into one space.

    ``cron run`` output is consumed as ``-p "$(nexuscli cron run <id>)"``; a
    multi-line prompt survives that quoting but not a bare pipe, and an empty
    line in the middle would split the prompt in two.
    """
    return " ".join(text.split())


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
