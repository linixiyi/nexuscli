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
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from nexuscli.config import NexusCliConfig
from nexuscli.policy.command_guard import CommandGuard, CommandPolicyError
from nexuscli.tools.commands import CommandExecutor

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
    """Load custom commands; project scope overrides user scope per name."""
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
