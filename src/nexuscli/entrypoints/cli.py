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
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from nexuscli import __version__
from nexuscli.agent import QueryEngine
from nexuscli.agent.agent import prompt_breakdown as measure_prompt_breakdown
from nexuscli.agent.results import gate_denial_reason
from nexuscli.bootstrap import build_tool_registry
from nexuscli.config import NexusCliConfig, get_config_paths, load_config
from nexuscli.entrypoints.repl import start_repl
from nexuscli.entrypoints.slash_commands import (
    CommandFrontmatterError,
    CustomCommand,
    build_init_prompt,
    ensure_usable_frontmatter,
    expand_custom_command,
    load_slash_commands,
    split_command_message,
)
from nexuscli.eval import (
    EVAL_HISTORY_RELATIVE,
    HISTORY_TRACKS,
    EvalSchemaError,
    PreviousReportError,
    build_real_report,
    build_report,
    builtin_eval_dir,
    builtin_real_eval_dir,
    format_real_report,
    format_report,
    load_eval_scenarios,
    load_previous_report,
    load_real_eval_scenarios,
    run_eval_scenarios,
    run_real_scenarios,
    write_real_report,
    write_report,
)
from nexuscli.eval.real import (
    RealTrackError,
    format_real_comparison,
    run_baseline_comparison,
    write_real_comparison,
)
from nexuscli.llm import create_llm_client
from nexuscli.llm.base import LlmClient
from nexuscli.llm.pricing import UNKNOWN_MODEL_PRICE_NOTE, CostBreakdown, ModelPriceProfile
from nexuscli.mcp import (
    McpConfigError,
    read_mcp_config_entries,
    serve_http,
    serve_stdio,
    write_chrome_devtools_config,
)
from nexuscli.prompt import PromptAssembler
from nexuscli.runtime import RuntimeApiServer
from nexuscli.runtime.api import runtime_api_key
from nexuscli.runtime.cron import CronError, cron_tasks_path, list_tasks, next_trigger, run_task
from nexuscli.session import SessionStore, SessionUsageTotals
from nexuscli.tools.registry import ToolRegistry
from nexuscli.types import Message, QueryResult, Usage

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
        typer.Option(
            "--mode",
            help=(
                "Agent mode: react, plan, or team. plan is read-only planning "
                "that never executes writes; --yes is ignored in plan mode"
            ),
        ),
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
    fail_on_blocked: Annotated[
        bool,
        typer.Option(
            "--fail-on-blocked",
            help=(
                "Single-prompt only: exit 2 when the run ends blocked (approval/"
                "permission/plan-gate refusal) or failed (exception)"
            ),
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
        expanded = _expand_custom_prompt(prompt, str(root))
        prompt = str(expanded)
        selected_mode, allowed_tools = _apply_prompt_command_frontmatter(
            config, expanded.command, mode
        )
        if selected_mode not in {"react", "plan", "team"}:
            raise typer.BadParameter("mode must be react, plan, or team", param_hint="--mode")
        if worker_mode not in {"react", "plan"}:
            raise typer.BadParameter(
                "worker-mode must be react or plan", param_hint="--worker-mode"
            )
        # Task 0039: --mode plan is read-only planning — the read-only gate
        # blocks every write before approval is even consulted, so --yes
        # cannot unblock anything here. Say so out loud instead of silently
        # accepting a flag that will do nothing.
        if yes and selected_mode == "plan":
            typer.echo(
                "Warning: --mode plan is read-only planning and never executes "
                "writes; --yes is ignored in plan mode.",
                err=True,
            )
        history, usage_baseline = _load_resume_history(
            str(root), resume=resume, continue_last=continue_session
        )
        if history and selected_mode != "react":
            typer.echo("Note: --resume/--continue only restores react mode history.", err=True)
            history = []
            # The run did not continue that transcript, so its usage records
            # are not part of this run's session ledger either.
            usage_baseline = SessionUsageTotals()
        # allowed_tools rides along only when a command actually narrows the
        # run: _run_prompt replacements (eval harnesses, tests) declare the
        # historic keyword-only shape, and an always-on empty narrowing would
        # break every one of them while carrying zero information.
        run_kwargs: dict = {}
        if allowed_tools:
            run_kwargs["allowed_tools"] = allowed_tools
        # Same rule for --fail-on-blocked: pass it only when switched on, so
        # _run_prompt replacements keep working unchanged.
        if fail_on_blocked:
            run_kwargs["fail_on_blocked"] = True
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
                **run_kwargs,
            )
        )
    elif resume or continue_session:
        asyncio.run(start_repl(str(root), config, resume=resume, continue_last=continue_session))
    else:
        asyncio.run(start_repl(str(root), config))


class _ExpandedPrompt(str):
    """Expanded one-shot prompt carrying the custom command that produced it.

    ``-p "/cmd"`` must both expand the body and apply the command file's
    frontmatter (``mode``/``allowed-tools``), but the established return
    contract of :func:`_expand_custom_prompt` is a plain prompt string
    (test_init_command.py asserts equality against the expanded text).
    Subclassing str keeps every existing consumer working while ``.command``
    tells main() which file's frontmatter to apply — ``None`` for plain
    messages, unknown ``/names``, and the built-in ``/init``.
    """

    __slots__ = ("command",)

    command: CustomCommand | None

    def __new__(cls, text: str, command: CustomCommand | None = None) -> _ExpandedPrompt:
        self = super().__new__(cls, text)
        self.command = command
        return self


def _expand_custom_prompt(prompt: str, root: str) -> _ExpandedPrompt:
    """Expand a custom slash command (or built-in ``/init``) in one-shot mode.

    Custom commands win on name conflicts: a user/project ``init`` command is
    looked up and expanded first, and the built-in initialization prompt is
    only used when no custom command matched. Any other unmatched ``/name``
    is returned unchanged as a plain prompt. The matched command rides on the
    result (see :class:`_ExpandedPrompt`) so the caller can apply its
    frontmatter the way the REPL already does (custom_cmds.py).
    """
    parsed = split_command_message(prompt)
    if parsed is None:
        return _ExpandedPrompt(prompt)
    name, args = parsed
    command = load_slash_commands(root).get(name.lstrip("/"))
    if command is None:
        if name.lstrip("/") == "init":
            return _ExpandedPrompt(build_init_prompt(args, root))
        return _ExpandedPrompt(prompt)
    return _ExpandedPrompt(expand_custom_command(command, args), command)


def _apply_prompt_command_frontmatter(
    config: NexusCliConfig,
    command: CustomCommand | None,
    explicit_mode: str | None,
) -> tuple[str, tuple[str, ...]]:
    """Apply a matched ``-p "/cmd"`` command's frontmatter to the run config.

    Returns ``(selected_mode, allowed_tools)``. ``mode`` mirrors the REPL
    consumption (``custom_cmds._run_custom_command``): ``plan`` is the
    read-only permission gate with the agent runner unchanged, ``react`` and
    ``team`` pick the runner. An explicit ``--mode`` flag outranks the file
    for the runner, and the read-only gate is OR-ed so frontmatter ``plan``
    never turns a flag-requested gate off. ``allowed-tools`` narrows the
    run's tool registry (applied by :func:`_run_prompt`); empty keeps the
    default registry.

    Unknown/illegal frontmatter values are not silently ignored: an invalid
    ``mode`` exits with a readable error naming the file and the legal
    values, before any run starts. Unknown ``allowed-tools`` names are
    reported by :func:`_run_prompt` when they match nothing in the built
    registry.
    """
    frontmatter_mode = ""
    allowed_tools: tuple[str, ...] = ()
    if command is not None:
        try:
            ensure_usable_frontmatter(command)
        except CommandFrontmatterError as exc:
            typer.echo(f"Fatal error: {exc}", err=True)
            raise typer.Exit(1) from exc
        frontmatter_mode = command.mode
        allowed_tools = command.allowed_tools
    # "plan" is a permission gate here, not a runner choice; "react" is the
    # default and "team" is the only runner a command file selects.
    runner_override = frontmatter_mode if frontmatter_mode in {"react", "team"} else ""
    selected_mode = (
        explicit_mode or runner_override or config.prompt.agent_mode or "react"
    ).lower()
    # The read-only gate must hold for every advertised plan run: an explicit
    # --mode plan also selects the Plan-and-Execute AGENT (the caller routes
    # on the returned mode), and frontmatter plan is the REPL's permission
    # gate with the runner unchanged. OR-ing means a mode can only turn the
    # gate on, never off, so it never leaks into runs that asked for plan.
    config.policy.plan_mode = selected_mode == "plan" or frontmatter_mode == "plan"
    return selected_mode, allowed_tools


def _narrow_registry(
    registry: ToolRegistry,
    allowed_tools: tuple[str, ...],
) -> tuple[ToolRegistry, tuple[str, ...]]:
    """Build a registry holding only the whitelisted tools of *registry*.

    Mirrors the REPL's whitelist narrowing (custom_cmds._narrow_registry) and
    the subagent filtering behind it: unknown names never make the cut. The
    names that matched nothing come back so the caller can report them —
    one-shot mode has no interactive moment to notice a missing tool.
    """
    narrowed = ToolRegistry()
    missing: list[str] = []
    for name in allowed_tools:
        tool = registry.get(name)
        if tool is None:
            missing.append(name)
        else:
            narrowed.register(tool)
    return narrowed, tuple(missing)


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
    token_budget: Annotated[
        bool,
        typer.Option(
            "--token-budget",
            help="Print the fixed prompt/tool token baseline instead of the environment check",
        ),
    ] = False,
) -> None:
    """Inspect the system: Python, uv, Node, API key, and config."""
    root = (cwd or Path.cwd()).resolve()
    config = load_config(project_root=root)
    if token_budget:
        # Task 0038: diagnose the fixed prompt/tool overhead the current
        # config starts every request from (system + tool schemas, empty
        # history) instead of the environment check.
        typer.echo(json.dumps(_token_budget_report(config, str(root)), ensure_ascii=False))
        return
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


def _token_budget_report(config: NexusCliConfig, root: str) -> dict[str, Any]:
    """Task 0038: the fixed prompt/tool overhead of an empty-history request.

    Builds the registry and effective system prompt the first request in this
    project would carry (builtin + VFS + MCP tools at the configured
    ``agent.prompt_profile``), then measures them with the shared
    :func:`prompt_breakdown` helper — ``history_chars`` is 0 by construction,
    so the numbers are the baseline every turn starts from. The model line of
    the dynamic prompt uses the configured provider/model (no LLM client is
    built, so no API key is needed to diagnose the baseline).
    """

    registry, _manager = asyncio.run(build_tool_registry(config=config, cwd=root))
    tool_names = registry.list_names()
    assembler = PromptAssembler(
        config=config,
        cwd=root,
        tool_names=tool_names,
        model=config.llm.model,
        provider=config.llm.provider,
    )
    effective_system = f"{assembler.build_static()}\n\n{assembler.build_dynamic('')}".strip()
    return {
        "prompt_profile": config.agent.prompt_profile,
        "context_window": config.llm.context_window,
        "max_tokens": config.llm.max_tokens,
        "baseline": measure_prompt_breakdown(
            system_prompt=effective_system,
            tool_definitions=registry.definitions(profile=config.agent.prompt_profile),
            messages=[],
        ),
    }


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
    workbench: Annotated[
        bool, typer.Option("--workbench", help="Serve the static workbench UI at /")
    ] = False,
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
    server = RuntimeApiServer(
        cwd=str(root), config=config, api_key=key, port=port, workbench=workbench
    )
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
    real: Annotated[
        bool,
        typer.Option(
            "--real",
            help=(
                "Run the real-model track: a real LLM (normal config chain) drives the "
                "real read-only builtin tools; writes are fail-closed denied and audited"
            ),
        ),
    ] = False,
    repeat: Annotated[
        int,
        typer.Option(
            "--repeat",
            min=1,
            help="Real track only: run every scenario k times serially (default 1)",
        ),
    ] = 1,
    baseline: Annotated[
        bool,
        typer.Option(
            "--baseline",
            help=(
                "Real track only: also run the bare-model ablation (same LLM, one "
                "direct completion, no tools/skills/harness prompt) and write the "
                "harness-gain comparison to .nexuscli/evals/last-real-comparison.json"
            ),
        ),
    ] = False,
) -> None:
    """Run eval scenarios and write the track's report.

    Default (hermetic) track: scripted eval scenarios run fully offline and
    diff against the previous report at ``<cwd>/.nexuscli/evals/last-report.json``;
    the next run marks pass->fail as regressions and fail->pass as fixes.
    Exits 1 when any scenario fails.

    ``--real`` switches to the real-model track: the scenario set is
    ``examples/evals/real/`` (or ``--file``), a real LLM is built from the
    normal config chain, only the read-only builtin tools execute, write calls
    are fail-closed denied and audited, and the report goes to
    ``<cwd>/.nexuscli/evals/last-real-report.json`` (the hermetic baseline is
    never touched). Exits 1 on any failed run or when no API key is configured.

    ``--real --baseline`` adds the bare-model ablation: after the agent arm,
    the same scenarios run as single direct completions (no tools, no skill
    injection, no harness prompt), and the per-scenario/summary harness-gain
    comparison lands in ``<cwd>/.nexuscli/evals/last-real-comparison.json``.
    The real-track report keeps its exact schema and stays agent-side only.
    The double-run is single-shot on purpose (``--repeat`` is rejected with
    ``--baseline``), and the exit code still reflects the AGENT arm only —
    the bare model failing tool-required questions is the expected ablation
    signal, not a command failure.
    """
    _ = all_scenarios  # --all is the default behaviour; kept as an explicit flag
    root = (cwd or Path.cwd()).resolve()
    if real:
        if baseline and repeat != 1:
            raise typer.BadParameter(
                "--repeat cannot be combined with --baseline: the comparison is a "
                "single-shot fair sample on both sides"
            )
        _eval_run_real(file, root, repeat=repeat, baseline=baseline)
        return
    if repeat != 1:
        raise typer.BadParameter("--repeat only applies to the real track (--real)")
    if baseline:
        raise typer.BadParameter("--baseline only applies to the real track (--real)")
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


def _eval_run_real(file: Path | None, root: Path, *, repeat: int, baseline: bool = False) -> None:
    """The ``--real`` branch of ``eval run`` (see the command docstring)."""
    target = file if file is not None else builtin_real_eval_dir()
    if target is None:
        typer.echo("No real-track eval scenarios found (examples/evals/real is missing).", err=True)
        raise typer.Exit(1)
    try:
        scenarios = load_real_eval_scenarios(target)
    except EvalSchemaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if not scenarios:
        typer.echo(f"No real-track eval scenarios in {target}", err=True)
        raise typer.Exit(1)
    comparison_run = None
    try:
        if baseline:
            # The ablation double-run: agent arm first (its results feed the
            # unchanged report below — same path, same schema), then the
            # bare-model arm, which stays in memory until the comparison
            # document lands in its own file.
            comparison_run = run_baseline_comparison(scenarios, root=str(root))
            results = comparison_run.agent_results
        else:
            results = run_real_scenarios(scenarios, root=str(root), repeat=repeat)
    except RealTrackError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    report = build_real_report(results, repeat=repeat)
    report_path = write_real_report(root, report)
    for line in format_real_report(report):
        typer.echo(line)
    typer.echo(f"Report written to {report_path}")
    if comparison_run is not None:
        comparison_path = write_real_comparison(root, comparison_run.comparison)
        for line in format_real_comparison(comparison_run.comparison):
            typer.echo(line)
        typer.echo(f"Comparison written to {comparison_path}")
    # The exit code reflects the AGENT arm only: the bare model failing
    # tool-required questions is the ablation's expected signal, reported in
    # the comparison document, not a command failure.
    if report["summary"]["failed"]:
        raise typer.Exit(1)


@eval_app.command("history")
def eval_history(
    limit: Annotated[
        int,
        typer.Argument(help="How many recent runs to list per track (default 10)"),
    ] = 10,
    track: Annotated[
        str | None,
        typer.Option("--track", help="Only list one track: 'hermetic' or 'real'"),
    ] = None,
    cwd: Annotated[Path | None, typer.Option("--cwd", help="Working directory")] = None,
) -> None:
    """List recent eval runs from the history snapshots (read-only).

    Reads ``<cwd>/.nexuscli/evals/history/{hermetic,real}/`` — one timestamped
    report snapshot per successful ``eval run``, newest first — and shows per
    snapshot: the UTC timestamp, passed/total, the run's exit verdict derived
    from the report summary (exit=1 when it failed), plus the track's headline
    signal: the cost-alert count (hermetic ``cost_increased``) or the all-pass
    rate (real ``all_pass_rate``).
    """
    if limit < 1:
        raise typer.BadParameter("limit must be >= 1")
    if track is not None and track not in HISTORY_TRACKS:
        raise typer.BadParameter("--track must be 'hermetic' or 'real'")
    root = (cwd or Path.cwd()).resolve()
    for name in (track,) if track else HISTORY_TRACKS:
        for line in _eval_history_lines(root, name, limit):
            typer.echo(line)


def _eval_history_lines(root: Path, track: str, limit: int) -> list[str]:
    """The history listing for one track: the newest ``limit`` snapshots first.

    Read-only. A missing or empty history directory gets a friendly line (the
    listing must stay usable before the first run), an unreadable or
    summary-less snapshot gets a placeholder line instead of aborting the
    listing. Snapshot filenames are the UTC stamps, so lexicographic order is
    chronological order.
    """
    directory = root / EVAL_HISTORY_RELATIVE / track
    snapshots = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not snapshots:
        return [f"{track}: no eval history yet (nothing recorded under {directory})"]
    head = f"{track} (last {min(limit, len(snapshots))}):"
    body = [_eval_history_line(track, path) for path in reversed(snapshots[-limit:])]
    return [head, *body]


def _eval_history_line(track: str, path: Path) -> str:
    """One history line: time, passed/total, exit verdict, track headline."""
    stamp = path.stem
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return f"{stamp}\t<unreadable snapshot>"
    summary = report.get("summary") if isinstance(report, dict) else None
    if not isinstance(summary, dict):
        return f"{stamp}\t<snapshot without summary>"
    passed = summary.get("passed", "?")
    total = summary.get("total_runs", summary.get("total", "?"))
    failed = summary.get("failed")
    exit_part = f"exit={1 if failed else 0}" if isinstance(failed, int) else "exit=?"
    if track == "real":
        tail = f"all_pass_rate={summary.get('all_pass_rate', '?')}"
    else:
        tail = f"cost_increased={summary.get('cost_increased', 0)}"
    return f"{stamp}\tpassed={passed}/{total}\t{exit_part}\t{tail}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _ask_streaming(
    engine: QueryEngine,
    prompt: str,
    *,
    history: list | None,
    stream_deltas: bool,
) -> tuple[QueryResult, str | None]:
    """Run react mode over the engine's live event stream.

    Text deltas are written straight to stdout (write + flush) as they arrive,
    so the answer shows up while the model is still generating instead of a
    long silent wait. With ``stream_deltas`` off (``--json`` reserves stdout
    for the single payload) events are consumed silently. Aggregation is not
    reimplemented: the ``QueryResult`` comes from the engine's existing event
    aggregation helper, keeping text/turns/usage/cost plus warning/error
    handling identical to the buffered ``ask_complete_async`` path.

    Returns ``(result, gate_denial)``: the first tool result the permission
    gates refused (Task 0036), classified by :func:`gate_denial_reason`, or
    None when every call went through. A headless run with no approver
    fails closed and still finishes with ``done``, so this pass over the
    stream is the only place the refusal shows up.
    """

    gate_denial: str | None = None

    async def _tee(events):
        nonlocal gate_denial
        async for event in events:
            if event.get("type") == "tool_result" and event.get("is_error") and not gate_denial:
                gate_denial = gate_denial_reason(str(event.get("result") or ""))
            if stream_deltas and event.get("type") == "text_delta":
                delta = str(event.get("text") or "")
                sys.stdout.write(delta)
                sys.stdout.flush()
            yield event

    result = await engine._complete_from_events(_tee(engine.ask(prompt, history)))
    return result, gate_denial


def _auto_approver(request: dict) -> str:
    """The --yes approval callback: approve without asking a human.

    Only reached for calls the permission chain already let through to the
    approval step, so deny rules, ask rules and hook blocks keep their
    priority. The executor audits these as hitl; the CLI warns at startup so
    the choice is visible in the transcript.
    """
    _ = request
    return "approve"


async def _plan_result_from_events(events) -> tuple[QueryResult, bool, dict[str, str]]:
    """Fold a plan-mode event stream into (QueryResult, plan_failed, blocked_tasks).

    Mirrors ``QueryEngine._complete_from_events`` (agent/query_engine.py is
    outside this change's boundary) with one addition: the plan-mode
    ``plan_failed`` event emitted by PlanExecuteAgent is consumed here so
    ``-p --mode plan`` can exit non-zero when tasks were blocked by the
    read-only gate, instead of reporting success for an unfinished plan.
    The event's ``blocked_tasks`` map (task id -> rejection reason) rides
    along for the headless ``status``/``blocked_reason`` fields (Task 0036).
    """
    text = ""
    tokens = 0
    turns = 0
    usage = Usage()
    cost = {}
    plan_failed = False
    blocked_tasks: dict[str, str] = {}
    async for event in events:
        event_type = event.get("type")
        if event_type == "text_delta":
            text += str(event.get("text") or "")
        elif event_type == "error":
            raise event["error"]
        elif event_type == "warning":
            # Same stderr surface as _complete_from_events: stdout stays
            # reserved for the answer text (and --json output).
            print(f"[warning] {event.get('message')}", file=sys.stderr)
        elif event_type == "plan_failed":
            plan_failed = True
            blocked_tasks = {str(k): str(v) for k, v in (event.get("blocked_tasks") or {}).items()}
        elif event_type == "done":
            tokens += int(event.get("total_tokens") or 0)
            turns += int(event.get("total_turns") or 0)
            usage = usage + Usage.from_mapping(event.get("usage") or {})
            cost = dict(event.get("cost") or cost)
    return (
        QueryResult(text=text, total_tokens=tokens, turns=turns, usage=usage, cost=cost),
        plan_failed,
        blocked_tasks,
    )


async def _team_result_from_events(
    events,
) -> tuple[QueryResult, str | None, bool, dict[str, str]]:
    """Fold a team-mode event stream into (result, gate_denial, plan_failed, blocked_tasks).

    Mirrors ``QueryEngine._complete_from_events`` (agent/query_engine.py is
    outside this change's boundary) with two additions:

    - the ``gate_denial`` event the orchestrator emits when a worker's tool
      call was refused by a permission/approval gate. The worker's raw
      is_error tool_result events never leave the SubAgent fold, so
      classifying them here the way react's ``_ask_streaming`` tee does would
      see nothing; the orchestrator classifies with the same
      :func:`gate_denial_reason` table and forwards the denial (Task 0036);
    - the ``plan_failed`` event the orchestrator forwards when a
      ``--worker-mode plan`` worker's nested plan ended blocked or failed
      (Task 0001). The nested plan's own plan_failed event is consumed
      inside ``SubAgent._execute_plan``, so the orchestrator carries the
      task-id -> rejection-reason map out and this fold picks it up so the
      team run reports ``status: "blocked"`` with the rejection reasons
      instead of a silent ``ok``. The flag stays separate from the map: a
      nested plan that stalled without gate rejections forwards an empty map
      and must still report blocked. Unlike plan mode's ``plan_failed``, the
      team failure keeps the default exit code 0 (only ``--fail-on-blocked``
      upgrades it) — same contract as the react gate denial.
    """
    text = ""
    tokens = 0
    turns = 0
    usage = Usage()
    cost = {}
    gate_denial: str | None = None
    plan_failed = False
    plan_blocked_tasks: dict[str, str] = {}
    async for event in events:
        event_type = event.get("type")
        if event_type == "text_delta":
            text += str(event.get("text") or "")
        elif event_type == "error":
            raise event["error"]
        elif event_type == "warning":
            # Same stderr surface as _complete_from_events: stdout stays
            # reserved for the answer text (and --json output).
            print(f"[warning] {event.get('message')}", file=sys.stderr)
        elif event_type == "gate_denial":
            gate_denial = str(event.get("reason") or "") or None
        elif event_type == "plan_failed":
            # Task 0001: same shape the orchestrator forwards from the nested
            # plan worker — the blocked_tasks map feeds blocked_reason below.
            plan_failed = True
            plan_blocked_tasks = {
                str(task_id): str(reason)
                for task_id, reason in (event.get("blocked_tasks") or {}).items()
            }
        elif event_type == "done":
            tokens += int(event.get("total_tokens") or 0)
            turns += int(event.get("total_turns") or 0)
            usage = usage + Usage.from_mapping(event.get("usage") or {})
            cost = dict(event.get("cost") or cost)
    return (
        QueryResult(text=text, total_tokens=tokens, turns=turns, usage=usage, cost=cost),
        gate_denial,
        plan_failed,
        plan_blocked_tasks,
    )


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
    allowed_tools: tuple[str, ...] = (),
    fail_on_blocked: bool = False,
) -> None:
    """Execute a single prompt and print the result.

    approve_all is the --yes switch: without an approval callback every tool
    that requires one is refused (the executor fails closed), which made -p
    useless for scripted work that writes files or runs commands. The default
    stays "deny"; --yes says out loud that nobody will be asked.

    allowed_tools is a ``-p "/cmd"`` command's ``allowed-tools`` frontmatter:
    the registry is narrowed to the listed tools before the engine sees it
    (empty keeps the default). Names matching nothing are reported on stderr
    instead of being silently dropped — the run has no interactive moment
    where a missing tool would otherwise become visible.

    fail_on_blocked is the --fail-on-blocked switch (Task 0036): the run
    always reports ``status`` ("ok" | "blocked" | "failed") — approval or
    permission-gate refusals and plan read-only interceptions are "blocked"
    with a ``blocked_reason``, exceptions are "failed" — but the default
    exit codes stay compatible (0 for ok and for react denials that used to
    pass silently, 1 for exceptions and 0034's blocked plans). With the
    switch, blocked/failed exit 2 instead. Non-JSON mode prints the status
    as one stderr line.
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
    if allowed_tools:
        registry, missing = _narrow_registry(registry, allowed_tools)
        if missing:
            typer.echo(
                "Note: allowed-tools not found in this run's tool registry (ignored): "
                + ", ".join(missing),
                err=True,
            )
    llm_client = create_llm_client(config.llm, telemetry_enabled=config.telemetry.enabled)
    # Task 0038: measure the fixed request overhead at the llm-client
    # boundary. The react Agent keeps its own record on
    # last_prompt_breakdown, but the engine owns that instance, so the CLI
    # reads the same calls through this transparent wrapper. Event streams
    # stay untouched (they must stay react≡query(), see
    # test_agent_overflow_parity), so usage.prompt_breakdown is attached to
    # the --json payload below instead.
    measured_client = _MeasuredLlmClient(llm_client)
    engine = QueryEngine(
        llm_client=measured_client,
        tool_registry=registry,
        config=config,
        cwd=cwd,
        approval_callback=_auto_approver if approve_all else None,
    )
    # Task 0034: plan mode folds the event stream itself (not via
    # plan_complete_async) so a plan the read-only gate blocked is visible as
    # a failure below; react runs through _ask_streaming's tee, and team folds
    # its own stream for the same reason (Task 0036): the orchestrator's
    # gate_denial event — a worker's approval/permission refusal — must reach
    # the status decision below instead of passing as ok. Task 0001 adds the
    # orchestrator's forwarded plan_failed event from a --worker-mode plan
    # worker's nested plan to the same treatment.
    plan_failed = False
    # Task 0001: the team fold's plan_failed stays separate from plan mode's
    # — a blocked team plan worker reports status "blocked" but keeps the
    # default exit code 0, so it must not reach the exit-1 branch below.
    team_plan_failed = False
    blocked_tasks: dict[str, str] = {}
    gate_denial: str | None = None
    failure: Exception | None = None
    result: QueryResult | None = None
    try:
        if mode == "plan":
            result, plan_failed, blocked_tasks = await _plan_result_from_events(engine.plan(prompt))
        elif mode == "team":
            result, gate_denial, team_plan_failed, blocked_tasks = await _team_result_from_events(
                engine.team(prompt, worker_mode=worker_mode)
            )
        else:
            result, gate_denial = await _ask_streaming(
                engine,
                prompt,
                history=history,
                stream_deltas=not json_output,
            )
    except Exception as exc:  # noqa: BLE001 - CLI should report model/config errors cleanly
        # Task 0036: the exception is recorded as status "failed" (and still
        # prints the fatal line); the exit code is decided below so --json
        # gets its payload even on this path.
        failure = exc
        typer.echo(f"Fatal error: {exc}", err=True)

    if failure is not None:
        status = "failed"
        blocked_reason: str | None = None
    elif plan_failed or team_plan_failed or gate_denial is not None or blocked_tasks:
        # blocked_tasks non-empty means a plan run the gate stopped (plan
        # mode) or a team plan worker whose nested plan forwarded
        # plan_failed (Task 0001); _plan_blocked_reason formats both.
        status = "blocked"
        blocked_reason = gate_denial or _plan_blocked_reason(blocked_tasks)
    else:
        status = "ok"
        blocked_reason = None

    if json_output:
        if failure is not None:
            # The run died mid-flight: emit the same schema with zeroed
            # accounting (usage was lost with the exception) so machine
            # consumers always get one JSON object with a status field.
            payload = {
                "text": "",
                "status": status,
                "mode": mode,
                "worker_mode": worker_mode if mode == "team" else None,
                "turns": 0,
                "total_tokens": 0,
                "usage": Usage().to_dict(),
                "cost": None,
                "error": str(failure),
                "session_usage": _single_shot_session_usage(
                    QueryResult(text="", total_tokens=0, turns=0),
                    usage_baseline,
                    cost_unknown=True,
                ),
            }
        else:
            # No resolvable price profile (model absent from the built-in table and
            # no llm.prices override) and the run produced no cost mapping: report
            # cost as null with a note instead of an empty dict / fabricated 0.0.
            # Known-model output is byte-identical to the previous contract.
            cost_unknown = _cost_unknown(llm_client, result.cost)
            usage_payload: dict[str, Any] = result.usage.to_dict()
            if mode == "react" and measured_client.last_prompt_breakdown is not None:
                # Task 0038: the fixed request overhead of the run's last LLM
                # call, under the usage object. Reported for react only — the
                # mode whose Agent records the same numbers — and absent when
                # nothing was measured (engine doubles that never call chat),
                # so the old JSON contract stays intact.
                usage_payload["prompt_breakdown"] = measured_client.last_prompt_breakdown
            payload = {
                "text": result.text,
                "status": status,
                "mode": mode,
                "worker_mode": worker_mode if mode == "team" else None,
                "turns": result.turns,
                "total_tokens": result.total_tokens,
                "usage": usage_payload,
                "cost": None if cost_unknown else result.cost,
            }
            if cost_unknown:
                payload["cost_note"] = UNKNOWN_MODEL_PRICE_NOTE
            if blocked_reason is not None:
                payload["blocked_reason"] = blocked_reason
            payload["session_usage"] = _single_shot_session_usage(
                result, usage_baseline, cost_unknown=cost_unknown
            )
        typer.echo(json.dumps(payload, ensure_ascii=False))
    elif failure is None and mode == "react":
        # The answer text was already echoed incrementally; only the trailing
        # newline that typer.echo(result.text) used to add is still missing.
        sys.stdout.write("\n")
        sys.stdout.flush()
    elif failure is None:
        typer.echo(result.text)
    if not json_output:
        # Task 0036: one machine-greppable status line on stderr, even for
        # "ok" — headless harnesses should not have to parse JSON to learn
        # how a run ended.
        detail = str(failure) if failure is not None else (blocked_reason or "")
        typer.echo(f"status: {status}" + (f" ({detail})" if detail else ""), err=True)
    if fail_on_blocked and status in {"blocked", "failed"}:
        raise typer.Exit(2)
    if failure is not None:
        raise typer.Exit(1) from failure
    if plan_failed:
        # Task 0034: the plan-mode run ended with tasks the read-only gate
        # blocked (or failed). The summary above already says the plan did
        # not complete; the exit code must agree instead of reporting 0.
        raise typer.Exit(1)


def _plan_blocked_reason(blocked_tasks: dict[str, str]) -> str:
    """Human-readable blocked_reason for a plan run the read-only gate stopped.

    The plan_failed event's ``blocked_tasks`` map carries one rejection reason
    per blocked task; a plan that stalled for another reason (dependencies
    unsatisfied) has no entries, so the reason says so generically instead of
    inventing a gate refusal that did not happen.
    """
    if not blocked_tasks:
        return "plan did not complete (tasks blocked or failed)"
    return "; ".join(f"{task_id}: {reason}" for task_id, reason in blocked_tasks.items())


def _single_shot_session_usage(
    result,
    usage_baseline: SessionUsageTotals | None,
    *,
    cost_unknown: bool = False,
) -> dict:
    """Dual-scope session usage for single-prompt ``--json``.

    Mirrors the REPL ``/usage`` JSON: ``historical_total`` is the resumed
    transcript's baseline plus this run's usage; ``current_process`` is this
    run only. One caveat stated in ``sources``: a single-prompt run does not
    append its usage to the transcript, so the baseline here is informational
    and is not persisted back.

    ``cost_unknown`` marks a run whose model price is unresolvable: the cost
    fields become null — including on ``historical_total``, since baseline
    plus unknown is itself unknown — instead of a fabricated 0.0.
    """
    this_run = SessionUsageTotals.from_turn(usage=result.usage, cost=result.cost)
    baseline = usage_baseline if usage_baseline is not None else SessionUsageTotals()
    historical = (baseline + this_run).to_dict()
    current = this_run.to_dict()
    if cost_unknown:
        for scope in (historical, current):
            scope["cost_usd"] = None
            scope["cost_cny"] = None
    return {
        "historical_total": historical,
        "current_process": current,
        "sources": {
            "historical_total": "resumed session usage baseline + this run (not persisted)",
            "current_process": "this run only",
        },
    }


# Clients that do not expose ``price_profile`` (test doubles, custom provider
# wrappers) stay on the pass-through contract; only an explicit None means the
# factory resolved no price profile for this model.
_PRICE_PROFILE_ABSENT = object()


class _MeasuredLlmClient:
    """Transparent LLM-client wrapper measuring each ``chat()`` request.

    Task 0038: every ``chat()`` call is measured with the shared
    :func:`nexuscli.agent.agent.prompt_breakdown` helper — the exact inputs
    the agent sends — and the last measurement backs ``-p --json``'s
    ``usage.prompt_breakdown``. Everything else delegates to the wrapped
    client, so streaming, retries, overflow recovery and cost resolution are
    unchanged; the wrapper exists because the engine owns the Agent instance
    (whose ``last_prompt_breakdown`` is the programmatic read-side) and the
    event streams must stay react≡query() (test_agent_overflow_parity).
    """

    def __init__(self, client: LlmClient) -> None:
        self._client = client
        self.last_prompt_breakdown: dict[str, int] | None = None

    @property
    def model_name(self) -> str:
        return self._client.model_name

    @property
    def provider_name(self) -> str:
        return self._client.provider_name

    @property
    def max_context_window(self) -> int:
        return self._client.max_context_window

    @property
    def price_profile(self) -> ModelPriceProfile | None:
        return getattr(self._client, "price_profile", None)

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        system_prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        self.last_prompt_breakdown = measure_prompt_breakdown(
            system_prompt=system_prompt,
            tool_definitions=list(tools),
            messages=list(messages),
        )
        return self._client.chat(messages, tools, system_prompt=system_prompt)

    def calculate_cost(
        self,
        usage: Usage | dict[str, Any],
        *,
        currency: str = "usd",
    ) -> CostBreakdown:
        return self._client.calculate_cost(usage, currency=currency)


def _cost_unknown(llm_client: object, cost: dict[str, Any] | None) -> bool:
    """True when ``--json`` must report cost as unknown (null + note).

    Both halves matter: the client resolved no price profile (built-in table
    miss and no ``llm.prices`` override), and the run indeed produced no cost
    mapping — the offline demo provider, whose scripted ``calculate_cost``
    returns honest zeros regardless of its ``None`` profile, keeps the
    pass-through contract.
    """
    profile = getattr(llm_client, "price_profile", _PRICE_PROFILE_ABSENT)
    return profile is None and not cost


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
