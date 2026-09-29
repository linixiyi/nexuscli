"""slash_commands.py — User-defined slash commands backed by markdown files.

A custom command is a ``*.md`` file in ``~/.nexuscli/commands/`` (user scope)
or ``<cwd>/.nexuscli/commands/`` (project scope, wins on name conflicts). The
file body is a prompt template; an optional YAML-ish frontmatter may set a
``description``, ``mode``, ``allowed-tools``, or ``argument-hint``.
``$ARGUMENTS`` in the body receives whatever the user typed after the command
name, ``$1..$9`` receive the first nine positional arguments, and
``!`cmd` `` fragments are replaced with the command's stdout (dangerous
commands are refused by the command guard). When no placeholder is present,
arguments are appended to the prompt. The expanded prompt is sent to the agent
as a normal message.

The built-in ``/init`` command is not file-backed: :func:`build_init_prompt`
expands ``/init [notes]`` (REPL branch and ``-p "/init"`` in cli.py) into a
Chinese initialization prompt that has the current agent inspect the workspace
and create or incrementally update ``AGENTS.md`` at the workspace root. A
custom command named ``init`` keeps priority over the built-in one.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from nexuscli.config import NexusCliConfig
from nexuscli.plugins import load_plugins
from nexuscli.policy.command_guard import CommandGuard, CommandPolicyError
from nexuscli.tools.commands import CommandExecutor
from nexuscli.workflow import WorkflowGraph, WorkflowStep

ARGUMENTS_PLACEHOLDER = "$ARGUMENTS"

INJECTION_TIMEOUT_SECONDS = 30.0

_VALID_MODES = frozenset({"react", "plan", "team"})

# ``!`cmd` `` shell-injection fragments inside a command body.
_SHELL_INJECTION_PATTERN = re.compile(r"!`([^`]+)`")

# ``$1`` .. ``$9`` positional argument placeholders.
_POSITIONAL_PATTERN = re.compile(r"\$([1-9])")

# Fallback executor for ``!`cmd` `` fragments when no config is supplied; its
# CommandGuard refuses built-in destructive commands before anything is
# spawned. Callers that have a config (the REPL) get a guard preloaded with
# policy.command_blacklist via :func:`_injection_executor`.
_INJECTION_EXECUTOR = CommandExecutor()


def _injection_executor(config: NexusCliConfig | None) -> CommandExecutor:
    """Executor for ``!`cmd` `` fragments, honoring the configured blacklist.

    The bash tool validates every command against
    ``CommandGuard(config.policy.command_blacklist)``; command expansion must
    not be a side door around it, so the same blacklist applies here. The
    built-in destructive-command patterns stay active either way.
    """
    if config is None:
        return _INJECTION_EXECUTOR
    return CommandExecutor(command_guard=CommandGuard(list(config.policy.command_blacklist)))


@dataclass(frozen=True, slots=True)
class CustomCommand:
    name: str
    description: str = ""
    body: str = ""
    source: str = "user"  # "user" or "project"
    path: Path | None = None
    mode: str = ""  # "react" | "plan" | "team"; empty = leave unchanged
    allowed_tools: tuple[str, ...] = ()  # empty = no filtering
    argument_hint: str = ""


@dataclass(frozen=True, slots=True)
class CommandExpansion:
    """Fully expanded prompt plus the frontmatter session hints."""

    prompt: str
    mode: str  # from frontmatter; empty = leave unchanged
    allowed_tools: tuple[str, ...]  # empty = no filtering
    rejected_injections: tuple[str, ...]  # refused/failed !`cmd` markers with reasons


def _is_valid_name(name: str) -> bool:
    if not name:
        return False
    compact = name.replace("-", "").replace("_", "")
    return compact.isascii() and compact.isalnum()


def _split_allowed_tools(value: str) -> tuple[str, ...]:
    tools: list[str] = []
    for piece in value.split(","):
        tool = piece.strip()
        if tool and tool not in tools:
            tools.append(tool)
    return tuple(tools)


def _parse_command_file(path: Path, source: str) -> CustomCommand | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    name = path.stem.lower()
    if not _is_valid_name(name):
        return None
    description = ""
    mode = ""
    allowed_tools: tuple[str, ...] = ()
    argument_hint = ""
    body = text
    if text.startswith("---"):
        segments = text.split("---", 2)
        if len(segments) == 3:
            frontmatter, body = segments[1].strip(), segments[2].lstrip("\n")
            for line in frontmatter.splitlines():
                key, _, value = line.partition(":")
                key = key.strip().lower()
                value = value.strip()
                if key == "description":
                    description = value
                elif key == "mode":
                    mode = value if value in _VALID_MODES else ""
                elif key == "allowed-tools":
                    allowed_tools = _split_allowed_tools(value)
                elif key == "argument-hint":
                    argument_hint = value
    if not body.strip():
        return None
    return CustomCommand(
        name=name,
        description=description,
        body=body,
        source=source,
        path=path,
        mode=mode,
        allowed_tools=allowed_tools,
        argument_hint=argument_hint,
    )


def load_slash_commands(cwd: str, home: Path | None = None) -> dict[str, CustomCommand]:
    """Load custom commands; project scope overrides user scope per name.

    Plugin commands (``<cwd>/.nexuscli/plugins/<name>/commands/``) merge after
    both scopes and win on name conflicts.
    """
    base = home if home is not None else Path.home()
    scopes = [
        (Path(base) / ".nexuscli" / "commands", "user"),
        (Path(cwd) / ".nexuscli" / "commands", "project"),
    ]
    commands: dict[str, CustomCommand] = {}
    for directory, source in scopes:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            command = _parse_command_file(path, source)
            if command is not None:
                commands[command.name] = command
    # Plugin commands merge last with the fixed "project" source (the
    # CustomCommand.source field only knows "user"/"project"; no new layer):
    # a plugin command overrides a project command, which overrides the user
    # layer — the same "last scan wins" chain the scopes above already follow.
    for plugin in load_plugins(cwd):
        if plugin.commands_dir is None:
            continue
        for path in sorted(plugin.commands_dir.glob("*.md")):
            command = _parse_command_file(path, "project")
            if command is not None:
                commands[command.name] = command
    return commands


def split_command_message(message: str) -> tuple[str, str] | None:
    """Split ``/name args`` into ``("/name", args)`` for a slash message."""
    stripped = message.strip()
    if not stripped.startswith("/"):
        return None
    head, _, tail = stripped.partition(" ")
    return head.lower(), tail.strip()


def split_positional_args(args: str) -> list[str]:
    """Split *args* into positional tokens for ``$1..$9`` substitution.

    Uses ``shlex.split(..., posix=True)`` so quoting works like a POSIX shell.
    Known limitation: on Windows, backslashes inside quotes are consumed as
    escape characters (``"C:\\path"`` collapses separators); Claude Code also
    splits on whitespace, so this is accepted. Malformed quoting falls back to
    plain whitespace splitting instead of raising.
    """
    try:
        return shlex.split(args, posix=True)
    except ValueError:
        return args.split()


def _first_line(text: str) -> str:
    """Return the first non-empty line of *text* as a single-line reason."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


async def _run_injection(command: str, executor: CommandExecutor) -> tuple[str, str | None]:
    """Run one ``!`cmd` `` fragment through *executor*'s CommandGuard.

    Returns ``(replacement, rejection)`` where *rejection* is ``None`` on
    success or the reason the command was refused or failed. Every failure is
    swallowed — one bad command must not break the whole expansion.
    """
    try:
        result = await executor.execute(
            command=command,
            timeout=INJECTION_TIMEOUT_SECONDS,
        )
    except CommandPolicyError:
        reason = "blocked by command guard"
        return f"[refused: {command} — {reason}]", reason
    except Exception as exc:  # noqa: BLE001 - any failure becomes a marker
        reason = _first_line(str(exc)) or exc.__class__.__name__
        return f"[failed: {command} — {reason}]", reason
    if result.timed_out:
        reason = "timed out"
    elif result.exit_code != 0:
        reason = _first_line(result.stderr) or f"exit code {result.exit_code}"
    else:
        return result.stdout.rstrip("\r\n"), None
    return f"[failed: {command} — {reason}]", reason


async def _expand_shell_injections(
    prompt: str,
    executor: CommandExecutor,
) -> tuple[str, tuple[str, ...]]:
    """Replace every ``!`cmd` `` fragment; collect refused/failed markers."""
    rejections: list[str] = []
    pieces: list[str] = []
    cursor = 0
    for match in _SHELL_INJECTION_PATTERN.finditer(prompt):
        replacement, rejection = await _run_injection(match.group(1), executor)
        if rejection is not None:
            rejections.append(replacement)
        pieces.append(prompt[cursor : match.start()])
        pieces.append(replacement)
        cursor = match.end()
    pieces.append(prompt[cursor:])
    return "".join(pieces), tuple(rejections)


def _fill_positional_args(prompt: str, positional: list[str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return positional[index - 1] if index <= len(positional) else ""

    return _POSITIONAL_PATTERN.sub(_replace, prompt)


async def expand_command(
    command: CustomCommand,
    args: str,
    config: NexusCliConfig | None = None,
) -> CommandExpansion:
    """Expand *command* into a :class:`CommandExpansion`.

    Substitution order: ``!`cmd` `` fragments first, then ``$1..$9`` (filled
    from :func:`split_positional_args`, 1-based, out-of-range becomes empty),
    then ``$ARGUMENTS`` with the raw *args*. A body without any placeholder
    gets *args* appended, mirroring :func:`expand_custom_command`. When
    *config* is given, injected commands are validated against the configured
    ``policy.command_blacklist`` just like the bash tool.
    """
    prompt, rejected_injections = await _expand_shell_injections(
        command.body,
        _injection_executor(config),
    )
    has_placeholder = bool(_POSITIONAL_PATTERN.search(prompt)) or ARGUMENTS_PLACEHOLDER in prompt
    prompt = _fill_positional_args(prompt, split_positional_args(args))
    if ARGUMENTS_PLACEHOLDER in prompt:
        prompt = prompt.replace(ARGUMENTS_PLACEHOLDER, args)
    elif not has_placeholder and args:
        prompt = f"{prompt.rstrip()}\n\n{args}"
    return CommandExpansion(
        prompt=prompt,
        mode=command.mode,
        allowed_tools=command.allowed_tools,
        rejected_injections=rejected_injections,
    )


def expand_custom_command(command: CustomCommand, args: str) -> str:
    if ARGUMENTS_PLACEHOLDER in command.body:
        return command.body.replace(ARGUMENTS_PLACEHOLDER, args)
    if args:
        return f"{command.body.rstrip()}\n\n{args}"
    return command.body


# ---------------------------------------------------------------------------
# Built-in /init command (workspace AGENTS.md bootstrap)
# ---------------------------------------------------------------------------

# Model-facing prompt behind ``/init [notes]``. Chinese on purpose: it is
# consumed by the model, not by the user (repo convention, see subagent.py).
_INIT_PROMPT_TEMPLATE = """\
你是 NexusCLI 内置 /init 命令的执行者，目标是为后续 Agent 生成或增量更新一份工作区指南。

目标位置：
- 工作区根目录：{root}
- 目标文件：{target}（文件名必须是 AGENTS.md；只针对当前工作区，不要写到用户主目录）

执行要求：
1. 先只读地调研工作区，此阶段不做任何修改：浏览目录结构；阅读 README 与构建配置\
（如 pyproject.toml / package.json / Makefile），归纳真实存在的构建、测试、lint 命令；\
检查工作区是否已存在 AGENTS.md 与 NEXUS.md，存在则先完整读取。
2. 若 {target} 不存在，用 write_file 新建；若已存在，必须用 edit_file 增量更新、\
严禁整体覆盖：保留仍然准确的内容，只修正过时或缺失的部分。
3. AGENTS.md 内容须涵盖（只写从仓库核实到的事实，不得编造）：
   - 构建 / 测试 / lint 命令；
   - 代码风格与目录结构约定；
   - 安全边界：权限规则、HITL 审批、审计日志等既有约定；
   - Agent 协作注意事项（如先只读探索再动手、改动最小化、如何验证改动）。
4. 所有写入必须通过 write_file / edit_file 工具完成（从而走审批链），\
禁止用 bash 重定向（>、>>、tee 等）绕过审批。
5. 完成后用一小段话总结新建或更新了哪些章节，并给出文件路径。
"""

# Appended to the prompt only when the user passed notes after ``/init``.
_INIT_NOTES_SECTION = """

用户随 /init 附带的重点关注（作为额外关注点融入 AGENTS.md，不得因此省略上面的常规内容）：
{notes}
"""


def build_init_prompt(notes: str, cwd: str) -> str:
    """Build the Chinese prompt behind the built-in ``/init [notes]`` command.

    The prompt has the current agent first inspect the workspace read-only,
    then create or incrementally update ``AGENTS.md`` at the workspace root —
    editing the existing file instead of overwriting it — and write only via
    ``write_file`` / ``edit_file`` so the normal approval chain applies.
    *notes* carries the user's extra focus points; when empty the notes
    section is omitted entirely.
    """
    root = Path(cwd)
    prompt = _INIT_PROMPT_TEMPLATE.format(root=str(root), target=str(root / "AGENTS.md"))
    stripped = notes.strip()
    if stripped:
        prompt += _INIT_NOTES_SECTION.format(notes=stripped)
    return prompt


# ---------------------------------------------------------------------------
# Built-in /expert command + shared workflow step dispatch (spec 2.4 merge:
# /expert and /workflow run share one step-graph engine and prompt layer)
# ---------------------------------------------------------------------------

# Fixed three-step expert loop behind ``/expert <topic>``: research → execute
# → report, a linear chain. Chinese on purpose: consumed by the model, not by
# the user (repo convention, see subagent.py and build_init_prompt). ZCode's
# built-in expert workflow has eight phases (workflow/definition.ts:39-48);
# the minimal expert loop keeps three.
_EXPERT_STEPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "research",
        "围绕主题「{topic}」做只读调研：梳理现状、相关文件与约束，"
        "不做任何修改，输出要点清单（每条给出依据：绝对路径或核实到的事实）。",
        (),
    ),
    (
        "execute",
        "基于上一步的调研要点，完成主题「{topic}」的核心工作："
        "按最小改动原则实施，过程中做必要的只读验证。",
        ("research",),
    ),
    (
        "report",
        "对照主题「{topic}」的目标复核上一步的成果：指出缺口与风险，"
        "输出最终综合报告（结论、关键路径、验证结果）。",
        ("execute",),
    ),
)


def build_expert_graph(topic: str) -> WorkflowGraph:
    """Build the fixed three-step expert loop graph for *topic*.

    Constructs the graph directly (no JSON round-trip); the topic is
    substituted into each step's prompt template.
    """
    steps = {
        step_id: WorkflowStep(
            id=step_id,
            prompt=prompt_template.format(topic=topic),
            depends_on=list(depends_on),
        )
        for step_id, prompt_template, depends_on in _EXPERT_STEPS
    }
    return WorkflowGraph(steps=steps)


def build_step_dispatch_prompt(
    graph: WorkflowGraph,
    step_id: str,
    upstream_reports: dict[str, str],
) -> str:
    """Build the controlled orchestration prompt for dispatching one step.

    Shared by ``/workflow run`` and ``/expert`` (spec 2.4 merge decision): the
    prompt fixes the subagent to exactly one step, lists the whole graph in
    topological order for orientation, injects upstream reports verbatim, and
    ends with the self-contained report requirement. Mirrors ZCode's
    ``buildPhasePrompt`` shape (workflow/expert/prompts.ts:13-52): frame, task,
    objective, upstream artifacts, scope constraint.
    """
    order = graph.topological_order()
    step = graph.steps[step_id]
    lines = [
        "你是受控工作流编排中的子代理，只执行分配给你的步骤，不要执行其他步骤，也不要反问用户。",
        "",
        "工作流步骤清单（拓扑序）：",
    ]
    for other_id in order:
        deps = graph.steps[other_id].depends_on
        dep_text = f"依赖：{', '.join(deps)}" if deps else "无依赖"
        lines.append(f"- {other_id}（{dep_text}）")
    position = order.index(step_id) + 1
    lines.append("")
    lines.append(f"本次执行的步骤：{step_id}（第 {position}/{len(order)} 步）")
    lines.append(step.prompt)
    if upstream_reports:
        lines.append("")
        lines.append("上游步骤报告：")
        for dep_id in step.depends_on:
            lines.append(f"### {dep_id}")
            lines.append(upstream_reports[dep_id])
    lines.append("")
    lines.append("完成后输出自包含的步骤报告：明确结论、关键路径与验证结果。")
    return "\n".join(lines)
