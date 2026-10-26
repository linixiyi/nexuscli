"""repl.py — Interactive REPL entry point: main loop, slash dispatch, rendering.

Owns the prompt loop, the ``_handle_slash`` dispatch skeleton, and the
session/rendering assembly (prompt message, bottom toolbar, permission-mode
key bindings, startup counters). Slash-command domain handlers live in the
``nexuscli.entrypoints.commands`` package and are imported back here; names
that tests and callers historically imported from this module (for example
``_skill_command``, ``_handle_slash``, ``SLASH_COMMANDS``) remain importable
as re-exports.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.table import Table

from nexuscli import __version__
from nexuscli.agent import Agent, AgentOrchestrator, PlanExecuteAgent
from nexuscli.agent.turn_checkpoint import TurnCheckpointStore
from nexuscli.bootstrap import build_tool_registry
from nexuscli.config import NexusCliConfig, config_to_public_dict
from nexuscli.context import ContextBudget, ContextWindowManager
from nexuscli.context.goal import GoalStore
from nexuscli.entrypoints.commands.approval import _approval_prompt
from nexuscli.entrypoints.commands.banner import _count_mcp_servers, _count_named_files
from nexuscli.entrypoints.commands.custom_cmds import _match_custom_command, _run_custom_command
from nexuscli.entrypoints.commands.effort import _effort_command
from nexuscli.entrypoints.commands.goal import _queue_goal_reminder
from nexuscli.entrypoints.commands.hooks_cmds import (
    _fire_session_start_hooks,
    _gate_workspace_hooks,
)
from nexuscli.entrypoints.commands.memory_cmds import _memory_command
from nexuscli.entrypoints.commands.model_cmds import (
    _activate_model,  # noqa: F401 - re-exported for tests; handler lives in commands.model_cmds
    _model_command,
)
from nexuscli.entrypoints.commands.notify import _announce_bg_subagents
from nexuscli.entrypoints.commands.permission import _hitl_command, _permission_mode_label
from nexuscli.entrypoints.commands.run import _run_agent, _run_events
from nexuscli.entrypoints.commands.session_cmds import (
    _apply_resume,
    _history_command,
    _persist_history,
    _print_session_list,
    _record_session_usage,
)
from nexuscli.entrypoints.commands.skill import _skill_command
from nexuscli.entrypoints.commands.snapshot_cmds import _snapshot_command
from nexuscli.entrypoints.commands.task_cmds import _parse_mode_argument, _task_command
from nexuscli.entrypoints.commands.usage import _session_usage_payload, _usage_stats_command
from nexuscli.entrypoints.commands.workflow_cmds import _run_workflow_graph
from nexuscli.entrypoints.slash_commands import (
    CustomCommand,
    build_expert_graph,
    build_init_prompt,
    load_slash_commands,
)
from nexuscli.llm import create_llm_client
from nexuscli.llm.usage_store import UsageStore
from nexuscli.memory import MemoryManager
from nexuscli.policy import AuditLog
from nexuscli.prompt import PromptAssembler
from nexuscli.rag import CodeIndex
from nexuscli.render import RichRenderer
from nexuscli.session import SessionStore, SessionUsageTotals, SessionWriter
from nexuscli.skill import SkillRegistry
from nexuscli.snapshot import SnapshotService
from nexuscli.tools import ToolRegistry
from nexuscli.types import Message, Usage
from nexuscli.workflow import WorkflowError, load_workflow_file

SLASH_COMMANDS = [
    "/help",
    "/exit",
    "/clear",
    "/resume",
    "/history",
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
    """Live session transcript state: the store plus the current writer.

    ``usage_baseline`` is the historical usage summed from the resumed
    transcript's per-turn ``usage`` record lines (zeros for a fresh session);
    ``process_usage`` accumulates the turns recorded in this process. The
    two add up to the transcript's historical ledger shown by ``/usage``.
    """

    store: SessionStore
    writer: SessionWriter
    usage_baseline: SessionUsageTotals = field(default_factory=SessionUsageTotals)
    process_usage: SessionUsageTotals = field(default_factory=SessionUsageTotals)


async def start_repl(
    cwd: str,
    config: NexusCliConfig,
    *,
    resume: str | None = None,
    continue_last: bool = False,
) -> None:
    console = Console()
    # Piped/scripted stdin (``echo '/help' | nexuscli``) has no terminal:
    # prompt_toolkit would fail mid-event-loop with "Event loop stopped before
    # Future completed" and a full rich traceback. Point script users at the
    # one-shot mode instead of entering the prompt loop at all.
    if not sys.stdin.isatty():
        # stdout stays clean so a pipeline never receives this notice as data;
        # the message is one line because a hard-wrapped diagnostic reads like a
        # crash. Keep the width clamp: at 80 columns rich would otherwise wrap it.
        sys.stderr.write(
            'Interactive REPL needs a terminal. For piped/script use, run: nexuscli -p "<prompt>"\n'
        )
        sys.stderr.flush()
        raise SystemExit(1)
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
        use_journal=True,
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
            agent=agent,
        ),
        history=FileHistory(str(history_path)),
        completer=SlashCommandCompleter(SLASH_COMMANDS + [f"/{name}" for name in custom_commands]),
        placeholder=[("class:placeholder", "Type your message or @image:path/to/file")],
        # Foreground-only palette drawn from the terminal's own ANSI slots.
        # No backgrounds, so the prompt rides the user's theme instead of
        # painting black strips over it. Named ANSI colours (never literals
        # like #c084fc or #d7ba7d) because the terminal guarantees those are
        # legible against its own background: the literal palette measured
        # 1.9–2.6:1 on a white terminal, far under the 4.5:1 floor.
        style=Style.from_dict(
            {
                "prompt": "bold ansicyan",
                "placeholder": "ansibrightblack",
                "prompt.dim": "ansibrightblack",
                "prompt.count.agents": "bold ansicyan",
                "prompt.count.mcp": "bold ansimagenta",
                "prompt.count.skills": "bold ansiyellow",
                "prompt.tools": "bold ansicyan",
                "toolbar.model": "bold",
                "toolbar.effort": "ansimagenta",
                "toolbar.ctx.bar": "ansigreen",
                "toolbar.ctx.value": "",
                "toolbar.cwd.value": "ansimagenta",
                "toolbar.mode.default": "bold ansigreen",
                "toolbar.mode.auto": "bold ansiyellow",
                "toolbar.mode.plan": "bold ansicyan",
                "toolbar.gap": "",
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
        # Ctrl+C must abort this turn, not the process. prompt_toolkit owns
        # SIGINT only while prompt_async() is on screen; its teardown calls
        # loop.remove_signal_handler(), which restores SIG_DFL rather than the
        # handler that preceded it. During a turn the OS default is therefore
        # back, and one Ctrl+C kills the CLI with exit 130 mid-generation
        # (pty-verified; the turn never reaches its transcript write). Re-arm
        # SIGINT for the turn and route it into task cancellation, which stays
        # inside the event loop where the handlers below can see it.
        sigint_armed = _arm_turn_sigint()
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
                    _record_session_usage(session_state, agent)
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
                    mcp_manager,
                )
                if should_exit:
                    return
                continue
            _queue_goal_reminder(agent, session_state.writer.meta.id)
            await _run_agent(agent, renderer, message)
            _persist_history(session_state, agent, console)
            _record_turn_usage(agent, session_state.writer.meta.id)
            _record_session_usage(session_state, agent)
            # Known boundary of this slice: only custom-command and regular
            # turns announce; slash-command exits and plan-mode internal
            # turns do not.
            _announce_bg_subagents(console)
        except KeyboardInterrupt:
            # Ctrl+C mid-turn aborts the turn, not the whole session.
            console.print("\n[yellow]Interrupted — turn aborted.[/yellow]")
        except asyncio.CancelledError:
            # Ctrl+C arrives here as a cancelled task (see _arm_turn_sigint).
            # uncancel() clears the cancellation request so the next prompt is
            # a normal await instead of raising immediately.
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
            console.print("\n[yellow]Interrupted — turn aborted.[/yellow]")
        except Exception as exc:  # noqa: BLE001 - an unexpected error must not kill the REPL
            console.print(f"[red]Error:[/red] {exc}")
        finally:
            _disarm_turn_sigint(sigint_armed)


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
    mcp_manager: Any | None = None,
) -> bool:
    command, _, rest = raw.partition(" ")
    arg = rest.strip()
    if command in {"/exit", "/quit"}:
        return True
    if command == "/help":
        console.print(
            "[dim]Type a message to talk to the agent; start with / for commands. "
            "Shift+Tab cycles permission mode · Ctrl+C aborts a turn · Ctrl+D exits.[/dim]"
        )
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
        # A cleared session starts a new transcript: no historical baseline,
        # and the new transcript's ledger is not the old one's turns.
        session_state.usage_baseline = SessionUsageTotals()
        session_state.process_usage = SessionUsageTotals()
        # The context bar reads renderer state, so a bare screen clear left it
        # showing the previous conversation's fill. Reset the counters and
        # redraw the banner: after /clear the terminal should look like a fresh
        # start, not an empty screen with stale numbers.
        if renderer is not None:
            renderer.reset_usage()
        console.clear()
        if renderer is not None:
            renderer.banner(
                model=agent.llm_client.model_name,
                provider=agent.llm_client.provider_name,
                cwd=cwd,
                tools=len(registry.list_names()) if registry is not None else 0,
                version=__version__,
                api_key_configured=bool(config.llm.api_key),
                hitl_mode=config.policy.hitl_mode,
            )
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
                # Same baseline as the -c/--resume path: historical usage
                # comes from the transcript's usage record lines. The process
                # accumulator must reset too — it belongs to the previous
                # transcript, not this one.
                session_state.usage_baseline = session_state.store.load_usage(record.meta.id)
                session_state.process_usage = SessionUsageTotals()
                title = f" — {record.meta.title}" if record.meta.title else ""
                console.print(
                    f"[green]Resumed session[/green] {record.meta.id} "
                    f"({len(record.messages)} messages){title}"
                )
    elif command == "/history":
        _history_command(arg, console, session_state.store, cwd)
    elif command == "/context":
        memories = MemoryManager(config.memory.long_term_db_path, scope=cwd).list(limit=5)
        # Frameless summary, same language as /config /policy: a bold heading
        # line, then a headerless two-column grid — no box characters.
        console.print("[bold]NexusCLI Context[/bold]")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold")
        grid.add_column()
        grid.add_row("cwd", cwd)
        grid.add_row("model", f"{config.llm.model} ({config.llm.provider})")
        grid.add_row("context window", str(agent.llm_client.max_context_window))
        grid.add_row("render", config.render_mode)
        grid.add_row("memory", f"{len(memories)} recent entries")
        grid.add_row("tools", str(len(registry.list_names())))
        console.print(grid)
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
            if not result.compressed:
                # compact_now refuses to hand back a history that grew: on a
                # short conversation the summary block costs more than the
                # turns it replaces. Saying so is the whole point — the old
                # table reported 24 -> 101 as a successful compaction.
                console.print("(nothing to compact: the summary would be larger than the history)")
            else:
                agent.history = result.messages
                # Frameless, same language as /context above: heading line plus
                # a headerless grid, no box characters.
                console.print("[bold]NexusCLI Compaction[/bold]")
                grid = Table.grid(padding=(0, 2))
                grid.add_column(style="bold")
                grid.add_column()
                grid.add_row("tokens before", str(result.estimated_tokens_before))
                grid.add_row("tokens after", str(result.estimated_tokens_after))
                grid.add_row("summarized messages", str(result.summarized_messages))
                if arg:
                    grid.add_row("focus", arg)
                console.print(grid)
    elif command == "/memory":
        await _memory_command(arg, console, cwd, config)
    elif command == "/save":
        options, fact = _parse_save_options(arg)
        if not fact:
            console.print(f"[red]Usage:[/red] {_SAVE_USAGE}")
            console.print(f"[dim]{_SAVE_OPTIONS_HELP}[/dim]")
        else:
            try:
                memory_id = MemoryManager(
                    config.memory.long_term_db_path,
                    scope=cwd,
                    max_entries=config.memory.max_long_term_entries,
                    max_content_length=config.memory.max_memory_chars,
                ).save(fact, source="manual", importance=0.8, **options)
            except ValueError as exc:
                console.print(f"[red]Cannot save memory:[/red] {exc}")
                console.print(f"[dim]{_SAVE_OPTIONS_HELP}[/dim]")
            else:
                console.print(f"Saved memory #{memory_id}")
    elif command == "/config":
        # A full config dump is hundreds of lines and scrolls the conversation
        # away. The fields a user actually checks before a run fit on one screen;
        # everything else is one command away.
        public = config_to_public_dict(config)
        llm = public.get("llm", {})
        summary = Table(title="NexusCLI Config", show_header=False, box=None)
        summary.add_column("Field", style="bold")
        summary.add_column("Value")
        for label, value in (
            ("provider", llm.get("provider")),
            ("model", llm.get("model")),
            ("base_url", llm.get("base_url")),
            ("api_key", "***" if llm.get("api_key") else "(unset)"),
            ("context_window", llm.get("context_window")),
            ("max_tokens", llm.get("max_tokens")),
            ("render_mode", public.get("render_mode")),
            ("hitl_mode", public.get("policy", {}).get("hitl_mode")),
            ("audit_log_path", public.get("policy", {}).get("audit_log_path")),
            ("auto_snapshot", public.get("features", {}).get("auto_snapshot")),
        ):
            summary.add_row(str(label), str(value))
        console.print(summary)
        console.print("[dim]Full dump: nexuscli doctor --cwd .[/dim]")
    elif command == "/tools":
        console.print("\n".join(registry.list_names()))
    elif command == "/hitl":
        _hitl_command(arg, console, permission_mode)
    elif command == "/policy":
        policy = config_to_public_dict(config)["policy"]
        summary = Table(title="NexusCLI Policy", show_header=False, box=None)
        summary.add_column("Field", style="bold")
        summary.add_column("Value")
        for label, value in (
            ("hitl_mode", policy.get("hitl_mode")),
            ("path_guard", policy.get("path_guard_enabled")),
            ("command_guard", policy.get("command_guard_enabled")),
            ("env_sanitization", policy.get("env_sanitization")),
            ("plan_mode", policy.get("plan_mode")),
            ("command_blacklist", f"{len(policy.get('command_blacklist') or [])} entries"),
        ):
            summary.add_row(str(label), str(value))
        console.print(summary)
        rules = config_to_public_dict(config).get("permissions", {})
        console.print(
            "[dim]permissions: "
            f"allow {len(rules.get('allow') or [])} · "
            f"ask {len(rules.get('ask') or [])} · "
            f"deny {len(rules.get('deny') or [])}[/dim]"
        )
    elif command == "/audit":
        limit = int(arg or "10") if (arg or "10").isdigit() else 10
        events = AuditLog(config.policy.audit_log_path).tail(limit)
        if not events:
            console.print("(no audit records)")
        else:
            # Frameless listing, same language as /history and /sessions: a
            # bold heading line, one dim header row, then a grid — no box
            # characters.
            console.print(f"[bold]NexusCLI Audit[/bold] [dim]— last {len(events)}[/dim]")
            grid = Table.grid(padding=(0, 2))
            grid.add_column(no_wrap=True)
            grid.add_column()
            grid.add_column()
            grid.add_column()
            grid.add_row(
                "[dim]time[/dim]",
                "[dim]tool[/dim]",
                "[dim]outcome[/dim]",
                "[dim]approver[/dim]",
            )
            for event in events:
                stamp = str(event.get("timestamp") or "")
                grid.add_row(
                    stamp[11:19] if len(stamp) >= 19 else stamp,
                    str(event.get("tool_name") or ""),
                    str(event.get("outcome") or ""),
                    str(event.get("approver") or ""),
                )
            console.print(grid)
    elif command == "/index":
        count = CodeIndex(cwd).rebuild(arg or ".")
        console.print(f"Indexed {count} code lines.")
    elif command == "/search":
        results = CodeIndex(cwd).search(arg, limit=20)
        output = "\n".join(f"{r.path}:{r.line}: {r.snippet}" for r in results)
        console.print(output or "(no matches)")
    elif command == "/plan":
        plan_arg = arg.strip()
        if not plan_arg:
            console.print("[red]Usage:[/red] /plan <task> | /plan --resume <plan_id>")
        else:
            plan_agent = PlanExecuteAgent(
                llm_client=agent.llm_client,
                tool_registry=registry,
                config=config,
                cwd=cwd,
                approval_callback=agent.approval_callback,
                use_journal=True,
            )
            if plan_arg.startswith("--resume"):
                plan_id = plan_arg[len("--resume") :].strip()
                if not plan_id:
                    console.print("[red]Usage:[/red] /plan --resume <plan_id>")
                else:
                    await _run_events(
                        plan_agent.run_resume(plan_id),
                        RichRenderer(),
                        agent.llm_client.max_context_window,
                    )
            else:
                await _run_events(
                    plan_agent.run(plan_arg),
                    RichRenderer(),
                    agent.llm_client.max_context_window,
                )
    elif command == "/turn-resume":
        # F10 回合级恢复：列出候选或按 id 接续（重建历史、沿用 run_id、
        # pending 按原 id 经 journal 重派，随后 continue_run 从本轮继续）。
        store = TurnCheckpointStore()
        if not arg:
            candidates = store.list_checkpoints(cwd=cwd)
            if not candidates:
                console.print(
                    "[yellow]没有可用的回合 checkpoint"
                    "（turn_checkpoints 随 REPL 默认开启）[/yellow]"
                )
            else:
                table = Table.grid(padding=(0, 2))
                table.add_column(style="cyan", no_wrap=True)
                table.add_column()
                for item in candidates[:10]:
                    pending_flag = (
                        f" · {len(item.pending_tool_calls)} 个在途调用"
                        if item.pending_tool_calls
                        else ""
                    )
                    table.add_row(
                        item.checkpoint_id, f"turn {item.turn} · {item.updated_at}{pending_flag}"
                    )
                console.print(table)
            return False
        checkpoint = store.load(arg.strip())
        cwd_matches = checkpoint is not None and os.path.realpath(
            checkpoint.cwd
        ) == os.path.realpath(cwd)
        if checkpoint is None or not cwd_matches:
            console.print("[red]checkpoint 不存在、损坏或不属于当前工作区[/red]")
            return False
        restored_agent = Agent(
            llm_client=agent.llm_client,
            tool_registry=registry,
            system_prompt=agent.system_prompt,
            cwd=cwd,
            config=config,
            approval_callback=agent.approval_callback,
            max_turns=config.agent.max_turns,
            use_journal=True,
            run_id=checkpoint.run_id,
            turn_checkpoints=True,
        )
        from dataclasses import fields as dc_fields

        valid_fields = {f.name for f in dc_fields(Message)}
        restored_agent.history = [
            Message(**{k: v for k, v in item.items() if k in valid_fields})
            for item in checkpoint.messages
        ]

        restored_agent.last_usage = Usage.from_mapping(checkpoint.usage_so_far)
        restored_agent.last_cost = dict(checkpoint.cost_so_far)
        if checkpoint.pending_tool_calls:
            from nexuscli.tools.base import ToolContext
            from nexuscli.tools.executor import ToolExecutor

            replay_context = ToolContext(
                cwd=cwd,
                config=config,
                approval_callback=agent.approval_callback,
                journal=restored_agent._journal,
                run_id=checkpoint.run_id,
            )
            replay_executor = ToolExecutor(registry)
            console.print(
                f"[yellow]重放 {len(checkpoint.pending_tool_calls)} 个在途调用"
                "（journal 分流）…[/yellow]"
            )
            for call in checkpoint.pending_tool_calls:
                results = await replay_executor.execute_all([call], replay_context)
                for result in results:
                    kind = result.error_type or ("reuse" if not result.is_error else "error")
                    console.print(f"  {result.tool_use_id}: {kind}")
                    restored_agent.history.append(
                        Message(
                            role="tool", content=result.content, tool_call_id=result.tool_use_id
                        )
                    )
        else:
            console.print("[yellow]最近一轮完整（无在途调用），从下一轮继续[/yellow]")
        await _run_events(
            restored_agent.continue_run(),
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
                "session_usage": _session_usage_payload(session_state),
                "pricing_note": "Built-in provider prices are dated defaults and may change.",
            }
            usage = agent.last_usage.to_dict()
            session = payload["session_usage"]
            line = Table.grid(padding=(0, 2))
            line.add_column(style="dim", no_wrap=True)
            line.add_column()
            line.add_row(
                "turn",
                f"{usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out",
            )
            hist = session.get("historical_total", {})
            line.add_row(
                "session",
                f"{hist.get('turns', 0)} turns · {hist.get('total_tokens', 0)} tokens",
            )
            cost = agent.last_cost or {}
            if cost:
                parts = [f"{cur.upper()} {value:.6f}" for cur, value in sorted(cost.items())]
                line.add_row("cost", ", ".join(parts))
            line.add_row("note", "[dim]built-in prices are dated defaults and may change[/dim]")
            console.print(line)
    elif command == "/skill":
        _skill_command(arg, console, cwd, agent)
    elif command == "/mcp":
        _mcp_status_command(console, mcp_manager, registry)
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
            # A fork's transcript holds messages only (no copied usage record
            # lines), so its historical usage baseline starts empty — and the
            # new ledger must not inherit the old transcript's turns.
            session_state.usage_baseline = SessionUsageTotals()
            session_state.process_usage = SessionUsageTotals()
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


_SAVE_USAGE = "/save <fact>"
# No square brackets here: rich would read "[kind=...]" as a markup tag and
# swallow it, which is exactly what the usage line must not do.
_SAVE_OPTIONS_HELP = (
    "Optional key=value tokens before the fact: kind=<kind> "
    "temporal=<current_state|past_event|future_plan> event_at=<ISO-8601> entities=<a,b>"
)
_SAVE_OPTION_KEYS = {"kind": "kind", "temporal": "temporal_kind", "event_at": "event_at"}


def _parse_save_options(arg: str) -> tuple[dict[str, Any], str]:
    """Split ``/save`` arguments into MemoryManager.save keyword options and the fact.

    Leading ``key=value`` tokens (kind / temporal / event_at / entities) are
    options; the first token that is not one of them starts the fact text, so
    a plain ``/save <fact>`` keeps working unchanged (inner spacing preserved).
    """
    options: dict[str, Any] = {}
    rest = arg.strip()
    while rest:
        parts = rest.split(None, 1)
        key, separator, value = parts[0].partition("=")
        if not separator or not value:
            break
        key = key.casefold()
        if key in _SAVE_OPTION_KEYS:
            options[_SAVE_OPTION_KEYS[key]] = value
        elif key == "entities":
            options["entities"] = [item for item in value.split(",") if item.strip()]
        else:
            break
        rest = parts[1].strip() if len(parts) > 1 else ""
    return options, rest


# Kept in repl.py (not commands/usage.py): tests/test_usage_history.py
# monkeypatches ``nexuscli.entrypoints.repl.UsageStore``, so the helper must
# resolve the store through this module's globals.
def _mcp_status_command(console: Any, mcp_manager: Any, registry: Any) -> None:
    """Show the MCP servers configured for this workspace and their live state.

    Reads the same specs the manager loaded at startup (project + user
    mcp.json), the per-server tools actually registered (``mcp__<server>__…``
    names), and any connection error the last load recorded — the surfaces
    `nexuscli mcp list` shows statically, but reflecting this session.
    """
    if mcp_manager is None:
        console.print("MCP support is disabled (features.mcp=false).")
        return
    if not mcp_manager.specs:
        console.print(
            "No MCP servers configured. Add them to .nexuscli/mcp.json (project) "
            "or ~/.nexuscli/mcp.json (user)."
        )
        console.print(
            "[dim]To expose this CLI's tools to other clients: "
            "nexuscli mcp serve --transport stdio|http --port 3000[/dim]"
        )
        return
    names = list(registry.list_names()) if registry is not None else []
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold", no_wrap=True)
    grid.add_column()
    for spec in mcp_manager.specs.values():
        target = spec.command or spec.url or ""
        tool_names = [n for n in names if n.startswith(f"mcp__{spec.name}__")]
        detail = f"[dim]{spec.type}[/dim] {target}" if target else f"[dim]{spec.type}[/dim]"
        state = (
            f"{len(tool_names)} tools"
            if spec.enabled and tool_names
            else ("enabled, 0 tools" if spec.enabled else "disabled")
        )
        grid.add_row(spec.name, f"{detail} · {state}")
        error = mcp_manager.last_errors.get(spec.name)
        if error:
            grid.add_row("", f"[red]error:[/red] {error}")
    console.print(grid)


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


def _arm_turn_sigint() -> bool:
    """Cancel the running turn on Ctrl+C instead of taking the process down.

    Returns True when a loop-level SIGINT handler was installed. A signal
    handler that raises KeyboardInterrupt does not work here: the exception
    escapes the event loop and asyncio.run() converts it into SystemExit(130),
    which is exactly the failure being fixed. task.cancel() keeps the
    interrupt inside the loop, so the turn's await raises CancelledError and
    the REPL loop handler prints the notice and prompts again.
    """
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    if task is None:
        return False
    try:
        loop.add_signal_handler(signal.SIGINT, task.cancel)
    except (NotImplementedError, RuntimeError, ValueError):
        # Windows proactor loops and non-main threads reject this: an
        # uninterruptible turn beats a REPL that will not start.
        return False
    return True


def _disarm_turn_sigint(armed: bool) -> None:
    if not armed:
        return
    with suppress(NotImplementedError, RuntimeError, ValueError):
        asyncio.get_running_loop().remove_signal_handler(signal.SIGINT)


class SlashCommandCompleter(Completer):
    """Complete slash commands, including the leading "/".

    WordCompleter cannot do this: prompt_toolkit's default word pattern treats
    ``/`` as a delimiter, so with the cursor after ``/sk`` the word under it is
    ``sk`` while every candidate starts with ``/`` — nothing ever matches and Tab
    silently does nothing. Matching on the whole text before the cursor fixes
    that and keeps completion inside the command position: once a space is
    typed the argument is free text and no candidates are offered.
    """

    def __init__(self, commands: list[str]) -> None:
        self.commands = sorted(set(commands))

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return
        for command in self.commands:
            if command.startswith(text):
                yield Completion(command, start_position=-len(text))


def _permission_key_bindings(permission_mode: PermissionModeController) -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add(Keys.BackTab)
    def _toggle_permission_mode(event) -> None:
        permission_mode.toggle()
        event.app.invalidate()

    return bindings


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
    agent: Any | None = None,
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
        *_bottom_toolbar(cwd, model, stats, permission_mode=permission_mode, agent=agent),
        ("class:prompt.dim", "\n\n"),
        ("class:prompt", "❯ "),
    ]


def _bottom_toolbar(
    cwd: str,
    model: str,
    stats: dict[str, Any] | None = None,
    *,
    permission_mode: PermissionMode = "default",
    agent: Any | None = None,
    width: int | None = None,
) -> list[tuple[str, str]]:
    stats = stats or {}
    has_usage = bool(stats.get("has_usage"))
    context_ratio = float(stats.get("context_ratio") or 0)
    context_text = _format_toolbar_percent(context_ratio) if has_usage else "0%"
    mode_label = _permission_mode_label(permission_mode)

    # Priority order when the terminal is narrow: the permission mode must
    # never be the thing that gets cut. At 60 columns the old fixed layout ran
    # past the edge, the mode name fell off first, and Shift+Tab changed state
    # with zero visible feedback — the user could believe they were in
    # read-only plan mode while auto-approve was actually active.
    segments: list[tuple[str, str]] = [
        ("class:toolbar.model", model),
        ("class:toolbar.effort", _effort_label(agent)),
    ]
    model_width = sum(len(text) for _, text in segments)
    mode_width = len(mode_label)
    gap = 2

    # Widths this layout needs, cheapest thing to drop first.
    cwd_text = _shorten_home(cwd)
    bar = _format_toolbar_bar(context_ratio if has_usage else 0)
    usage_width = gap + len(bar) + 1 + len(context_text)
    cwd_width = gap + len(cwd_text)
    mode_block = gap + mode_width + gap + len("⇧Tab")

    def _width() -> int:
        if width is not None:
            return width
        try:
            from prompt_toolkit.application import get_app

            return get_app().output.get_size().columns
        except Exception:  # noqa: BLE001 - no app outside a running prompt
            return shutil.get_terminal_size((120, 24)).columns

    available = _width()
    show_cwd = model_width + mode_block + usage_width + cwd_width <= available
    show_usage = model_width + mode_block + usage_width <= available
    if not show_cwd and show_usage and model_width + mode_block > available:
        show_usage = False
    if model_width + mode_block > available:
        # Last resort on a very narrow pane: shorten the model name, never the
        # mode. Below this the terminal is too small for a status bar at all.
        room = max(8, available - mode_block)
        if len(model) > room:
            segments[0] = ("class:toolbar.model", _ellipsize(model, room))

    if show_usage:
        segments.append(("class:toolbar.gap", "  "))
        segments.append(("class:toolbar.ctx.bar", bar))
        segments.append(("class:toolbar.gap", " "))
        segments.append(("class:toolbar.ctx.value", context_text))
    if show_cwd:
        segments.append(("class:toolbar.gap", "  "))
        segments.append(("class:toolbar.cwd.value", cwd_text))
    segments.append(("class:toolbar.gap", "  "))
    segments.append((f"class:toolbar.mode.{permission_mode}", mode_label))
    segments.append(("class:toolbar.gap", "  ⇧Tab"))
    return segments


def _effort_label(agent: Any) -> str:
    """Status-bar thinking level, pi-style: a suffix on the model name.

    ``off`` and "never touched" both put no ``reasoning_effort`` on the wire, but
    they are different states to the person who just typed ``/effort off``: the
    first is a decision, the second is a default. Showing `:off` for the decision
    makes the status bar confirm the command instead of going silent.
    """
    client = getattr(agent, "llm_client", None)
    if getattr(client, "reasoning_effort_explicit_off", False):
        return ":off"
    level = getattr(client, "reasoning_effort", None)
    if level:
        return f":{level}"
    return ""


def _ellipsize(text: str, limit: int) -> str:
    """Shorten to limit cells, keeping the tail readable.

    Model names carry their distinguishing part at the end (glm-5.3-flash),
    so a plain head-cut would leave a row of identical prefixes.
    """
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"
    return "…" + text[-(limit - 1) :]


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
