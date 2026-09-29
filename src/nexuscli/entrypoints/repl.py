from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from nexuscli import __version__
from nexuscli.agent import Agent, AgentOrchestrator, PlanExecuteAgent
from nexuscli.bootstrap import build_tool_registry
from nexuscli.config import NexusCliConfig, config_to_public_dict, user_level_hooks
from nexuscli.context import ContextBudget, ContextWindowManager
from nexuscli.context.goal import GoalStore
from nexuscli.entrypoints.model_selector import ModelSelectorState, run_model_selector
from nexuscli.entrypoints.slash_commands import (
    CommandExpansion,
    CustomCommand,
    build_expert_graph,
    build_init_prompt,
    build_step_dispatch_prompt,
    expand_command,
    load_slash_commands,
    split_command_message,
)
from nexuscli.hooks import fire_event, has_hooks, trust
from nexuscli.llm import create_llm_client
from nexuscli.llm.model_profiles import (
    DEFAULT_MODEL_PROFILES,
    PROVIDER_DEFAULTS,
    CustomModelStore,
    ModelProfile,
)
from nexuscli.llm.usage_store import UsageStore
from nexuscli.memory import MemoryManager
from nexuscli.policy import AuditLog
from nexuscli.prompt import PromptAssembler
from nexuscli.rag import CodeIndex
from nexuscli.render import RichRenderer
from nexuscli.runtime import DurableTaskManager
from nexuscli.session import SessionStore, SessionWriter
from nexuscli.skill import SkillRegistry
from nexuscli.snapshot import SnapshotService
from nexuscli.tools import ToolRegistry
from nexuscli.tools.base import ToolContext
from nexuscli.workflow import WorkflowError, WorkflowGraph, load_workflow_file

SLASH_COMMANDS = [
    "/help",
    "/exit",
    "/clear",
    "/resume",
    "/context",
    "/compact",
    "/memory",
    "/save",
    "/config",
    "/tools",
    "/hitl",
    "/policy",
    "/audit",
    "/index",
    "/search",
    "/init",
    "/plan",
    "/team",
    "/model",
    "/usage",
    "/skill",
    "/mcp",
    "/task",
    "/workflow",
    "/expert",
    "/fork",
    "/goal",
    "/effort",
    "/snapshot",
    "/restore",
]


PermissionMode = Literal["default", "auto", "plan"]


@dataclass
class PermissionModeController:
    """Apply one of the three interactive permission modes to the live config."""

    config: NexusCliConfig
    mode: PermissionMode = "default"

    def __post_init__(self) -> None:
        self._default_hitl_mode = self.config.policy.hitl_mode
        self._default_path_guard_enabled = self.config.policy.path_guard_enabled
        self._default_command_guard_enabled = self.config.policy.command_guard_enabled
        self.set(self.mode)

    def set(self, mode: PermissionMode) -> PermissionMode:
        self.mode = mode
        if mode == "auto":
            self.config.policy.hitl_mode = "never"
            self.config.policy.path_guard_enabled = False
            self.config.policy.command_guard_enabled = False
            self.config.policy.plan_mode = False
        else:
            # "plan" restores the configured HITL/guard values exactly like
            # "default": plan mode is a read-only constraint, not an
            # approval bypass — writes are hard-rejected by the executor.
            self.config.policy.hitl_mode = self._default_hitl_mode
            self.config.policy.path_guard_enabled = self._default_path_guard_enabled
            self.config.policy.command_guard_enabled = self._default_command_guard_enabled
            self.config.policy.plan_mode = mode == "plan"
        return self.mode

    def toggle(self) -> PermissionMode:
        return self.set({"default": "auto", "auto": "plan", "plan": "default"}[self.mode])


@dataclass
class ReplSessionState:
    """Live session transcript state: the store plus the current writer."""

    store: SessionStore
    writer: SessionWriter


async def start_repl(
    cwd: str,
    config: NexusCliConfig,
    *,
    resume: str | None = None,
    continue_last: bool = False,
) -> None:
    console = Console()
    permission_mode = PermissionModeController(config)
    registry, mcp_manager = await build_tool_registry(config=config, cwd=cwd)
    client = create_llm_client(config.llm, telemetry_enabled=config.telemetry.enabled)
    system_prompt = PromptAssembler(
        config=config,
        cwd=cwd,
        tool_names=registry.list_names(),
        model=client.model_name,
        provider=client.provider_name,
    ).build_static()
    tool_count = len(registry.list_names())
    mcp_server_count = _count_mcp_servers(mcp_manager)
    skill_count = len(SkillRegistry(cwd).list())
    agents_file_count = _count_named_files(cwd, "AGENTS.md")
    renderer = RichRenderer(context_window=client.max_context_window)
    renderer.banner(
        model=client.model_name,
        provider=client.provider_name,
        cwd=cwd,
        tools=tool_count,
        version=__version__,
        api_key_configured=bool(config.llm.api_key),
        mcp_servers=mcp_server_count,
        skills=skill_count,
        agents_files=agents_file_count,
        hitl_mode=config.policy.hitl_mode,
    )
    agent = Agent(
        llm_client=client,
        tool_registry=registry,
        system_prompt=system_prompt,
        cwd=cwd,
        config=config,
        approval_callback=lambda request: _approval_prompt(request, console, permission_mode),
        max_turns=config.agent.max_turns,
    )

    session_store = SessionStore()
    session_state = ReplSessionState(
        store=session_store,
        writer=session_store.new_writer(
            cwd=cwd,
            model=client.model_name,
            provider=client.provider_name,
        ),
    )
    _apply_resume(
        console,
        session_state,
        agent,
        resume=resume,
        continue_last=continue_last,
    )
    custom_commands = load_slash_commands(cwd)

    # Project-layer hooks must be trusted before any of them can fire — this
    # includes SessionStart below, which would otherwise run an untrusted
    # workspace hook before the user ever sees a prompt.
    await _gate_workspace_hooks(config, cwd, console)

    # SessionStart hooks run once before the prompt loop; they cannot block
    # the session, so only their errors and additional context are shown.
    await _fire_session_start_hooks(config, console, cwd)

    history_path = Path.home() / ".nexuscli" / "history" / "prompt_history.txt"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    session = PromptSession(
        message=lambda: _prompt_message(
            cwd=cwd,
            model=agent.llm_client.model_name,
            tools=tool_count,
            agents_files=agents_file_count,
            mcp_servers=mcp_server_count,
            skills=skill_count,
            stats=renderer.toolbar_status(),
            permission_mode=permission_mode.mode,
        ),
        history=FileHistory(str(history_path)),
        completer=WordCompleter(
            SLASH_COMMANDS + [f"/{name}" for name in custom_commands],
            ignore_case=True,
        ),
        placeholder=[("class:placeholder", "Type your message or @path/to/file")],
        style=Style.from_dict(
            {
                "prompt": "bold #ffffff bg:#262626",
                "placeholder": "#9a9a9a bg:#262626",
                "prompt.dim": "#a3a3a3 bg:#000000",
                "prompt.count.agents": "bold #22d3ee bg:#000000",
                "prompt.count.mcp": "bold #c084fc bg:#000000",
                "prompt.count.skills": "bold #facc15 bg:#000000",
                "prompt.tools": "bold #22d3ee bg:#000000",
                "toolbar.model": "noreverse bold #ffffff bg:#000000",
                "toolbar.ctx.bar": "noreverse #22c55e bg:#000000",
                "toolbar.ctx.value": "noreverse #ffffff bg:#000000",
                "toolbar.cwd.value": "noreverse #c084fc bg:#000000",
                "toolbar.mode.default": "noreverse bold #22c55e bg:#000000",
                "toolbar.mode.auto": "noreverse bold #f59e0b bg:#000000",
                "toolbar.mode.plan": "noreverse bold #38bdf8 bg:#000000",
                "toolbar.gap": "noreverse #ffffff bg:#000000",
            }
        ),
        key_bindings=_permission_key_bindings(permission_mode),
    )

    while True:
        try:
            user_input = await session.prompt_async()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        message = user_input.strip()
        if not message:
            continue
        try:
            if message.startswith("/"):
                custom_match = await _match_custom_command(message, custom_commands, config)
                if custom_match is not None:
                    _queue_goal_reminder(agent, session_state.writer.meta.id)
                    await _run_custom_command(
                        agent,
                        renderer,
                        console,
                        permission_mode,
                        custom_match[1],
                    )
                    _persist_history(session_state, agent, console)
                    _record_turn_usage(agent, session_state.writer.meta.id)
                    # Known boundary of this slice: only custom-command and
                    # regular turns announce; slash-command exits and
                    # plan-mode internal turns do not.
                    _announce_bg_subagents(console)
                    continue
                should_exit = await _handle_slash(
                    message,
                    console,
                    cwd,
                    config,
                    agent,
                    registry,
                    permission_mode,
                    renderer,
                    session_state,
                    custom_commands,
                )
                if should_exit:
                    return
                continue
            _queue_goal_reminder(agent, session_state.writer.meta.id)
            await _run_agent(agent, renderer, message)
            _persist_history(session_state, agent, console)
            _record_turn_usage(agent, session_state.writer.meta.id)
            # Known boundary of this slice: only custom-command and regular
            # turns announce; slash-command exits and plan-mode internal
            # turns do not.
            _announce_bg_subagents(console)
        except KeyboardInterrupt:
            # Ctrl+C mid-turn aborts the turn, not the whole session.
            console.print("\n[yellow]Interrupted — turn aborted.[/yellow]")
        except Exception as exc:  # noqa: BLE001 - an unexpected error must not kill the REPL
            console.print(f"[red]Error:[/red] {exc}")


async def _run_agent(agent: Agent, renderer: RichRenderer, message: str) -> None:
    await _run_events(agent.run(message), renderer, agent.llm_client.max_context_window)


def _announce_bg_subagents(console: Console) -> None:
    """Drain finished background subagent tasks and print a one-shot summary.

    Purely synchronous and non-blocking: only tasks that already reached a
    terminal state are announced, all of the current batch in one call;
    still-running subagents stay silent and are picked up by a later turn.
    Notices are for the user only — they are never injected into the model
    context.
    """
    # Imported lazily: nexuscli.agent.bg_tasks transitively imports the
    # subagent runner, which imports tools.builtins — same anti-cycle reason
    # as the task handler there.
    from nexuscli.agent.bg_tasks import bg_subagent_registry

    # The bracket prefix is rich-markup-escaped (\[) so it renders literally.
    prefix = "\\[bg-subagent]"
    for note in bg_subagent_registry.drain_notifications():
        agent_type = note["agent_type"]
        description = note["description"]
        task_id = note["task_id"]
        if note["status"] == "completed":
            console.print(
                f'{prefix} completed {agent_type} "{description}" '
                f"(task {task_id}) — report: {note['report_path']}"
            )
        else:
            console.print(
                f'{prefix} failed {agent_type} "{description}" (task {task_id}): {note["error"]}'
            )


async def _run_workflow_graph(
    graph: WorkflowGraph,
    console: Console,
    cwd: str,
    config: NexusCliConfig,
    agent: Agent,
) -> None:
    """Dispatch subagents over the graph serially in topological order.

    Minimal slice (spec C8): no parallel executor — steps run one at a time
    and each step's report is collected into its dependents' prompts. A step
    failure aborts the remaining steps (their upstream reports would be
    missing), the minimal analogue of ZCode's pause-on-failure in
    workflow/expert/run-loop.ts:172-186. Steps never declare an agent type;
    they always run as the built-in ``general-purpose`` subagent.
    """
    # Imported lazily: nexuscli.agent.subagent transitively imports
    # tools.builtins — same anti-cycle reason as _announce_bg_subagents above.
    from nexuscli.agent.subagent import BUILTIN_AGENTS, run_subagent

    context = ToolContext(cwd=cwd, config=config, approval_callback=agent.approval_callback)
    order = graph.topological_order()
    reports: dict[str, str] = {}
    # The bracket prefix is rich-markup-escaped (\[) so it renders literally.
    prefix = "\\[workflow]"
    for index, step_id in enumerate(order):
        step = graph.steps[step_id]
        prompt = build_step_dispatch_prompt(
            graph,
            step_id,
            {dep: reports[dep] for dep in step.depends_on},
        )
        console.print(f"{prefix} ({index + 1}/{len(order)}) dispatching step {step_id}")
        try:
            report = await run_subagent(BUILTIN_AGENTS["general-purpose"], prompt, context)
        except Exception as exc:  # noqa: BLE001 - one failing step must not kill the REPL
            message = str(exc).strip() or exc.__class__.__name__
            console.print(f"{prefix} Step {step_id} failed: {message.splitlines()[0]}")
            console.print(f"{prefix} 中止剩余步骤：{', '.join(order[index + 1 :])}")
            return
        reports[step_id] = report
    console.print(f"{prefix} Workflow complete — {len(reports)} step reports:")
    for step_id in order:
        console.print(f"=== {step_id} ===")
        console.print(reports[step_id])


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


def _persist_history(
    session_state: ReplSessionState,
    agent: Agent,
    console: Console,
) -> None:
    try:
        session_state.writer.append(agent.history)
    except OSError as exc:
        console.print(f"[yellow]Failed to persist session transcript:[/yellow] {exc}")


def _record_turn_usage(agent: Agent, session_id: str) -> None:
    """Persist one completed turn's usage to ~/.nexuscli/usage.db.

    Best-effort: any failure (missing attr on a stub client, disk error,
    locked db) is swallowed — recording must never interrupt a turn.
    UsageStore() is opened per turn and closed immediately (same per-call
    construction convention as _task_command's manager).

    Coverage boundary (minimal slice): only regular turns and custom-command
    turns reach this call site — turns dispatched inside /init, /plan,
    /workflow and /expert are not recorded.
    """
    try:
        UsageStore().record_turn(
            model=agent.llm_client.model_name,
            provider=agent.llm_client.provider_name,
            session_id=session_id,
            usage=agent.last_usage,
            cost=agent.last_cost,
        )
    except Exception:
        return


def _apply_resume(
    console: Console,
    session_state: ReplSessionState,
    agent: Agent,
    *,
    resume: str | None = None,
    continue_last: bool = False,
) -> None:
    """Restore a previous transcript into the live agent, if requested."""
    if not resume and not continue_last:
        return
    record = None
    if resume:
        record = session_state.store.resolve(resume, cwd=agent.cwd)
        if record is None:
            console.print(f"[red]Session not found:[/red] {resume}")
            return
    else:
        recent = session_state.store.list(limit=1, cwd=agent.cwd)
        if not recent:
            console.print("[yellow]No previous session to continue.[/yellow]")
            return
        record = session_state.store.load(recent[0].id)
    if record is None:
        console.print("[yellow]Session transcript is unreadable; starting fresh.[/yellow]")
        return
    agent.history = list(record.messages)
    session_state.writer = session_state.store.writer_for(record.meta, record.messages)
    title = f" — {record.meta.title}" if record.meta.title else ""
    console.print(
        f"[green]Resumed session[/green] {record.meta.id} ({len(record.messages)} messages){title}"
    )


def _print_session_list(console: Console, store: SessionStore, cwd: str) -> None:
    metas = store.list(limit=20, cwd=cwd)
    if not metas:
        console.print("(no saved sessions)")
        return
    table = Table(title="NexusCLI Sessions")
    table.add_column("#", justify="right")
    table.add_column("ID")
    table.add_column("Title")
    table.add_column("Messages", justify="right")
    table.add_column("Updated")
    for index, meta in enumerate(metas, 1):
        table.add_row(
            str(index),
            meta.id,
            meta.title or "(untitled)",
            str(meta.message_count),
            time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.updated_at)),
        )
    console.print(table)
    console.print("[dim]Resume with /resume <index-or-id>[/dim]")


async def _run_events(events, renderer: RichRenderer, context_window: int | None = None) -> None:
    renderer.set_context_window(context_window)
    renderer.start_run()
    renderer.newline()
    try:
        async for event in events:
            renderer.handle(event)
            if event.get("type") == "error":
                break
    finally:
        # Close the async generator so agent-level finally blocks (post-turn
        # snapshot, HTTP client cleanup) run even when we break early.
        with suppress(Exception):
            await events.aclose()
    renderer.newline()


async def _handle_slash(
    raw: str,
    console: Console,
    cwd: str,
    config: NexusCliConfig,
    agent: Agent,
    registry: ToolRegistry,
    permission_mode: PermissionModeController,
    renderer: RichRenderer,
    session_state: ReplSessionState,
    custom_commands: dict[str, CustomCommand] | None = None,
) -> bool:
    command, _, rest = raw.partition(" ")
    arg = rest.strip()
    if command in {"/exit", "/quit"}:
        return True
    if command == "/help":
        console.print("\n".join(SLASH_COMMANDS))
        for name, custom in (custom_commands or {}).items():
            description = f" — {custom.description}" if custom.description else ""
            console.print(f"/{name}{description} [dim]({custom.source})[/dim]")
    elif command == "/clear":
        agent.clear_history()
        session_state.writer = session_state.store.new_writer(
            cwd=cwd,
            model=agent.llm_client.model_name,
            provider=agent.llm_client.provider_name,
        )
        console.clear()
    elif command == "/resume":
        if not arg:
            _print_session_list(console, session_state.store, cwd)
        else:
            record = session_state.store.resolve(arg, cwd=cwd)
            if record is None:
                console.print(f"[red]Session not found:[/red] {arg}")
            else:
                agent.history = list(record.messages)
                session_state.writer = session_state.store.writer_for(record.meta, record.messages)
                title = f" — {record.meta.title}" if record.meta.title else ""
                console.print(
                    f"[green]Resumed session[/green] {record.meta.id} "
                    f"({len(record.messages)} messages){title}"
                )
    elif command == "/context":
        memories = MemoryManager(config.memory.long_term_db_path, scope=cwd).list(limit=5)
        table = Table(title="NexusCLI Context")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("cwd", cwd)
        table.add_row("model", f"{config.llm.model} ({config.llm.provider})")
        table.add_row("context window", str(agent.llm_client.max_context_window))
        table.add_row("render", config.render_mode)
        table.add_row("memory", f"{len(memories)} recent entries")
        table.add_row("tools", str(len(registry.list_names())))
        console.print(table)
    elif command == "/compact":
        if len(agent.history) <= 1:
            console.print("(no conversation history to compact)")
        else:
            manager = ContextWindowManager(
                ContextBudget(
                    context_window=agent.llm_client.max_context_window,
                    max_output_tokens=config.llm.max_tokens,
                    compression_threshold=config.memory.compression_threshold,
                    compression_target=config.memory.compression_target,
                    reserve_tokens=config.memory.compression_reserve_tokens,
                ),
                max_history_messages=config.memory.max_conversation_history,
                min_recent_messages=config.memory.min_recent_messages,
                summary_max_chars=config.memory.summary_max_chars,
            )
            result = manager.compact_now(agent.history, focus=arg)
            agent.history = result.messages
            table = Table(title="NexusCLI Compaction")
            table.add_column("Field")
            table.add_column("Value")
            table.add_row("tokens before", str(result.estimated_tokens_before))
            table.add_row("tokens after", str(result.estimated_tokens_after))
            table.add_row("summarized messages", str(result.summarized_messages))
            if arg:
                table.add_row("focus", arg)
            console.print(table)
    elif command == "/memory":
        await _memory_command(arg, console, cwd, config)
    elif command == "/save":
        if not arg:
            console.print("[red]Usage:[/red] /save <fact>")
        else:
            memory_id = MemoryManager(
                config.memory.long_term_db_path,
                scope=cwd,
                max_entries=config.memory.max_long_term_entries,
                max_content_length=config.memory.max_memory_chars,
            ).save(arg, source="manual", importance=0.8)
            console.print(f"Saved memory #{memory_id}")
    elif command == "/config":
        console.print_json(json.dumps(config_to_public_dict(config), ensure_ascii=False))
    elif command == "/tools":
        console.print("\n".join(registry.list_names()))
    elif command == "/hitl":
        _hitl_command(arg, console, permission_mode)
    elif command == "/policy":
        console.print_json(json.dumps(config_to_public_dict(config)["policy"], ensure_ascii=False))
    elif command == "/audit":
        limit = int(arg or "20") if (arg or "20").isdigit() else 20
        console.print_json(
            json.dumps(AuditLog(config.policy.audit_log_path).tail(limit), ensure_ascii=False)
        )
    elif command == "/index":
        count = CodeIndex(cwd).rebuild(arg or ".")
        console.print(f"Indexed {count} code lines.")
    elif command == "/search":
        results = CodeIndex(cwd).search(arg, limit=20)
        output = "\n".join(f"{r.path}:{r.line}: {r.snippet}" for r in results)
        console.print(output or "(no matches)")
    elif command == "/plan":
        if not arg:
            console.print("[red]Usage:[/red] /plan <task>")
        else:
            plan_agent = PlanExecuteAgent(
                llm_client=agent.llm_client,
                tool_registry=registry,
                config=config,
                cwd=cwd,
                approval_callback=agent.approval_callback,
            )
            await _run_events(
                plan_agent.run(arg),
                RichRenderer(),
                agent.llm_client.max_context_window,
            )
    elif command == "/init":
        # Unlike /plan (which spawns a fresh PlanExecuteAgent), /init runs on
        # the current agent so the react loop and session context carry over.
        init_prompt = build_init_prompt(arg, cwd)
        await _run_agent(agent, renderer, init_prompt)
    elif command == "/team":
        if not arg:
            console.print("[red]Usage:[/red] /team <task>")
        else:
            try:
                worker_mode, team_task = _parse_mode_argument(arg)
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
                return False
            orchestrator = AgentOrchestrator(
                llm_client=agent.llm_client,
                tool_registry=registry,
                config=config,
                cwd=cwd,
                approval_callback=agent.approval_callback,
                default_worker_mode=worker_mode,
            )
            await _run_events(
                orchestrator.run(team_task),
                RichRenderer(),
                agent.llm_client.max_context_window,
            )
    elif command == "/model":
        await _model_command(arg, console, cwd, config, agent, registry, renderer)
    elif command == "/usage":
        sub, _, rest = arg.partition(" ")
        if sub == "stats":
            _usage_stats_command(rest, console)
        else:
            payload = {
                "usage": agent.last_usage.to_dict(),
                "cost": agent.last_cost,
                "pricing_note": "Built-in provider prices are dated defaults and may change.",
            }
            console.print_json(json.dumps(payload, ensure_ascii=False))
    elif command == "/skill":
        _skill_command(arg, console, cwd, agent)
    elif command == "/mcp":
        console.print(
            "Use `nexuscli mcp serve --transport stdio|http --port 3000` to expose tools."
        )
    elif command == "/task":
        _task_command(arg, console, cwd)
    elif command == "/workflow":
        # Known priority boundary (no code change): custom commands match
        # before built-in branches in start_repl, so a user-defined command
        # named "workflow" shadows this built-in — same convention as /init
        # ("A custom command named ``init`` keeps priority").
        sub, _, rest = arg.partition(" ")
        if sub != "run" or not rest:
            console.print("[red]Usage:[/red] /workflow run <file>.json")
            return False
        try:
            graph = load_workflow_file(Path(cwd) / rest)
        except (OSError, WorkflowError) as exc:
            message = str(exc).strip() or exc.__class__.__name__
            console.print(f"[red]Workflow error:[/red] {message.splitlines()[0]}")
            return False
        await _run_workflow_graph(graph, console, cwd, config, agent)
    elif command == "/expert":
        if not arg:
            console.print("[red]Usage:[/red] /expert <topic>")
        else:
            await _run_workflow_graph(build_expert_graph(arg), console, cwd, config, agent)
    elif command == "/snapshot":
        _snapshot_command(arg, console, cwd)
    elif command == "/restore":
        if not arg:
            console.print("[red]Usage:[/red] /restore <snapshot-id-or-index>")
        else:
            record = SnapshotService(cwd).restore(arg)
            console.print(f"Restored {record.id}")
    elif command == "/fork":
        # Fork reads the transcript file, so the current turn must be flushed
        # first or the last exchange would be missing from the copy (a no-op
        # on an empty history).
        _persist_history(session_state, agent, console)
        record = session_state.store.fork(session_state.writer.meta.id, title=arg or None)
        if record is None:
            console.print("[yellow]Nothing to fork yet — send a message first.[/yellow]")
        else:
            # The queued one-shot context (skill body / goal reminder) belongs
            # to the old session's next turn; a new session starts clean.
            agent.skill_context_buffer.clear()
            session_state.writer = session_state.store.writer_for(record.meta, record.messages)
            title = f" — {record.meta.title}" if record.meta.title else ""
            console.print(
                f"[green]Forked session[/green] {record.meta.forked_from} → "
                f"{record.meta.id} ({record.meta.message_count} messages){title}"
            )
    elif command == "/goal":
        sub, _, rest = arg.partition(" ")
        store = GoalStore(session_state.writer.meta.id)
        if sub == "set" and rest.strip():
            goal = store.set(rest)
            console.print(f"[green]Session goal set:[/green] {goal}")
            console.print("[dim]It will be prepended to every turn of this session.[/dim]")
        elif sub == "show":
            console.print(store.show() or "(no session goal set)")
        elif sub == "clear":
            console.print("Cleared session goal." if store.clear() else "(no session goal set)")
        else:
            console.print("[red]Usage:[/red] /goal set <text> | /goal show | /goal clear")
    elif command == "/effort":
        _effort_command(arg, console, agent)
    else:
        console.print(f"[red]Unknown command:[/red] {command}")
    return False


async def _memory_command(arg: str, console: Console, cwd: str, config: NexusCliConfig) -> None:
    manager = MemoryManager(
        config.memory.long_term_db_path,
        scope=cwd,
        max_entries=config.memory.max_long_term_entries,
        max_content_length=config.memory.max_memory_chars,
    )
    sub, _, rest = arg.partition(" ")
    if sub == "clear":
        count = manager.clear()
        console.print(f"Cleared {count} memories.")
    elif sub == "search":
        rows = manager.search(rest)
        console.print("\n".join(f"#{row.id} {row.content}" for row in rows) or "(no matches)")
    elif sub == "stats":
        console.print_json(json.dumps(manager.stats(), ensure_ascii=False))
    elif sub == "delete" and rest.strip().isdigit():
        console.print(f"Deleted: {manager.delete(int(rest.strip()))}")
    else:
        rows = manager.list()
        console.print("\n".join(f"#{row.id} {row.content}" for row in rows) or "(no memories)")


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


async def _model_command(
    arg: str,
    console: Console,
    cwd: str,
    config: NexusCliConfig,
    agent: Agent,
    registry: ToolRegistry,
    renderer: RichRenderer,
) -> None:
    if arg:
        parts = arg.split(maxsplit=1)
        provider = config.llm.provider if len(parts) == 1 else parts[0]
        model = parts[0] if len(parts) == 1 else parts[1]
        profile = next(
            (
                item
                for item in DEFAULT_MODEL_PROFILES
                if item.provider == provider.lower() and item.model == model
            ),
            None,
        )
        if profile is None:
            base_url = (
                config.llm.base_url
                if len(parts) == 1 and config.llm.base_url
                else _provider_defaults(provider)[1]
            )
            profile = ModelProfile(
                id="command-line-selection",
                name=model,
                provider=provider,
                model=model,
                base_url=base_url,
                context_window=config.llm.context_window or 128_000,
                description="Selected from /model arguments",
                api_key_env=_provider_api_key_env(provider),
            )
        _activate_model(profile, config, agent, registry, renderer, cwd)
        console.print(f"[green]Switched model:[/green] {model} ({provider})")
        return

    store = CustomModelStore()
    while True:
        state = ModelSelectorState(
            defaults=list(DEFAULT_MODEL_PROFILES),
            custom=store.list(),
            current_provider=agent.llm_client.provider_name,
            current_model=agent.llm_client.model_name,
        )
        action = await run_model_selector(state)
        if action is None:
            return
        if action.kind == "add":
            profile = _prompt_custom_model(console)
            if profile is not None:
                store.add(profile)
                _activate_model(profile, config, agent, registry, renderer, cwd)
                console.print(
                    f"[green]Saved and switched to custom model:[/green] {profile.name} "
                    f"[dim]({store.path})[/dim]"
                )
                return
            continue
        if action.kind == "delete" and action.profile is not None:
            if store.delete(action.profile.id):
                console.print(f"Deleted custom model: {action.profile.name}")
            continue
        if action.profile is not None:
            _activate_model(action.profile, config, agent, registry, renderer, cwd)
            console.print(
                f"[green]Switched model:[/green] {action.profile.name} "
                f"[dim]({action.profile.provider}/{action.profile.model})[/dim]"
            )
            return


def _prompt_custom_model(console: Console) -> ModelProfile | None:
    console.print("\n[bold]Add custom model[/bold]")
    provider = Prompt.ask(
        "Provider",
        choices=list(PROVIDER_DEFAULTS),
        default="openai-compatible",
    )
    provider_label, default_base_url, default_context = _provider_defaults(provider)
    model = Prompt.ask("Model ID").strip()
    if not model:
        console.print("[red]Model ID is required.[/red]")
        return None
    name = Prompt.ask("Display name", default=f"{provider_label} · {model}").strip()
    base_url = Prompt.ask("Base URL", default=default_base_url).strip()
    api_key_env = Prompt.ask(
        "API key environment variable",
        default=_provider_api_key_env(provider),
    ).strip()
    api_key = Prompt.ask(
        f"API key (optional; leave blank to use ${api_key_env})",
        default="",
        password=True,
        show_default=False,
    )
    context_text = Prompt.ask("Context window", default=str(default_context)).replace(",", "")
    try:
        context_window = int(context_text)
        return ModelProfile.custom_profile(
            name=name,
            provider=provider,
            model=model,
            base_url=base_url,
            context_window=context_window,
            api_key=api_key,
            api_key_env=api_key_env,
        )
    except ValueError as exc:
        console.print(f"[red]Invalid custom model:[/red] {exc}")
        return None


def _activate_model(
    profile: ModelProfile,
    config: NexusCliConfig,
    agent: Agent,
    registry: ToolRegistry,
    renderer: RichRenderer,
    cwd: str,
) -> None:
    old_provider = config.llm.provider.lower()
    old_api_key = config.llm.api_key
    config.llm.provider = profile.provider
    config.llm.model = profile.model
    config.llm.base_url = profile.base_url
    config.llm.context_window = profile.context_window
    config.llm.api_key = profile.resolve_api_key(
        current_provider=old_provider,
        current_api_key=old_api_key,
    )
    client = create_llm_client(config.llm, telemetry_enabled=config.telemetry.enabled)
    agent.llm_client = client
    agent.system_prompt = PromptAssembler(
        config=config,
        cwd=cwd,
        tool_names=registry.list_names(),
        model=client.model_name,
        provider=client.provider_name,
    ).build_static()
    renderer.set_context_window(client.max_context_window)


def _provider_defaults(provider: str) -> tuple[str, str, int]:
    normalized = provider.lower()
    if normalized in PROVIDER_DEFAULTS:
        return PROVIDER_DEFAULTS[normalized]
    return (provider, config_base_url(provider), 128_000)


def config_base_url(provider: str) -> str:
    from nexuscli.llm.factory import DEEPSEEK_BASE_URL, OPENAI_BASE_URL, PROVIDER_BASE_URLS

    normalized = provider.lower()
    if normalized == "deepseek":
        return DEEPSEEK_BASE_URL
    return PROVIDER_BASE_URLS.get(normalized, OPENAI_BASE_URL)


def _provider_api_key_env(provider: str) -> str:
    return {
        "deepseek": "DEEPSEEK_API_KEY",
        "glm": "ZAI_API_KEY",
        "zhipu": "ZAI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openai-compatible": "NEXUSCLI_API_KEY",
    }.get(provider.lower(), "NEXUSCLI_API_KEY")


def _push_turn_context(agent: Agent, name: str, body: str) -> None:
    """Shared per-turn prompt-injection seam (W1 /skill load, W6 /goal).

    Both producers queue onto agent.skill_context_buffer; the buffer is
    drained once per turn by agent._prepend_skill_context (agent.py:221)
    into the user message. /skill load queues once (one-shot); the session
    goal reminder re-queues at every turn start while a goal is set.
    """
    agent.skill_context_buffer.push(name, body)


_GOAL_REMINDER_NAME = "session-goal"


def _queue_goal_reminder(agent: Agent, session_id: str) -> None:
    """Re-queue the session goal reminder for this turn (no goal → no-op).

    Called from both main-loop turn branches right before the agent runs, so
    the reminder survives the buffer's one-shot drain and reaches every turn
    until /goal clear. Coverage boundary: turns dispatched inside /init,
    /plan and other slash-dispatched agent runs bypass these call sites and
    get no reminder — the same applicability /skill load has in plan mode.
    """
    goal = GoalStore(session_id).show()
    if goal:
        _push_turn_context(agent, _GOAL_REMINDER_NAME, f"[session goal] {goal}")


_EFFORT_LEVELS = ("minimal", "low", "medium", "high")


def _effort_command(arg: str, console: Console, agent: Agent) -> None:
    """Session-level reasoning effort switch (live on the current client)."""
    client = agent.llm_client
    if not arg:
        current = getattr(client, "reasoning_effort", None)
        console.print(f"Reasoning effort: {current or 'not set (provider default)'}")
        return
    level = arg.strip().lower()
    if level not in _EFFORT_LEVELS:
        console.print("[red]Usage:[/red] /effort minimal|low|medium|high")
        return
    if not hasattr(client, "reasoning_effort"):
        # AnthropicClient is a slots dataclass without the field
        # (llm/anthropic.py:47-59): hasattr is the capability probe.
        console.print(
            f"[yellow]Provider {client.provider_name} does not support "
            "reasoning effort; field not set.[/yellow]"
        )
        return
    client.reasoning_effort = level
    console.print(
        f"[green]Reasoning effort set to {level}[/green] "
        "(live for this client; a /model switch creates a fresh client and resets it)"
    )


def _skill_command(arg: str, console: Console, cwd: str, agent: Agent) -> None:
    """Handle /skill subcommands; `load` one-shot injects a skill into the next prompt."""
    registry = SkillRegistry(cwd)
    sub, _, rest = arg.partition(" ")
    # `/skill load <name>` rewrites the next prompt: the skill is pushed onto
    # the agent's one-shot skill_context_buffer, which the next turn drains and
    # prepends to the user message as `## Loaded Skill: <name>` (see
    # agent._prepend_skill_context). Words after the skill name (ZCode's
    # optional `[task]` suffix) are deliberately ignored — minimal slice.
    if sub == "load" and rest:
        name = rest.split()[0]
        skill = SkillRegistry(cwd).load(name)
        if not skill:
            console.print(f'Skill "{name}" not found.')
            return
        _push_turn_context(agent, skill.name, skill.content)
        console.print(
            f'[green]Skill "{skill.name}" loaded; '
            "it will be prepended to your next message.[/green]"
        )
        return
    if sub == "show" and rest:
        skill = registry.load(rest.strip())
        if not skill:
            console.print(f'Skill "{rest.strip()}" not found.')
            return
        console.print(skill.content[:12_000])
        return
    if sub == "on" and rest:
        console.print("enabled" if registry.enable(rest.strip()) else "skill not found")
        return
    if sub == "off" and rest:
        console.print("disabled" if registry.disable(rest.strip()) else "skill not found")
        return
    if sub == "reload":
        registry.reload()
        console.print("skills reloaded")
        return
    rows = registry.all_skills()
    lines = [
        f"{item.name}\t{item.source}\t{'on' if item.enabled else 'off'}\t{item.description}"
        for item in rows
    ]
    console.print("\n".join(lines) or "(no skills)")


def _usage_stats_command(rest: str, console: Console) -> None:
    """Render `/usage stats [N days]`: trailing-N-day totals plus per-model split.

    Any store failure (missing db, locked file, corrupt schema) is reported
    and swallowed — the stats view must never kill the REPL. An empty table
    renders as an all-zero summary, which is the desired first-run answer.
    """
    days = int(rest.strip()) if rest.strip().isdigit() and int(rest.strip()) >= 1 else 7
    try:
        stats = UsageStore().stats(days=days)
    except Exception as exc:  # noqa: BLE001 - report and keep the REPL alive
        console.print(f"[red]Usage stats unavailable:[/red] {exc}")
        return
    summary = Table(title=f"NexusCLI Usage — last {days} day(s)")
    summary.add_column("Field")
    summary.add_column("Value", justify="right")
    summary.add_row("turns", str(stats["turns"]))
    summary.add_row("total tokens", str(stats["total_tokens"]))
    summary.add_row("total cost (USD)", f"{stats['total_cost']['usd']:.6f}")
    summary.add_row("total cost (CNY)", f"{stats['total_cost']['cny']:.6f}")
    console.print(summary)
    per_model = Table(title="By model")
    per_model.add_column("Model")
    per_model.add_column("Turns", justify="right")
    per_model.add_column("Tokens", justify="right")
    per_model.add_column("USD", justify="right")
    per_model.add_column("CNY", justify="right")
    for row in stats["by_model"]:
        per_model.add_row(
            row["model"],
            str(row["turns"]),
            str(row["total_tokens"]),
            f"{row['total_cost']['usd']:.6f}",
            f"{row['total_cost']['cny']:.6f}",
        )
    console.print(per_model)


def _task_command(arg: str, console: Console, cwd: str) -> None:
    manager = DurableTaskManager(Path.home() / ".nexuscli" / "tasks" / "tasks.db", scope=cwd)
    sub, _, rest = arg.partition(" ")
    if sub == "add" and rest:
        try:
            mode, prompt = _parse_mode_argument(rest, allowed={"react", "plan", "team"})
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            return
        task_id = manager.add(prompt, mode=mode)
        console.print(
            f"Queued {task_id} ({mode}). Run `nexuscli worker` or `nexuscli serve` to consume it."
        )
    elif sub == "cancel" and rest:
        console.print(f"Canceled: {manager.cancel(rest.strip())}")
    elif sub == "log" and rest:
        task = manager.get(rest.strip())
        if not task:
            console.print("(task not found)")
        else:
            console.print(task.result or task.error or f"Task {task.id} is {task.status}")
    else:
        rows = manager.list(limit=20)
        console.print(
            "\n".join(
                f"{task.id} {task.status} {task.mode} attempts={task.attempts} {task.prompt[:80]}"
                for task in rows
            )
            or "(no tasks)"
        )


def _snapshot_command(arg: str, console: Console, cwd: str) -> None:
    service = SnapshotService(cwd)
    if arg == "clean":
        console.print(f"Cleaned {service.clean()} snapshots.")
        return
    rows = service.list(limit=20)
    output = "\n".join(
        f"{index}. {row.id} {row.phase} {row.created_at}" for index, row in enumerate(rows, 1)
    )
    console.print(output or "(no snapshots)")


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
    answer = Prompt.ask("Approve?", choices=["y", "n", "a", "s"], default="n")
    if answer == "a":
        permission_mode.set("auto")
        return "approve"
    if answer == "y":
        return "approve"
    if answer == "s":
        return "skip"
    return "deny"


def _count_mcp_servers(manager: Any) -> int:
    if manager is None:
        return 0
    return sum(1 for spec in manager.specs.values() if spec.enabled)


def _count_named_files(root: str, filename: str) -> int:
    excluded_dirs = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
    count = 0
    for _dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in excluded_dirs]
        if filename in filenames:
            count += 1
    return count


def _parse_mode_argument(
    value: str,
    *,
    allowed: set[str] | None = None,
) -> tuple[str, str]:
    modes = allowed or {"react", "plan"}
    parts = value.strip().split(maxsplit=2)
    if len(parts) >= 2 and parts[0] in {"--mode", "-m"}:
        mode = parts[1].lower()
        if mode not in modes:
            raise ValueError(f"mode must be one of: {', '.join(sorted(modes))}")
        prompt = parts[2].strip() if len(parts) == 3 else ""
        if not prompt:
            raise ValueError("task text is required after --mode")
        return mode, prompt
    if parts and parts[0] == "--plan":
        prompt = value.strip()[len("--plan") :].strip()
        if not prompt:
            raise ValueError("task text is required after --plan")
        return "plan", prompt
    return "react", value.strip()


def _permission_key_bindings(permission_mode: PermissionModeController) -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add(Keys.BackTab)
    def _toggle_permission_mode(event) -> None:
        permission_mode.toggle()
        event.app.invalidate()

    return bindings


def _permission_mode_label(mode: PermissionMode) -> str:
    if mode == "auto":
        return "Auto (full access)"
    if mode == "plan":
        return "plan (read-only)"
    return "Default"


def _prompt_message(
    *,
    cwd: str,
    model: str,
    tools: int,
    agents_files: int,
    mcp_servers: int,
    skills: int,
    stats: dict[str, Any] | None = None,
    permission_mode: PermissionMode = "default",
) -> list[tuple[str, str]]:
    return [
        ("class:prompt.count.agents", str(agents_files)),
        ("class:prompt.dim", f" {_plural_label(agents_files, 'AGENTS.md file')} · "),
        ("class:prompt.count.mcp", str(mcp_servers)),
        ("class:prompt.dim", f" {_plural_label(mcp_servers, 'MCP server')} · "),
        ("class:prompt.count.skills", str(skills)),
        ("class:prompt.dim", f" {_plural_label(skills, 'skill')} · Tools "),
        ("class:prompt.tools", str(tools)),
        ("class:prompt.dim", "\n"),
        *_bottom_toolbar(cwd, model, stats, permission_mode=permission_mode),
        ("class:prompt.dim", "\n\n"),
        ("class:prompt", "* "),
    ]


def _bottom_toolbar(
    cwd: str,
    model: str,
    stats: dict[str, Any] | None = None,
    *,
    permission_mode: PermissionMode = "default",
) -> list[tuple[str, str]]:
    stats = stats or {}
    has_usage = bool(stats.get("has_usage"))
    context_ratio = float(stats.get("context_ratio") or 0)
    context_text = _format_toolbar_percent(context_ratio) if has_usage else "0%"
    return [
        ("class:toolbar.model", model),
        ("class:toolbar.gap", "    "),
        ("class:toolbar.ctx.bar", _format_toolbar_bar(context_ratio if has_usage else 0)),
        ("class:toolbar.gap", " "),
        ("class:toolbar.ctx.value", context_text),
        ("class:toolbar.gap", "  "),
        ("class:toolbar.cwd.value", _shorten_home(cwd)),
        ("class:toolbar.gap", "  "),
        (
            f"class:toolbar.mode.{permission_mode}",
            _permission_mode_label(permission_mode),
        ),
        ("class:toolbar.gap", "  Shift+Tab"),
    ]


def _plural_label(count: int, singular: str) -> str:
    return singular if count == 1 else singular + "s"


def _shorten_home(path: str) -> str:
    home = str(Path.home())
    if path == home:
        return "~"
    prefix = home + os.sep
    if path.startswith(prefix):
        return "~/" + path[len(prefix) :]
    return path


def _format_toolbar_bar(value: float, *, width: int = 12) -> str:
    bounded = max(0.0, min(value, 1.0))
    filled = round(bounded * width)
    if bounded > 0 and filled == 0:
        filled = 1
    return "█" * filled + "░" * (width - filled)


def _format_toolbar_percent(value: float) -> str:
    if value <= 0:
        return "0%"
    if value < 0.01:
        return "<1%"
    return f"{value:.0%}"
